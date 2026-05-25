from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

ClaimStatus = Literal["approved", "pending", "requires_documentation"]
ClaimType = Literal["auto", "home", "health", "life"]


class CallerRecord(BaseModel):
    """A row from the Airtable `callers` table.

    `airtable_id` is Airtable's internal `rec...` identifier (not the
    user-facing auto-number field). It's the value used when linking
    interactions back to a caller.

    Optional fields are populated for callers whose claim has progressed
    far enough to have that data (e.g., `approved_amount` only exists for
    approved claims; `documents_needed` is meaningful only when status is
    `requires_documentation`).
    """

    model_config = ConfigDict(extra="ignore")

    airtable_id: str
    first_name: str
    last_name: str
    phone: str
    claim_id: str
    claim_status: ClaimStatus
    claim_type: ClaimType
    claim_date: date

    claim_amount: float | None = None
    approved_amount: float | None = None
    adjuster_name: str | None = None
    estimated_payout_date: date | None = None
    documents_needed: str | None = None
    claim_description: str | None = None
    incident_date: date | None = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"
