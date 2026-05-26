"""Langfuse tracing: every observation for a given VAPI call rolls up under
one trace, so the call renders as a native waterfall in Langfuse.

We derive a deterministic 128-bit trace ID from VAPI's call_id and attach
it as an OpenTelemetry parent context before starting each span. Langfuse
v3 is OTel-native, so spans created under that context group correctly.

No-ops when Langfuse env isn't configured.
"""

import contextlib
import hashlib
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry import context as otel_context
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

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
        logger.info("langfuse: disabled (env missing or LANGFUSE_ENABLED=false)")
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
        logger.exception("langfuse: init failed; tracing disabled")
        _client = None
    return _client


def reset_for_tests() -> None:
    global _client, _client_init_attempted
    _client = None
    _client_init_attempted = False


def get_openai_client() -> "AsyncOpenAI":
    """Return an AsyncOpenAI client; uses langfuse.openai's auto-instrumented
    variant when Langfuse is enabled so every chat completion becomes a
    generation span."""
    client = _get_client()
    if client is not None:
        try:
            from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI
            return TracedAsyncOpenAI()
        except Exception:
            logger.exception("langfuse: failed to import langfuse.openai; using plain client")
    from openai import AsyncOpenAI
    return AsyncOpenAI()


def _trace_id_for_call(call_id: str) -> int:
    return int(hashlib.sha256(f"vapi-call-{call_id}".encode()).hexdigest()[:32], 16)


def _root_span_id_for_call(call_id: str) -> int:
    # Synthetic span ID for the parent SpanContext; never emitted as an
    # observation, only carries the trace_id for OTel context propagation.
    return int.from_bytes(
        hashlib.sha256(f"vapi-root-{call_id}".encode()).digest()[:8], "big"
    )


def _parent_context_for_call(call_id: str):
    span_context = SpanContext(
        trace_id=_trace_id_for_call(call_id),
        span_id=_root_span_id_for_call(call_id),
        is_remote=True,
        trace_flags=TraceFlags(0x01),
    )
    return set_span_in_context(NonRecordingSpan(span_context))


def _trace_url(call_id: str) -> str | None:
    host = os.environ.get("LANGFUSE_HOST", "").rstrip("/")
    return f"{host}/trace/{_trace_id_for_call(call_id):032x}" if host else None


def _flush(client) -> None:
    with contextlib.suppress(Exception):
        client.flush()


def _set_call_trace_metadata(span, call_id: str, tags: list[str]) -> None:
    with contextlib.suppress(Exception):
        span.update_trace(
            name=f"call:{call_id[:8]}",
            session_id=call_id,
            tags=tags,
        )


@contextmanager
def trace_pipeline(call_id: str):
    """Wrap the post-call pipeline. Yields a TraceHandle whose `.url` deep
    links to the call's trace."""
    handle = TraceHandle()
    client = _get_client()
    if client is None:
        yield handle
        return

    handle.url = _trace_url(call_id)
    token = otel_context.attach(_parent_context_for_call(call_id))
    try:
        with client.start_as_current_span(name="post_call_pipeline") as span:
            _set_call_trace_metadata(span, call_id, ["claims-agent", "post-call"])
            logger.info("langfuse: trace open call_id=%s url=%s", call_id, handle.url)
            yield handle
    finally:
        otel_context.detach(token)
        _flush(client)


@contextmanager
def trace_lookup(call_id: str | None):
    """Wrap /lookup execution. When call_id is known, the span attaches to
    the call's shared trace; otherwise it's an orphan trace."""
    client = _get_client()
    if client is None:
        yield None
        return

    if call_id is None:
        with client.start_as_current_span(name="lookup_caller") as span:
            yield span
        _flush(client)
        return

    token = otel_context.attach(_parent_context_for_call(call_id))
    try:
        with client.start_as_current_span(name="lookup_caller") as span:
            _set_call_trace_metadata(span, call_id, ["claims-agent", "live", "tool"])
            yield span
    finally:
        otel_context.detach(token)
        _flush(client)


def log_call_event(call_id: str, event_type: str, fields: dict | None = None) -> None:
    """Record a VAPI webhook event as a span in the call's shared trace."""
    client = _get_client()
    if client is None:
        return

    token = otel_context.attach(_parent_context_for_call(call_id))
    try:
        with client.start_as_current_span(name=f"vapi:{event_type}") as span:
            _set_call_trace_metadata(
                span, call_id, ["claims-agent", "live", f"event:{event_type}"]
            )
            if fields:
                with contextlib.suppress(Exception):
                    span.update(input=fields)
    except Exception:
        logger.exception("langfuse: log_call_event failed")
    finally:
        otel_context.detach(token)
        _flush(client)
