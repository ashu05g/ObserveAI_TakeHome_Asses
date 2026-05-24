import json

import httpx
import pytest
import respx

from api.services.email_alert import send_alert


class TestSendAlert:
    @respx.mock
    async def test_posts_to_resend(self):
        route = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "email_xyz"}
        )

        await send_alert(
            caller_name="Jane Doe",
            sentiment="negative",
            qa_score=0.4,
            summary="Caller upset about delays.",
        )

        assert route.called
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer re_test_key"
        body = json.loads(request.read())
        assert body["from"] == "alerts@test.local"
        assert body["to"] == ["ops@test.local"]
        assert "HIGH" in body["subject"]
        assert "Jane Doe" in body["subject"]
        assert "Caller upset about delays." in body["html"]
        assert "40%" in body["html"]

    @respx.mock
    async def test_medium_severity_for_moderate_score(self):
        route = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "x"}
        )

        await send_alert(
            caller_name="Tom",
            sentiment="negative",
            qa_score=0.55,
            summary="okay-ish",
        )

        body = json.loads(route.calls.last.request.read())
        assert "MEDIUM" in body["subject"]

    @respx.mock
    async def test_no_qa_score_shows_na(self):
        route = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "x"}
        )

        await send_alert(
            caller_name="Tom",
            sentiment="negative",
            qa_score=None,
            summary="short call",
        )

        body = json.loads(route.calls.last.request.read())
        assert "N/A" in body["html"]
        assert "MEDIUM" in body["subject"]

    @respx.mock
    async def test_unauthenticated_caller_shows_placeholder(self):
        route = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "x"}
        )

        await send_alert(
            caller_name=None,
            sentiment="negative",
            qa_score=0.4,
            summary="couldn't verify",
        )

        body = json.loads(route.calls.last.request.read())
        assert "Unauthenticated" in body["subject"]
        assert "Unauthenticated" in body["html"]

    @respx.mock
    async def test_includes_trace_link_when_provided(self):
        route = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "x"}
        )

        await send_alert(
            caller_name="Tom",
            sentiment="negative",
            qa_score=0.4,
            summary="x",
            trace_url="https://us.cloud.langfuse.com/trace/abc",
        )

        body = json.loads(route.calls.last.request.read())
        assert "https://us.cloud.langfuse.com/trace/abc" in body["html"]

    @respx.mock
    async def test_omits_trace_link_when_none(self):
        route = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "x"}
        )

        await send_alert(
            caller_name="Tom",
            sentiment="negative",
            qa_score=0.4,
            summary="x",
            trace_url=None,
        )

        body = json.loads(route.calls.last.request.read())
        assert "langfuse" not in body["html"].lower()

    @respx.mock
    async def test_raises_on_resend_4xx(self):
        respx.post("https://api.resend.com/emails").respond(
            403, json={"error": "forbidden"}
        )

        with pytest.raises(httpx.HTTPStatusError):
            await send_alert(
                caller_name="x", sentiment="negative", qa_score=0.4, summary="x"
            )
