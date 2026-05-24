import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.webhook import router as webhook_router
from api.services import analysis

VALID_SECRET = "test-secret-do-not-use-in-prod"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(webhook_router)
    return TestClient(app)


@pytest.fixture
def captured_pipeline_calls(monkeypatch):
    """Replace the pipeline with a tracker; assert later it ran with the
    expected event."""
    calls = []

    async def _record(event):
        calls.append(event)

    monkeypatch.setattr(analysis, "run_post_call_pipeline", _record)
    return calls


def _end_of_call_payload():
    return {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "call_abc", "durationSeconds": 123},
            "transcript": "agent: hi\ncaller: hi",
            "summary": "summary",
        }
    }


class TestWebhookAuth:
    def test_rejects_missing_header(self, client, captured_pipeline_calls):
        response = client.post("/webhook", json=_end_of_call_payload())
        assert response.status_code == 401
        assert captured_pipeline_calls == []

    def test_rejects_wrong_secret(self, client, captured_pipeline_calls):
        response = client.post(
            "/webhook",
            json=_end_of_call_payload(),
            headers={"X-VAPI-Secret": "definitely-wrong"},
        )
        assert response.status_code == 401
        assert captured_pipeline_calls == []

    def test_accepts_correct_secret(self, client, captured_pipeline_calls):
        response = client.post(
            "/webhook",
            json=_end_of_call_payload(),
            headers={"X-VAPI-Secret": VALID_SECRET},
        )
        assert response.status_code == 200


class TestWebhookDispatch:
    def test_end_of_call_schedules_pipeline(self, client, captured_pipeline_calls):
        response = client.post(
            "/webhook",
            json=_end_of_call_payload(),
            headers={"X-VAPI-Secret": VALID_SECRET},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "received"}
        assert len(captured_pipeline_calls) == 1
        assert captured_pipeline_calls[0].call.id == "call_abc"

    def test_ignores_non_end_of_call_events(self, client, captured_pipeline_calls):
        payload = _end_of_call_payload()
        payload["message"]["type"] = "status-update"

        response = client.post(
            "/webhook",
            json=payload,
            headers={"X-VAPI-Secret": VALID_SECRET},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "type": "status-update"}
        assert captured_pipeline_calls == []

    def test_rejects_unknown_event_type(self, client, captured_pipeline_calls):
        payload = _end_of_call_payload()
        payload["message"]["type"] = "made-up-event"

        response = client.post(
            "/webhook",
            json=payload,
            headers={"X-VAPI-Secret": VALID_SECRET},
        )

        assert response.status_code == 422
        assert captured_pipeline_calls == []

    def test_rejects_malformed_payload(self, client, captured_pipeline_calls):
        response = client.post(
            "/webhook",
            json={"not": "a vapi payload"},
            headers={"X-VAPI-Secret": VALID_SECRET},
        )
        assert response.status_code == 422
        assert captured_pipeline_calls == []
