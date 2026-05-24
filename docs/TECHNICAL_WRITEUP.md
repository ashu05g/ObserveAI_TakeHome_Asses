# Technical Write-Up

## 1. Tools, Frameworks, and APIs

### STT — Deepgram Nova-3 (via VAPI)
Telephony audio arrives at 8 kHz with background noise, speaker overlap, and clipped pronunciation. Deepgram's Nova family is purpose-built for telephony-grade audio and consistently outperforms general-purpose models like Whisper on noisy 8 kHz input, with sub-300 ms streaming latency. For an authentication step where misheard digits mean failed verification, WER on telephony audio matters more than any other metric.

### TTS — ElevenLabs Flash v2.5 (via VAPI)
Flash v2.5 is ElevenLabs' lowest-latency model, with time-to-first-byte well under 100 ms. Human conversation has inter-turn gaps of 200–400 ms; with STT + LLM-first-token + TTS-TTFB the system stays under one second per turn, which sounds intentional rather than broken.

### LLM split — GPT-4.1 for the agent, GPT-4.1-mini for analysis
The agent brain needs deterministic tool invocation on the authentication branch. A misfire (calling `lookup_caller` before getting the phone number, or skipping identity confirmation) is a compliance failure. GPT-4.1 was chosen over GPT-4o for two concrete reasons: (1) **better instruction-following and function-calling reliability** — exactly the failure mode we care about, and (2) it's **cheaper** ($2/$8 vs $5/$15 per million tokens), so the upgrade is strictly Pareto-better. Reasoning models (o1, o3) were ruled out — their inference latency breaks the sub-second conversational feel.

For post-call analysis, GPT-4.1-mini handles structured JSON extraction with stronger schema adherence than 4o-mini, at moderate cost ($0.40/$1.60 per million). The async post-call path is latency-insensitive so a heavier model is acceptable; the small quality bump means fewer downstream Airtable validation errors and more accurate QA scores. We considered `gpt-4.1-nano` for additional savings but it's unreliable on the 9-item rubric's structured output.

### Backend — FastAPI on Railway
Two endpoints (`/lookup`, `/webhook`) plus a health probe. The post-call pipeline runs as a FastAPI `BackgroundTask` so the webhook handler returns within VAPI's timeout while analysis, QA scoring, and Airtable writes happen asynchronously.

### Data — Airtable
Two tables (`callers`, `interactions`) with REST access. Sufficient for the take-home; for production this swaps to Postgres on the same Pydantic model surface (the `airtable.py` service is the only file that would change).

### Observability — Langfuse v3
Every LLM call is wrapped in a span and rolled up under a per-call `post_call_pipeline` parent trace. The trace URL is written to the Airtable row for one-click navigation from the dashboard.

### Alerts — Resend
Single API key, no SMTP, single HTTP call. Sandbox sender (`onboarding@resend.dev`) means no domain verification for the demo; in production this moves to a verified domain.

### Why this stack scales
- **Telephony**: VAPI handles concurrency at the platform layer; Twilio/PSTN scaling is not our problem.
- **Backend**: FastAPI + uvicorn workers scale horizontally on Railway. Each request is short (lookups are <500 ms; webhook returns immediately).
- **Airtable**: ~5 req/sec/base limit is fine for tens of concurrent calls. At >100 concurrent calls, migrate to Postgres on Supabase — same Pydantic models, same router code, only `api/services/airtable.py` is rewritten.
- **Post-call pipeline**: If the BackgroundTask in-process pattern becomes the bottleneck, move to a Redis-backed task queue (RQ or Celery) — the function signature `run_post_call_pipeline(event)` is already queueable.
- **Langfuse**: cloud tier handles 50k events/month free; self-host on a single Railway instance for unlimited.

---

## 2. Problem Solving & Debugging

### Challenge: linking the post-call interaction back to the correct caller

The webhook fires at end-of-call with a transcript and a tool-call history, but VAPI doesn't surface a "which Airtable record was this caller" field. The agent called `lookup_caller` mid-call and got back an `airtable_record_id`, but how do we recover it server-side after the call?

**Three options considered:**

1. **Re-query Airtable by phone** at webhook time, using VAPI's caller-ID. Reliable but adds another API call and an extra failure point — and depends on VAPI's payload exposing the inbound number, which varies by call type.
2. **Have the agent call a second `log_interaction` tool** at end-of-call, passing the record ID it remembered. Doubles the tool surface, depends on the LLM remembering to call it, and duplicates what the webhook already does.
3. **Walk the tool-call history in the webhook payload**, scan backward for the most recent successful `lookup_caller` response, and pull the ID from there.

