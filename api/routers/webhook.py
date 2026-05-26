"""VAPI server-URL webhook.

Receives every event VAPI is subscribed to send (subscribe list in
`vapi_sync.py` -> serverMessages). Live events are logged to Langfuse for
the per-call waterfall; `end-of-call-report` additionally triggers the
post-call pipeline. Auth: shared-secret header `X-VAPI-Secret`.
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
    # Interim STT chunks fire every ~200ms; we only want final transcripts
    # in the waterfall to keep signal-to-noise high.
    return not (event.type == "transcript" and event.transcript_type == "partial")


def _event_fields(event: VAPIEvent) -> dict:
    """Event-type-specific fields to surface in Langfuse trace input."""
    if event.type == "status-update":
        return {"status": event.status, "ended_reason": event.ended_reason}
    if event.type == "transcript":
        return {
            "role": event.role,
            "transcript": event.transcript,
            "transcript_type": event.transcript_type,
        }
    if event.type == "end-of-call-report":
        return {
            "duration_seconds": event.duration_seconds,
            "transcript_chars": len(event.transcript or ""),
            "summary": event.summary,
        }
    return {}
