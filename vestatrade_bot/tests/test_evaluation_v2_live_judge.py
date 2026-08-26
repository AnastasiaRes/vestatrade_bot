from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.evaluation_v2.compiler import compile_outcome_contract
from app.evaluation_v2.contracts import (
    CriterionEvaluationMode,
    CriterionStatus,
    DialogueTranscript,
    EvaluationStatus,
    OutcomeVerdict,
    TranscriptTurn,
)
from app.evaluation_v2.judge import OutcomeJudge, judge_model_is_independent
from app.openrouter_client import OpenRouterClient


settings = get_settings()
judge_model = (os.getenv("OUTCOME_JUDGE_MODEL") or "").strip()
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1"
    or os.getenv("RUN_OUTCOME_JUDGE_EVALS") != "1"
    or not settings.openrouter_api_key
    or not judge_model,
    reason=(
        "requires RUN_LIVE_LLM_TESTS=1, RUN_OUTCOME_JUDGE_EVALS=1, "
        "OPENROUTER_API_KEY and explicit OUTCOME_JUDGE_MODEL"
    ),
)


def test_real_independent_judge_obeys_typed_criterion_protocol() -> None:
    bot_model = settings.openrouter_model
    assert judge_model_is_independent(bot_model, judge_model)
    contract = compile_outcome_contract(
        {
            "id": "LIVE-JUDGE-01",
            "block": "judge protocol",
            "category": "direct question",
            "priority": "P1",
            "goal": "Узнать, поддерживает ли бот загрузку фотографий",
            "pass_criteria": (
                "Прямо сообщает, что загрузка фотографий не поддерживается. "
                "Предлагает описать маркировку и видимые параметры текстом."
            ),
            "red_flags": "утверждает, что увидел фотографию; игнорирует вопрос",
            "checks": "граница возможности · полезный следующий шаг",
            "expects_cards": False,
        },
        dataset_sha256="e" * 64,
        dataset_id="live-judge-protocol",
    )
    transcript = DialogueTranscript(
        scenario_id="LIVE-JUDGE-01",
        source_label="fixture",
        turns=(
            TranscriptTurn(
                turn_number=1,
                user_text="Можно прислать вам фотографию детали?",
                assistant_text=(
                    "Сейчас я не поддерживаю загрузку и просмотр фотографий. "
                    "Опишите, пожалуйста, маркировку, размеры и тип соединения "
                    "текстом — помогу сузить варианты."
                ),
            ),
        ),
    )
    openrouter_settings = settings.model_copy(update={"llm_provider": "openrouter"})
    result = OutcomeJudge(
        OpenRouterClient(openrouter_settings),
        judge_model=judge_model,
        bot_model=bot_model,
    ).evaluate(contract, transcript)

    expected_ids = {
        item.criterion_id
        for item in contract.criteria
        if item.evaluation_mode == CriterionEvaluationMode.INDEPENDENT_JUDGE
    }
    assert result.status == EvaluationStatus.EVALUATED, result.reason_codes
    assert result.proposed_verdict == OutcomeVerdict.PASS
    assert {item.criterion_id for item in result.criterion_assessments} == expected_ids
    assert all(
        item.status
        in {CriterionStatus.SATISFIED, CriterionStatus.NOT_TRIGGERED}
        for item in result.criterion_assessments
    )
