"""Langfuse LLM-trace wrapper.

Exposes one context manager `trace_pipeline(call_id)` that yields an object
with a `.url` pointing at the Langfuse trace view (so we can deep-link from
Airtable + email alerts), plus `observed(name)` for wrapping individual LLM
calls as child spans.

When Langfuse isn't configured (env vars unset or LANGFUSE_ENABLED=false),
both helpers become no-ops yielding a TraceHandle with url=None. This
keeps the pipeline code path identical between prod and tests.
"""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")
_client = None
_client_init_attempted = False


@dataclass
class TraceHandle:
    url: str | None = None


@dataclass
class SpanHandle:
    """Subset of langfuse Span surface we use, plus a no-op fallback."""

    _span: object | None = None

    def update(self, **kwargs) -> None:
        if self._span is not None:
            try:
                self._span.update(**kwargs)
            except Exception:
                logger.exception("langfuse span.update failed")


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
        return None
    try:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ["LANGFUSE_HOST"],
        )
    except Exception:
        logger.exception("failed to initialize Langfuse client; tracing disabled")
        _client = None
    return _client


def reset_for_tests() -> None:
    """Drop the cached client so env-var changes take effect mid-test."""
    global _client, _client_init_attempted
    _client = None
    _client_init_attempted = False


@contextmanager
def trace_pipeline(call_id: str) -> Iterator[TraceHandle]:
    handle = TraceHandle()
    client = _get_client()
    if client is None:
        yield handle
        return

    try:
        with client.start_as_current_span(name="post_call_pipeline") as span:
            try:
                span.update_trace(name=f"call_{call_id}", tags=["claims-agent"])
                handle.url = _build_trace_url(client, span)
            except Exception:
                logger.exception("failed to set trace metadata; continuing")
            yield handle
    finally:
        try:
            client.flush()
        except Exception:
            logger.exception("langfuse flush failed")


@contextmanager
def observed(name: str) -> Iterator[SpanHandle]:
    """Wrap a child operation in a span. No-op if Langfuse is off or no
    enclosing trace is active."""
    client = _get_client()
    if client is None:
        yield SpanHandle()
        return
    try:
        with client.start_as_current_span(name=name) as span:
            yield SpanHandle(_span=span)
    except Exception:
        logger.exception("langfuse span failed; continuing")
        yield SpanHandle()


def _build_trace_url(client, span) -> str | None:
    """Try a couple of known SDK shapes for resolving the trace URL."""
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
