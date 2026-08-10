"""The LLM turn classifier must rescue «объясни/научи» turns — and nothing else.

Live dialogues (reports/context_consultant_unknown_params_2026-08-05.md) showed
that while a clarifying question is pending, the rule layer treats every reply as
an answer or as noise, so «а как подобрать диаметр?» got the same question back.
The classifier decides what the turn *is*; these tests pin both the rescue and
its guard rails.
"""

from __future__ import annotations

from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.agents.turn_classifier import TurnClassifierAgent
from app.config import get_settings
from app.openrouter_client import LLMResult


class _ClassifierLLM:
    """Answers the classifier with a fixed kind; every other agent gets nothing."""

    last_json_output_accepted = True
    last_fallback_reason = None

    def __init__(self, kind: str, term: str | None = None, reply: str | None = None) -> None:
        self.kind = kind
        self.term = term
        self.reply = reply
        self.classify_calls = 0

    def complete_json(self, agent, messages, fallback):
        if agent != "TurnClassifierAgent":
            return fallback, False
        self.classify_calls += 1
        self.last_json_output_accepted = True
        return {"kind": self.kind, "term": self.term}, True

    def complete(self, *args, **kwargs) -> LLMResult:
        if self.reply is None:
            return LLMResult(content=None, llm_used=False, fallback_reason="not needed")
        return LLMResult(content=self.reply, llm_used=True)


def _bot(llm: Any, products: list | None = None) -> ChatOrchestrator:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )
    return ChatOrchestrator(settings=settings, products=products or [], llm_client=llm)


# ----------------------------------------------------------------- the rescue


def test_teaching_question_is_answered_instead_of_repeating_the_funnel() -> None:
    llm = _ClassifierLLM("teaching", reply="Диаметр подбирают по расходу и длине трассы.")
    bot = _bot(llm)

    bot.handle_chat("teach", "нужны трубы для отопления")
    response = bot.handle_chat("teach", "а как подобрать диаметр?")

    assert llm.classify_calls >= 1
    assert "TurnClassifierAgent" in response.debug["agents_used"]
    # The reported symptom: the pending question came back instead of an answer.
    assert "для какого участка отопления" not in response.answer.lower()
    assert "не буду подставлять случайный товар" not in response.answer.lower()


def test_terminology_question_is_answered_while_a_question_is_pending() -> None:
    # «что такое X?» is already rescued by an earlier deterministic branch, so
    # the classifier is not needed here. What matters is the outcome: the
    # customer gets an explanation instead of the pending question back.
    llm = _ClassifierLLM("terminology", term="монтажная длина", reply="Это расстояние между гайками.")
    bot = _bot(llm)

    bot.handle_chat("term", "нужен циркуляционный насос")
    response = bot.handle_chat("term", "а что такое монтажная длина?")

    answer = response.answer.lower()
    assert response.answer.strip()
    assert "это замена старого или новый подбор" not in answer
    assert "не буду подставлять случайный товар" not in answer


# ------------------------------------------------------------------ guardrails


def test_answer_to_pending_is_never_hijacked() -> None:
    # The prompt biases towards answer_to_pending; routing must respect it so a
    # real answer keeps closing the question.
    llm = _ClassifierLLM("answer_to_pending")
    bot = _bot(llm)

    bot.handle_chat("ans", "нужен котел")
    response = bot.handle_chat("ans", "электрический, 100 квадратов")

    assert "TurnClassifierAgent" not in response.debug["agents_used"]


def test_product_request_is_not_treated_as_a_question() -> None:
    llm = _ClassifierLLM("product_request")
    bot = _bot(llm)

    response = bot.handle_chat("prod", "нужен котел на 100 квадратов")

    assert "TurnClassifierAgent" not in response.debug["agents_used"]


def test_statement_without_question_shape_never_reaches_the_classifier() -> None:
    # Cheap pre-filter: even a misclassifying model cannot swallow a plain
    # product statement, because it is not shaped like a question.
    llm = _ClassifierLLM("teaching")
    bot = _bot(llm)

    bot.handle_chat("shape", "нужны трубы для отопления")
    bot.handle_chat("shape", "полипропилен 25 мм")

    assert llm.classify_calls == 0


def test_classifier_result_outside_the_closed_list_is_ignored() -> None:
    llm = _ClassifierLLM("МОЖНО_ВСЁ")
    agent = TurnClassifierAgent(llm)

    turn = agent.classify(message="а как подобрать диаметр?", pending_question=None)

    assert turn.kind == "unknown"
    assert turn.wants_explanation is False
    assert "closed list" in (turn.rejection_reason or "")


def test_term_not_present_in_the_message_is_dropped() -> None:
    # The term must be the customer's word, not something the model recalled.
    llm = _ClassifierLLM("terminology", term="кавитация")
    agent = TurnClassifierAgent(llm)

    turn = agent.classify(message="что такое монтажная длина?", pending_question=None)

    assert turn.kind == "terminology"
    assert turn.term is None


def test_pipeline_is_untouched_when_the_llm_is_unavailable() -> None:
    class _DeadLLM:
        last_json_output_accepted = False
        last_fallback_reason = "provider down"

        def complete_json(self, agent, messages, fallback):
            return fallback, False

        def complete(self, *args, **kwargs) -> LLMResult:
            return LLMResult(content=None, llm_used=False, fallback_reason="provider down")

    bot = _bot(_DeadLLM())
    bot.handle_chat("dead", "нужны трубы для отопления")
    response = bot.handle_chat("dead", "а как подобрать диаметр?")

    # Falls back to the existing deterministic behaviour rather than erroring.
    assert response.answer.strip()
    assert "TurnClassifierAgent" not in response.debug["agents_used"]
