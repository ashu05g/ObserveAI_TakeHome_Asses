"""VAPI webhook payload model.

VAPI wraps every server-URL event under a top-level `message` field. We model
only the fields the post-call pipeline needs; everything else is ignored.
Schemas across VAPI event types overlap heavily, so we keep a single permissive
model and branch on `message.type` in the router.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class VAPIToolMessage(BaseModel):
    """One entry in `call.messages` — could be system/user/assistant/tool."""

    model_config = ConfigDict(extra="ignore")

    role: str | None = None
    name: str | None = None
    content: Any = None
    tool_call_id: str | None = Field(default=None, alias="toolCallId")


class VAPICall(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")
    messages: list[VAPIToolMessage] = Field(default_factory=list)


class VAPIEvent(BaseModel):
    """The inner `message` object containing the actual event data."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: Literal[
        "end-of-call-report",
        "status-update",
        "transcript",
        "function-call",
        "hang",
        "speech-update",
        "conversation-update",
        "tool-calls",
    ]
    call: VAPICall
    transcript: str | None = None
    summary: str | None = None
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")


class VAPIWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: VAPIEvent
