"""Customer-facing presentation for an already checked V2 selection.

The answer renderer consumes only ``SelectionResult``.  It does not look up
products, recover attributes from prose, or alter the ordering accepted by the
selection outcome gate.
"""

from __future__ import annotations

from app.catalog_v2.contracts import (
    ProductKind,
    SelectionFactInput,
    SelectionResult,
    SelectionResultStatus,
)
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


def render_selection_no_match(result: SelectionResult) -> str | None:
    """Render a small family-specific explanation for a checked no-match.

    The V2 selection outcome gate already proves that there are no cards for
    the unchanged typed filters.  This function only makes that result easier
    to understand; it never inspects the catalogue or suggests a smaller
    radiator as suitable.
    """

    if result.status != SelectionResultStatus.NO_MATCH:
        raise ValueError("no-match renderer requires a checked no-match")
    if result.reason_code == "no_verified_cheaper_candidate":
        reference = (
            f" дешевле {result.price_reference_amount:g} ₽"
            if result.price_reference_amount is not None
            else ""
        )
        return (
            "Среди вариантов, которые сохраняют ваши технические условия, "
            f"в текущем каталоге нет подтверждённого варианта{reference}."
        )
    if result.product_kind == ProductKind.BOREHOLE_PUMP:
        below_point = next(
            (
                item
                for item in result.passport_flow_head_evidence
                if item.status == "below_required_head"
            ),
            None,
        )
        if below_point is not None:
            point = below_point.passport_point
            return "\n".join(
                (
                    "По точной точке Q/H в паспорте для насоса "
                    f"{below_point.sku}: при расходе "
                    f"{below_point.requested_flow_l_h:g} л/ч указан напор "
                    f"{point.head_m:g} м, а для вашего запроса нужно "
                    f"{below_point.required_head_m:g} м.",
                    "Не показываю эту модель даже как предварительно "
                    "подходящую.",
                )
            )
        missing_ratings = any(
            "catalogue_required_rating_missing" in reason_codes
            for reason_codes in result.excluded_candidate_reason_codes.values()
        )
        if missing_ratings:
            return "\n".join(
                (
                    "В каталоге нет скважинного насоса с одновременно "
                    "подтверждёнными максимальными напором и расходом под "
                    "ваши исходные данные.",
                    "Не показываю модель с неполными характеристиками как "
                    "предварительно подходящую.",
                )
            )
    if result.product_kind != ProductKind.RADIATOR:
        return None
    area = next(
        (
            fact
            for fact in result.applied_facts
            if (
                fact.name == "area_m2"
                and fact.status == "known"
                and isinstance(fact.value, (int, float))
                and not isinstance(fact.value, bool)
            )
        ),
        None,
    )
    if area is None:
        return None
    return "\n".join(
        (
            "В каталоге нет радиатора с заявленной площадью обогрева "
            f"от {_fact_value(area)}.",
            "Радиаторы с меньшей заявленной площадью не показываю "
            "как подходящие.",
        )
    )


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
    if result.controlled_relaxation_differences:
        lines = [
            "Точного варианта с запрошенной длиной и подтверждённым наличием "
            "не нашёл.",
            f"Показываю {_preliminary_count_phrase(count)} короче — диаметр и "
            "наружное применение сохранены.",
        ]
        seen: set[tuple[str, str, str]] = set()
        for difference in result.controlled_relaxation_differences:
            requested = format_public_fact_value(
                difference.requested_value,
                predicate=difference.fact_name,
                imply_unit=True,
            )
            actual = format_public_fact_value(
                difference.candidate_value,
                predicate=difference.fact_name,
                imply_unit=True,
            )
            key = (difference.fact_name, requested, actual)
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"Отличие: {_label(difference.fact_name)} {actual} вместо "
                f"запрошенных {requested}."
            )
        lines.append(
            "Это явно разрешённая замена по длине, а не точное совпадение."
        )
        return "\n".join(lines)
    if result.availability_analog:
        lines = [
            "Точного варианта с подтверждённым наличием в каталоге нет.",
            f"Показываю {_preliminary_count_phrase(count)} из наличия — "
            "это ближайший предварительный аналог по каталогу.",
        ]
        for difference in result.availability_analog_differences:
            requested = format_public_fact_value(
                difference.requested_value,
                predicate=difference.fact_name,
                imply_unit=True,
            )
            actual = format_public_fact_value(
                difference.candidate_value,
                predicate=difference.fact_name,
                imply_unit=True,
            )
            lines.append(
                f"Отличие: {_label(difference.fact_name)} {actual} вместо "
                f"запрошенных {requested}."
            )
        lines.append(
            "Это не подтверждённый тепловой расчёт: перед покупкой нужно "
            "сверить теплопотери и условия монтажа."
        )
        return "\n".join(lines)
    if not result.is_preliminary:
        all_out_of_stock = bool(result.cards) and all(
            card.stock_qty == 0
            or "нет в наличии" in card.stock_status.casefold()
            for card in result.cards
        )
        if all_out_of_stock:
            if count == 1:
                return (
                    "Подходящий вариант в каталоге найден, но сейчас его нет "
                    "в наличии. Карточка ниже."
                )
            return (
                f"Подобрал {count} подходящих {_variant_word(count)}, но сейчас "
                "все они отсутствуют в наличии. Карточки ниже."
            )
        if count == 1:
            return "Нашёл подходящий вариант. Карточка ниже."
        if "price_below_delivered_scope_reference" in result.ordering_reason_codes:
            return (
                f"Показываю {count} подходящих {_variant_word(count)} дешевле "
                "ранее показанных. Технические условия сохранены."
            )
        if "price_ordered_among_technically_presentable_candidates" in result.ordering_reason_codes:
            return (
                f"Подобрал {count} подходящих {_variant_word(count)}. "
                "Карточки ниже отсортированы по цене среди технически "
                "подходящих вариантов."
            )
        return (
            f"Подобрал {count} подходящих {_variant_word(count)}. "
            "Карточки ниже расположены по соответствию запросу."
        )

    stock_required = any(
        item.name == "stock_availability"
        and item.status == "known"
        and item.value is True
        for item in result.applied_facts
    )
    # Availability is a commercial delivery filter, not a product
    # characteristic.  Rendering its boolean value as «Характеристика: да»
    # would expose implementation detail instead of the buyer's request.
    known = [
        item
        for item in result.applied_facts
        if item.status == "known" and item.name != "stock_availability"
    ]
    confirmed = "; ".join(
        f"{_label(item.name).capitalize()}: {_fact_value(item)}"
        for item in known[:3]
    )
    lines = [
        f"Показываю {_preliminary_count_phrase(count)}"
        + (f" по подтверждённым данным: {confirmed}." if confirmed else ".")
    ]
    if stock_required:
        lines.append("Показываю только варианты с подтверждённым наличием.")
    if "price_below_delivered_scope_reference" in result.ordering_reason_codes:
        lines.append(
            "Эти варианты дешевле ранее показанных; технические условия "
            "сохранены."
        )
    elif "price_ordered_among_technically_presentable_candidates" in result.ordering_reason_codes:
        lines.append(
            "Карточки ниже отсортированы по цене среди технически "
            "подходящих вариантов."
        )
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
    if result.product_kind == ProductKind.BOREHOLE_PUMP:
        card_skus = {card.sku for card in result.cards}
        exact_points = tuple(
            item
            for item in result.passport_flow_head_evidence
            if item.status == "clears_required_head" and item.sku in card_skus
        )
        for exact_point in exact_points:
            point = exact_point.passport_point
            lines.append(
                "В паспорте есть точная точка Q/H для "
                f"{exact_point.sku}: при расходе "
                f"{exact_point.requested_flow_l_h:g} л/ч указан напор "
                f"{point.head_m:g} м — не ниже требуемых "
                f"{exact_point.required_head_m:g} м."
            )
        lines.append(
            "Подтверждённая точка относится только к указанному расходу. "
            "Это не гидравлический расчёт системы: перед покупкой нужно "
            "сверить условия монтажа и рабочую точку по кривой производителя."
            if exact_points
            else "Расчёт напора предварительный: карточки отсеивают только насосы с "
            "недостаточными заявленными максимумами. Перед покупкой рабочую "
            "точку по расходу и напору нужно сверить с кривой производителя."
        )
        return "\n".join(lines)
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
            "разделены по его указанному значению. Если этот параметр важен "
            "для выбора, напишите нужный вариант."
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
