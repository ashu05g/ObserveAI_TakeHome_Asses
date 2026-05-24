from datetime import date, datetime

import pytest
from pydantic import ValidationError

from api.models.caller import CallerRecord
from api.models.interaction import InteractionLog
from api.models.webhook import VAPIWebhookPayload


class TestCallerRecord:
    def test_parses_airtable_response_shape(self):
        record = CallerRecord(
            airtable_id="rec123",
            first_name="Jane",
            last_name="Doe",
            phone="+14155550001",
            claim_id="CLM-2024-0001",
            claim_status="approved",
            claim_type="auto",
            claim_date="2024-08-12",
        )
        assert record.full_name == "Jane Doe"
        assert record.claim_date == date(2024, 8, 12)

    def test_ignores_unknown_fields(self):
        record = CallerRecord(
            airtable_id="rec123",
            first_name="Jane",
            last_name="Doe",
            phone="+14155550001",
            claim_id="CLM-2024-0001",
            claim_status="pending",
            claim_type="home",
            claim_date="2024-08-12",
            linked_interactions=["recABC"],
            record_id=42,
        )
        assert not hasattr(record, "linked_interactions")

    def test_rejects_invalid_claim_status(self):
        with pytest.raises(ValidationError):
            CallerRecord(
                airtable_id="rec123",
                first_name="Jane",
                last_name="Doe",
                phone="+14155550001",
                claim_id="CLM-2024-0001",
                claim_status="closed",
                claim_type="auto",
                claim_date="2024-08-12",
            )


class TestInteractionLog:
    def _base(self, **overrides):
        defaults = {
            "caller_airtable_id": "rec123",
            "timestamp": datetime(2026, 5, 24, 12, 0, 0),
            "authenticated": True,
            "call_duration_seconds": 180,
            "transcript": "agent: hi\ncaller: hi",
            "summary": "Caller confirmed claim status.",
            "sentiment": "positive",
            "sentiment_arc": "[]",
            "detected_intent": "claim_status",
            "qa_score": 0.95,
            "qa_breakdown": "[]",
            "escalated": False,
        }
        return InteractionLog(**{**defaults, **overrides})

    def test_qa_score_nullable_for_all_na_calls(self):
        log = self._base(qa_score=None)
        assert log.qa_score is None

    def test_qa_score_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            self._base(qa_score=1.5)

    def test_unauthenticated_caller_has_no_airtable_id(self):
        log = self._base(caller_airtable_id=None, authenticated=False)
        assert log.caller_airtable_id is None

    def test_topics_default_empty(self):
        log = self._base()
        assert log.topics_mentioned == []


class TestVAPIWebhookPayload:
    def test_parses_minimal_end_of_call_event(self):
        payload = VAPIWebhookPayload.model_validate(
            {
                "message": {
                    "type": "end-of-call-report",
                    "call": {"id": "call_abc"},
                }
            }
        )
        assert payload.message.type == "end-of-call-report"
        assert payload.message.call.id == "call_abc"
        assert payload.message.call.messages == []

    def test_parses_camelcase_aliases(self):
        payload = VAPIWebhookPayload.model_validate(
            {
                "message": {
                    "type": "end-of-call-report",
                    "call": {"id": "call_abc", "durationSeconds": 123.4},
                    "durationSeconds": 123.4,
                }
            }
        )
        assert payload.message.call.duration_seconds == 123.4
        assert payload.message.duration_seconds == 123.4

    def test_parses_tool_call_history(self):
        payload = VAPIWebhookPayload.model_validate(
            {
                "message": {
                    "type": "end-of-call-report",
                    "call": {
                        "id": "call_abc",
                        "messages": [
                            {"role": "assistant", "content": "Hi!"},
                            {
                                "role": "tool",
                                "name": "lookup_caller",
                                "content": '{"found": true, "airtable_record_id": "rec123"}',
                                "toolCallId": "call_xyz",
                            },
                        ],
                    },
                }
            }
        )
        msgs = payload.message.call.messages
        assert len(msgs) == 2
        assert msgs[1].name == "lookup_caller"
        assert msgs[1].tool_call_id == "call_xyz"

    def test_rejects_unknown_event_type(self):
        with pytest.raises(ValidationError):
            VAPIWebhookPayload.model_validate(
                {"message": {"type": "definitely-not-a-vapi-event", "call": {"id": "x"}}}
            )

    def test_ignores_extra_top_level_fields(self):
        payload = VAPIWebhookPayload.model_validate(
            {
                "message": {"type": "end-of-call-report", "call": {"id": "call_abc"}},
                "metadata": {"region": "us"},
                "timestamp": "2026-05-24T12:00:00Z",
            }
        )
        assert payload.message.call.id == "call_abc"
