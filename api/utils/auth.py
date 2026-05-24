"""Webhook header-token verification.

VAPI is configured (Dashboard → Assistant → Server URL → Custom Headers) to
send `X-VAPI-Secret: <random-string>` on every webhook. We compare in
constant time to avoid timing oracles, and reject if the env var is empty
to prevent accidental "no-secret-configured = always-pass" failures.
"""

import hmac
import os


def verify_vapi_secret(provided: str | None) -> bool:
    expected = os.environ.get("VAPI_WEBHOOK_SECRET", "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)
