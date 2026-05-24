import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx

from api.models.webhook import VAPIEvent
from api.services import analysis
from api.services.analysis import (
    analyze_transcript,
    extract_airtable_id,
    extract_transcript,
    run_post_call_pipeline,
    should_alert,
)


def _event(**overrides) -> VAPIEvent:
    base = {
        "type": "end-of-call-report",
        "call": {
            "id": "call_abc",
            "durationSeconds": 180,
            "messages": [],
        },
        "transcript": "agent: hi\ncaller: hi back",
    }
    base.update(overrides)
    return VAPIEvent.model_validate(base)


def _analysis_json(**overrides) -> str:
    defaults = {
        "summary": "Caller confirmed identity and was told claim is approved.",
        "sentiment": "positive",
        "sentiment_arc": [
            {"turn": 1, "speaker": "agent", "sentiment": "neutral"},
            {"turn": 2, "speaker": "caller", "sentiment": "positive"},
        ],
        "intent": "claim_status",
        "authenticated": True,
        "escalated": False,
        "topics_mentioned": ["claim_status"],
        "caller_name": "Jane Doe",
    }
    defaults.update(overrides)
    return json.dumps(defaults)


def _fake_openai(analysis_json: str, qa_json: str) -> MagicMock:
    """Returns analysis JSON on first call, QA JSON on second call."""
    responses = [
        MagicMock(choices=[MagicMock(message=MagicMock(content=analysis_json))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content=qa_json))]),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=responses)
    return client


class TestExtractTranscript:
    def test_uses_top_level_transcript_when_present(self):
        event = _event(transcript="explicit transcript here")
        assert extract_transcript(event) == "explicit transcript here"

    def test_falls_back_to_messages(self):
        event = _event(
            transcript=None,
            call={
                "id": "c",
                "messages": [
                    {"role": "assistant", "content": "Hi, this is Claire."},
                    {"role": "user", "content": "Hi, my number is 415..."},
                    {"role": "tool", "name": "lookup_caller", "content": "{}"},
                ],
            },
        )
        assert extract_transcript(event) == (
            "agent: Hi, this is Claire.\n"
            "caller: Hi, my number is 415..."
        )

    def test_returns_empty_when_no_signal(self):
        event = _event(transcript=None, call={"id": "c", "messages": []})
        assert extract_transcript(event) == ""


class TestExtractAirtableId:
    def test_returns_id_from_successful_lookup(self):
        event = _event(
            call={
                "id": "c",
                "messages": [
                    {
                        "role": "tool",
                        "name": "lookup_caller",
                        "content": '{"found": true, "airtable_record_id": "recJane"}',
                    }
                ],
            }
        )
        assert extract_airtable_id(event) == "recJane"

    def test_uses_most_recent_lookup_when_called_twice(self):
        event = _event(
            call={
                "id": "c",
                "messages": [
                    {
                        "role": "tool",
                        "name": "lookup_caller",
                        "content": '{"found": false}',
                    },
                    {
                        "role": "tool",
                        "name": "lookup_caller",
                        "content": '{"found": true, "airtable_record_id": "recJane"}',
                    },
                ],
            }
        )
        assert extract_airtable_id(event) == "recJane"

    def test_returns_none_when_lookup_not_found(self):
        event = _event(
            call={
                "id": "c",
                "messages": [
                    {
                        "role": "tool",
                        "name": "lookup_caller",
                        "content": '{"found": false}',
                    }
                ],
            }
        )
        assert extract_airtable_id(event) is None

    def test_returns_none_when_no_lookup_called(self):
        event = _event(call={"id": "c", "messages": []})
        assert extract_airtable_id(event) is None

    def test_ignores_other_tool_calls(self):
        event = _event(
            call={
                "id": "c",
                "messages": [
                    {
                        "role": "tool",
                        "name": "some_other_tool",
                        "content": '{"airtable_record_id": "recIgnored"}',
                    }
                ],
            }
        )
        assert extract_airtable_id(event) is None

    def test_handles_dict_content(self):
        event = _event(
            call={
                "id": "c",
                "messages": [
                    {
                        "role": "tool",
                        "name": "lookup_caller",
                        "content": {"found": True, "airtable_record_id": "recJane"},
                    }
                ],
            }
        )
        assert extract_airtable_id(event) == "recJane"

    def test_skips_malformed_json_content(self):
        event = _event(
            call={
                "id": "c",
                "messages": [
                    {"role": "tool", "name": "lookup_caller", "content": "not json"},
                    {
                        "role": "tool",
                        "name": "lookup_caller",
                        "content": '{"found": true, "airtable_record_id": "recJane"}',
                    },
                ],
            }
        )
        assert extract_airtable_id(event) == "recJane"


class TestShouldAlert:
    @pytest.mark.parametrize(
        "qa_score,sentiment,expected",
        [
            (0.95, "positive", False),
            (0.5, "positive", True),
            (0.6, "positive", False),
            (0.59, "positive", True),
            (0.95, "negative", True),
            (None, "negative", True),
            (None, "positive", False),
            (None, "neutral", False),
            (0.9, "neutral", False),
        ],
    )
    def test_alert_logic(self, qa_score, sentiment, expected):
        assert should_alert(qa_score, sentiment) is expected


