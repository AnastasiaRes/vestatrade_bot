from __future__ import annotations

from app.models import IntentResult


def test_final_response_boundary_removes_markdown_not_rendered_by_widget(orchestrator) -> None:
    session = orchestrator.sessions.get("plain-widget")
    raw = "## Ответ\n**Подтверждено:** `25 мм`. [Карточка](https://example.test/item)"
    orchestrator._append_history(session, "Покажи ответ", raw)

    response = orchestrator._response(
        "plain-widget",
        raw,
        [],
        False,
        IntentResult(intent_type="attribute_request", category="pipes"),
        session,
        ["ResponseComposerAgent"],
    )

    assert "**" not in response.answer
    assert "`" not in response.answer
    assert "##" not in response.answer
    assert "Карточка: https://example.test/item" in response.answer
    assert session.history[-1]["content"] == response.answer


def test_all_unavailable_results_start_with_an_explicit_warning(orchestrator) -> None:
    orchestrator.handle_chat(
        "all-unavailable",
        "Наружная канализационная труба 110 мм",
    )
    response = orchestrator.handle_chat("all-unavailable", "1000 мм")

    assert response.products
    assert all("нет в наличии" in item.stock_status.lower() for item in response.products)
    assert response.answer.startswith("Важно: у всех найденных позиций")
    assert "доступный аналог" in response.answer
