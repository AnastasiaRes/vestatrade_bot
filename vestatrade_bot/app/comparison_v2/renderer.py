"""Deterministic presentation of a checked ComparisonResult."""

from __future__ import annotations

from app.v2_presentation import format_public_fact_value, public_fact_label

from .contracts import ComparisonResult, ComparisonResultStatus


def _value(
    value: object,
    unit: str | None,
    predicate: str | None = None,
    *,
    source_value: str | None = None,
) -> str:
    # The canonical snapshot normalizes brand values for matching.  Their
    # original card spelling is still evidence-bound in the source reference
    # and is what a customer should see (``Wilo``, not ``wilo``).
    presented = source_value if predicate == "brand" and source_value else value
    return format_public_fact_value(presented, predicate=predicate, unit=unit)


def _reference_list(result: ComparisonResult, names: dict[str, str]) -> list[str]:
    return [
        f"{index}. {names.get(sku, sku)} ({sku})"
        for index, sku in enumerate(result.compared_skus, start=1)
    ]


def _dimension_values(
    dimension,
    ordinal_by_sku: dict[str, int],
    source_values: dict[str, str | None],
) -> str:
    return "; ".join(
        f"{ordinal_by_sku.get(item.sku, item.sku)} — "
        f"{_value(item.value, item.unit, item.predicate, source_value=_source_value(item, source_values))}"
        for item in dimension.values
    )


def _source_value(item, source_values: dict[str, str | None]) -> str | None:
    return next(
        (
            source_values.get(source_id)
            for source_id in item.source_ref_ids
            if source_values.get(source_id)
        ),
        None,
    )


def render_comparison_result(result: ComparisonResult, *, names: dict[str, str]) -> str:
    if result.status == ComparisonResultStatus.NEED_CLARIFICATION:
        if "ordinal_outside_customer_visible_v2_scope" in result.reason_codes:
            return (
                "В текущей выдаче нет одной из названных позиций. "
                "Назовите две карточки из показанных — например, «первый и третий»."
            )
        return (
            "Для сравнения нужны минимум две реально показанные карточки. "
            "Покажите ещё один вариант или назовите второй товар."
        )
    if result.status == ComparisonResultStatus.NOT_COMPARABLE:
        if "comparison_mixed_product_kind_scope" in result.reason_codes:
            return "Показанные товары относятся к разным типам; общего технического сравнения для них нет. Уточните, какие две позиции сопоставить."
        if "comparison_explicit_predicate_insufficient_evidence" in result.reason_codes:
            labels = ", ".join(
                public_fact_label(item) for item in result.missing_data
            )
            return (
                "Не могу доказательно сравнить показанные товары по характеристике "
                f"«{labels}»: для одной или нескольких позиций нет подтверждённых данных."
            )
        return "По показанным карточкам нет подтверждённого различия для сравнения. Уточните характеристику, которая важна для выбора."
    if result.status == ComparisonResultStatus.SOURCE_CONFLICT:
        return (
            "По одной из сравниваемых характеристик карточка и привязанный паспорт "
            "дают несовместимые данные. Не буду выбирать значение наугад; "
            "уточните его у менеджера или производителя."
        )
    if result.status == ComparisonResultStatus.REJECTED:
        return "Не могу безопасно сравнить эти карточки: их подтверждённый состав или версия каталога уже не совпадают. Покажите варианты заново."

    ordinal_by_sku = {
        sku: index for index, sku in enumerate(result.compared_skus, start=1)
    }
    source_values = {
        item.source_ref_id: item.raw_value
        for item in result.sources
    }
    if result.recommendation is not None:
        winner = result.recommendation.sku
        price_dimension = next(
            (item for item in result.dimensions if item.predicate == "price"),
            None,
        )
        price = next(
            (item for item in (price_dimension.values if price_dimension else ()) if item.sku == winner),
            None,
        )
        if price is not None:
            return (
                f"Из показанных дешевле {names.get(winner, winner)} ({winner}) — "
                f"{_value(price.value, price.unit, price.predicate, source_value=_source_value(price, source_values))}."
            )

    heading = (
        "Сравнение названных моделей:"
        if "comparison_from_explicit_catalog_pair" in result.reason_codes
        else "Сравнение показанных вариантов:"
    )
    lines = [heading, *_reference_list(result, names)]
    for dimension in result.dimensions:
        if not dimension.values:
            continue
        lines.append(
            f"• {dimension.label.capitalize()}: "
            f"{_dimension_values(dimension, ordinal_by_sku, source_values)}."
        )
        if dimension.missing_skus:
            lines.append("  Для части позиций значение в карточке не подтверждено.")
    if result.missing_data:
        lines.append(
            "Не хватает подтверждённых данных о характеристиках: "
            + ", ".join(public_fact_label(item) for item in result.missing_data)
            + "."
        )
    if result.deciding_question:
        lines.append(result.deciding_question)
    return "\n".join(lines)
