import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import webhook as webhook_module
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


@pytest.fixture
def captured_trace_events(monkeypatch):
    """Replace the Langfuse event logger with a tracker so we can assert
    each VAPI webhook gets traced under the call's session."""
    events = []

    def _record(call_id, event_type, fields):
        events.append((call_id, event_type, fields))

    monkeypatch.setattr(webhook_module, "log_call_event", _record)
    return events


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

    def test_non_end_of_call_events_are_logged_not_run_through_pipeline(
        self, client, captured_pipeline_calls
    ):
        payload = _end_of_call_payload()
        payload["message"]["type"] = "status-update"

        response = client.post(
            "/webhook",
            json=payload,
            headers={"X-VAPI-Secret": VALID_SECRET},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "logged", "type": "status-update"}
        assert captured_pipeline_calls == []

    def test_unknown_event_type_is_accepted_and_logged(
        self, client, captured_pipeline_calls
    ):
        # We accept any string `type` (VAPI's event list is open-ended).
        # Unknown types just get logged to Langfuse; no pipeline triggered.
        payload = _end_of_call_payload()
        payload["message"]["type"] = "made-up-event"

        response = client.post(
            "/webhook",
            json=payload,
            headers={"X-VAPI-Secret": VALID_SECRET},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "logged", "type": "made-up-event"}
        assert captured_pipeline_calls == []


class TestLiveTracing:
    def test_status_update_event_is_traced(self, client, captured_trace_events):
        payload = {
            "message": {
                "type": "status-update",
                "status": "in-progress",
                "call": {"id": "call_abc"},
            }
        }
        response = client.post(
            "/webhook",
            json=payload,
            headers={"X-VAPI-Secret": VALID_SECRET},
        )
        assert response.status_code == 200
        assert len(captured_trace_events) == 1
        call_id, event_type, fields = captured_trace_events[0]
        assert call_id == "call_abc"
        assert event_type == "status-update"
        assert fields == {"status": "in-progress", "ended_reason": None}

    def test_final_transcript_is_traced(self, client, captured_trace_events):
        payload = {
            "message": {
                "type": "transcript",
                "role": "user",
                "transcript": "Hello, my phone number is 415-555-0001",
                "transcriptType": "final",
                "call": {"id": "call_abc"},
            }
        }
        client.post("/webhook", json=payload, headers={"X-VAPI-Secret": VALID_SECRET})

        assert len(captured_trace_events) == 1
        _, event_type, fields = captured_trace_events[0]
        assert event_type == "transcript"
        assert fields["role"] == "user"
        assert "Hello" in fields["transcript"]

    def test_partial_transcript_is_not_traced(self, client, captured_trace_events):
        """Interim STT chunks fire every ~200ms; tracing them all would
        10x our Langfuse spend with no signal."""
        payload = {
            "message": {
                "type": "transcript",
                "role": "user",
                "transcript": "Hello, my pho",
                "transcriptType": "partial",
                "call": {"id": "call_abc"},
            }
        }
        client.post("/webhook", json=payload, headers={"X-VAPI-Secret": VALID_SECRET})
        assert captured_trace_events == []

    def test_model_output_is_traced(self, client, captured_trace_events):
        payload = {
            "message": {
                "type": "model-output",
                "output": "Am I speaking with Jane Doe?",
                "call": {"id": "call_abc"},
            }
        }
        client.post("/webhook", json=payload, headers={"X-VAPI-Secret": VALID_SECRET})
        assert len(captured_trace_events) == 1
        _, event_type, fields = captured_trace_events[0]
        assert event_type == "model-output"
        assert fields["output"] == "Am I speaking with Jane Doe?"

    def test_end_of_call_is_both_traced_and_pipelined(
        self, client, captured_trace_events, captured_pipeline_calls
    ):
        client.post(
            "/webhook",
            json=_end_of_call_payload(),
            headers={"X-VAPI-Secret": VALID_SECRET},
        )
        assert len(captured_trace_events) == 1
        assert len(captured_pipeline_calls) == 1

    def test_rejects_malformed_payload(self, client, captured_pipeline_calls):
        response = client.post(
            "/webhook",
            json={"not": "a vapi payload"},
            headers={"X-VAPI-Secret": VALID_SECRET},
        )
        assert response.status_code == 422
        assert captured_pipeline_calls == []
