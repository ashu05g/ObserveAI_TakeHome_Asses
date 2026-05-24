"""Sync the VAPI assistant + lookup_caller tool from this repo to VAPI.

Idempotent: looks up by name, PATCHes if exists, POSTs if not. Safe to
re-run after every prompt or config tweak — your VAPI workspace converges
to whatever this file defines.

This makes the repo the source of truth: no more manual dashboard edits,
no drift, prompt iteration becomes "edit file -> python scripts/vapi_sync.py".

Required env vars:
  VAPI_API_KEY          — private API key from VAPI dashboard
  SERVER_URL            — public base URL of the deployed FastAPI service
  VAPI_WEBHOOK_SECRET   — same value the FastAPI side uses for header auth

    python scripts/vapi_sync.py
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

VAPI_BASE = "https://api.vapi.ai"
ASSISTANT_NAME = "Emma"
TOOL_NAME = "lookup_caller"

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "agent_system_prompt.txt"
)


def build_tool_config(server_url: str, secret: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Look up a caller's account and claim information using their "
                "phone number. Call this immediately after the caller provides "
                "their phone number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": (
                            "The caller's phone number, exactly as they stated "
                            "it. Include area code."
                        ),
                    },
                },
                "required": ["phone"],
            },
        },
        "server": {
            "url": f"{server_url}/lookup",
            "headers": {"X-VAPI-Secret": secret},
        },
    }


def build_assistant_config(
    server_url: str,
    secret: str,
    system_prompt: str,
    tool_id: str,
) -> dict:
    return {
        "name": ASSISTANT_NAME,
        "model": {
            "provider": "openai",
            "model": "gpt-4.1",
            "temperature": 0.3,
            "messages": [{"role": "system", "content": system_prompt}],
            "toolIds": [tool_id],
        },
        "voice": {
            # ElevenLabs voice IDs. When the VAPI workspace has ElevenLabs
            # credentials linked, VAPI looks up the voice in that account
            # rather than VAPI's curated list — so we use the canonical
            # ID hash from ElevenLabs' default library (Rachel), which is
            # available on every ElevenLabs account.
            "provider": "11labs",
            "voiceId": "21m00Tcm4TlvDq8ikWAM",
            "model": "eleven_flash_v2_5",
        },
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "en-US",
        },
        "firstMessage": (
            "Thank you for calling Observe Insurance. My name is Emma, and "
            "I'm here to help you with your claim. To get started, could you "
            "please provide me with the phone number associated with your "
            "account?"
        ),
        "endCallPhrases": ["goodbye", "have a good day", "thank you goodbye"],
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 600,
        "server": {
            "url": f"{server_url}/webhook",
            "headers": {"X-VAPI-Secret": secret},
        },
        "serverMessages": ["end-of-call-report"],
    }


async def upsert_tool(client: httpx.AsyncClient, config: dict) -> str:
    existing = await _find_by(client, "/tool", lambda t: t.get("function", {}).get("name") == TOOL_NAME)
    if existing:
        tool_id = existing["id"]
        print(f"Updating tool {TOOL_NAME!r} (id={tool_id})...")
        # VAPI's UpdateToolDTO rejects `type` — it's set at creation and immutable.
        patch_payload = {k: v for k, v in config.items() if k != "type"}
        response = await client.patch(f"/tool/{tool_id}", json=patch_payload)
        _check(response, f"patch tool {tool_id}")
        return tool_id

    print(f"Creating tool {TOOL_NAME!r}...")
    response = await client.post("/tool", json=config)
    _check(response, "create tool")
    return response.json()["id"]


async def upsert_assistant(client: httpx.AsyncClient, config: dict) -> str:
    existing = await _find_by(client, "/assistant", lambda a: a.get("name") == ASSISTANT_NAME)
    if existing:
        assistant_id = existing["id"]
        print(f"Updating assistant {ASSISTANT_NAME!r} (id={assistant_id})...")
        response = await client.patch(f"/assistant/{assistant_id}", json=config)
        _check(response, f"patch assistant {assistant_id}")
        return assistant_id

    print(f"Creating assistant {ASSISTANT_NAME!r}...")
    response = await client.post("/assistant", json=config)
    _check(response, "create assistant")
    return response.json()["id"]


async def _find_by(client: httpx.AsyncClient, path: str, predicate) -> dict | None:
    response = await client.get(path)
    _check(response, f"list {path}")
    items = response.json()
    return next((item for item in items if predicate(item)), None)


def _check(response: httpx.Response, action: str) -> None:
    if response.status_code >= 400:
        print(f"  ERROR ({action}): {response.status_code} {response.text}", file=sys.stderr)
        response.raise_for_status()


async def sync() -> int:
    api_key = os.environ["VAPI_API_KEY"]
    server_url = os.environ["SERVER_URL"].rstrip("/")
    secret = os.environ["VAPI_WEBHOOK_SECRET"]
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=VAPI_BASE, headers=headers, timeout=20.0) as client:
        tool_id = await upsert_tool(client, build_tool_config(server_url, secret))
        print(f"  tool id: {tool_id}")
        assistant_id = await upsert_assistant(
            client,
            build_assistant_config(server_url, secret, system_prompt, tool_id),
        )
        print(f"  assistant id: {assistant_id}")

    print("\nSync complete. Phone number assignment (if not already done) is a")
    print("one-time step in the VAPI dashboard -> Phone Numbers -> assign to assistant.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(sync()))
    except KeyError as exc:
        print(f"Missing required env var: {exc}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError:
        sys.exit(1)
