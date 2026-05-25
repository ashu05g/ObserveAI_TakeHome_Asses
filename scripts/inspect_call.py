"""Fetch a VAPI call by ID and dump everything useful for debugging.

Shows: the assistant config that was actually in effect, the system prompt
the model saw, every tool call with its arguments and the exact result the
LLM received, and the full OpenAI message thread. Use this when the agent's
behavior contradicts what the prompt says — you'll see in 5 seconds whether
the issue is on our side, the prompt side, or the tool-result-passing side.

    python scripts/inspect_call.py <call_id>
"""

import asyncio
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

VAPI_BASE = "https://api.vapi.ai"


def _truncate(value, limit: int = 300) -> str:
    if isinstance(value, dict | list):
        value = json.dumps(value, indent=2)
    s = str(value)
    return s if len(s) <= limit else s[:limit] + f"... [+{len(s) - limit} chars]"


def _dump_assistant(call: dict) -> None:
    """Show what assistant config was actually applied to this call."""
    assistant = call.get("assistant") or call.get("assistantOverrides") or {}
    if not assistant:
        # Some VAPI versions only return assistantId; show that.
        aid = call.get("assistantId")
        if aid:
            print(f"assistantId: {aid}")
            print("(full assistant config not in /call response; use GET /assistant/{id} to inspect)")
        return

    model = assistant.get("model") or {}
    voice = assistant.get("voice") or {}
    transcriber = assistant.get("transcriber") or {}

    print(f"name:        {assistant.get('name')}")
    print(f"model:       {model.get('provider')}/{model.get('model')} (temp={model.get('temperature')})")
    print(f"voice:       {voice.get('provider')}/{voice.get('voiceId')} ({voice.get('model')})")
    print(f"transcriber: {transcriber.get('provider')}/{transcriber.get('model')}")
    print("firstMessage:")
    print(f"  {_truncate(assistant.get('firstMessage', ''), 200)}")

    sys_prompt = next(
        (m.get("content", "") for m in model.get("messages", []) if m.get("role") == "system"),
        "",
    )
    print(f"system prompt ({len(sys_prompt)} chars):")
    print(f"  {_truncate(sys_prompt, 600)}")

    tool_ids = model.get("toolIds") or []
    inline_tools = model.get("tools") or []
    print(f"toolIds:     {tool_ids}")
    print(f"inline tools: {len(inline_tools)}")

    server = assistant.get("server") or {}
    print(f"server URL:  {server.get('url')}")
    headers = server.get("headers") or {}
    print(f"server headers: {list(headers.keys())} (values redacted)")


def _find_messages(call: dict) -> list:
    """VAPI puts the LLM message thread in different places by version."""
    artifact = call.get("artifact") or {}
    for path in (
        artifact.get("messages"),
        artifact.get("messagesOpenAIFormatted"),
        call.get("messages"),
    ):
        if path:
            return path
    return []


def _dump_messages(call: dict) -> None:
    messages = _find_messages(call)
    print(f"\ntotal messages: {len(messages)}")
    if not messages:
        return

    for i, msg in enumerate(messages):
        role = msg.get("role") or msg.get("type") or "?"
        name = msg.get("name", "")

        if role == "system":
            print(f"[{i:>3}] system ({len(msg.get('content', ''))} chars)")
        elif role == "user":
            print(f"[{i:>3}] user:      {_truncate(msg.get('content', ''), 180)}")
        elif role == "assistant":
            content = msg.get("content") or ""
            tool_calls = msg.get("toolCalls") or msg.get("tool_calls") or []
            if content:
                print(f"[{i:>3}] assistant: {_truncate(content, 180)}")
            for tc in tool_calls:
                fn = tc.get("function") or {}
                print(f"      -> call {fn.get('name')}({_truncate(fn.get('arguments'), 120)})")
        elif role in ("tool", "function", "tool_calls"):
            content = msg.get("content")
            if content is None:
                content = msg.get("result", "<no content/result>")
            print(f"[{i:>3}] {role}{f' name={name}' if name else ''}:")
            print(f"      {_truncate(content, 600)}")
        else:
            print(f"[{i:>3}] {role}: {_truncate(msg, 200)}")


def _dump_summary(call: dict) -> None:
    print(f"id:          {call.get('id')}")
    print(f"status:      {call.get('status')}")
    print(f"type:        {call.get('type')}")
    print(f"started:     {call.get('startedAt')}")
    print(f"ended:       {call.get('endedAt')}")
    print(f"endedReason: {call.get('endedReason')}")
    summary = call.get("summary") or (call.get("analysis") or {}).get("summary")
    if summary:
        print(f"summary:     {_truncate(summary, 300)}")


async def inspect(call_id: str) -> int:
    api_key = os.environ["VAPI_API_KEY"]

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(base_url=VAPI_BASE, headers=headers, timeout=20.0) as client:
        response = await client.get(f"/call/{call_id}")
        if response.status_code != 200:
            print(f"VAPI returned {response.status_code}: {response.text}", file=sys.stderr)
            return 1
        call = response.json()

    print("=" * 60)
    print("CALL")
    print("=" * 60)
    _dump_summary(call)

    print("\n" + "=" * 60)
    print("ASSISTANT (what was actually used for this call)")
    print("=" * 60)
    _dump_assistant(call)

    print("\n" + "=" * 60)
    print("MESSAGE THREAD (what the LLM saw)")
    print("=" * 60)
    _dump_messages(call)

    transcript = call.get("transcript") or (call.get("artifact") or {}).get("transcript")
    if transcript:
        print("\n" + "=" * 60)
        print("TRANSCRIPT")
        print("=" * 60)
        print(transcript)

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/inspect_call.py <call_id>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(inspect(sys.argv[1])))
