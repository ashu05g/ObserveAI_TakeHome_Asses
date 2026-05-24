from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Sentiment = Literal["positive", "neutral", "negative"]
Intent = Literal["claim_status", "faq", "escalation", "new_claim", "other"]


class InteractionLog(BaseModel):
    """A row to be written to the Airtable `interactions` table."""

    model_config = ConfigDict(extra="forbid")

    caller_airtable_id: str | None
    timestamp: datetime
    authenticated: bool
    call_duration_seconds: int
    transcript: str
    summary: str
    sentiment: Sentiment
    sentiment_arc: str
    detected_intent: Intent
    qa_score: float | None = Field(ge=0.0, le=1.0, default=None)
    qa_breakdown: str
    topics_mentioned: list[str] = Field(default_factory=list)
    escalated: bool
    langfuse_trace_url: str | None = None
