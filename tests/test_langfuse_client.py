from unittest.mock import MagicMock, patch

import pytest

from api.services.langfuse_client import (
    SpanHandle,
    observed,
    reset_for_tests,
    trace_pipeline,
)


class TestDisabledMode:
    """Default conftest sets LANGFUSE_ENABLED=false. All operations must
    no-op, never reach out to the network, and yield safe handles."""

    def test_trace_pipeline_yields_handle_with_no_url(self):
        with trace_pipeline("call_abc") as handle:
            assert handle.url is None

    def test_observed_yields_noop_span(self):
        with observed("anything") as span:
            assert isinstance(span, SpanHandle)
            # should not raise
            span.update(foo="bar", model="gpt-4o-mini")

    def test_no_langfuse_client_imported(self, monkeypatch):
        # Make `from langfuse import Langfuse` raise if it ever executes
        # — proves _get_client() short-circuits.
        with (
            patch.dict("sys.modules", {"langfuse": None}),
            trace_pipeline("call_abc") as handle,
        ):
            assert handle.url is None


class TestEnabledMode:
    @pytest.fixture
    def fake_langfuse_module(self, monkeypatch):
        """Inject a stub `langfuse` module so _get_client succeeds without
        making network calls."""
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

    def test_observed_passes_update_to_span(self, fake_langfuse_module):
        _, fake_span = fake_langfuse_module
        with observed("analyze_transcript") as span:
            span.update(output="x", model="gpt-4o-mini")
        fake_span.update.assert_called_with(output="x", model="gpt-4o-mini")

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

    def test_explicit_disabled_overrides_present_keys(self, monkeypatch):
        # Keys are present (from autouse fixture) but LANGFUSE_ENABLED=false
        # (also from autouse) — handle must come back empty.
        with trace_pipeline("c") as handle:
            assert handle.url is None
