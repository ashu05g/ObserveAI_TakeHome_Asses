# Technical Write-Up

Every claim in this document maps to a specific file and line range in the source. Citations are written as `path/to/file.py:start-end`. Verify any claim by opening that range.

## 1. Tools, Frameworks, and APIs

### Voice orchestrator — VAPI

VAPI handles the telephony, transcription, TTS, and turn-taking. We connect by provisioning a VAPI-native US phone number and pointing it at an "assistant" (a config object holding model, prompt, voice, tools, and server URL). We picked VAPI over Retell or LiveKit because the time-to-first-call is shortest and the function-tool model maps cleanly to one FastAPI endpoint. **VAPI-native number** over Twilio because it saves a separate account and billing relationship for the demo.

The assistant + tool configurations are managed as code. `scripts/vapi_sync.py` is the source of truth:

- The assistant config is built at `scripts/vapi_sync.py:101-170` (`build_assistant_config`).
- The tool config is built at `scripts/vapi_sync.py:64-98` (`build_tool_config`).
- Both are pushed via REST API to VAPI (PATCH if a same-named resource exists, else POST) by `upsert_assistant` (lines 190-202) and `upsert_tool` (lines 173-187). The PATCH path explicitly strips the `type` field (line 179) because VAPI's `UpdateToolDTO` rejects it as immutable.
- The system prompt is assembled at sync time: `assemble_system_prompt` at lines 46-61 reads `prompts/agent_system_prompt.txt` and concatenates the four `docs/knowledge_base/*.md` files listed at lines 38-43.

Run `python scripts/vapi_sync.py` after any prompt or config edit — it converges your VAPI workspace to whatever the repo says.

### STT — Deepgram Nova-3 (via VAPI)

Configured at `scripts/vapi_sync.py:126-130`:

```python
"transcriber": {
    "provider": "deepgram",
    "model": "nova-3",
    "language": "en-US",
},
```

Telephony audio is 8 kHz with background noise, speaker overlap, and clipped pronunciation. Deepgram's Nova family is purpose-built for telephony audio and consistently outperforms general-purpose models like Whisper on noisy 8 kHz input, with sub-300 ms streaming latency.

### TTS — ElevenLabs Flash v2.5 (via VAPI)

Configured at `scripts/vapi_sync.py:116-125`:

```python
"voice": {
    "provider": "11labs",
    "voiceId": "21m00Tcm4TlvDq8ikWAM",   # Rachel (default ElevenLabs library)
    "model": "eleven_flash_v2_5",
},
```

Flash v2.5 is ElevenLabs' lowest-latency model. With STT + LLM-first-token + TTS-TTFB the system stays under one second per turn. The `voiceId` is the canonical ElevenLabs hash for the Rachel voice — using a named string ("rachel", "jennifer") fails when the workspace has ElevenLabs credentials linked because VAPI looks up named voices against the user's account rather than the curated default list.

### LLM split — GPT-4.1 (agent) + GPT-4.1-mini (post-call)

Two different OpenAI models, two different jobs.

**Agent brain** at `scripts/vapi_sync.py:109-115`:

```python
"model": {
    "provider": "openai",
    "model": "gpt-4.1",
    "temperature": 0.3,
    "messages": [{"role": "system", "content": system_prompt}],
    "toolIds": [tool_id],
},
```

GPT-4.1 over GPT-4o because it has better instruction following and function-calling reliability and is cheaper ($2/$8 vs $5/$15 per million tokens). Reasoning models (o1, o3) ruled out: their inference latency breaks the conversational feel.

**Post-call analysis** at `api/services/analysis.py:106-116` (`analyze_transcript`):

```python
response = await client.chat.completions.create(
    model="gpt-4.1-mini",
    temperature=0,
    response_format={"type": "json_object"},
    ...
)
```

**QA scoring** at `api/services/qa_scorer.py:72-85` (`score_call`): same model, same `temperature=0`, same `response_format={"type": "json_object"}`.

GPT-4.1-mini handles structured JSON extraction with stronger schema adherence than 4o-mini at moderate cost ($0.40/$1.60 per million). The async post-call path is latency-insensitive so a heavier model is acceptable. `gpt-4.1-nano` was considered for additional savings but is unreliable on the 9-item rubric structure.

### Backend — FastAPI on Railway

Three HTTP routes:

