from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

ClaimStatus = Literal["approved", "pending", "requires_documentation"]
ClaimType = Literal["auto", "home", "health", "life"]


class CallerRecord(BaseModel):
    """A row from the Airtable `callers` table.

    `airtable_id` is Airtable's internal `rec...` ID (used when linking
    interactions). Optional fields depend on claim stage — e.g.,
    `approved_amount` only exists for approved claims.
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

    # Identity verification factor — challenged during authentication
    # (see prompts/agent_system_prompt.txt).
    date_of_birth: date | None = None

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
