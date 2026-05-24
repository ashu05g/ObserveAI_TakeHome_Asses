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

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"
