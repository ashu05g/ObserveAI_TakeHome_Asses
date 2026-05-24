"""Mid-call caller lookup endpoint.

VAPI invokes this as the `lookup_caller` tool. Returns a 200 in both the hit
and miss cases — the agent's system prompt branches on the `found` flag.
Using {"found": false} instead of 404 keeps VAPI's tool layer from
interpreting the miss as an error and retrying.
"""

from fastapi import APIRouter, HTTPException, Query

from api.services.airtable import get_caller_by_phone
from api.utils.phone import InvalidPhoneNumber, normalize_phone

router = APIRouter(tags=["lookup"])


@router.get("/lookup")
async def lookup_caller(phone: str = Query(..., min_length=1)):
    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    caller = await get_caller_by_phone(normalized)
    if caller is None:
        return {"found": False}

    return {
        "found": True,
        "first_name": caller.first_name,
        "last_name": caller.last_name,
        "claim_id": caller.claim_id,
        "claim_status": caller.claim_status,
        "claim_type": caller.claim_type,
        "claim_date": caller.claim_date.isoformat(),
        "airtable_record_id": caller.airtable_id,
    }
