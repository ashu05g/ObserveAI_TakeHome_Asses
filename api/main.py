"""FastAPI entry point.

Loads `.env` for local dev (no-op when env vars are already set by the
hosting platform, e.g. Railway). Validates required env vars on startup
so misconfiguration fails fast instead of producing cryptic runtime
errors on the first webhook hit.
"""

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from api.routers import lookup, webhook  # noqa: E402 — must follow load_dotenv

REQUIRED_ENV = (
    "AIRTABLE_API_KEY",
    "AIRTABLE_BASE_ID",
    "AIRTABLE_CALLERS_TABLE",
    "AIRTABLE_INTERACTIONS_TABLE",
    "OPENAI_API_KEY",
    "RESEND_API_KEY",
    "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO",
    "VAPI_WEBHOOK_SECRET",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _missing_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.environ.get(name)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = _missing_env()
    if missing:
        raise RuntimeError(
            f"Required environment variables are not set: {', '.join(missing)}"
        )
    yield


app = FastAPI(title="Observe Claims Agent", version="0.1.0", lifespan=lifespan)
app.include_router(lookup.router)
app.include_router(webhook.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
