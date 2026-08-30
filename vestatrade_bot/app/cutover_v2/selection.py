"""Customer-facing presentation for an already checked V2 selection.

The answer renderer consumes only ``SelectionResult``.  It does not look up
products, recover attributes from prose, or alter the ordering accepted by the
selection outcome gate.
"""

from __future__ import annotations

from app.catalog_v2.contracts import SelectionFactInput, SelectionResult
from app.models import ChatProductGroup
from app.v2_presentation import format_public_fact_value, public_fact_label


def _label(fact_name: str) -> str:
    return public_fact_label(fact_name)


def _fact_value(fact: SelectionFactInput) -> str:
    return format_public_fact_value(
        fact.value,
        predicate=fact.name,
        unit=fact.unit,
        imply_unit=True,
    )


def _variant_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "вариант"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return "варианта"
    return "вариантов"


def _preliminary_count_phrase(count: int) -> str:
    if count == 1:
        return "1 предварительный вариант"
    return f"{count} предварительных {_variant_word(count)}"


def preliminary_product_groups(result: SelectionResult) -> list[ChatProductGroup]:
    """Project source-checked grouping metadata onto the public response."""

    return [
        ChatProductGroup(
            label=f"{item.label.capitalize()}: {item.value}",
            product_skus=list(item.card_skus),
        )
        for item in result.presentation_groups
    ]


def render_selection_result(result: SelectionResult) -> str:
    """Render concise copy while the widget renders the checked cards.

    The result is intentionally small: names, price and availability already
    belong to the cards. Repeating them in prose was both hard to scan and
    made the exact-selection path look materially different from a
    preliminary one. The renderer therefore only communicates the confidence
    level and the one next decision; it never re-ranks or inspects products.
    """

    if result.status.value != "shown" or not result.cards:
        raise ValueError("selection renderer requires shown cards")
    count = len(result.cards)
    if not result.is_preliminary:
        if count == 1:
            return "Нашёл подходящий вариант. Карточка ниже."
        return (
            f"Подобрал {count} подходящих {_variant_word(count)}. "
            "Карточки ниже расположены по соответствию запросу."
        )

    known = [item for item in result.applied_facts if item.status == "known"]
    confirmed = "; ".join(
        f"{_label(item.name).capitalize()}: {_fact_value(item)}"
        for item in known[:3]
    )
    lines = [
        f"Показываю {_preliminary_count_phrase(count)}"
        + (f" по подтверждённым данным: {confirmed}." if confirmed else ".")
    ]
    if result.source_backed_conflicts:
        cards_by_sku = {card.sku: card for card in result.cards}
        for conflict in result.source_backed_conflicts:
            card = cards_by_sku.get(conflict.card_sku)
            product_name = f"«{card.name}»" if card is not None else conflict.card_sku
            customer_area = _fact_value(
                SelectionFactInput(
                    name=conflict.customer_fact_name,
                    value=conflict.customer_value,
                    unit=conflict.customer_unit,
                    status="known",
                    source="selection_result",
                    source_turn=1,
                )
            )
            coverage = _fact_value(
                SelectionFactInput(
                    name=conflict.card_fact_name,
                    value=conflict.card_value,
                    unit=conflict.card_unit,
                    status="known",
                    source="catalog_card",
                    source_turn=1,
                )
            )
            lines.append(
                f"Для {product_name} в карточке указана заявленная площадь "
                f"отопления {coverage}, а вы указали {customer_area}."
            )
        lines.append(
            "Мощность взята из вашего запроса, но это расхождение не "
            "подтверждает пригодность котла. Карточка ниже предварительная: "
            "для окончательного выбора нужен тепловой расчёт или проектная "
            "мощность."
        )
        return "\n".join(lines)
    area = next(
        (
            item
            for item in known
            if item.name == "area_m2" and item.value is not None
        ),
        None,
    )
    if area is not None:
        lines.append(
            "У этих моделей заявленная в карточке площадь отопления не меньше "
            f"{_fact_value(area)}. Это ориентир для предварительной выдачи, "
            "а не расчёт теплопотерь и не окончательная рекомендация."
        )
    if result.presentation_groups:
        grouped_fact = result.presentation_groups[0].label
        if grouped_fact == "количество контуров":
            lines.append(
                "Контурность пока не определена, поэтому варианты разделены на "
                "группы. Выберите: котёл только для отопления или также для "
                "горячей воды."
            )
            return "\n".join(lines)
        lines.append(
            f"Параметр «{grouped_fact}» пока не подтверждён, поэтому карточки "
            "разделены по его указанному значению. Перед покупкой выберите "
            "группу, соответствующую вашему соединению."
        )
    elif result.preliminary_fact_names:
        labels = ", ".join(f"«{_label(item)}»" for item in result.preliminary_fact_names[:2])
        lines.append(
            f"Для точного подтверждения пригодности ещё нужно уточнить {labels}. "
            "Карточки ниже — предварительные, не окончательная рекомендация."
        )
    else:
        lines.append(
            "Карточки ниже предварительные: перед покупкой проверьте отмеченные "
            "ограничения."
        )
    return "\n".join(lines)


def render_preliminary_selection_result(result: SelectionResult) -> str:
    """Backward-compatible narrow entry point for preliminary callers."""

    if not result.is_preliminary:
        raise ValueError("preliminary renderer requires shown preliminary cards")
    return render_selection_result(result)
