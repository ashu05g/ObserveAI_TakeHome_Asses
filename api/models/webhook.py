"""VAPI webhook payload model.

VAPI's event-type list is open-ended, so `type` is a free string and the
router branches on it. Event-type-specific fields are all optional —
each event populates a different subset.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VAPIToolMessage(BaseModel):
    """One entry in `call.messages`. Tool-result messages
    (role=`tool_call_result`) put the LLM-visible payload in `result`,
    not `content` — we capture both and let the consumer pick."""

    model_config = ConfigDict(extra="ignore")

    role: str | None = None
    name: str | None = None
    content: Any = None
    result: Any = None
    tool_call_id: str | None = Field(default=None, alias="toolCallId")


class VAPICall(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")
    messages: list[VAPIToolMessage] = Field(default_factory=list)


class VAPIEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str
    call: VAPICall

    # end-of-call-report
    transcript: str | None = None
    summary: str | None = None
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")

    # status-update
    status: str | None = None
    ended_reason: str | None = Field(default=None, alias="endedReason")

    # transcript event
    role: str | None = None
    transcript_type: str | None = Field(default=None, alias="transcriptType")

    # model-output
    output: str | None = None


class VAPIWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: VAPIEvent
