# Observe Claims Agent

Inbound voice AI agent for an insurance claims hotline. Built for the Observe.AI AI Agent Engineer take-home.

**Stack:** **VAPI** (telephony + voice orchestration) · **Deepgram Nova-3** (STT) · **ElevenLabs Flash v2.5** (TTS) · **GPT-4.1** (agent brain) · **GPT-4.1-mini** (post-call analysis + QA scoring) · **FastAPI on Railway** · **Airtable** · **Langfuse** · **Resend**.

## What it does

- Takes inbound phone calls, greets the caller, asks for their phone number.
- Looks them up in Airtable (silently) and asks the caller to **verify their identity by stating their full name and date of birth**. Both must match before any claim information is disclosed.
- Communicates the claim status (approved / pending / requires documentation) and specific claim details (amount, adjuster, payout date, etc.).
- Answers general questions from the inlined knowledge base (company info, policy types, claims process, customer service).
- Handles escalation requests and 911 emergencies.
- At end-of-call: runs a structured LLM analysis (summary, sentiment, intent, topics), scores the agent against a 9-item QA rubric (with N/A weight redistribution), writes everything to Airtable, and emails an alert for low-score or negative-sentiment calls.
- Every event on the call (status updates, transcripts, the tool call, the post-call pipeline) lands as observations in **one Langfuse trace per call** with a native waterfall view. The trace URL is written back to the Airtable interaction row.

## Live deliverables

> Fill these in after recording the demo.

- **Phone number:** _(VAPI-provisioned, US)_
- **Langfuse workspace:** _(link to your project)_
- **Demo recordings:** _happy path + error path_

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System diagram, call-flow sequence diagrams (happy + error paths), monitoring touchpoints, failure modes — every element cited to its file:line in code |
| [`docs/TECHNICAL_WRITEUP.md`](docs/TECHNICAL_WRITEUP.md) | Tools/frameworks/APIs choices, the engineering challenge solved, metrics + ROI, one-more-week roadmap |
| [`docs/CODE_WALKTHROUGH.md`](docs/CODE_WALKTHROUGH.md) | Top-to-bottom code-level deep dive: every file, every helper, every choice with line numbers |
| [`docs/knowledge_base/`](docs/knowledge_base/) | The four markdown files inlined into the agent's system prompt at sync time |

## Project structure

```
observe-claims-agent/
├── api/
│   ├── main.py                       FastAPI entry — /health, lifespan env validation
│   ├── routers/
│   │   ├── lookup.py                 POST /lookup — mid-call tool
│   │   └── webhook.py                POST /webhook — live events + end-of-call ingest
│   ├── services/
│   │   ├── airtable.py               Airtable client (read callers, write interactions)
│   │   ├── analysis.py               Post-call pipeline orchestrator
│   │   ├── qa_scorer.py              9-item rubric + weighted-score with N/A redistribution
│   │   ├── email_alert.py            Resend transactional email
│   │   └── langfuse_client.py        Single-trace-per-call via OTel context
│   ├── models/                       Pydantic — caller, interaction, webhook payload, tool call
│   └── utils/
│       ├── auth.py                   X-VAPI-Secret constant-time check
│       ├── phone.py                  E.164 normalization
│       └── prompts.py                Disk-backed prompt loader
├── prompts/
│   ├── agent_system_prompt.txt       Agent instructions (auth, claim handling, FAQ, escalation)
│   ├── analysis_prompt.txt           Post-call analyzer
│   └── qa_rubric_prompt.txt          QA evaluator
├── scripts/
│   ├── setup_airtable_schema.py      Creates callers + interactions tables idempotently
│   ├── seed_airtable.py              Upserts 5 test callers by phone
│   ├── vapi_sync.py                  Pushes assistant + tool config to VAPI (IaC)
│   └── inspect_call.py               Diagnostic: dumps a VAPI call's assistant + message thread
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TECHNICAL_WRITEUP.md
│   ├── CODE_WALKTHROUGH.md
│   └── knowledge_base/
│       ├── about_company.md
│       ├── policies_and_coverage.md
│       ├── claims_process.md
│       └── customer_service.md
├── tests/                            175 tests, all green
├── pyproject.toml                    Deps + tool config (pytest, ruff)
├── requirements.txt                  Runtime pins (for Railway)
├── railway.toml                      Railway deployment config
└── .env.example
```

## Setup

### Prerequisites

- Python 3.11+
- Accounts: VAPI, Airtable, OpenAI, Langfuse, Resend

### 1. Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Environment variables

Copy `.env.example` to `.env` and fill in:

