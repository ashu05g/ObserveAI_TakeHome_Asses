import pytest


@pytest.fixture(autouse=True)
def _airtable_env(monkeypatch):
    monkeypatch.setenv("AIRTABLE_API_KEY", "pat_test_key")
    monkeypatch.setenv("AIRTABLE_BASE_ID", "appTest1234567890")
    monkeypatch.setenv("AIRTABLE_CALLERS_TABLE", "callers")
    monkeypatch.setenv("AIRTABLE_INTERACTIONS_TABLE", "interactions")


@pytest.fixture(autouse=True)
def _vapi_env(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "test-secret-do-not-use-in-prod")


@pytest.fixture(autouse=True)
def _resend_env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "alerts@test.local")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@test.local")


@pytest.fixture(autouse=True)
def _openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


@pytest.fixture(autouse=True)
def _langfuse_env(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
    # Disable Langfuse network calls in tests by default; individual tests
    # that exercise tracing can flip this back on.
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    from api.services import langfuse_client
    langfuse_client.reset_for_tests()
    yield
    langfuse_client.reset_for_tests()
