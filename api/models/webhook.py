"""VAPI webhook payload model.

VAPI wraps every server-URL event under a top-level `message` field, and the
catalog of event types is open-ended (assistant-request, transcript,
status-update, end-of-call-report, model-output, tool-calls, hang,
user-interrupted, ...). We accept any string `type` and let the router
branch — only `end-of-call-report` triggers the post-call pipeline; the
rest are ignored.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VAPIToolMessage(BaseModel):
    """One entry in `call.messages`.

    VAPI's tool-result messages use role=`tool_call_result` and put the
    string result the LLM sees in a `result` field (NOT `content`).
    Other roles (`system`, `bot`, `user`, `tool_calls`) put text in
    `content` or are wrappers around `toolCalls`. We capture both fields
    and let the consumer pick the right one.
    """

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
    """The inner `message` object containing the actual event data."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str
    call: VAPICall
    transcript: str | None = None
    summary: str | None = None
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")


class VAPIWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: VAPIEvent
