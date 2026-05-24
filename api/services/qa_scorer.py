"""LLM-scored QA rubric for completed calls.

Each rubric item is judged independently as applicable or N/A. The final
score is the weighted average over *applicable* items only, with the
weights of skipped items redistributed across the rest. This keeps a clean
call from being dragged down by criteria that never came up (e.g., a non-
emergency call shouldn't lose points for `emergency_handling`).

Returns `score = None` when every criterion is N/A so callers can
distinguish "no signal" from "scored 0".
"""

import json
from typing import Any

from openai import AsyncOpenAI

from api.services.langfuse_client import observed
from api.utils.prompts import load_prompt

QA_RUBRIC: list[dict[str, Any]] = [
    {
        "id": "greeting",
        "criterion": "Agent greeted the caller warmly and professionally before asking for verification.",
        "weight": 0.10,
    },
    {
        "id": "authentication",
        "criterion": "Agent asked for the phone number and looked up the caller before disclosing any claim information.",
        "weight": 0.20,
    },
    {
        "id": "identity_confirm",
        "criterion": "After lookup, agent confirmed identity by asking 'Am I speaking with [first name] [last name]?' before proceeding.",
        "weight": 0.15,
    },
    {
        "id": "claim_status_accurate",
        "criterion": "Agent communicated the claim status (approved / pending / requires documentation) clearly and matching the looked-up record.",
        "weight": 0.20,
    },
    {
        "id": "documentation_instructions",
        "criterion": "When status was requires_documentation, agent provided clear submission instructions (portal URL or email).",
        "weight": 0.10,
    },
    {
        "id": "escalation_handling",
        "criterion": "When caller requested a human, agent confirmed a callback or transfer would be arranged.",
        "weight": 0.10,
    },
    {
        "id": "emergency_handling",
        "criterion": "When caller mentioned an emergency, agent instructed them to hang up and call 911.",
        "weight": 0.05,
    },
    {
        "id": "scope_management",
        "criterion": "Agent redirected off-topic questions back to insurance support scope.",
        "weight": 0.05,
    },
    {
        "id": "tone",
        "criterion": "Agent maintained a calm, supportive, and professional tone throughout.",
        "weight": 0.05,
    },
]

_RUBRIC_TEXT = json.dumps(
    [{"id": r["id"], "criterion": r["criterion"]} for r in QA_RUBRIC],
    indent=2,
)
_RUBRIC_WEIGHTS = {r["id"]: r["weight"] for r in QA_RUBRIC}


async def score_call(transcript: str, client: AsyncOpenAI | None = None) -> dict:
    client = client or AsyncOpenAI()
    with observed("qa_scoring") as span:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": load_prompt("qa_rubric_prompt.txt")},
                {"role": "user", "content": f"RUBRIC:\n{_RUBRIC_TEXT}\n\nTRANSCRIPT:\n{transcript}"},
            ],
        )
        content = response.choices[0].message.content
        breakdown = json.loads(content).get("results", [])
        score = weighted_score(breakdown)
        span.update(
            input={"transcript": transcript},
            output={"score": score, "breakdown": breakdown},
            model="gpt-4o-mini",
        )
        return {"score": score, "breakdown": breakdown}


def weighted_score(breakdown: list[dict]) -> float | None:
    """Weighted average over applicable, recognized rubric items only."""
    applicable = [
        item for item in breakdown
        if item.get("applicable", True) and item.get("id") in _RUBRIC_WEIGHTS
    ]
    if not applicable:
        return None

    weight_sum = sum(_RUBRIC_WEIGHTS[item["id"]] for item in applicable)
    if weight_sum == 0:
        return None

    weighted = sum(
        _clamp(item.get("score", 0)) * _RUBRIC_WEIGHTS[item["id"]]
        for item in applicable
    )
    return round(weighted / weight_sum, 3)


def _clamp(score: Any) -> float:
    try:
        return max(0.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        return 0.0
