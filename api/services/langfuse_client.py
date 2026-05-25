"""Langfuse LLM-trace integration.

Strategy (v3):
  - Initialize a singleton Langfuse client at first use.
  - For every LLM call, use `langfuse.openai.AsyncOpenAI` which is a drop-in
    replacement for `openai.AsyncOpenAI` that auto-captures input, output,
    model, latency and token usage as observation spans.
  - Wrap the whole pipeline in `trace_pipeline(call_id)` so the auto-traced
    OpenAI spans roll up as children of one named trace per call, and so
    we can resolve a deep-link URL to store on the Airtable row.

When Langfuse isn't configured (env unset or LANGFUSE_ENABLED=false) both
helpers no-op — the pipeline runs unchanged.
"""

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")
_client = None
_client_init_attempted = False


@dataclass
class TraceHandle:
    url: str | None = None


def _is_enabled() -> bool:
    if os.environ.get("LANGFUSE_ENABLED", "true").lower() == "false":
        return False
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


def _get_client():
    global _client, _client_init_attempted
    if _client is not None or _client_init_attempted:
        return _client
    _client_init_attempted = True
    if not _is_enabled():
        logger.info("langfuse: disabled (LANGFUSE_ENABLED=false or required env vars missing)")
        return None
    try:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ["LANGFUSE_HOST"],
        )
        logger.info("langfuse: client initialized host=%s", os.environ["LANGFUSE_HOST"])
    except Exception:
        logger.exception("langfuse: client init failed; tracing disabled")
        _client = None
    return _client


def reset_for_tests() -> None:
    global _client, _client_init_attempted
    _client = None
    _client_init_attempted = False


def get_openai_client() -> "AsyncOpenAI":
    """Return an AsyncOpenAI client. When Langfuse is enabled, the returned
    client is `langfuse.openai.AsyncOpenAI` which auto-instruments every
    chat completion call as a Langfuse generation span."""
    client = _get_client()
    if client is not None:
        try:
            from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI
            logger.debug("langfuse: returning traced AsyncOpenAI client")
            return TracedAsyncOpenAI()
        except Exception:
            logger.exception("langfuse: failed to import langfuse.openai; using plain client")
    from openai import AsyncOpenAI
    return AsyncOpenAI()


@contextmanager
def trace_pipeline(call_id: str):
    """Open a named Langfuse trace for the post-call pipeline, grouped under
    the call's session so it appears alongside live events in the Sessions
    view. Yields a TraceHandle whose `.url` deep-links to the trace.
    """
    handle = TraceHandle()
    client = _get_client()
    if client is None:
        yield handle
        return

    try:
        with client.start_as_current_span(name="post_call_pipeline") as span:
            try:
                span.update_trace(
                    name="post_call_pipeline",
                    session_id=call_id,
                    tags=["claims-agent", "post-call"],
                )
            except Exception:
                logger.exception("langfuse: update_trace failed; continuing")
            handle.url = _build_trace_url(client, span)
            logger.info("langfuse: trace started call_id=%s url=%s", call_id, handle.url)
            yield handle
    finally:
        try:
            client.flush()
            logger.debug("langfuse: flushed call_id=%s", call_id)
        except Exception:
            logger.exception("langfuse: flush failed")


@contextmanager
def trace_lookup(call_id: str | None):
    """Wrap a /lookup invocation in a Langfuse trace. When `call_id` is
    provided (always true for VAPI-triggered requests), the trace is grouped
    under the call's session for waterfall view alongside other live events.
    """
    client = _get_client()
    if client is None:
        yield None
        return

    try:
        with client.start_as_current_span(name="lookup_caller") as span:
            try:
                update_kwargs: dict = {
                    "name": "lookup_caller",
                    "tags": ["claims-agent", "live", "tool"],
                }
                if call_id:
                    update_kwargs["session_id"] = call_id
                span.update_trace(**update_kwargs)
            except Exception:
                logger.exception("langfuse: update_trace failed; continuing")
            yield span
    finally:
        try:
            client.flush()
        except Exception:
            logger.exception("langfuse: flush failed")


def log_call_event(call_id: str, event_type: str, fields: dict | None = None) -> None:
    """Record a VAPI webhook event as a short Langfuse trace grouped under
    the call's session. Used for live waterfall — one trace per webhook
    event (status-update, transcript, model-output, ...)."""
    client = _get_client()
    if client is None:
        return

    try:
        with client.start_as_current_span(name=f"vapi:{event_type}") as span:
            try:
                span.update_trace(
                    name=f"vapi:{event_type}",
                    session_id=call_id,
                    tags=["claims-agent", "live", f"event:{event_type}"],
                )
                if fields:
                    span.update(input=fields)
            except Exception:
                logger.exception("langfuse: log_call_event update failed")
    except Exception:
        logger.exception("langfuse: log_call_event failed")
    finally:
        try:
            client.flush()
        except Exception:
            logger.exception("langfuse: flush failed")


def _build_trace_url(client, span) -> str | None:
    trace_id = getattr(span, "trace_id", None)
    if not trace_id:
        return None
    for method in ("get_trace_url", "get_trace_url_for_id"):
        fn = getattr(client, method, None)
        if callable(fn):
            try:
                return fn(trace_id=trace_id)
            except Exception:
                continue
    host = os.environ.get("LANGFUSE_HOST", "").rstrip("/")
    return f"{host}/trace/{trace_id}" if host else None
