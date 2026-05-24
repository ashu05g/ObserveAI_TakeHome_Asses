"""Seed the Airtable `callers` table with five test records.

Idempotent: skips any caller whose phone is already in the table. Run
once after creating the base, then re-run any time without duplicates.

    python scripts/seed_airtable.py
"""

import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

CALLERS = [
    {
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+14155550001",
        "claim_id": "CLM-2024-0001",
        "claim_status": "approved",
        "claim_type": "auto",
        "claim_date": "2024-08-12",
    },
    {
        "first_name": "Marcus",
        "last_name": "Chen",
        "phone": "+14155550002",
        "claim_id": "CLM-2024-0002",
        "claim_status": "pending",
        "claim_type": "home",
        "claim_date": "2024-10-03",
    },
    {
        "first_name": "Priya",
        "last_name": "Sharma",
        "phone": "+14155550003",
        "claim_id": "CLM-2024-0003",
        "claim_status": "requires_documentation",
        "claim_type": "health",
        "claim_date": "2024-11-15",
    },
    {
        "first_name": "Tom",
        "last_name": "Rivera",
        "phone": "+14155550004",
        "claim_id": "CLM-2024-0004",
        "claim_status": "approved",
        "claim_type": "life",
        "claim_date": "2024-09-22",
    },
    {
        "first_name": "Alice",
        "last_name": "Nguyen",
        "phone": "+14155550005",
        "claim_id": "CLM-2024-0005",
        "claim_status": "pending",
        "claim_type": "auto",
        "claim_date": "2024-12-01",
    },
]


async def seed() -> int:
    api_key = os.environ["AIRTABLE_API_KEY"]
    base_id = os.environ["AIRTABLE_BASE_ID"]
    table = os.environ["AIRTABLE_CALLERS_TABLE"]

    url = f"https://api.airtable.com/v0/{base_id}/{table}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        existing = await client.get(url, headers=headers, params={"fields[]": "phone"})
        existing.raise_for_status()
        existing_phones = {
            r["fields"].get("phone") for r in existing.json().get("records", [])
        }

        to_create = [c for c in CALLERS if c["phone"] not in existing_phones]
        if not to_create:
            print("All seed callers are already present — nothing to do.")
            return 0

        payload = {"records": [{"fields": c} for c in to_create]}
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        created = response.json()["records"]

        print(f"Created {len(created)} caller record(s):")
        for r in created:
            f = r["fields"]
            print(f"  - {f['first_name']} {f['last_name']:<10} ({f['phone']}) "
                  f"claim {f['claim_id']} [{f['claim_status']}]")
        return len(created)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(seed()) >= 0 else 1)
