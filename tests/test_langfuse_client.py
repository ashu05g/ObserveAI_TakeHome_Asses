from unittest.mock import MagicMock, patch

import pytest

from api.services.langfuse_client import (
    get_openai_client,
    log_call_event,
    reset_for_tests,
    trace_lookup,
    trace_pipeline,
)


class TestDisabledMode:
    """Default conftest sets LANGFUSE_ENABLED=false. All operations must
    no-op, never reach out to the network, and yield safe handles."""

    def test_trace_pipeline_yields_handle_with_no_url(self):
        with trace_pipeline("call_abc") as handle:
            assert handle.url is None

    def test_get_openai_client_returns_plain_client(self):
        from openai import AsyncOpenAI as PlainAsyncOpenAI
        client = get_openai_client()
        assert isinstance(client, PlainAsyncOpenAI)

    def test_no_langfuse_client_imported(self):
        # If `from langfuse import Langfuse` is ever executed, this patch
        # would have replaced the langfuse module with None and the import
        # would raise. trace_pipeline must short-circuit before that.
        with (
            patch.dict("sys.modules", {"langfuse": None}),
            trace_pipeline("call_abc") as handle,
        ):
            assert handle.url is None


class TestEnabledMode:
    @pytest.fixture
    def fake_langfuse_module(self, monkeypatch):
        """Inject a stub `langfuse` module so the Langfuse client init
        succeeds without making network calls."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        reset_for_tests()

        fake_span = MagicMock()
        fake_span.trace_id = "trace_abc123"
        fake_span.__enter__ = MagicMock(return_value=fake_span)
        fake_span.__exit__ = MagicMock(return_value=False)

        fake_client = MagicMock()
        fake_client.start_as_current_span = MagicMock(return_value=fake_span)
        fake_client.get_trace_url = MagicMock(
            return_value="https://us.cloud.langfuse.com/trace/trace_abc123"
        )

        fake_module = MagicMock()
        fake_module.Langfuse = MagicMock(return_value=fake_client)

        monkeypatch.setitem(__import__("sys").modules, "langfuse", fake_module)
        yield fake_client, fake_span
        reset_for_tests()

    def test_trace_pipeline_yields_real_url(self, fake_langfuse_module):
        with trace_pipeline("call_abc") as handle:
            assert handle.url == "https://us.cloud.langfuse.com/trace/trace_abc123"

    def test_trace_pipeline_flushes_on_exit(self, fake_langfuse_module):
        fake_client, _ = fake_langfuse_module
        with trace_pipeline("call_abc"):
            pass
        fake_client.flush.assert_called_once()

    def test_trace_pipeline_calls_update_trace_with_session(self, fake_langfuse_module):
        _, fake_span = fake_langfuse_module
        with trace_pipeline("call_xyz"):
            pass
        # session_id=call_id so the post-call trace appears alongside live
        # events in the Langfuse Sessions waterfall view.
        fake_span.update_trace.assert_called_with(
            name="post_call_pipeline",
            session_id="call_xyz",
            tags=["claims-agent", "post-call"],
        )

    def test_get_openai_client_returns_traced_when_available(self, monkeypatch):
        """When langfuse.openai is importable, get_openai_client returns
        the traced AsyncOpenAI."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        reset_for_tests()

        fake_traced_client = MagicMock(name="TracedAsyncOpenAI_instance")
        fake_traced_class = MagicMock(return_value=fake_traced_client)

        fake_langfuse_module = MagicMock()
        fake_langfuse_module.Langfuse = MagicMock(return_value=MagicMock())

        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = fake_traced_class

        modules = __import__("sys").modules
        monkeypatch.setitem(modules, "langfuse", fake_langfuse_module)
        monkeypatch.setitem(modules, "langfuse.openai", fake_openai_module)

        client = get_openai_client()
        assert client is fake_traced_client
        reset_for_tests()

    def test_falls_back_to_plain_openai_if_traced_import_fails(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        reset_for_tests()

        fake_langfuse_module = MagicMock()
        fake_langfuse_module.Langfuse = MagicMock(return_value=MagicMock())

        modules = __import__("sys").modules
        monkeypatch.setitem(modules, "langfuse", fake_langfuse_module)
        monkeypatch.setitem(modules, "langfuse.openai", None)

        from openai import AsyncOpenAI as PlainAsyncOpenAI
        client = get_openai_client()
        assert isinstance(client, PlainAsyncOpenAI)
        reset_for_tests()

    def test_falls_back_to_host_url_when_get_trace_url_missing(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        reset_for_tests()

        fake_span = MagicMock()
        fake_span.trace_id = "trace_xyz"
        fake_span.__enter__ = MagicMock(return_value=fake_span)
        fake_span.__exit__ = MagicMock(return_value=False)

        fake_client = MagicMock(spec=["start_as_current_span", "flush"])
        fake_client.start_as_current_span = MagicMock(return_value=fake_span)

        fake_module = MagicMock()
        fake_module.Langfuse = MagicMock(return_value=fake_client)
        monkeypatch.setitem(__import__("sys").modules, "langfuse", fake_module)

        with trace_pipeline("call_abc") as handle:
            assert handle.url == "https://us.cloud.langfuse.com/trace/trace_xyz"

        reset_for_tests()

    def test_initialization_failure_falls_back_to_disabled(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        reset_for_tests()

        fake_module = MagicMock()
        fake_module.Langfuse = MagicMock(side_effect=RuntimeError("init failed"))
        monkeypatch.setitem(__import__("sys").modules, "langfuse", fake_module)

        with trace_pipeline("call_abc") as handle:
            assert handle.url is None

        reset_for_tests()


class TestLiveEventLogging:
    """Live VAPI events (transcript, status-update, model-output) and the
    /lookup tool call must all land under the same Langfuse session as the
    post-call pipeline. We assert by checking session_id on the trace
    metadata passed to the fake Langfuse client."""

    @pytest.fixture
    def fake_langfuse(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        reset_for_tests()

        fake_span = MagicMock()
        fake_span.trace_id = "trace_abc"
        fake_span.__enter__ = MagicMock(return_value=fake_span)
        fake_span.__exit__ = MagicMock(return_value=False)

        fake_client = MagicMock()
        fake_client.start_as_current_span = MagicMock(return_value=fake_span)
        fake_client.get_trace_url = MagicMock(return_value="https://lf/trace/abc")

        fake_module = MagicMock()
        fake_module.Langfuse = MagicMock(return_value=fake_client)
        monkeypatch.setitem(__import__("sys").modules, "langfuse", fake_module)
        yield fake_client, fake_span
        reset_for_tests()

    def test_log_call_event_uses_call_id_as_session(self, fake_langfuse):
        _, fake_span = fake_langfuse
        log_call_event("call_abc", "transcript", {"role": "user", "transcript": "hello"})
        kwargs = fake_span.update_trace.call_args.kwargs
        assert kwargs["session_id"] == "call_abc"
        assert kwargs["name"] == "vapi:transcript"
        assert "event:transcript" in kwargs["tags"]

    def test_log_call_event_attaches_input_fields(self, fake_langfuse):
        _, fake_span = fake_langfuse
        log_call_event("call_abc", "status-update", {"status": "ended"})
        fake_span.update.assert_called_with(input={"status": "ended"})

    def test_log_call_event_with_no_fields_skips_update(self, fake_langfuse):
        _, fake_span = fake_langfuse
        log_call_event("call_abc", "status-update", None)
        fake_span.update.assert_not_called()

    def test_log_call_event_is_noop_when_disabled(self, monkeypatch):
        # default conftest sets LANGFUSE_ENABLED=false — no client created.
        # Verify by patching the langfuse module so import would fail; if
        # log_call_event were trying to use it, this would raise.
        from unittest.mock import patch
        with patch.dict("sys.modules", {"langfuse": None}):
            log_call_event("call_abc", "transcript", {"role": "user"})
            # no exception raised = success

    def test_trace_lookup_uses_call_id_as_session(self, fake_langfuse):
        _, fake_span = fake_langfuse
        with trace_lookup("call_abc"):
            pass
        kwargs = fake_span.update_trace.call_args.kwargs
        assert kwargs["session_id"] == "call_abc"
        assert kwargs["name"] == "lookup_caller"

    def test_trace_lookup_without_call_id_omits_session(self, fake_langfuse):
        _, fake_span = fake_langfuse
        with trace_lookup(None):
            pass
        kwargs = fake_span.update_trace.call_args.kwargs
        assert "session_id" not in kwargs

    def test_trace_lookup_yields_span_when_enabled(self, fake_langfuse):
        _, fake_span = fake_langfuse
        with trace_lookup("call_abc") as span:
            assert span is fake_span

    def test_trace_lookup_yields_none_when_disabled(self):
        # default conftest disables Langfuse
        with trace_lookup("call_abc") as span:
            assert span is None


class TestEnvGating:
    def test_disabled_when_keys_missing(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        reset_for_tests()
        with trace_pipeline("c") as handle:
            assert handle.url is None
        reset_for_tests()

    def test_explicit_disabled_overrides_present_keys(self):
        # Keys are present (from autouse fixture) but LANGFUSE_ENABLED=false
        # (also from autouse) — handle must come back empty.
        with trace_pipeline("c") as handle:
            assert handle.url is None
