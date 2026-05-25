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
    """The inner `message` object containing the actual event data.

    Different event types populate different fields:
      - end-of-call-report: transcript, summary, duration_seconds
      - status-update: status, ended_reason
      - transcript: role, transcript, transcript_type
      - model-output: output
    Unset fields stay None.
    """

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