- `POST /lookup` — `api/routers/lookup.py:26-74`
- `POST /webhook` — `api/routers/webhook.py:23-51`
- `GET /health` — `api/main.py:51-53`

The post-call pipeline runs as a FastAPI `BackgroundTask` so the webhook handler returns within VAPI's timeout while analysis, QA scoring, and Airtable writes happen asynchronously. Scheduled at `api/routers/webhook.py:48`:

```python
background_tasks.add_task(analysis.run_post_call_pipeline, event)
```

Pipeline body at `api/services/analysis.py:28-103`.

App entry: `api/main.py:46` instantiates FastAPI; `api/main.py:34-43` defines the `lifespan` context manager that validates all 9 required env vars at startup (listed at `api/main.py:16-26`) and warms the Langfuse client.

### Data — Airtable

Two tables, schema and seed managed by scripts.

- **Schema setup** (`scripts/setup_airtable_schema.py`): creates `callers` and `interactions` tables idempotently via Airtable's Meta API. The `callers` schema includes `date_of_birth` as the second-factor authentication challenge field (see "Authentication" below).
- **Seed data** (`scripts/seed_airtable.py`): upsert by phone — looks up existing rows, PATCHes if found, POSTs if not. Five test callers defined as the `CALLERS` constant; each has a realistic DOB.

**Read** at `api/services/airtable.py:29-44` (`get_caller_by_phone`):

```python
response = await client.get(
    f"/{table}",
    params={
        "filterByFormula": f"{{phone}}='{phone}'",
        "maxRecords": 1,
    },
)
```

**Write** at `api/services/airtable.py:47-76` (`write_interaction`). The `typecast: True` flag at line 73 makes Airtable auto-create unknown `singleSelect` and `multipleSelects` option values, so the LLM can emit new `topics_mentioned` without manual schema sync.

### Knowledge base — markdown inlined into the system prompt

Four files in `docs/knowledge_base/`: `about_company.md`, `policies_and_coverage.md`, `claims_process.md`, `customer_service.md`. They're concatenated into the agent's system prompt at sync time by `assemble_system_prompt` at `scripts/vapi_sync.py:46-61`. The base prompt instructs the LLM to consult this section for general questions (vs the per-caller claim record questions, which it answers from the `lookup_caller` tool result).

Inline rather than a search tool because the total is ~5 KB — fits in one prompt easily. Past ~30 KB or if KB content needs versioning, this becomes a `search_knowledge_base` function tool.

### Observability — Langfuse v3 with single-trace-per-call

Every observation for one VAPI call rolls up under one Langfuse trace. Mechanism:

- Derive a deterministic 128-bit `trace_id` from VAPI's `call_id` at `api/services/langfuse_client.py:91-92` (`_trace_id_for_call` — SHA256 prefix).
- Wrap it in an OpenTelemetry parent context at `api/services/langfuse_client.py:103-110` (`_parent_context_for_call`). The synthetic root span ID comes from `_root_span_id_for_call` at lines 95-100.
- Attach that context with `otel_context.attach(...)` before starting any span inside the three public helpers — `trace_pipeline` (lines 132-151), `trace_lookup` (lines 154-176), `log_call_event` (lines 179-198). All spans created under that context share the trace.

Langfuse v3 is OTel-native, so spans created under this context group correctly. Auto-instrumentation of OpenAI calls happens via `get_openai_client` at lines 76-88 — it returns `langfuse.openai.AsyncOpenAI` when Langfuse is enabled. Each `chat.completions.create` then becomes a generation span automatically with input/output/model/latency/tokens.

Trace URL written to Airtable at `api/services/analysis.py:78` (passed into `InteractionLog(langfuse_trace_url=trace.url)`); URL computed deterministically at `api/services/langfuse_client.py:113-115` so it can be embedded into emails and Airtable rows even before any span has been emitted.

Live webhook events subscribed at `scripts/vapi_sync.py:165-169`:

```python
"serverMessages": [
    "end-of-call-report",
    "status-update",
    "transcript",
],
```

`model-output` is deliberately *not* subscribed (`scripts/vapi_sync.py:156-164` documents why) — it fires per streaming token chunk and would 50× the per-call event volume.

### Authentication — two-factor knowledge challenge

The assignment specifies confirming identity with *"Am I speaking with {first name} {last name}?"*. That pattern is weak from a compliance standpoint — the agent reveals the name, so a caller who has only the phone number can social-engineer their way through by saying "yes". HIPAA and PCI both require reasonable identity verification before disclosing protected information; the literal-spec pattern would not pass an audit.

