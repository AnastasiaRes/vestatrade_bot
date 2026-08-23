"""Regressions for the repeated-question loop (found by live dialogs 2026-08-05).

A novice asking for a boiler received the identical question «Понял, подбираем
котёл примерно на 100 м². Газовый или электрический?» four turns in a row and
never reached a product. The escalation prefixes also leaked internal jargon
(«можно отложить ветку») and were glued in front of a question that already
started with a slot confirmation.

Safety contract kept intact: a missing key parameter still never yields a random
product, so these tests assert wording and progression, not products.
"""

from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator


def _boiler_ladder(bot: ChatOrchestrator, sid: str) -> list[str]:
    """Drive a dialogue where only boiler_type stays unknown."""
    answers = []
    for message in [
        "нужен котел",
        "дом 100 квадратов",
        "нужно и отопление и горячая вода",
        # «Что посоветуете?» теперь получает сравнение газового и
        # электрического по существу, а не анкету, поэтому лестница эскалации
        # начинается на ход позже — набор реплик продлён, чтобы проверять то же
        # самое: выход на предложение менеджера и остановку на нём.
        "хорошо, что посоветуете?",
        "а всё-таки?",
        "ну хоть что-нибудь",
        "ну правда, никак?",
    ]:
        answers.append(bot.handle_chat(sid, message).answer)
    return answers


def test_same_question_is_never_sent_verbatim_twice(orchestrator) -> None:
    answers = _boiler_ladder(orchestrator, "ladder-verbatim")

    # Each step of the ladder must differ from the previous one: the reported
    # symptom was four byte-identical replies in a row.
    for earlier, later in zip(answers[1:4], answers[2:5]):
        assert earlier != later, answers

    # Once the ladder ends on the manager offer it may hold there — that is a
    # clear action for the customer, not a question asked over and over.
    assert answers[-1] == answers[-2]
    assert "передай менеджеру" in answers[-1].lower()


def test_escalation_does_not_leak_internal_jargon(orchestrator) -> None:
    answers = _boiler_ladder(orchestrator, "ladder-jargon")

    joined = " ".join(answers).lower()
    assert "отложить ветку" not in joined
    assert "без догадок" not in joined


def test_escalation_ends_by_offering_a_manager_not_another_question(orchestrator) -> None:
    answers = _boiler_ladder(orchestrator, "ladder-handoff")

    final = answers[-1].lower()
    assert "передай менеджеру" in final
    # The dead end must stop restating the question instead of asking forever.
    assert "газовый или электрический" not in final


def test_third_attempt_explains_the_blocker_before_offering_handoff(orchestrator) -> None:
    answers = _boiler_ladder(orchestrator, "ladder-explain")

    third = answers[4].lower()
    assert "случайный товар" in third
    assert "паспорте" in third or "шильдике" in third


def test_progress_resets_the_escalation(orchestrator) -> None:
    # Answering the question must clear the escalated tone rather than keep it.
    orchestrator.handle_chat("ladder-progress", "нужен котел")
    orchestrator.handle_chat("ladder-progress", "дом 100 квадратов")
    escalated = orchestrator.handle_chat("ladder-progress", "не знаю").answer
    resolved = orchestrator.handle_chat("ladder-progress", "электрический").answer

    assert escalated != resolved
    assert "уточню ещё раз" not in resolved.lower()


def test_missing_parameter_still_never_returns_a_random_product(orchestrator) -> None:
    # The safety contract the project deliberately enforces.
    for answer_turn in _boiler_ladder(orchestrator, "ladder-safety"):
        assert answer_turn
    session_products = orchestrator.handle_chat("ladder-safety", "ну хоть что-нибудь").products
    assert session_products == []
