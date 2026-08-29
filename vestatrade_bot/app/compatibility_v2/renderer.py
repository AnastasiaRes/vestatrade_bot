"""Deterministic customer text for a checked CompatibilityResult."""

from __future__ import annotations

from .contracts import CompatibilityRelationKind, CompatibilityResult, CompatibilityResultStatus


_LABELS = {
    "control_thread": "посадочная резьба термоголовки",
    "connection_size": "размер резьбового соединения",
    "connection_pattern": "тип резьбового соединения",
    "diameter_mm": "номинальный диаметр",
    "sewer_scope": "назначение канализации",
    "sewer_system_family": "система канализации",
    "integrated_circulation_pump": "встроенный циркуляционный насос",
}
_VALUE_LABELS = {
    "female_female": "внутренняя–внутренняя",
    "female_male": "внутренняя–наружная",
    "male_female": "наружная–внутренняя",
    "male_male": "наружная–наружная",
    "internal": "внутренняя",
    "external": "наружная",
}


def _value(value: object, unit: str | None) -> str:
    rendered = _VALUE_LABELS.get(str(value), str(value))
    return f"{rendered} {unit}".strip() if unit else rendered


def _facts(result: CompatibilityResult) -> list[str]:
    lines: list[str] = []
    for predicate in result.interface_predicates:
        values = [item for item in result.facts if item.predicate == predicate]
        if len(values) != 2:
            continue
        lines.append(
            f"• {_LABELS.get(predicate, predicate)}: "
            f"{values[0].sku} — {_value(values[0].value, values[0].unit)}; "
            f"{values[1].sku} — {_value(values[1].value, values[1].unit)}."
        )
    return lines


def render_compatibility_result(result: CompatibilityResult) -> str:
    left = result.left.canonical_sku or "первый товар"
    right = result.right.canonical_sku or "второй товар"
    pair = f"{left} и {right}"
    if result.status == CompatibilityResultStatus.COMPATIBLE:
        lines = [f"{pair}: совместимость по подтверждённым интерфейсам есть.", *_facts(result)]
        if result.relation == CompatibilityRelationKind.SEWER_CONNECTION:
            lines.append("Это подтверждает только соединение по системе и номинальному диаметру; комплектацию уплотнениями нужно сверить отдельно.")
        return "\n".join(lines)
    if result.status == CompatibilityResultStatus.INCOMPATIBLE:
        lines = [f"{pair}: напрямую не совместимы по подтверждённым интерфейсам.", *_facts(result)]
        return "\n".join(lines)
    if result.status == CompatibilityResultStatus.SOURCE_CONFLICT:
        return (
            f"Для {pair} источники дают конфликтующие данные по интерфейсу. "
            "Не буду объявлять позиции совместимыми или несовместимыми до проверки точной карточки или паспорта."
        )
    if result.status == CompatibilityResultStatus.REJECTED:
        return (
            f"Не могу безопасно проверить совместимость {pair}: состав товаров или версия каталога уже не совпадают. "
            "Назовите позиции заново или покажите актуальные карточки."
        )
    if "pump_boiler_requires_hydraulic_calculation" in result.reason_codes:
        boiler_fact = next(
            (
                item
                for item in result.facts
                if item.predicate == "integrated_circulation_pump"
            ),
            None,
        )
        if "boiler_integrated_pump_confirmed" in result.reason_codes:
            return (
                f"В котле {boiler_fact.sku if boiler_fact else right} подтверждён "
                "встроенный циркуляционный насос. Поэтому внешний насос нельзя "
                "считать обязательной заменой или автоматически ставить вместо штатного. "
                "Уточните, для какого контура он нужен: основное радиаторное отопление, "
                "тёплый пол, бойлер косвенного нагрева, гидрострелка или длинная отдельная ветка."
            )
        if "boiler_integrated_pump_explicitly_absent" in result.reason_codes:
            return (
                f"В документации к котлу {boiler_fact.sku if boiler_fact else right} "
                "встроенный циркуляционный насос указан как отсутствующий. Внешний "
                "насос может потребоваться, но подобрать его только по модели котла нельзя: "
                "нужны назначение контура, расход, напор и схема подключения."
            )
        if "boiler_integrated_pump_not_confirmed" in result.reason_codes:
            return (
                f"По котлу в паре {pair} я не нашёл подтверждения, встроен ли "
                "циркуляционный насос. Это не означает, что его нет: без такого факта "
                "внешний насос не подбираю. Нужен паспорт именно этой модели либо "
                "уточнение артикула."
            )
        return (
            f"Прямую совместимость насоса и котла {pair} по карточкам не подтверждаю: "
            "для этого нужен расчёт расхода, напора, сопротивления контура и схема подключения."
        )
    if "compatibility_product_references_missing" in result.reason_codes:
        return (
            "Чтобы проверить совместимость, назовите две позиции — артикулами или "
            "точными названиями — либо сначала покажите карточки. По всему каталогу "
            "наугад искать пару не буду."
        )
    if "compatibility_second_product_missing" in result.reason_codes:
        return (
            f"Для {left} нужна вторая конкретная позиция: назовите её артикул или "
            "точное название. Тогда сверю интерфейсы обеих сторон."
        )
    if "compatibility_relation_not_supported" in result.reason_codes:
        return (
            f"Для {pair} пока нет безопасного правила проверки этого типа соединения. "
            "Нужны точные данные интерфейса обеих сторон; случайный переходник не назначаю."
        )
    missing = ", ".join(
        item.split(":", 1)[-1].replace("_", " ")
        for item in result.missing_predicates
    )
    return (
        f"Для {pair} недостаточно подтверждённых данных о соединении"
        + (f": {missing}." if missing else ".")
        + " Поэтому не буду утверждать, что они совместимы или несовместимы."
    )
