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
    """Open a named Langfuse trace for the duration of the with-block.
    Any langfuse.openai LLM calls inside become child spans automatically.
    Yields a TraceHandle whose `.url` is the deep-link to the trace view.

    `client.start_as_current_span(...)` returns a context manager, not the
    span itself — the span is the value bound by `with ... as span`. Calling
    `update_trace` on the context manager object raises AttributeError.
    """
    handle = TraceHandle()
    client = _get_client()
    if client is None:
        yield handle
        return

    try:
        with client.start_as_current_span(name="post_call_pipeline") as span:
            try:
                span.update_trace(name=f"call_{call_id}", tags=["claims-agent"])
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
