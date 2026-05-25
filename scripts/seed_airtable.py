"""Seed the Airtable `callers` table with five realistic test records.

Upserts by phone: for each caller, looks up by phone, PATCHes the existing
row if found, POSTs if not. Safe to re-run after schema changes — existing
rows get the new fields populated without duplication.

    python scripts/seed_airtable.py
"""

import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

# Realistic detail per caller so the agent has something to answer
# "how much is my claim for?" / "who's my adjuster?" with.
CALLERS = [
    {
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+14155550001",
        "claim_id": "CLM-2024-0001",
        "claim_status": "approved",
        "claim_type": "auto",
        "claim_date": "2024-08-12",
        "incident_date": "2024-08-10",
        "claim_amount": 8500.00,
        "approved_amount": 8000.00,
        "adjuster_name": "Robert Chen",
        "estimated_payout_date": "2024-08-25",
        "documents_needed": "",
        "claim_description": (
            "Rear-end collision on I-280 northbound. Minor body damage and "
            "broken rear bumper, no injuries reported."
        ),
    },
    {
        "first_name": "Marcus",
        "last_name": "Chen",
        "phone": "+14155550002",
        "claim_id": "CLM-2024-0002",
        "claim_status": "pending",
        "claim_type": "home",
        "claim_date": "2024-10-03",
        "incident_date": "2024-10-01",
        "claim_amount": 14500.00,
        "approved_amount": None,
        "adjuster_name": "Linda Park",
        "estimated_payout_date": None,
        "documents_needed": "",
        "claim_description": (
            "Water damage to basement from burst pipe during October cold "
            "snap. Drywall and flooring damaged across approx. 400 sq ft."
        ),
    },
    {
        "first_name": "Priya",
        "last_name": "Sharma",
        "phone": "+14155550003",
        "claim_id": "CLM-2024-0003",
        "claim_status": "requires_documentation",
        "claim_type": "health",
        "claim_date": "2024-11-15",
        "incident_date": "2024-11-10",
        "claim_amount": 3200.00,
        "approved_amount": None,
        "adjuster_name": "David Martinez",
        "estimated_payout_date": None,
        "documents_needed": (
            "Itemized medical bill from the treating provider, dated visit "
            "summary, and proof-of-payment receipt. Upload via the portal or "
            "email support@observeinsurance.com with the claim ID in the subject."
        ),
        "claim_description": (
            "Emergency room visit for a sprained ankle with follow-up x-ray "
            "imaging the next day."
        ),
    },
    {
        "first_name": "Tom",
        "last_name": "Rivera",
        "phone": "+14155550004",
        "claim_id": "CLM-2024-0004",
        "claim_status": "approved",
        "claim_type": "life",
        "claim_date": "2024-09-22",
        "incident_date": "2024-09-15",
        "claim_amount": 250000.00,
        "approved_amount": 250000.00,
        "adjuster_name": "Sarah Kim",
        "estimated_payout_date": "2024-10-05",
        "documents_needed": "",
        "claim_description": (
            "Life insurance beneficiary claim filed by the surviving spouse. "
            "Death certificate and policy documentation received and verified."
        ),
    },
    {
        "first_name": "Alice",
        "last_name": "Nguyen",
        "phone": "+14155550005",
        "claim_id": "CLM-2024-0005",
        "claim_status": "pending",
        "claim_type": "auto",
        "claim_date": "2024-12-01",
        "incident_date": "2024-11-28",
        "claim_amount": 4800.00,
        "approved_amount": None,
        "adjuster_name": "James Wilson",
        "estimated_payout_date": None,
        "documents_needed": "",
        "claim_description": (
            "Hail damage to vehicle in an open parking lot. Hood and roof "
            "panels affected; windshield intact."
        ),
    },
]


def _airtable_fields(caller: dict) -> dict:
    """Strip None values — Airtable returns 422 if you send null for an
    empty optional field instead of just omitting it."""
    return {k: v for k, v in caller.items() if v is not None and v != ""}


async def seed() -> int:
    api_key = os.environ["AIRTABLE_API_KEY"]
    base_id = os.environ["AIRTABLE_BASE_ID"]
    table = os.environ["AIRTABLE_CALLERS_TABLE"]

    url = f"https://api.airtable.com/v0/{base_id}/{table}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        existing_resp = await client.get(
            url, headers=headers, params={"fields[]": "phone"}
        )
        existing_resp.raise_for_status()
        existing_by_phone = {
            r["fields"].get("phone"): r["id"]
            for r in existing_resp.json().get("records", [])
            if r["fields"].get("phone")
        }

        created = updated = 0
        for caller in CALLERS:
            phone = caller["phone"]
            fields = _airtable_fields(caller)
            if phone in existing_by_phone:
                rec_id = existing_by_phone[phone]
                resp = await client.patch(
                    f"{url}/{rec_id}",
                    headers=headers,
                    json={"fields": fields, "typecast": True},
                )
                resp.raise_for_status()
                updated += 1
                print(f"  updated {caller['first_name']} {caller['last_name']} ({phone})")
            else:
                resp = await client.post(
                    url,
                    headers=headers,
                    json={"fields": fields, "typecast": True},
                )
                resp.raise_for_status()
                created += 1
                print(f"  created {caller['first_name']} {caller['last_name']} ({phone})")

        print(f"\nDone. created={created} updated={updated}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(seed()))
