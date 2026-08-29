"""Deterministic presentation of a checked ComparisonResult."""

from __future__ import annotations

from .contracts import ComparisonResult, ComparisonResultStatus


def _value(value: object, unit: str | None) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if unit == "RUB":
        return f"{value} ₽"
    return f"{value} {unit}".strip() if unit else str(value)


def _reference_list(result: ComparisonResult, names: dict[str, str]) -> list[str]:
    return [
        f"{index}. {names.get(sku, sku)} ({sku})"
        for index, sku in enumerate(result.compared_skus, start=1)
    ]


def _dimension_values(dimension, ordinal_by_sku: dict[str, int]) -> str:
    return "; ".join(
        f"{ordinal_by_sku.get(item.sku, item.sku)} — {_value(item.value, item.unit)}"
        for item in dimension.values
    )


def render_comparison_result(result: ComparisonResult, *, names: dict[str, str]) -> str:
    if result.status == ComparisonResultStatus.NEED_CLARIFICATION:
        return (
            "Для сравнения нужны минимум две реально показанные карточки. "
            "Покажите ещё один вариант или назовите второй товар."
        )
    if result.status == ComparisonResultStatus.NOT_COMPARABLE:
        if "comparison_mixed_product_kind_scope" in result.reason_codes:
            return "Показанные товары относятся к разным типам; общего технического сравнения для них нет. Уточните, какие две позиции сопоставить."
        return "По показанным карточкам нет подтверждённого различия для сравнения. Уточните характеристику, которая важна для выбора."
    if result.status == ComparisonResultStatus.REJECTED:
        return "Не могу безопасно сравнить эти карточки: их подтверждённый состав или версия каталога уже не совпадают. Покажите варианты заново."

    ordinal_by_sku = {
        sku: index for index, sku in enumerate(result.compared_skus, start=1)
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
                f"{_value(price.value, price.unit)}."
            )

    lines = ["Сравнение показанных вариантов:", *_reference_list(result, names)]
    for dimension in result.dimensions:
        if not dimension.values:
            continue
        lines.append(
            f"• {dimension.label.capitalize()}: "
            f"{_dimension_values(dimension, ordinal_by_sku)}."
        )
        if dimension.missing_skus:
            lines.append("  Для части позиций значение в карточке не подтверждено.")
    if result.missing_data:
        lines.append("Не хватает подтверждённых данных по: " + ", ".join(result.missing_data) + ".")
    if result.deciding_question:
        lines.append(result.deciding_question)
    return "\n".join(lines)
