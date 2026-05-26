"""Constant-time verification of the `X-VAPI-Secret` header configured in
the VAPI dashboard. Rejects if the env var is empty so that a misconfigured
deploy fails closed rather than open."""

import hmac
import os


def verify_vapi_secret(provided: str | None) -> bool:
    expected = os.environ.get("VAPI_WEBHOOK_SECRET", "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)
