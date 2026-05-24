# Architecture

## System overview

```mermaid
flowchart LR
    Caller([Caller])
    Twilio[VAPI PSTN Number]
    VAPI[VAPI<br/>Voice Orchestrator]
    STT[Deepgram Nova-3<br/>STT]
    LLM[GPT-4o<br/>Agent Brain]
    TTS[ElevenLabs Flash v2.5<br/>TTS]

    subgraph Backend["FastAPI on Railway"]
        Lookup["/lookup<br/>caller lookup tool"]
        Webhook["/webhook<br/>end-of-call ingest"]
        Pipeline[Post-call Pipeline<br/>BackgroundTask]
        QA[QA Scorer<br/>GPT-4o-mini]
        Analysis[Transcript Analysis<br/>GPT-4o-mini]
    end

    Airtable[(Airtable<br/>callers + interactions)]
    Langfuse[Langfuse<br/>LLM traces]
    Resend[Resend<br/>email alerts]
    Retool[Retool Dashboard]

    Caller <-->|PSTN| Twilio
    Twilio --> VAPI
    VAPI <--> STT
    VAPI <--> LLM
    VAPI <--> TTS
    LLM -->|lookup_caller tool| Lookup
    Lookup --> Airtable
    VAPI -->|end-of-call webhook<br/>X-VAPI-Secret| Webhook
    Webhook --> Pipeline
    Pipeline --> Analysis
    Pipeline --> QA
    Pipeline --> Airtable
    Pipeline -.->|low score or<br/>negative sentiment| Resend
    Analysis -.-> Langfuse
    QA -.-> Langfuse
    Airtable --> Retool
```

Legend: solid arrows = synchronous call/write; dotted arrows = observability or conditional alert.

## Happy-path call sequence

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller
    participant V as VAPI
    participant A as Agent (GPT-4o)
    participant API as FastAPI /lookup
    participant AT as Airtable
    participant H as FastAPI /webhook
    participant P as Post-call pipeline

    C->>V: dial
    V->>A: "first message" prompt
    A->>C: greeting + ask for phone
    C->>A: "415-555-0001"
    A->>API: lookup_caller(phone="415-555-0001")
    API->>API: normalize to +14155550001
    API->>AT: filterByFormula
    AT-->>API: {Jane Doe, claim approved}
    API-->>A: {found:true, first_name, claim_status, airtable_record_id}
    A->>C: "Am I speaking with Jane Doe?"
    C->>A: "Yes"
    A->>C: "Great — your claim is approved..."
    C->>A: "Thanks, goodbye"
    V->>H: POST /webhook (end-of-call-report)
    H-->>V: 200 (returns immediately)
    H->>P: schedule background task
    P->>P: analyze transcript (GPT-4o-mini)
    P->>P: QA score (GPT-4o-mini)
    P->>AT: write interaction row
```

## Error path — caller not found

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller
    participant A as Agent
    participant API as FastAPI /lookup
    participant AT as Airtable

    C->>A: "415-555-9999"
    A->>API: lookup_caller(phone)
    API->>AT: filterByFormula
    AT-->>API: {records: []}
    API-->>A: {found:false}
    A->>C: "I wasn't able to find an account with that number..."
    Note over C,A: agent offers re-verification or human callback
```

## Monitoring touchpoints

| Where | What is captured |
|---|---|
| Railway logs | uvicorn access log, app-level structured logs, unhandled exceptions |
| Langfuse | every LLM call: input, output, latency, model, token count; grouped under a `post_call_pipeline` parent span per call |
| Airtable `interactions` | source of truth for every completed call — transcript, summary, sentiment, QA score, per-rubric breakdown |
| Email alerts (Resend) | low-score or negative-sentiment calls — includes deep-link to Langfuse trace |
| Retool dashboard | reads Airtable; surfaces containment rate, avg QA score, sentiment distribution, per-call drill-down |

## Error capture points

| Boundary | Failure mode | Behavior |
|---|---|---|
| `/webhook` | Missing/wrong `X-VAPI-Secret` | 401 returned, no work done |
| `/webhook` | Malformed payload | 422 returned, no work done |
| `/lookup` | Invalid phone format | 400 returned with reason |
| Post-call pipeline | LLM call failure | logged with `exception`, pipeline aborted, no partial Airtable write |
| Post-call pipeline | Airtable write failure | logged, pipeline continues to email step |
| Post-call pipeline | Email send failure | logged, swallowed (alerts are best-effort) |
