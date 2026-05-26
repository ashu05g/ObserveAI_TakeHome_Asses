"""Post-call pipeline: analyze transcript -> QA score -> write to Airtable -> alert.

Runs as a FastAPI BackgroundTask once VAPI's end-of-call webhook returns 200.
Failures in any step are logged and swallowed — there's no client to return
them to, and partial signal in Airtable beats none.
"""

import json
import logging
from datetime import UTC, datetime

from openai import AsyncOpenAI

from api.models.interaction import InteractionLog
from api.models.webhook import VAPIEvent
from api.services.airtable import write_interaction
from api.services.email_alert import send_alert
from api.services.langfuse_client import get_openai_client, trace_pipeline
from api.services.qa_scorer import score_call
from api.utils.prompts import load_prompt

ALERT_QA_THRESHOLD = 0.6
TOOL_RESULT_ROLES = ("tool_call_result", "tool", "function")

logger = logging.getLogger(__name__)


async def run_post_call_pipeline(event: VAPIEvent) -> None:
    call_id = event.call.id
    transcript = extract_transcript(event)
    if not transcript:
        logger.warning("pipeline: skipped (no transcript) call_id=%s", call_id)
        return

    caller_airtable_id = extract_airtable_id(event)
    duration = int(event.call.duration_seconds or event.duration_seconds or 0)
    logger.info(
        "pipeline: start call_id=%s duration=%ss transcript_chars=%d caller_link=%s",
        call_id, duration, len(transcript), caller_airtable_id or "none",
    )

    client = get_openai_client()
    with trace_pipeline(call_id) as trace:
        try:
            llm_analysis = await analyze_transcript(transcript, client)
            logger.info(
                "pipeline: analysis call_id=%s sentiment=%s intent=%s authenticated=%s escalated=%s caller_name=%s",
                call_id,
                llm_analysis.get("sentiment"),
                llm_analysis.get("intent"),
                llm_analysis.get("authenticated"),
                llm_analysis.get("escalated"),
                llm_analysis.get("caller_name"),
            )
            qa = await score_call(transcript, client)
            logger.info(
                "pipeline: qa call_id=%s score=%s n_items=%d",
                call_id, qa["score"], len(qa["breakdown"]),
            )
        except Exception:
            logger.exception("pipeline: LLM step failed; aborting call_id=%s", call_id)
            return

        log = InteractionLog(
            caller_airtable_id=caller_airtable_id,
            timestamp=datetime.now(UTC),
            authenticated=bool(llm_analysis.get("authenticated", False)),
            call_duration_seconds=duration,
            transcript=transcript,
            summary=llm_analysis.get("summary", ""),
            sentiment=llm_analysis.get("sentiment", "neutral"),
            sentiment_arc=json.dumps(llm_analysis.get("sentiment_arc", [])),
            detected_intent=llm_analysis.get("intent", "other"),
            qa_score=qa["score"],
            qa_breakdown=json.dumps(qa["breakdown"]),
            topics_mentioned=llm_analysis.get("topics_mentioned", []),
            escalated=bool(llm_analysis.get("escalated", False)),
            langfuse_trace_url=trace.url,
        )

        try:
            record_id = await write_interaction(log)
            logger.info("pipeline: airtable write OK call_id=%s record_id=%s", call_id, record_id)
        except Exception:
            logger.exception("pipeline: airtable write failed call_id=%s", call_id)

        if should_alert(qa["score"], log.sentiment):
            try:
                await send_alert(
                    caller_name=llm_analysis.get("caller_name"),
                    sentiment=log.sentiment,
                    qa_score=qa["score"],
                    summary=log.summary,
                    trace_url=trace.url,
                )
                logger.info(
                    "pipeline: alert sent call_id=%s reason=%s",
                    call_id,
                    "negative_sentiment" if log.sentiment == "negative" else "low_qa_score",
                )
            except Exception:
                logger.exception("pipeline: alert send failed call_id=%s", call_id)
        logger.info("pipeline: done call_id=%s trace_url=%s", call_id, trace.url)


async def analyze_transcript(transcript: str, client: AsyncOpenAI) -> dict:
    response = await client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": load_prompt("analysis_prompt.txt")},
            {"role": "user", "content": f"TRANSCRIPT:\n{transcript}"},
        ],
    )
    return json.loads(response.choices[0].message.content)


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
    """Pull `airtable_record_id` from the most recent successful lookup_caller
    tool result, or None if no successful lookup is recorded on the call.

    VAPI uses role=`tool_call_result` with the payload in a `result` field;
    `tool` / `function` and `content` are accepted as fallbacks for other
    VAPI versions.
    """
    for msg in reversed(event.call.messages):
        if msg.role not in TOOL_RESULT_ROLES:
            continue
        if msg.name and msg.name != "lookup_caller":
            continue

        body = msg.result if msg.result is not None else msg.content
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                continue
        if isinstance(body, dict) and body.get("found") and body.get("airtable_record_id"):
            return body["airtable_record_id"]
    return None
