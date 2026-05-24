# Observe Claims Agent

Inbound voice AI agent for an insurance claims hotline. Built for the Observe.AI AI Agent Engineer take-home.

Stack: **VAPI** (telephony + voice orchestration) · **Deepgram Nova-3** (STT) · **ElevenLabs Flash v2.5** (TTS) · **GPT-4o** (agent) · **GPT-4o-mini** (post-call analysis + QA scoring) · **FastAPI on Railway** · **Airtable** · **Langfuse** · **Resend** · **Retool**.

## What it does

- Takes inbound phone calls, greets the caller, asks for their phone number.
- Looks them up in Airtable, confirms identity, communicates claim status (approved / pending / requires documentation).
- Answers FAQs (office hours, mailing address, how to start a new claim, claims process).
- Handles escalation requests and 911 emergencies.
- At end-of-call: runs a structured LLM analysis (summary, sentiment, intent, topics), scores the agent against a 9-item QA rubric, writes everything to Airtable, and emails an alert for low-score or negative-sentiment calls.
- Every LLM call is traced in Langfuse with a deep-link from the Airtable row.

## Live deliverables

> Fill these in after Phase 4 deployment.

- **Phone number:** _(VAPI-provisioned, US)_
- **Retool dashboard:** _(link)_
- **Langfuse workspace:** _(link)_
- **Demo recordings:** _happy path + error path in `docs/demo/`_

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full system diagram, call-flow sequence diagrams (happy + error paths), and monitoring touchpoints.

## Technical write-up

See [`docs/TECHNICAL_WRITEUP.md`](docs/TECHNICAL_WRITEUP.md) for the tools/frameworks/APIs choices, the one engineering challenge that was solved, what would be next with another week, and the metrics + ROI section.

## Project structure

```
observe-claims-agent/
├── api/
│   ├── main.py              # FastAPI entry point, /health, lifespan env-var validation
│   ├── models/              # Pydantic: caller, interaction, webhook payload
│   ├── routers/
│   │   ├── lookup.py        # GET  /lookup?phone=... — VAPI tool
│   │   └── webhook.py       # POST /webhook         — VAPI end-of-call ingest
│   ├── services/
│   │   ├── airtable.py      # Airtable client (read callers, write interactions)
│   │   ├── analysis.py      # Post-call pipeline orchestrator
│   │   ├── qa_scorer.py     # 9-item rubric + weighted-score with N/A redistribution
│   │   ├── email_alert.py   # Resend transactional email
│   │   └── langfuse_client.py
│   └── utils/
│       ├── auth.py          # X-VAPI-Secret constant-time check
│       ├── phone.py         # E.164 normalization
│       └── prompts.py       # disk-backed prompt loader
├── prompts/
│   ├── agent_system_prompt.txt
│   ├── analysis_prompt.txt
│   └── qa_rubric_prompt.txt
├── scripts/
│   └── seed_airtable.py     # idempotent — seeds 5 test callers
├── tests/                   # 124 tests, all green
├── docs/
│   ├── ARCHITECTURE.md
│   └── TECHNICAL_WRITEUP.md
├── pyproject.toml
├── requirements.txt
├── railway.toml
└── .env.example
```

## Setup

### Prerequisites

- Python 3.11+
- A VAPI account (free tier OK)
- Airtable, OpenAI, Langfuse, Resend accounts

### 1. Install

```bash
python -m venv .venv
.venv\Scripts\activate          # PowerShell on Windows
pip install -e ".[dev]"
```

### 2. Environment variables

Copy `.env.example` to `.env` and fill in:

| Var | Where to find it |
|---|---|
| `AIRTABLE_API_KEY` | airtable.com → Account → Personal Access Tokens. Scopes: `data.records:read`, `data.records:write`, `schema.bases:read`. |
| `AIRTABLE_BASE_ID` | The `app...` portion of your base URL (only the part before the first `/`). |
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `LANGFUSE_*` | cloud.langfuse.com → Settings → API Keys. Set `LANGFUSE_ENABLED=false` to disable tracing locally. |
| `RESEND_API_KEY` | resend.com → API Keys (Sending access) |
| `ALERT_EMAIL_TO` | On Resend's free tier without a verified domain, this **must** match the email you signed up with. |
| `VAPI_WEBHOOK_SECRET` | Any random 32+ char hex string. The same value goes into the VAPI dashboard as a custom header `X-VAPI-Secret`. |

