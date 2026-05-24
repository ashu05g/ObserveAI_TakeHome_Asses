import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.qa_scorer import QA_RUBRIC, score_call, weighted_score


def _fake_openai(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


def _full_perfect_breakdown():
    return [
        {"id": r["id"], "applicable": True, "score": 1, "explanation": "ok"}
        for r in QA_RUBRIC
    ]


class TestWeightedScore:
    def test_all_perfect_returns_one(self):
        assert weighted_score(_full_perfect_breakdown()) == 1.0

    def test_all_zero_returns_zero(self):
        breakdown = [
            {"id": r["id"], "applicable": True, "score": 0, "explanation": "no"}
            for r in QA_RUBRIC
        ]
        assert weighted_score(breakdown) == 0.0

    def test_all_na_returns_none(self):
        breakdown = [
            {"id": r["id"], "applicable": False, "score": 0, "explanation": "n/a"}
            for r in QA_RUBRIC
        ]
        assert weighted_score(breakdown) is None

    def test_na_items_are_excluded_and_weights_redistributed(self):
        # Make a 2-item breakdown: one applicable item scoring 1.0 (weight 0.10),
        # one N/A (weight 0.05 — emergency_handling). Expected: 1.0 — the
        # N/A weight is redistributed, not counted against the score.
        breakdown = [
            {"id": "greeting", "applicable": True, "score": 1, "explanation": "ok"},
            {"id": "emergency_handling", "applicable": False, "score": 0, "explanation": "n/a"},
        ]
        assert weighted_score(breakdown) == 1.0

    def test_partial_credit_weighted_correctly(self):
        # greeting (w=0.10) score=0.5, authentication (w=0.20) score=1.0
        # weighted = (0.5 * 0.10 + 1.0 * 0.20) / (0.10 + 0.20) = 0.25 / 0.30 = 0.833
        breakdown = [
            {"id": "greeting", "applicable": True, "score": 0.5, "explanation": ""},
            {"id": "authentication", "applicable": True, "score": 1.0, "explanation": ""},
        ]
        assert weighted_score(breakdown) == 0.833

    def test_default_applicable_is_true_when_missing(self):
        breakdown = [{"id": "greeting", "score": 1, "explanation": ""}]
        assert weighted_score(breakdown) == 1.0

    def test_unknown_rubric_id_is_ignored(self):
        breakdown = [
            {"id": "greeting", "applicable": True, "score": 1, "explanation": ""},
            {"id": "hallucinated_rubric", "applicable": True, "score": 0, "explanation": ""},
        ]
        assert weighted_score(breakdown) == 1.0

    def test_score_above_one_clamped(self):
        breakdown = [
            {"id": "greeting", "applicable": True, "score": 5, "explanation": ""},
        ]
        assert weighted_score(breakdown) == 1.0

    def test_negative_score_clamped_to_zero(self):
        breakdown = [
            {"id": "greeting", "applicable": True, "score": -1, "explanation": ""},
        ]
        assert weighted_score(breakdown) == 0.0

    def test_non_numeric_score_treated_as_zero(self):
        breakdown = [
            {"id": "greeting", "applicable": True, "score": "perfect", "explanation": ""},
        ]
        assert weighted_score(breakdown) == 0.0

    def test_empty_breakdown_returns_none(self):
        assert weighted_score([]) is None

    def test_only_unknown_ids_returns_none(self):
        breakdown = [{"id": "made_up", "applicable": True, "score": 1}]
        assert weighted_score(breakdown) is None


class TestScoreCall:
    async def test_calls_openai_with_rubric_and_transcript(self):
        client = _fake_openai(json.dumps({"results": _full_perfect_breakdown()}))

        await score_call("agent: hi\ncaller: hi", client=client)

        client.chat.completions.create.assert_awaited_once()
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["temperature"] == 0
        assert kwargs["response_format"] == {"type": "json_object"}

        user_msg = next(m for m in kwargs["messages"] if m["role"] == "user")
        assert "RUBRIC:" in user_msg["content"]
        assert "TRANSCRIPT:" in user_msg["content"]
        assert "agent: hi" in user_msg["content"]
        for rubric in QA_RUBRIC:
            assert rubric["id"] in user_msg["content"]

    async def test_returns_score_and_breakdown(self):
        breakdown = _full_perfect_breakdown()
        client = _fake_openai(json.dumps({"results": breakdown}))

        result = await score_call("transcript here", client=client)

        assert result["score"] == 1.0
        assert result["breakdown"] == breakdown

    async def test_handles_missing_results_key(self):
        client = _fake_openai(json.dumps({"unexpected": "shape"}))

        result = await score_call("transcript", client=client)

        assert result["score"] is None
        assert result["breakdown"] == []

    async def test_propagates_invalid_json(self):
        client = _fake_openai("definitely not json")

        with pytest.raises(json.JSONDecodeError):
            await score_call("transcript", client=client)
