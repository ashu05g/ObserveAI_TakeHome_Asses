import json
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.lookup import router as lookup_router

VALID_SECRET = "test-secret-do-not-use-in-prod"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(lookup_router)
    return TestClient(app)


def _caller_response(found_count: int = 1):
    if found_count == 0:
        return {"records": []}
    return {
        "records": [
            {
                "id": "recJaneDoe",
                "fields": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "phone": "+14155550001",
                    "date_of_birth": "1985-04-15",
                    "claim_id": "CLM-2024-0001",
                    "claim_status": "approved",
                    "claim_type": "auto",
                    "claim_date": "2024-08-12",
                    "incident_date": "2024-08-10",
                    "claim_amount": 8500.00,
                    "approved_amount": 8000.00,
                    "adjuster_name": "Robert Chen",
                    "estimated_payout_date": "2024-08-25",
                    "claim_description": "Rear-end collision on I-280.",
                },
            }
        ]
    }


def _tool_payload(phone: str, *, function_name: str = "lookup_caller", arguments=None, call_id: str = "call_abc"):
    if arguments is None:
        arguments = json.dumps({"phone": phone})
    return {
        "message": {
            "type": "tool-calls",
            "toolCalls": [
                {
                    "id": call_id,
                    "function": {
                        "name": function_name,
                        "arguments": arguments,
                    },
                }
            ],
        }
    }


def _post(client, body):
    return client.post(
        "/lookup",
        json=body,
        headers={"X-VAPI-Secret": VALID_SECRET},
    )


class TestLookupAuth:
    def test_rejects_missing_secret(self, client):
        response = client.post("/lookup", json=_tool_payload("+14155550001"))
        assert response.status_code == 401

    def test_rejects_wrong_secret(self, client):
        response = client.post(
            "/lookup",
            json=_tool_payload("+14155550001"),
            headers={"X-VAPI-Secret": "nope"},
        )
        assert response.status_code == 401


def _result(response, index: int = 0) -> dict:
    """Parse the JSON-encoded result string for the given toolCall index."""
    return json.loads(response.json()["results"][index]["result"])


class TestLookupSuccess:
    @respx.mock
    def test_hit_returns_caller_in_results(self, client):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response())

        response = _post(client, _tool_payload("(415) 555-0001"))

        assert response.status_code == 200
        body = response.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["toolCallId"] == "call_abc"
        assert _result(response) == {
            "found": True,
            "first_name": "Jane",
            "last_name": "Doe",
            "date_of_birth": "1985-04-15",
            "claim_id": "CLM-2024-0001",
            "claim_status": "approved",
            "claim_type": "auto",
            "claim_date": "2024-08-12",
            "incident_date": "2024-08-10",
            "claim_amount": 8500.00,
            "approved_amount": 8000.00,
            "adjuster_name": "Robert Chen",
            "estimated_payout_date": "2024-08-25",
            "documents_needed": None,
            "claim_description": "Rear-end collision on I-280.",
            "airtable_record_id": "recJaneDoe",
        }

    @respx.mock
    def test_miss_returns_found_false(self, client):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response(found_count=0))

        response = _post(client, _tool_payload("+19999999999"))

        assert response.status_code == 200
        assert _result(response) == {"found": False}

    @respx.mock
    def test_accepts_dict_arguments(self, client):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response())

        response = _post(
            client,
            _tool_payload("+14155550001", arguments={"phone": "+14155550001"}),
        )
        assert response.status_code == 200
        assert _result(response)["found"] is True

    def test_result_is_json_encoded_string(self, client):
        """VAPI's LLM tool-message content is a string; passing an object
        has been observed to drop the payload. Always stringify."""
        with respx.mock:
            respx.get(
                "https://api.airtable.com/v0/appTest1234567890/callers"
            ).respond(200, json=_caller_response())
            response = _post(client, _tool_payload("+14155550001"))

        result_field = response.json()["results"][0]["result"]
        assert isinstance(result_field, str)
        # And it round-trips back to the expected structure
        parsed = json.loads(result_field)
        assert parsed["found"] is True

    @respx.mock
    def test_normalizes_phone_before_lookup(self, client):
        route = respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response())

        _post(client, _tool_payload("415.555.0001"))

        query = parse_qs(urlparse(str(route.calls.last.request.url)).query)
        assert query["filterByFormula"] == ["{phone}='+14155550001'"]


class TestLookupErrors:
    def test_invalid_phone_returns_200_with_error_payload(self, client):
        # Per VAPI: non-2xx aborts the call. We surface the error inside
        # the result so the LLM can read it and ask the caller to retry.
        response = _post(client, _tool_payload("not-a-phone"))

        assert response.status_code == 200
        result = _result(response)
        assert result["found"] is False
        assert "expected a 10-digit" in result["error"]

    def test_missing_phone_argument_returns_error(self, client):
        response = _post(client, _tool_payload(phone="", arguments={}))
        assert response.status_code == 200
        result = _result(response)
        assert result["found"] is False
        assert "missing phone" in result["error"]

    def test_unknown_function_name_returns_error(self, client):
        response = _post(
            client,
            _tool_payload("+14155550001", function_name="do_something_else"),
        )
        assert response.status_code == 200
        assert "unsupported function" in _result(response)["error"]

    @respx.mock
    def test_airtable_failure_returns_200_with_error(self, client):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(500, json={"error": "boom"})

        response = _post(client, _tool_payload("+14155550001"))
        assert response.status_code == 200
        result = _result(response)
        assert result["found"] is False
        assert "unavailable" in result["error"]

    def test_malformed_arguments_string_treated_as_missing(self, client):
        response = _post(
            client,
            _tool_payload("ignored", arguments="not valid json"),
        )
        assert response.status_code == 200
        assert "missing phone" in _result(response)["error"]


class TestMultipleToolCallsInOnePayload:
    @respx.mock
    def test_resolves_each_call_independently(self, client):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).mock(side_effect=[
            respx.MockResponse(200, json=_caller_response()),
            respx.MockResponse(200, json=_caller_response(found_count=0)),
        ])

        body = {
            "message": {
                "type": "tool-calls",
                "toolCalls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "lookup_caller",
                            "arguments": json.dumps({"phone": "+14155550001"}),
                        },
                    },
                    {
                        "id": "call_2",
                        "function": {
                            "name": "lookup_caller",
                            "arguments": json.dumps({"phone": "+19999999999"}),
                        },
                    },
                ],
            }
        }
        response = _post(client, body)

        results = response.json()["results"]
        assert len(results) == 2
        assert results[0]["toolCallId"] == "call_1"
        assert json.loads(results[0]["result"])["found"] is True
        assert results[1]["toolCallId"] == "call_2"
        assert json.loads(results[1]["result"])["found"] is False