### 3. Create Airtable schema

In your Airtable base, create two tables with these fields:

**`callers`**
| Field | Type |
|---|---|
| `first_name` | Single line text |
| `last_name` | Single line text |
| `phone` | Phone number (store as E.164: `+14155550001`) |
| `claim_id` | Single line text |
| `claim_status` | Single select: `approved`, `pending`, `requires_documentation` |
| `claim_type` | Single select: `auto`, `home`, `health`, `life` |
| `claim_date` | Date |
| `linked_interactions` | Link to `interactions` |

**`interactions`**
| Field | Type |
|---|---|
| `caller` | Link to `callers` |
| `timestamp` | Date/time |
| `authenticated` | Checkbox |
| `call_duration_seconds` | Number |
| `transcript` | Long text |
| `summary` | Long text |
| `sentiment` | Single select: `positive`, `neutral`, `negative` |
| `sentiment_arc` | Long text (JSON) |
| `detected_intent` | Single select: `claim_status`, `faq`, `escalation`, `new_claim`, `other` |
| `qa_score` | Number (decimal, 3 places) |
| `qa_breakdown` | Long text (JSON) |
| `topics_mentioned` | Multiple select |
| `escalated` | Checkbox |
| `langfuse_trace_url` | URL |

### 4. Seed test data

```bash
python scripts/seed_airtable.py
```

Idempotent — re-running won't create duplicates.

### 5. Run locally

```bash
uvicorn api.main:app --reload --port 8000
```

`/health` should return `{"status":"ok"}`.

To make the local API reachable by VAPI, expose it with ngrok:

```bash
ngrok http 8000
```

Use the `https://...ngrok-free.app` URL as the VAPI server URL.

### 6. VAPI configuration

In the VAPI dashboard:

1. **Phone Numbers** → **Buy Number** → choose VAPI (not Twilio) → US number → assign to your assistant.
2. **Assistants** → **Create Assistant**:
   - Model: OpenAI GPT-4o, temperature 0.3
   - System Prompt: paste the contents of `prompts/agent_system_prompt.txt`
   - Transcriber: Deepgram, model `nova-3`, language `en-US`
   - Voice: 11Labs, model `eleven_flash_v2_5`, voice `Rachel`
   - First Message: `"Thank you for calling Observe Insurance. My name is Claire, and I'm here to help you with your claim. To get started, could you please provide me with the phone number associated with your account?"`
3. **Tools** → **Create Tool** → `lookup_caller`:
   - Type: Function (VAPI always POSTs to function tools — no method selector)
   - Server URL: `<your-railway-or-ngrok-url>/lookup`
   - Parameters: `phone` (string, required)
   - Description: `"Look up a caller's account and claim information using their phone number. Call this immediately after the caller provides their phone number."`
   - **Custom Headers** → add `X-VAPI-Secret: <your VAPI_WEBHOOK_SECRET value>`
   - Attach to the assistant.
4. **Assistant → Server URL**: `<your-railway-or-ngrok-url>/webhook` (must be the `/webhook` path, not `/` or `/health`)
   - **Custom Headers** → add `X-VAPI-Secret: <your VAPI_WEBHOOK_SECRET value>`
   - **Server Messages**: enable `end-of-call-report`

### 7. Test calls

- **Happy path:** dial the VAPI number, give phone `(415) 555-0001`, confirm name "Jane Doe" — agent should report claim approved.
- **Error path:** dial, give a phone not in Airtable — agent should offer re-verification or human callback.

## Deployment to Railway

1. Push the repo to GitHub.
2. railway.app → New Project → Deploy from GitHub repo. Nixpacks auto-detects Python.
3. Variables tab: paste in all `.env` values (do **not** commit `.env` itself).
4. Once deployed, copy the public URL into VAPI as the server URL and the tool URL.
5. `railway.toml` already pins the start command and `/health` healthcheck.

## Testing

```bash
pytest                        # all 124 tests
pytest tests/test_qa_scorer.py -v
pytest --co                   # collect-only, list test names
```

Test coverage is end-to-end at the HTTP boundary — Airtable, OpenAI, Resend, and Langfuse are all mocked via respx / unittest.mock. No live network calls in `pytest`.
