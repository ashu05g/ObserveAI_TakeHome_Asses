"""Mid-call caller lookup tool.

VAPI POSTs here with one or more `toolCalls`. We resolve each
`lookup_caller` call and return the structured response VAPI expects.

Per-call failures (bad phone format, caller not found, Airtable error)
return `{found: false, ...}` rather than HTTP errors — VAPI's tool-call
layer treats non-2xx as a hard error and aborts the call.
"""

import json
import logging

from fastapi import APIRouter, Header, HTTPException

from api.models.tool_call import VAPIToolCallRequest, VAPIToolResponse, VAPIToolResult
from api.services.airtable import get_caller_by_phone
from api.utils.auth import verify_vapi_secret
from api.utils.phone import InvalidPhoneNumber, normalize_phone

LOOKUP_FUNCTION = "lookup_caller"

logger = logging.getLogger(__name__)

router = APIRouter(tags=["lookup"])


@router.post("/lookup")
async def lookup_caller(
    payload: VAPIToolCallRequest,
    x_vapi_secret: str | None = Header(default=None),
) -> VAPIToolResponse:
    if not verify_vapi_secret(x_vapi_secret):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing X-VAPI-Secret header",
        )

    results: list[VAPIToolResult] = []
    for call in payload.message.tool_calls:
        if call.function.name != LOOKUP_FUNCTION:
            results.append(VAPIToolResult(
                tool_call_id=call.id,
                result={"error": f"unsupported function: {call.function.name}"},
            ))
            continue

        args = _parse_arguments(call.function.arguments)
        phone = args.get("phone")
        results.append(VAPIToolResult(
            tool_call_id=call.id,
            result=await _resolve_lookup(phone),
        ))

    return VAPIToolResponse(results=results)


async def _resolve_lookup(phone: str | None) -> dict:
    if not phone:
        return {"found": False, "error": "missing phone argument"}

    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        return {"found": False, "error": str(exc)}

    try:
        caller = await get_caller_by_phone(normalized)
    except Exception:
        logger.exception("Airtable lookup failed for phone %s", normalized)
        return {"found": False, "error": "lookup service unavailable"}

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


def _parse_arguments(arguments) -> dict:
    """VAPI sends arguments as either a JSON string or an already-decoded
    object — accept both, return {} for anything else."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
