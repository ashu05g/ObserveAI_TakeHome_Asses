"""VAPI function-tool request/response payloads.

VAPI POSTs to the tool URL with a `message.toolCalls[]` array (each call has
an id, function name, and arguments) and expects a `results[]` array back —
each entry pairs the original toolCallId with the value the LLM should see.
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


class VAPIToolCallMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str
    tool_calls: list[VAPIToolCall] = Field(default_factory=list, alias="toolCalls")


class VAPIToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: VAPIToolCallMessage


class VAPIToolResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_call_id: str = Field(serialization_alias="toolCallId")
    result: Any


class VAPIToolResponse(BaseModel):
    results: list[VAPIToolResult]
