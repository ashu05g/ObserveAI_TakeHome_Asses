"""Create the `callers` and `interactions` tables in the configured base.

Idempotent: existing tables are reused, existing fields are skipped, only
missing fields are added. Safe to re-run after every schema change.

Requires the PAT to include the `schema.bases:write` scope in addition
to the read/write data scopes used by the running app.

    python scripts/setup_airtable_schema.py
"""

import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

META_BASE = "https://api.airtable.com/v0/meta/bases"


def callers_schema() -> list[dict]:
    """First entry is the primary field."""
    return [
        {"name": "first_name", "type": "singleLineText"},
        {"name": "last_name", "type": "singleLineText"},
        {"name": "phone", "type": "phoneNumber"},
        {"name": "claim_id", "type": "singleLineText"},
        {
            "name": "claim_status",
            "type": "singleSelect",
            "options": {"choices": [
                {"name": "approved"},
                {"name": "pending"},
                {"name": "requires_documentation"},
            ]},
        },
        {
            "name": "claim_type",
            "type": "singleSelect",
            "options": {"choices": [
                {"name": "auto"},
                {"name": "home"},
                {"name": "health"},
                {"name": "life"},
            ]},
        },
        {
            "name": "claim_date",
            "type": "date",
            "options": {"dateFormat": {"name": "iso"}},
        },
        # Richer claim details so the agent can answer real caller questions
        # ("how much is my claim for?", "who's my adjuster?", "when will I be
        # paid?") without inventing values.
        {
            "name": "claim_amount",
            "type": "currency",
            "options": {"precision": 2, "symbol": "$"},
        },
        {
            "name": "approved_amount",
            "type": "currency",
            "options": {"precision": 2, "symbol": "$"},
        },
        {"name": "adjuster_name", "type": "singleLineText"},
        {
            "name": "estimated_payout_date",
            "type": "date",
            "options": {"dateFormat": {"name": "iso"}},
        },
        {"name": "documents_needed", "type": "multilineText"},
        {"name": "claim_description", "type": "multilineText"},
        {
            "name": "incident_date",
            "type": "date",
            "options": {"dateFormat": {"name": "iso"}},
        },
    ]


def interactions_schema() -> list[dict]:
    """First entry is the primary field. Airtable's API doesn't allow
    creating autoNumber fields, so `timestamp` doubles as the primary —
    we use Airtable's built-in record IDs for linking anyway."""
    return [
        {
            "name": "timestamp",
            "type": "dateTime",
            "options": {
                "dateFormat": {"name": "iso"},
                "timeFormat": {"name": "24hour"},
                "timeZone": "utc",
            },
        },
        {
            "name": "authenticated",
            "type": "checkbox",
            "options": {"icon": "check", "color": "greenBright"},
        },
        {"name": "call_duration_seconds", "type": "number", "options": {"precision": 0}},
        {"name": "transcript", "type": "multilineText"},
        {"name": "summary", "type": "multilineText"},
        {
            "name": "sentiment",
            "type": "singleSelect",
            "options": {"choices": [
                {"name": "positive"},
                {"name": "neutral"},
                {"name": "negative"},
            ]},
        },
        {"name": "sentiment_arc", "type": "multilineText"},
        {
            "name": "detected_intent",
            "type": "singleSelect",
            "options": {"choices": [
                {"name": "claim_status"},
                {"name": "faq"},
                {"name": "escalation"},
                {"name": "new_claim"},
                {"name": "other"},
            ]},
        },
        {"name": "qa_score", "type": "number", "options": {"precision": 3}},
        {"name": "qa_breakdown", "type": "multilineText"},
        {"name": "topics_mentioned", "type": "multipleSelects", "options": {"choices": []}},
        {
            "name": "escalated",
            "type": "checkbox",
            "options": {"icon": "flag", "color": "redBright"},
        },
        {"name": "langfuse_trace_url", "type": "url"},
    ]


async def fetch_tables(client: httpx.AsyncClient, headers: dict, base_id: str) -> dict:
    response = await client.get(f"{META_BASE}/{base_id}/tables", headers=headers)
    response.raise_for_status()
    return {t["name"]: t for t in response.json().get("tables", [])}