| Var | Where to find it |
|---|---|
| `AIRTABLE_API_KEY` | airtable.com → Account → Personal Access Tokens. Scopes: `data.records:read`, `data.records:write`, `schema.bases:read`, `schema.bases:write` (last one needed by `setup_airtable_schema.py`). |
| `AIRTABLE_BASE_ID` | The `app...` portion of your base URL (everything before the first `/`). |
| `AIRTABLE_CALLERS_TABLE` | `callers` |
| `AIRTABLE_INTERACTIONS_TABLE` | `interactions` |
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | cloud.langfuse.com → Settings → API Keys. Set `LANGFUSE_ENABLED=false` to disable tracing locally. |
| `RESEND_API_KEY`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` | resend.com → API Keys. On the free tier without a verified domain, `ALERT_EMAIL_TO` must be the address you signed up with. |
| `VAPI_WEBHOOK_SECRET` | A random 32+ char hex string. You generate it; same value goes into VAPI as a custom header `X-VAPI-Secret`. |
| `VAPI_API_KEY` | VAPI dashboard → Org Settings → Private API key. Used by `scripts/vapi_sync.py`. |
| `SERVER_URL` | The public base URL of your deployed FastAPI service (Railway). Used by `scripts/vapi_sync.py` for the server URLs it sets on the VAPI assistant + tool. |

### 3. Create the Airtable schema

```powershell
python scripts/setup_airtable_schema.py
```

Idempotent — re-running adds any missing fields. The script creates `callers` (first_name, last_name, phone, **date_of_birth**, claim_id, claim_status, claim_type, claim_date, plus enriched fields like claim_amount, adjuster_name, etc.) and `interactions` (timestamp, transcript, summary, sentiment, qa_score, langfuse_trace_url, etc.). The reverse link from interactions → callers is auto-renamed to `linked_interactions`.

### 4. Seed test data

```powershell
python scripts/seed_airtable.py
```

Upserts five test callers by phone. Re-run any time after schema changes.

| Phone | Name | DOB | Claim status |
|---|---|---|---|
| +14155550001 | Jane Doe | 1985-04-15 | approved |
| +14155550002 | Marcus Chen | 1979-09-22 | pending |
| +14155550003 | Priya Sharma | 1992-11-08 | requires_documentation |
| +14155550004 | Tom Rivera | 1968-06-30 | approved |
| +14155550005 | Alice Nguyen | 1995-02-12 | pending |

### 5. Run locally

```powershell
uvicorn api.main:app --reload --port 8000
```

`GET /health` should return `{"status":"ok"}`. To expose to VAPI for local development:

```powershell
ngrok http 8000
```

### 6. VAPI configuration — managed as code

The VAPI assistant and tool are defined in `scripts/vapi_sync.py` and pushed via REST API. **No manual dashboard editing.**

```powershell
python scripts/vapi_sync.py
```

This script:
1. Reads `prompts/agent_system_prompt.txt` and concatenates the four `docs/knowledge_base/*.md` files under a `# REFERENCE INFORMATION` section.
2. Builds the assistant config (model `gpt-4.1` temp 0.3, Deepgram Nova-3, ElevenLabs Flash v2.5, the assembled prompt, `serverMessages` for live events, endpointing tuned for digit pauses).
3. Builds the tool config (`lookup_caller` function tool with `async: False` — critical so VAPI passes the response to the LLM).
4. PATCHes if a same-named assistant/tool exists, POSTs if not.

After this runs, **assign your VAPI phone number to the "Emma" assistant** in the dashboard (one-time UI step). Then every prompt change is just: edit the markdown → `python scripts/vapi_sync.py`.

### 7. Test calls

Once Railway is deployed and the VAPI assistant is synced and the phone number is attached:

- **Happy path**: dial the VAPI number, give phone `four one five, five five five, zero zero zero one`, then when asked to verify your identity say *"Jane Doe, April fifteenth nineteen eighty-five"*. Agent should confirm and report claim approved.
- **Partial answer**: give just the name (no DOB) — agent should ask specifically for DOB.
- **Wrong factor**: give Jane Doe's name but wrong DOB — agent should re-prompt without revealing which part was wrong.
- **Caller not in system**: give phone `(415) 555-9999` — agent should explain it can't find the account.
- **Twice-wrong identity**: fail verification twice — agent should escalate to a human callback and end the call.

Diagnose any call via:

```powershell
python scripts/inspect_call.py <vapi_call_id>
```

This dumps the assistant config that was applied, the full message thread the LLM saw, every tool call with its actual result, and the transcript.

## Deployment to Railway

1. Push the repo to GitHub.
2. railway.app → New Project → Deploy from GitHub repo. Nixpacks auto-detects Python and `requirements.txt`.
3. Variables tab: paste in all `.env` values (do **not** commit `.env` itself).
4. Settings → Networking → Generate Domain. Copy the URL into your `.env` as `SERVER_URL`.
5. Run `python scripts/vapi_sync.py` locally to push the new server URLs into VAPI.
6. `railway.toml` already pins the start command, the `/health` healthcheck, and the restart policy.

## Testing

```powershell
pytest                              # all 175 tests
pytest tests/test_qa_scorer.py -v   # one module
pytest --co                         # collect only (list test names)
```

All external dependencies (Airtable, OpenAI, Resend, Langfuse, VAPI REST) are mocked at the HTTP boundary via `respx` (an `httpx`-aware mock). No live network calls in `pytest`. Tests cover every error branch, every QA scorer edge case (N/A redistribution, score clamping, hallucinated rubric IDs), and the two-factor auth-related code paths.

## What's in the agent prompt

`prompts/agent_system_prompt.txt` (read it for the canonical version) covers:

1. **Confidentiality rule** — never speak stored values (name, DOB, claim details) before identity verification.
2. **Authentication flow** — phone lookup, two-factor challenge (name + DOB), partial-answer handling, fuzzy DOB parsing across spoken formats, two-strike escalation.
3. **Claim handling** — exact wording per `claim_status`, claim-ID pronunciation rules.
4. **Specific claim questions** — mapping each natural-language question to a tool result field.
5. **General questions** — directs the LLM to the inlined REFERENCE INFORMATION (the knowledge base).
6. **Unclear / repeated questions** — "could you repeat" patterns + escalation after retries.
7. **Escalation & safety** — human callback, 911, off-topic redirect.
8. **Tone** — concise, calm, supportive, name-only-after-verification.
