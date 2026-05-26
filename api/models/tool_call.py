"""VAPI function-tool request/response payloads.

VAPI POSTs `message.toolCalls[]` and expects `results[]` back, pairing each
original `toolCallId` with the value the LLM should see.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VAPIFunctionCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    arguments: Any


class VAPIToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    function: VAPIFunctionCall


class _CallInfo(BaseModel):
    # Minimal wrapper — we only need call.id for Langfuse session grouping.
    model_config = ConfigDict(extra="ignore")

    id: str | None = None


class VAPIToolCallMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str
    tool_calls: list[VAPIToolCall] = Field(default_factory=list, alias="toolCalls")
    call: _CallInfo | None = None

    @property
    def call_id(self) -> str | None:
        return self.call.id if self.call else None


class VAPIToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: VAPIToolCallMessage


class VAPIToolResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_call_id: str = Field(serialization_alias="toolCallId")
    result: Any


class VAPIToolResponse(BaseModel):
    results: list[VAPIToolResult]