Chose (3) — `extract_airtable_id()` in `api/services/analysis.py`. It's a 15-line function with no extra network calls, no agent dependency, and handles the cases where lookup was never called (unauthenticated call), called and missed (`found: false`), or called multiple times with different numbers (uses the most recent successful one).

The risk was: what if VAPI's payload schema changes the tool-message shape? The model uses Pydantic with `extra="ignore"` to tolerate added fields, and the extractor defensively handles both string and dict content. There's a unit test for each of the six branching cases (`tests/test_analysis.py::TestExtractAirtableId`).

### If I had one more week

1. **Replace Airtable with Postgres** behind the same `airtable.py`-shaped interface. The 5 req/sec limit is fine for a demo but will be the first thing to break under any real load. Same Pydantic models, same router code.
2. **Real-time agent quality monitoring.** Today the QA scorer runs post-call. With a sliding-window evaluator running on the live transcript, low-score calls could be escalated to a human *during* the call instead of after.
3. **Voice biometrics for authentication.** The "Am I speaking with Jane Doe?" confirmation is weak — the agent reveals the name. A short voice-print check against a stored sample would close that hole without adding friction.
4. **Prompt versioning + A/B testing in Langfuse.** Langfuse supports prompt management and side-by-side eval. Wire prompts to Langfuse so changes are versioned, A/B-tested on a fraction of traffic, and roll-backable without a deploy.
5. **Replay testing.** Save a corpus of real transcripts and re-run them through the LLM pipeline whenever prompts change, then diff the QA scores to catch regressions before deploying.

---

## 3. Data & Metrics Evaluation

### Metrics tracked

| Metric | Definition | Source |
|---|---|---|
| **Containment rate** | calls where `escalated == false` / total | Airtable `interactions.escalated` |
| **Authentication success rate** | calls where `authenticated == true` / total with lookup attempt | Airtable `interactions.authenticated` |
| **Average QA score (weekly)** | mean of `qa_score` over the last 7 days, excluding nulls | Airtable `interactions.qa_score` |
| **Average handle time** | mean `call_duration_seconds` | Airtable `interactions.call_duration_seconds` |
| **Sentiment distribution** | counts of positive / neutral / negative | Airtable `interactions.sentiment` |
| **Per-rubric pass rate** | for each QA rubric item, % of applicable calls scoring ≥ 0.5 | Airtable `interactions.qa_breakdown` (JSON) |
| **First-call resolution** | calls with no second call from same `caller` within 24h | computed on Airtable join |
| **LLM cost per call** | sum of analysis + QA token cost | Langfuse traces |

### Using the data

Run the QA scorer **continuously**, not as a point-in-time review. Track the weekly average per rubric item. If a specific item — say `documentation_instructions` — drops 10 points week-over-week, that's a signal the agent's wording on that branch has degraded (often a prompt-rot effect from minor edits) or the data has shifted (more documentation-required claims this week).

Two actions follow:
1. **Pull the low-scoring transcripts** for that rubric item (Airtable filter), open them in Langfuse to inspect the LLM input/output at that turn.
2. **Adjust the prompt**, then re-run the QA scorer on the same transcripts to verify the improvement before deploying. This is the value of Langfuse + a deterministic QA rubric: prompt changes become measurable instead of guess-and-check.

### Worked example — containment rate drop

> Week-over-week, containment falls from 87% to 79%.

1. Query Airtable: `WHERE escalated = TRUE AND timestamp >= last_monday`.
2. Group by `detected_intent`: 60% are `claim_status` + `requires_documentation`.
3. Pull the 12 transcripts. Pattern: callers who hear "requires documentation" immediately ask for a human because they don't understand what to submit.
4. Open one trace in Langfuse → look at the agent's response at that turn. Confirm the wording is vague ("upload the required documents").
5. Update `agent_system_prompt.txt` to add specifics ("such as your police report, medical records, or repair estimate, depending on your claim type"). Commit, deploy.
6. Re-run QA scorer on the same 12 transcripts — verify `documentation_instructions` pass rate moves from 30% to 90%.
7. Watch next week's containment rate. If it recovers, the fix is validated; if not, dig deeper (could be a different intent inflating the bucket).

The whole loop — detect, diagnose, patch, verify — is closeable within a single day because every signal is captured in Airtable + Langfuse, and the QA scorer is replayable against historical transcripts.
