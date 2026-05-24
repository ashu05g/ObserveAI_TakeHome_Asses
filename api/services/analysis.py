"""Post-call intelligence pipeline.

Runs as a FastAPI BackgroundTask after the webhook returns 200 to VAPI.
Orchestration:
  1. extract transcript + Airtable caller link from the VAPI event
  2. one LLM call: structured analysis (summary, sentiment, intent, ...)
  3. one LLM call: QA rubric scoring
  4. write the interaction row to Airtable
  5. fire email alert iff QA < threshold or sentiment is negative

A failure in any post-step is logged but not raised — there's no client
left to return it to, and we want partial signal in Airtable over none.
"""

import json
import logging
from datetime import UTC, datetime

from openai import AsyncOpenAI

from api.models.interaction import InteractionLog
from api.models.webhook import VAPIEvent
from api.services.airtable import write_interaction
from api.services.email_alert import send_alert
from api.services.langfuse_client import observed, trace_pipeline
from api.services.qa_scorer import score_call
from api.utils.prompts import load_prompt

ALERT_QA_THRESHOLD = 0.6

logger = logging.getLogger(__name__)


async def run_post_call_pipeline(event: VAPIEvent) -> None:
    transcript = extract_transcript(event)
    if not transcript:
        logger.warning("post-call pipeline received event with no transcript; skipping")
        return

    caller_airtable_id = extract_airtable_id(event)
    duration = int(event.call.duration_seconds or event.duration_seconds or 0)

    client = AsyncOpenAI()
    with trace_pipeline(event.call.id) as trace:
        try:
            analysis = await analyze_transcript(transcript, client)
            qa = await score_call(transcript, client)
        except Exception:
            logger.exception("LLM analysis failed; aborting pipeline for call %s", event.call.id)
            return

        log = InteractionLog(
            caller_airtable_id=caller_airtable_id,
            timestamp=datetime.now(UTC),
            authenticated=bool(analysis.get("authenticated", False)),
            call_duration_seconds=duration,
            transcript=transcript,
            summary=analysis.get("summary", ""),
            sentiment=analysis.get("sentiment", "neutral"),
            sentiment_arc=json.dumps(analysis.get("sentiment_arc", [])),
            detected_intent=analysis.get("intent", "other"),
            qa_score=qa["score"],
            qa_breakdown=json.dumps(qa["breakdown"]),
            topics_mentioned=analysis.get("topics_mentioned", []),
            escalated=bool(analysis.get("escalated", False)),
            langfuse_trace_url=trace.url,
        )

        try:
            await write_interaction(log)
        except Exception:
            logger.exception("failed to write interaction to Airtable for call %s", event.call.id)

        if should_alert(qa["score"], log.sentiment):
            try:
                await send_alert(
                    caller_name=analysis.get("caller_name"),
                    sentiment=log.sentiment,
                    qa_score=qa["score"],
                    summary=log.summary,
                    trace_url=trace.url,
                )
            except Exception:
                logger.exception("failed to send email alert for call %s", event.call.id)


async def analyze_transcript(transcript: str, client: AsyncOpenAI) -> dict:
    with observed("analyze_transcript") as span:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": load_prompt("analysis_prompt.txt")},
                {"role": "user", "content": f"TRANSCRIPT:\n{transcript}"},
            ],
        )
        content = response.choices[0].message.content
        span.update(
            input={"transcript": transcript},
            output=content,
            model="gpt-4o-mini",
        )
        return json.loads(content)


def should_alert(qa_score: float | None, sentiment: str) -> bool:
    return sentiment == "negative" or (
        qa_score is not None and qa_score < ALERT_QA_THRESHOLD
    )


def extract_transcript(event: VAPIEvent) -> str:
    """Prefer VAPI's prebuilt transcript field; reconstruct from messages
    only if it isn't present."""
    if event.transcript:
        return event.transcript

    parts: list[str] = []
    for msg in event.call.messages:
        if msg.role in ("user", "assistant") and isinstance(msg.content, str):
            speaker = "caller" if msg.role == "user" else "agent"
            parts.append(f"{speaker}: {msg.content}")
    return "\n".join(parts)


def extract_airtable_id(event: VAPIEvent) -> str | None:
    """Walk the tool-call history backwards and return the airtable_record_id
    from the most recent successful lookup_caller response, or None if no
    such response is found (unauthenticated call)."""
    for msg in reversed(event.call.messages):
        if msg.role != "tool" or msg.name != "lookup_caller":
            continue
        body = msg.content
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                continue
        if isinstance(body, dict) and body.get("found") and body.get("airtable_record_id"):
            return body["airtable_record_id"]
    return None