class TestAnalyzeTranscript:
    async def test_calls_openai_with_analysis_prompt(self):
        client = _fake_openai(_analysis_json(), "{}")
        result = await analyze_transcript("agent: hi\ncaller: hi", client)

        assert result["summary"]
        assert result["sentiment"] == "positive"

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["temperature"] == 0
        assert kwargs["response_format"] == {"type": "json_object"}
        user_msg = next(m for m in kwargs["messages"] if m["role"] == "user")
        assert "TRANSCRIPT:" in user_msg["content"]


class TestRunPostCallPipeline:
    @pytest.fixture
    def patched_openai(self, monkeypatch):
        """Replace AsyncOpenAI() in the analysis module with our fake."""
        def _factory():
            return _fake_openai(
                _analysis_json(sentiment="positive"),
                json.dumps({"results": [
                    {"id": "greeting", "applicable": True, "score": 1},
                    {"id": "authentication", "applicable": True, "score": 1},
                ]}),
            )
        monkeypatch.setattr(analysis, "AsyncOpenAI", _factory)

    @respx.mock
    async def test_happy_path_writes_interaction_no_alert(self, patched_openai):
        airtable_post = respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recNewLog"})
        resend_post = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "email_id"}
        )

        event = _event(
            call={
                "id": "call_abc",
                "durationSeconds": 180,
                "messages": [
                    {
                        "role": "tool",
                        "name": "lookup_caller",
                        "content": '{"found": true, "airtable_record_id": "recJane"}',
                    }
                ],
            }
        )
        await run_post_call_pipeline(event)

        assert airtable_post.called
        sent_fields = json.loads(airtable_post.calls.last.request.read())["fields"]
        assert sent_fields["caller"] == ["recJane"]
        assert sent_fields["sentiment"] == "positive"
        assert sent_fields["qa_score"] == 1.0
        assert resend_post.called is False

    @respx.mock
    async def test_negative_sentiment_triggers_alert(self, monkeypatch):
        monkeypatch.setattr(
            analysis,
            "AsyncOpenAI",
            lambda: _fake_openai(
                _analysis_json(sentiment="negative", summary="Caller frustrated."),
                json.dumps({"results": [
                    {"id": "greeting", "applicable": True, "score": 1},
                ]}),
            ),
        )
        respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX"})
        resend = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "email_id"}
        )

        await run_post_call_pipeline(_event())

        assert resend.called
        body = json.loads(resend.calls.last.request.read())
        assert "MEDIUM" in body["subject"] or "HIGH" in body["subject"]
        assert "Jane Doe" in body["subject"]
        assert "Caller frustrated." in body["html"]

    @respx.mock
    async def test_low_qa_score_triggers_alert(self, monkeypatch):
        monkeypatch.setattr(
            analysis,
            "AsyncOpenAI",
            lambda: _fake_openai(
                _analysis_json(sentiment="positive"),
                json.dumps({"results": [
                    {"id": "greeting", "applicable": True, "score": 0},
                    {"id": "authentication", "applicable": True, "score": 0},
                ]}),
            ),
        )
        respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX"})
        resend = respx.post("https://api.resend.com/emails").respond(
            200, json={"id": "email_id"}
        )

        await run_post_call_pipeline(_event())

        assert resend.called

    @respx.mock
    async def test_unauthenticated_call_writes_without_caller_link(self, patched_openai):
        airtable_post = respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX"})

        event = _event(call={"id": "c", "durationSeconds": 30, "messages": []})
        await run_post_call_pipeline(event)

        sent = json.loads(airtable_post.calls.last.request.read())["fields"]
        assert "caller" not in sent

    async def test_empty_transcript_skips_pipeline(self, patched_openai):
        event = _event(transcript=None, call={"id": "c", "messages": []})
        # Should not call OpenAI or Airtable — no assertion needed; just
        # verifies no exception is raised when there's nothing to do.
        await run_post_call_pipeline(event)

    @respx.mock
    async def test_airtable_failure_is_swallowed(self, patched_openai, caplog):
        respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(500, json={"error": "boom"})

        await run_post_call_pipeline(_event())

        assert "failed to write interaction" in caplog.text

    @respx.mock
    async def test_llm_failure_aborts_pipeline(self, monkeypatch, caplog):
        def _broken_factory():
            client = MagicMock()
            client.chat.completions.create = AsyncMock(side_effect=RuntimeError("api down"))
            return client

        monkeypatch.setattr(analysis, "AsyncOpenAI", _broken_factory)
        airtable_post = respx.post(
            "https://api.airtable.com/v0/appTest1234567890/interactions"
        ).respond(200, json={"id": "recX"})

        await run_post_call_pipeline(_event())

        assert "LLM analysis failed" in caplog.text
        assert airtable_post.called is False
