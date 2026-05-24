from unittest.mock import MagicMock, patch

import pytest

from api.services.langfuse_client import (
    get_openai_client,
    reset_for_tests,
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

    def test_trace_pipeline_calls_update_trace(self, fake_langfuse_module):
        _, fake_span = fake_langfuse_module
        with trace_pipeline("call_xyz"):
            pass
        fake_span.update_trace.assert_called_with(
            name="call_call_xyz", tags=["claims-agent"]
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