The implemented flow uses **two-factor knowledge auth**:

1. Agent asks for the caller's phone number, looks them up (silently — the agent has the record in context but does not speak any of it).
2. Agent asks: *"To verify your identity, could you please confirm your full name and your date of birth?"*
3. Caller supplies both. The LLM performs fuzzy matching against the stored values:
   - Name: case-insensitive, tolerant of pronunciation variation.
   - DOB: parses spoken formats ("April fifteenth nineteen eighty-five", "4/15/85", "fifteenth of April 1985", numeric, etc.) and compares against the stored ISO date. Ambiguous formats (e.g., "06/05/2000") default to US month-day-year unless the caller explicitly says day-first.
4. Branches on the response:
   - Both match → "Thank you for confirming, {first_name}." Proceeds to claim handling.
   - Only name given → asks specifically for DOB. Only DOB given → asks specifically for name.
   - Either or both wrong → "I'm sorry, that doesn't quite match our records." (Does not reveal which part was wrong.) One retry.
   - Second failure → escalates to a human callback. Call ends without disclosing claim details.

This is still not full multi-factor — there's no possession factor (no OTP via SMS to the registered number) and DOB is not a strong secret in real-world threat models. Code citations: `prompts/agent_system_prompt.txt` AUTHENTICATION FLOW section, `api/models/caller.py:32-34` (`date_of_birth` field), `api/routers/lookup.py:115` (returned in `/lookup` response), `scripts/setup_airtable_schema.py` (Airtable column).

In production we would add: an OTP sent to the registered phone as the possession factor; a server-side `verify_identity` tool that compares signed name+DOB inputs (so the LLM never has access to the stored values, eliminating the social-engineering surface); voice biometrics for repeat callers.

### Alerts — Resend

`api/services/email_alert.py:13-55` (`send_alert`). Triggered conditionally from `api/services/analysis.py:87-102`. Threshold logic at `api/services/analysis.py:119-122`:

```python
def should_alert(qa_score: float | None, sentiment: str) -> bool:
    return sentiment == "negative" or (
        qa_score is not None and qa_score < ALERT_QA_THRESHOLD
    )
```

`ALERT_QA_THRESHOLD = 0.6` at `api/services/analysis.py:22`. Severity tier (HIGH < 0.5 in subject line) at `api/services/email_alert.py:10` and `api/services/email_alert.py:21-25`.

### Tests — 175 unit tests, HTTP-boundary mocking

