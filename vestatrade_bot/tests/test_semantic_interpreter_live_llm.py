from __future__ import annotations

import os
import json

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter
from app.config import get_settings
from app.models import PendingQuestionState, SessionState
from app.openrouter_client import OpenRouterClient


pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_LLM_TESTS") != "1"
        or not os.getenv("OPENROUTER_API_KEY"),
        reason="requires RUN_LIVE_LLM_TESTS=1 and OPENROUTER_API_KEY",
    ),
]


def _interpreter() -> SemanticInterpreter:
    base = get_settings()
    semantic_model = os.getenv(
        "SEMANTIC_LIVE_MODEL",
        os.getenv("OPENROUTER_MODEL_STRONG", base.openrouter_model_strong),
    )
    settings = base.model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": os.environ["OPENROUTER_API_KEY"],
            "openrouter_model": semantic_model,
            "openrouter_model_strong": semantic_model,
            "llm_max_retries": 1,
        }
    )
    return SemanticInterpreter(OpenRouterClient(settings), model=semantic_model)


@pytest.mark.parametrize(
    ("message", "target_category", "required_secondary_acts"),
    [
        (
            "У меня дома радиаторы, но сейчас подобрать надо именно "
            "циркуляционный насос. Что есть по цене?",
            "pumps",
            {"check_price"},
        ),
        (
            "Ищу штуку, которая соединит 50-ю трубу с 32-й, покажите варианты.",
            "fittings",
            set(),
        ),
        (
            "Нужен котёл без вайфая, желательно настенный, и сразу проверьте наличие.",
            "boilers",
            {"check_stock"},
        ),
    ],
)
def test_live_semantics_survive_product_request_paraphrases(
    message: str,
    target_category: str,
    required_secondary_acts: set[str],
) -> None:
    result = _interpreter().interpret(message, SessionState(session_id="live"))

    assert result.status == "accepted", result.rejection_reason
    understanding = result.understanding
    assert understanding is not None
    targets = [item for item in understanding.products if item.role.value == "target"]
    diagnostic = json.dumps(
        understanding.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert any(item.category.value == target_category for item in targets), diagnostic
    acts = {act.value for act in understanding.acts}
    assert acts.intersection({"find", "select"}), diagnostic
    assert required_secondary_acts.issubset(acts), diagnostic


def test_live_semantics_keep_unknown_parameter_unknown() -> None:
    message = (
        "Подберите насос 25-60 из того, что я знаю. Монтажную длину назвать не могу."
    )
    result = _interpreter().interpret(message, SessionState(session_id="live"))

    assert result.status == "accepted", result.rejection_reason
    understanding = result.understanding
    assert understanding is not None
    assert "select" in {act.value for act in understanding.acts}
    assert any(
        fact.status.value in {"unknown", "refused", "deferred"}
        and fact.value is None
        and (
            "длин" in fact.name.casefold()
            or fact.name.casefold() in {"mounting_length", "mounting_length_mm"}
        )
        for fact in understanding.constraints
    ), json.dumps(
        understanding.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )


def test_live_semantics_understand_short_correction_against_pending_question() -> None:
    state = SessionState(session_id="live", category="pipes")
    state.pending_question_state = PendingQuestionState(
        question_id="pipes.diameter",
        text="Какой нужен диаметр трубы?",
        expected_slots=["diameter_mm"],
        category="pipes",
        intent_type="product_search",
    )
    result = _interpreter().interpret("Не 20, а 25 миллиметров.", state)

    assert result.status == "accepted", result.rejection_reason
    understanding = result.understanding
    assert understanding is not None
    assert understanding.operation.value in {"correct", "refine"}
    assert understanding.answers_pending_question is True
    assert any(
        str(fact.value) in {"25", "25.0"}
        and fact.status.value == "known"
        for fact in understanding.constraints
    )