async def ensure_table(
    client: httpx.AsyncClient,
    headers: dict,
    base_id: str,
    name: str,
    desired_fields: list[dict],
    existing_tables: dict,
) -> dict:
    if name not in existing_tables:
        print(f"Creating table {name!r}...")
        response = await client.post(
            f"{META_BASE}/{base_id}/tables",
            headers=headers,
            json={"name": name, "fields": desired_fields},
        )
        _check(response, f"create table {name}")
        print(f"  created with {len(desired_fields)} field(s)")
        return response.json()

    table = existing_tables[name]
    existing_field_names = {f["name"] for f in table["fields"]}
    missing = [f for f in desired_fields if f["name"] not in existing_field_names]
    if not missing:
        print(f"Table {name!r} already complete.")
        return table

    print(f"Table {name!r} exists, adding {len(missing)} missing field(s):")
    for field in missing:
        response = await client.post(
            f"{META_BASE}/{base_id}/tables/{table['id']}/fields",
            headers=headers,
            json=field,
        )
        _check(response, f"add field {field['name']} to {name}")
        print(f"  + {field['name']}")
    refreshed = await fetch_tables(client, headers, base_id)
    return refreshed[name]


async def ensure_caller_link(
    client: httpx.AsyncClient,
    headers: dict,
    base_id: str,
    interactions: dict,
    callers: dict,
) -> None:
    existing_field_names = {f["name"] for f in interactions["fields"]}
    if "caller" in existing_field_names:
        print("Link field 'caller' already on interactions.")
        return
    print("Adding link field 'caller' on interactions -> callers...")
    response = await client.post(
        f"{META_BASE}/{base_id}/tables/{interactions['id']}/fields",
        headers=headers,
        json={
            "name": "caller",
            "type": "multipleRecordLinks",
            "options": {"linkedTableId": callers["id"]},
        },
    )
    _check(response, "add link field 'caller'")
    print("  link created")


async def rename_reverse_link(
    client: httpx.AsyncClient,
    headers: dict,
    base_id: str,
    callers_name: str,
    interactions_id: str,
) -> None:
    tables = await fetch_tables(client, headers, base_id)
    callers = tables[callers_name]
    for field in callers["fields"]:
        if field["name"] == "linked_interactions":
            print("Reverse link already named 'linked_interactions'.")
            return
        if (
            field["type"] == "multipleRecordLinks"
            and field.get("options", {}).get("linkedTableId") == interactions_id
        ):
            print(f"Renaming reverse link {field['name']!r} -> 'linked_interactions'...")
            response = await client.patch(
                f"{META_BASE}/{base_id}/tables/{callers['id']}/fields/{field['id']}",
                headers=headers,
                json={"name": "linked_interactions"},
            )
            _check(response, "rename reverse link")
            print("  renamed")
            return


def _check(response: httpx.Response, action: str) -> None:
    if response.status_code >= 400:
        print(f"  ERROR ({action}): {response.status_code} {response.text}")
        response.raise_for_status()


async def setup() -> int:
    api_key = os.environ["AIRTABLE_API_KEY"]
    base_id = os.environ["AIRTABLE_BASE_ID"]
    callers_name = os.environ["AIRTABLE_CALLERS_TABLE"]
    interactions_name = os.environ["AIRTABLE_INTERACTIONS_TABLE"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        tables = await fetch_tables(client, headers, base_id)
        callers = await ensure_table(
            client, headers, base_id, callers_name, callers_schema(), tables
        )
        interactions = await ensure_table(
            client, headers, base_id, interactions_name, interactions_schema(), tables
        )
        await ensure_caller_link(client, headers, base_id, interactions, callers)
        await rename_reverse_link(
            client, headers, base_id, callers_name, interactions["id"]
        )

    print("\nSchema setup complete.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(setup()))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            print(
                "\n403 Forbidden — your PAT is missing the `schema.bases:write` scope.\n"
                "Add it at https://airtable.com/create/tokens and re-run."
            )
        sys.exit(1)
