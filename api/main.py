"""FastAPI entry point. Loads `.env` for local dev, validates required env
vars on startup, and warms the Langfuse client so init failures surface
in startup logs rather than on the first webhook."""

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Required environment variables are not set: {', '.join(missing)}"
        )
    from api.services.langfuse_client import _get_client
    _get_client()
    yield


app = FastAPI(title="Observe Claims Agent", version="0.1.0", lifespan=lifespan)
app.include_router(lookup.router)
app.include_router(webhook.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
