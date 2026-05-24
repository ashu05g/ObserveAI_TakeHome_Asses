"""Airtable client for the `callers` and `interactions` tables.

Reads config from environment variables on each call. A new httpx client is
created per request because call volume is low and the overhead is dwarfed by
the round-trip to Airtable; this also keeps the module trivially mockable in
tests.
"""

import os

import httpx

from api.models.caller import CallerRecord
from api.models.interaction import InteractionLog

AIRTABLE_BASE_URL = "https://api.airtable.com/v0"
DEFAULT_TIMEOUT_SECONDS = 10.0


def _client() -> httpx.AsyncClient:
    api_key = os.environ["AIRTABLE_API_KEY"]
    base_id = os.environ["AIRTABLE_BASE_ID"]
    return httpx.AsyncClient(
        base_url=f"{AIRTABLE_BASE_URL}/{base_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )


async def get_caller_by_phone(phone: str) -> CallerRecord | None:
    """Look up a caller by E.164 phone. Returns None if no match."""
    table = os.environ["AIRTABLE_CALLERS_TABLE"]
    async with _client() as client:
        response = await client.get(
            f"/{table}",
            params={
                "filterByFormula": f"{{phone}}='{phone}'",
                "maxRecords": 1,
            },
        )
        response.raise_for_status()
        records = response.json().get("records", [])
        if not records:
            return None
        return CallerRecord(airtable_id=records[0]["id"], **records[0]["fields"])


async def write_interaction(log: InteractionLog) -> str:
    """Insert a completed interaction. Returns the new Airtable record ID."""
    table = os.environ["AIRTABLE_INTERACTIONS_TABLE"]
    fields: dict = {
        "timestamp": log.timestamp.isoformat(),
        "authenticated": log.authenticated,
        "call_duration_seconds": log.call_duration_seconds,
        "transcript": log.transcript,
        "summary": log.summary,
        "sentiment": log.sentiment,
        "sentiment_arc": log.sentiment_arc,
        "detected_intent": log.detected_intent,
        "qa_breakdown": log.qa_breakdown,
        "topics_mentioned": log.topics_mentioned,
        "escalated": log.escalated,
    }
    if log.qa_score is not None:
        fields["qa_score"] = log.qa_score
    if log.langfuse_trace_url:
        fields["langfuse_trace_url"] = log.langfuse_trace_url
    if log.caller_airtable_id:
        fields["caller"] = [log.caller_airtable_id]

    async with _client() as client:
        response = await client.post(f"/{table}", json={"fields": fields})
        response.raise_for_status()
        return response.json()["id"]
