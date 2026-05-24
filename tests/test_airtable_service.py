from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from api.models.interaction import InteractionLog
from api.services.airtable import get_caller_by_phone, write_interaction


def _caller_response(**field_overrides):
    fields = {
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+14155550001",
        "claim_id": "CLM-2024-0001",
        "claim_status": "approved",
        "claim_type": "auto",
        "claim_date": "2024-08-12",
    }
    fields.update(field_overrides)
    return {"records": [{"id": "recJaneDoe", "fields": fields}]}


def _interaction_log(**overrides):
    defaults = {
        "caller_airtable_id": "recJaneDoe",
        "timestamp": datetime(2026, 5, 24, 12, 0, 0),
        "authenticated": True,
        "call_duration_seconds": 180,
        "transcript": "agent: hi\ncaller: hi",
        "summary": "Caller confirmed claim.",
        "sentiment": "positive",
        "sentiment_arc": "[]",
        "detected_intent": "claim_status",
        "qa_score": 0.92,
        "qa_breakdown": "[]",
        "topics_mentioned": ["claim_status"],
        "escalated": False,
        "langfuse_trace_url": "https://us.cloud.langfuse.com/trace/abc",
    }
    return InteractionLog(**{**defaults, **overrides})


class TestGetCallerByPhone:
    @respx.mock
    async def test_returns_caller_on_hit(self):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response())

        caller = await get_caller_by_phone("+14155550001")

        assert caller is not None
        assert caller.airtable_id == "recJaneDoe"
        assert caller.full_name == "Jane Doe"
        assert caller.claim_status == "approved"

    @respx.mock
    async def test_returns_none_when_no_match(self):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json={"records": []})

        assert await get_caller_by_phone("+19999999999") is None

    @respx.mock
    async def test_sends_filter_formula_and_max_records(self):
        route = respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response())

        await get_caller_by_phone("+14155550001")

        request = route.calls.last.request
        query = parse_qs(urlparse(str(request.url)).query)
        assert query["filterByFormula"] == ["{phone}='+14155550001'"]
        assert query["maxRecords"] == ["1"]

    @respx.mock
    async def test_sends_bearer_token(self):
        route = respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json={"records": []})

        await get_caller_by_phone("+14155550001")

        assert route.calls.last.request.headers["authorization"] == "Bearer pat_test_key"

    @respx.mock
    async def test_raises_on_auth_failure(self):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(401, json={"error": "AUTHENTICATION_REQUIRED"})

        with pytest.raises(httpx.HTTPStatusError):
            await get_caller_by_phone("+14155550001")


class TestWriteInteraction:
    @respx.mock
    async def test_returns_new_record_id(self):
        respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recNewInteraction", "fields": {}})

        record_id = await write_interaction(_interaction_log())

        assert record_id == "recNewInteraction"

    @respx.mock
    async def test_sends_all_required_fields(self):
        route = respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX", "fields": {}})

        await write_interaction(_interaction_log())

        body = route.calls.last.request.read().decode()
        import json
        sent = json.loads(body)["fields"]

        assert sent["timestamp"] == "2026-05-24T12:00:00"
        assert sent["authenticated"] is True
        assert sent["call_duration_seconds"] == 180
        assert sent["sentiment"] == "positive"
        assert sent["detected_intent"] == "claim_status"
        assert sent["qa_score"] == 0.92
        assert sent["caller"] == ["recJaneDoe"]
        assert sent["langfuse_trace_url"] == "https://us.cloud.langfuse.com/trace/abc"

    @respx.mock
    async def test_omits_qa_score_when_none(self):
        route = respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX", "fields": {}})

        await write_interaction(_interaction_log(qa_score=None))

        import json
        sent = json.loads(route.calls.last.request.read())["fields"]
        assert "qa_score" not in sent

    @respx.mock
    async def test_omits_caller_link_for_unauthenticated_call(self):
        route = respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX", "fields": {}})

        await write_interaction(
            _interaction_log(caller_airtable_id=None, authenticated=False)
        )

        import json
        sent = json.loads(route.calls.last.request.read())["fields"]
        assert "caller" not in sent
        assert sent["authenticated"] is False

    @respx.mock
    async def test_omits_trace_url_when_none(self):
        route = respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX", "fields": {}})

        await write_interaction(_interaction_log(langfuse_trace_url=None))

        import json
        sent = json.loads(route.calls.last.request.read())["fields"]
        assert "langfuse_trace_url" not in sent

    @respx.mock
    async def test_raises_on_422_invalid_fields(self):
        respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(422, json={"error": "INVALID_VALUE_FOR_COLUMN"})

        with pytest.raises(httpx.HTTPStatusError):
            await write_interaction(_interaction_log())
