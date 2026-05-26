"""Mid-call caller lookup tool.

VAPI POSTs here with one or more `toolCalls`. Per-call failures (bad phone,
not found, Airtable error) return `{found: false, ...}` inside a 200 — VAPI
treats non-2xx tool responses as hard errors that abort the call.
"""

import json
import logging

from fastapi import APIRouter, Header, HTTPException

from api.models.tool_call import VAPIToolCallRequest, VAPIToolResponse, VAPIToolResult
from api.services.airtable import get_caller_by_phone
from api.services.langfuse_client import trace_lookup
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

    call_id = payload.message.call_id

    with trace_lookup(call_id) as span:
        results: list[VAPIToolResult] = []
        for call in payload.message.tool_calls:
            if call.function.name != LOOKUP_FUNCTION:
                results.append(VAPIToolResult(
                    tool_call_id=call.id,
                    result=json.dumps({"error": f"unsupported function: {call.function.name}"}),
                ))
                continue

            args = _parse_arguments(call.function.arguments)
            phone = args.get("phone")
            # `result` must be a string — a dict here causes VAPI to drop
            # the body and inject "Success." for the LLM instead.
            result_dict = await _resolve_lookup(phone)
            results.append(VAPIToolResult(
                tool_call_id=call.id,
                result=json.dumps(result_dict),
            ))

        if span is not None:
            try:
                span.update(
                    input={"phone_requests": [
                        {"id": c.id, "args": c.function.arguments}
                        for c in payload.message.tool_calls
                    ]},
                    output={"results": [
                        {"tool_call_id": r.tool_call_id, "result": r.result}
                        for r in results
                    ]},
                )
            except Exception:
                logger.exception("langfuse: lookup span update failed; continuing")

    return VAPIToolResponse(results=results)


async def _resolve_lookup(phone: str | None) -> dict:
    if not phone:
        logger.info("lookup: missing phone argument")
        return {"found": False, "error": "missing phone argument"}

    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        logger.info("lookup: invalid phone raw=%r reason=%s", phone, exc)
        return {"found": False, "error": str(exc)}

    logger.info("lookup: querying airtable raw=%r normalized=%s", phone, normalized)
    try:
        caller = await get_caller_by_phone(normalized)
    except Exception:
        logger.exception("lookup: airtable failure for phone=%s", normalized)
        return {"found": False, "error": "lookup service unavailable"}

    if caller is None:
        logger.info("lookup: MISS phone=%s", normalized)
        return {"found": False}

    logger.info(
        "lookup: HIT phone=%s name=%s claim=%s status=%s airtable_id=%s",
        normalized,
        caller.full_name,
        caller.claim_id,
        caller.claim_status,
        caller.airtable_id,
    )
    return {
        "found": True,
        "first_name": caller.first_name,
        "last_name": caller.last_name,
        "claim_id": caller.claim_id,
        "claim_status": caller.claim_status,
        "claim_type": caller.claim_type,
        "claim_date": caller.claim_date.isoformat(),
        "incident_date": caller.incident_date.isoformat() if caller.incident_date else None,
        "claim_amount": caller.claim_amount,
        "approved_amount": caller.approved_amount,
        "adjuster_name": caller.adjuster_name,
        "estimated_payout_date": (
            caller.estimated_payout_date.isoformat()
            if caller.estimated_payout_date
            else None
        ),
        "documents_needed": caller.documents_needed,
        "claim_description": caller.claim_description,
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