Each external dependency (Airtable, OpenAI, Resend, Langfuse, VAPI's REST API) is mocked at the HTTP boundary using `respx`. Test count verified: `pytest --collect-only` reports 175 collected.

Test files mirror source modules one-to-one: `tests/test_models.py`, `tests/test_phone.py`, `tests/test_auth.py`, `tests/test_airtable_service.py`, `tests/test_email_alert.py`, `tests/test_langfuse_client.py`, `tests/test_qa_scorer.py`, `tests/test_analysis.py`, `tests/test_lookup_router.py`, `tests/test_webhook_router.py`, `tests/test_vapi_sync.py`, `tests/test_main.py`. Shared fixtures (env vars, langfuse reset) live in `tests/conftest.py`.

### Why this stack scales

- **Telephony**: VAPI handles concurrency at the platform layer.
- **Backend**: FastAPI + uvicorn workers scale horizontally on Railway. Each request is short; `/lookup` is bound by one Airtable round-trip; `/webhook` returns immediately and offloads to `BackgroundTask`.
- **Airtable**: ~5 req/sec/base. At >100 concurrent calls, migrate to Postgres on Supabase — only `api/services/airtable.py` changes; the Pydantic models (`api/models/caller.py`, `api/models/interaction.py`) and router code stay identical.
- **Post-call pipeline**: today it runs in-process via `BackgroundTask`. Queueing the same function (`api/services/analysis.py:28` — `run_post_call_pipeline(event)`) into Redis / RQ / Celery is a drop-in change.
- **Langfuse**: cloud tier handles 50k events/month free; self-host on a single Railway instance for unlimited.

---

## 2. Problem Solving & Debugging

### Challenge: the agent ignored every tool result

**Symptom.** Test calls. Caller says "415-555-0001". `/lookup` returns Jane Doe with `claim_status: "approved"` (Railway logs at `api/routers/lookup.py:99-106` confirm `lookup: HIT phone=+14155550001 name=Jane Doe claim=CLM-2024-0001 status=approved`). The agent then asks "Am I speaking with you?" — no name. When asked for claim status it says "currently under review" — the *pending* response template, on an approved claim. The system prompt at `prompts/agent_system_prompt.txt` has the right wording with explicit examples, yet the LLM consistently behaved as if it had received an empty tool result.

**False starts.** Patched in this order:

1. Rewrote the prompt to use literal example mappings instead of `{first_name}` placeholders.
2. Switched from GPT-4o to GPT-4.1, dropped temperature (now `temperature: 0.3` at `scripts/vapi_sync.py:112`).
3. JSON-stringified the tool result at `api/routers/lookup.py:56` (`json.dumps(result_dict)`) in case VAPI couldn't handle a dict.
4. Broadened the role match in `extract_airtable_id` to include `tool_call_result`, `tool`, `function` at `api/services/analysis.py:23` and `api/services/analysis.py:147-159`.

None of these fixed the underlying issue. We were patching without evidence.

**The diagnostic.** Built `scripts/inspect_call.py`. Given a VAPI `call_id`, it fetches `/call/{id}` from VAPI's REST API and dumps the assistant config used (`_dump_assistant` at lines 32-69), the full message thread the LLM actually saw (`_dump_messages` at lines 85-114), every tool call with its real arguments and result, and the transcript. Ran it against the failing call. Found this in the message thread:

```json
{
  "name": "lookup_caller",
  "role": "tool_call_result",
  "result": "Success.",
  "toolCallId": "call_iANuENeOKG58rHpiBd3X2rYj"
}
```

The LLM had seen `"Success."` as the result — not Jane Doe's data.

**Root cause.** The tool was configured with `"async": true` in VAPI (the dashboard default for function tools). Async tools are fire-and-forget — VAPI sends the HTTP request to the tool URL but ignores the response body and injects a generic `"Success."` as the LLM-visible result. Our `/lookup` was returning the right data at `api/routers/lookup.py:107-127`; VAPI was discarding it.

**Fix.** One line in the tool config at `scripts/vapi_sync.py:64-98`. Line 72:

```python
"async": False,
```

Plus a regression test in `tests/test_vapi_sync.py` (the `test_tool_is_synchronous` test) so this can't sneak back in. Plus the `inspect_call.py` script committed as a first-class diagnostic.

**Lesson.** Patch-fixing without evidence is a tax. Building the diagnostic tool should have been the first move.

### If I had one more week

1. **Move Airtable to Postgres.** The 5 req/sec rate limit is the first thing that will break under real load. Only `api/services/airtable.py` changes.
2. **Real-time agent quality monitoring.** Today QA scoring runs post-call. With a sliding-window evaluator on the live transcript (using the `transcript` webhook events already subscribed at `scripts/vapi_sync.py:165-169`), low-score calls could be escalated to a human supervisor *during* the call.
3. **Webhook event buffering.** For high-volume production, accept the webhook, drop it into Redis, respond in <100ms; a worker process runs Langfuse logging (`api/services/langfuse_client.py:179-198`) + the post-call pipeline (`api/services/analysis.py:28-103`).
4. **Server-side identity verification.** Today the LLM does the name+DOB matching in-context (see Authentication section above). The next step is a dedicated `verify_identity` server-side tool: the LLM passes what the caller said, the server compares to Airtable, returns boolean. The LLM never has access to the stored secrets, closing the prompt-injection surface. Add OTP as the possession factor on top.
5. **Prompt versioning in Langfuse.** Langfuse supports versioned prompts with A/B testing. Wire `api/utils/prompts.py:7-9` (`load_prompt`) to fetch from Langfuse so changes are versioned and roll-backable.
6. **Replay testing.** Save a corpus of transcripts and re-run them through the LLM pipeline (`api/services/analysis.py:106-116` + `api/services/qa_scorer.py:72-85`) whenever prompts change. Diff the QA scores.
7. **Retool dashboard.** Surfaces containment rate, QA score trends, sentiment distribution, per-rubric pass rates. Reads from Airtable; no backend change needed.
8. **Per-call cost tracking.** Langfuse captures token counts per LLM call (via `langfuse.openai.AsyncOpenAI` at `api/services/langfuse_client.py:83`); aggregate them per VAPI `call_id` on the dashboard.

---

## 3. Data & Metrics Evaluation

### Metrics tracked

Each metric maps to specific Airtable columns or Langfuse fields. Column definitions for `interactions` are in the Pydantic model at `api/models/interaction.py:10-28`.

| Metric | Definition | Source |
|---|---|---|
| **Containment rate** | calls where `escalated == false` / total | Airtable `interactions.escalated` (model field at `api/models/interaction.py:27`); set in pipeline at `api/services/analysis.py:77` |
| **Authentication success rate** | calls where `authenticated == true` / total | Airtable `interactions.authenticated` (`api/models/interaction.py:17`); set at `api/services/analysis.py:67` |
| **Average QA score (weekly)** | mean of `qa_score` last 7 days, excluding nulls | Airtable `interactions.qa_score` (`api/models/interaction.py:24`); computed in `weighted_score` at `api/services/qa_scorer.py:88-105` |
| **Per-rubric pass rate** | for each of 9 rubric items, % of *applicable* calls scoring ≥ 0.5 | Airtable `interactions.qa_breakdown` (JSON column, `api/models/interaction.py:25`); rubric definition at `api/services/qa_scorer.py:17-63` |
| **Average handle time** | mean `call_duration_seconds` | Airtable `interactions.call_duration_seconds` (`api/models/interaction.py:18`) |
| **Sentiment distribution** | counts of positive / neutral / negative | Airtable `interactions.sentiment` (`api/models/interaction.py:21`) |
| **First-call resolution** | calls with no second call from same `caller` within 24h | Airtable join (`interactions.caller_airtable_id` → `callers`) — model field at `api/models/interaction.py:15` |
| **LLM cost per call** | sum of analysis + QA generation token cost | Langfuse traces (token counts captured automatically via `langfuse.openai.AsyncOpenAI` returned by `api/services/langfuse_client.py:76-88`) |
| **Tool latency** | wall-clock duration of `lookup_caller` invocation | Langfuse span emitted by `trace_lookup` at `api/services/langfuse_client.py:154-176` |

### Using the data

Run the QA scorer continuously, not as point-in-time review. Track the weekly average per rubric item. If a specific item drops 10 points week-over-week, that's a signal the agent's wording on that branch has degraded.

Two actions follow:

1. **Pull the low-scoring transcripts** for that rubric item (Airtable filter on `interactions.qa_breakdown`), open them in Langfuse via the per-call `langfuse_trace_url` field (set at `api/services/analysis.py:78`) to inspect the LLM input/output at the failing turn.
2. **Adjust the prompt** in `prompts/agent_system_prompt.txt`, then re-run the QA scorer (`api/services/qa_scorer.py:72-85`) on the same transcripts to verify the improvement before deploying.

### Worked example — containment rate drop

> Week-over-week, containment falls from 87% to 79%.

1. Query Airtable: `WHERE escalated = TRUE AND timestamp >= last_monday`. `escalated` lives at `api/models/interaction.py:27`.
2. Group by `detected_intent` (`api/models/interaction.py:23`): 60% are `claim_status` + `requires_documentation` (these are two of the five allowed `Intent` values defined at `api/models/interaction.py:7`).
3. Pull the 12 transcripts (Airtable column `interactions.transcript` at `api/models/interaction.py:19`). Pattern: callers who hear "requires documentation" immediately ask for a human because they don't understand what to submit.
4. Open one trace via the `langfuse_trace_url` field. Look at the agent's response at that turn. Confirm the wording is vague.
5. Update `prompts/agent_system_prompt.txt` (the `requires_documentation` clause in the CLAIM HANDLING section) to add specifics. Commit. Run `python scripts/vapi_sync.py` — `assemble_system_prompt` at `scripts/vapi_sync.py:46-61` rebuilds the prompt and `upsert_assistant` at lines 190-202 PATCHes it onto VAPI.
6. Re-run QA scorer (`api/services/qa_scorer.py:72-85`) on the same 12 transcripts — verify `documentation_instructions` rubric (defined at `api/services/qa_scorer.py:38-42`) pass rate moves from 30% to 90%.
7. Watch next week's containment rate. If it recovers, fix is validated.

The whole loop — detect, diagnose, patch, verify — is closeable within a single day because every signal is captured in Airtable + Langfuse, the QA scorer is replayable against historical transcripts, and the prompt is one git commit + one script run from being live.
