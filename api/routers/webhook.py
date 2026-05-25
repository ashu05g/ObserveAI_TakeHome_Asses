"""VAPI server-URL webhook.

Receives every event VAPI is subscribed to send (subscribe list lives in
`vapi_sync.py` → `serverMessages`). For most events we just log them to
Langfuse as part of the live call waterfall; for `end-of-call-report` we
also kick off the post-call analysis pipeline.

Auth: shared-secret header (X-VAPI-Secret) configured in the VAPI dashboard.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from api.models.webhook import VAPIEvent, VAPIWebhookPayload
from api.services import analysis
from api.services.langfuse_client import log_call_event
from api.utils.auth import verify_vapi_secret

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])


@router.post("/webhook")
async def vapi_webhook(
    payload: VAPIWebhookPayload,
    background_tasks: BackgroundTasks,
    x_vapi_secret: str | None = Header(default=None),
):
    if not verify_vapi_secret(x_vapi_secret):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing X-VAPI-Secret header",
        )

    event = payload.message
    call_id = event.call.id

    if _should_trace(event):
        log_call_event(call_id, event.type, _event_fields(event))

    if event.type == "end-of-call-report":
        logger.info(
            "webhook: end-of-call-report call_id=%s duration=%s transcript_chars=%s",
            call_id,
            event.call.duration_seconds or event.duration_seconds,
            len(event.transcript or ""),
        )
        background_tasks.add_task(analysis.run_post_call_pipeline, event)
        return {"status": "received"}

    return {"status": "logged", "type": event.type}


def _should_trace(event: VAPIEvent) -> bool:
    """Skip noisy events. Interim STT chunks fire every ~200ms; tracing
    them all would 10x our Langfuse cost without adding signal — the
    final transcript per turn is what matters."""
    return not (event.type == "transcript" and event.transcript_type == "partial")


def _event_fields(event: VAPIEvent) -> dict:
    """Pull the small set of event-type-specific fields we want surfaced in
    Langfuse. Excludes raw payload to keep traces compact."""
    if event.type == "status-update":
        return {
            "status": event.status,
            "ended_reason": event.ended_reason,
        }
    if event.type == "transcript":
        return {
            "role": event.role,
            "transcript": event.transcript,
            "transcript_type": event.transcript_type,
        }
    if event.type == "model-output":
        return {"output": event.output}
    if event.type == "end-of-call-report":
        return {
            "duration_seconds": event.duration_seconds,
            "transcript_chars": len(event.transcript or ""),
            "summary": event.summary,
        }
    return {}
