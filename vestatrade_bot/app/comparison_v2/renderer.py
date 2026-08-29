"""Deterministic presentation of a checked ComparisonResult."""

from __future__ import annotations

from .contracts import ComparisonResult, ComparisonResultStatus


def _value(value: object, unit: str | None) -> str:
    return f"{value} {unit}".strip()


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

    lines = ["Сравниваю показанные варианты по подтверждённым карточкам:"]
    for dimension in result.dimensions:
        if not dimension.values:
            continue
        values = "; ".join(
            f"{names.get(item.sku, item.sku)} ({item.sku}) — {_value(item.value, item.unit)}"
            for item in dimension.values
        )
        lines.append(f"• {dimension.label}: {values}.")
        if dimension.missing_skus:
            lines.append("  Для части позиций значение в карточке не подтверждено.")
    if result.missing_data:
        lines.append("Не хватает подтверждённых данных по: " + ", ".join(result.missing_data) + ".")
    if result.recommendation is not None:
        lines.append(
            f"По критерию «самая низкая цена» дешевле {names.get(result.recommendation.sku, result.recommendation.sku)} ({result.recommendation.sku})."
        )
    elif result.deciding_question:
        lines.append(result.deciding_question)
    return "\n".join(lines)
