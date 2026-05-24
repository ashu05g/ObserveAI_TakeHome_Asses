"""End-of-call webhook from VAPI.

Auth: shared-secret header (X-VAPI-Secret) configured in the VAPI dashboard.
The full pipeline runs in a background task so VAPI's webhook deadline isn't
blocked by Airtable + 2 OpenAI calls (~3-6s total).
"""

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from api.models.webhook import VAPIWebhookPayload
from api.services import analysis
from api.utils.auth import verify_vapi_secret

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
    if event.type != "end-of-call-report":
        return {"status": "ignored", "type": event.type}

    background_tasks.add_task(analysis.run_post_call_pipeline, event)
    return {"status": "received"}
