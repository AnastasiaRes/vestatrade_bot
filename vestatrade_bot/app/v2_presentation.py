"""Deterministic Russian presentation for checked V2 contracts.

The V2 state, source snapshots and evidence gates deliberately use stable
canonical identifiers such as ``glass_fiber`` and ``diameter_mm``.  Those
identifiers are useful inside typed contracts but are not customer-facing
language.  This module is the one read-only seam that turns checked facts
into widget text.  It must never infer a fact, translate an arbitrary source
excerpt, or alter a value used by a gate.
"""

from __future__ import annotations

from math import isfinite


_FACT_LABELS = {
    "angle_deg": "угол",
    "area_m2": "площадь",
    "availability": "наличие",
    "boiler_type": "тип котла",
    "brand": "бренд",
    "center_distance_mm": "межосевое расстояние",
    "circuits": "количество контуров",
    "combustion_chamber": "камера сгорания",
    "connection_diameter_mm": "диаметр присоединения",
    "connection_pattern": "тип резьбового соединения",
    "connection_size": "размер присоединения",
    "control_thread": "посадочная резьба термоголовки",
    "declared_heated_area_m2": "заявленная отапливаемая площадь",
    "diameter_mm": "диаметр",
    "duty_point_flow_l_h": "расход в рабочей точке",
    "duty_point_head_m": "напор в рабочей точке",
    "filter_method": "тип фильтрации",
    "handle_type": "тип ручки",
    "heat_output_w": "тепловая мощность",
    "integrated_circulation_pump": "встроенный циркуляционный насос",
    "installation_length_mm": "монтажная длина",
    "length_mm": "длина",
    "material": "материал",
    "max_flow_l_h": "максимальный расход",
    "max_head_m": "максимальный напор",
    "maximum_operating_temperature_c": "максимальная рабочая температура",
    "micron_rating_um": "тонкость фильтрации",
    "mounting_length_mm": "монтажная длина",
    "operating_pressure_bar": "рабочее давление",
    "operating_temperature_c": "рабочая температура",
    "pipe_service": "назначение трубы",
    "power_kw": "мощность",
    "price": "цена",
    "radiator_heating_pressure_bar": "давление при радиаторном отоплении",
    "reinforcement": "тип армирования",
    "secondary_diameter_mm": "второй диаметр",
    "sewer_scope": "назначение канализации",
    "sewer_system_family": "система канализации",
    "stock_status": "наличие",
    "thermostatic_head_thread": "резьба под термоголовку",
}

_VALUE_LABELS = {
    "aluminium": "алюминий",
    "angle": "угловое исполнение",
    "bimetal": "биметалл",
    "black": "чёрный",
    "borehole": "скважинный",
    "carbon": "угольная очистка",
    "closed": "закрытая",
    "cold_water heating": "холодная вода и отопление",
    "cold_water hot_water": "холодная и горячая вода",
    "cold_water hot_water heating": "холодная и горячая вода, отопление",
    "cold_water": "холодная вода",
    "chrome": "хром",
    "circulation": "циркуляционный",
    "dhw_circulation": "циркуляционный для ГВС",
    "drainage": "дренажный",
    "electric": "электрический",
    "ethylene_glycol": "этиленгликоль",
    "external": "наружная",
    "female_female": "внутренняя/внутренняя резьба",
    "female_male": "внутренняя/наружная резьба",
    "false": "нет",
    "gas": "газовый",
    "glass_fiber": "стекловолокно",
    "gray": "серый",
    "grey": "серый",
    "glycol_unspecified": "гликолевый теплоноситель без уточнения типа",
    "heating": "отопление",
    "hot_water heating": "горячая вода и отопление",
    "hot_water": "горячая вода",
    "iron_removal": "обезжелезивание",
    "in_stock": "в наличии",
    "internal": "внутренняя",
    "male_female": "наружная/внутренняя резьба",
    "male_male": "наружная/наружная резьба",
    "magnetic": "магнитная очистка",
    "mechanical": "механическая очистка",
    "no": "нет",
    "open": "открытая",
    "out_of_stock": "нет в наличии",
    "pex": "сшитый полиэтилен",
    "pex_a": "PEX-a",
    "polypropylene": "полипропилен",
    "pp_alux": "полипропилен, армированный алюминием",
    "pp_fiber": "полипропилен, армированный волокном",
    "ppr": "полипропилен PPR",
    "preorder": "доступно под заказ",
    "propylene_glycol": "пропиленгликоль",
    "pump_station": "насосная станция",
    "reverse_osmosis": "обратный осмос",
    "sewage": "канализационный",
    "softening": "умягчение",
    "steel": "сталь",
    "straight": "прямое исполнение",
    "true": "да",
    "unreinforced": "без армирования",
    "unknown": "наличие не подтверждено",
    "water": "вода",
    "white": "белый",
    "yes": "да",
    # These are product-system designations rather than natural-language
    # values.  Preserve the exact, readable designation instead of inventing
    # a translation.
    "ht": "HT",
    "htb": "HTB",
    "htem": "HTEM",
    "kg": "KG",
}

_PUBLIC_UNITS = {
    "%": "%",
    "bar": "бар",
    "c": "°C",
    "°c": "°C",
    "cm": "см",
    "deg": "°",
    "inch": "дюйм",
    "kw": "кВт",
    "l/h": "л/ч",
    "l/min": "л/мин",
    "m": "м",
    "m2": "м²",
    "m3/h": "м³/ч",
    "m³/h": "м³/ч",
    "mm": "мм",
    "mpa": "МПа",
    "rub": "₽",
    "rur": "₽",
    "um": "мкм",
    "w": "Вт",
}

_UNIT_ALIASES = {
    "mm": ("mm", "мм"),
    "cm": ("cm", "см"),
    "m": ("m", "м", "метр", "метра", "метров"),
    "kw": ("kw", "квт"),
    "w": ("w", "вт"),
    "l/h": ("l/h", "л/ч"),
    "l/min": ("l/min", "л/мин"),
    "m3/h": ("m3/h", "м3/ч", "м³/ч"),
    "m³/h": ("m³/h", "м3/ч", "м³/ч"),
    "bar": ("bar", "бар"),
    "mpa": ("mpa", "мпа"),
    "c": ("c", "°c", "°с"),
    "°c": ("°c", "°с"),
    "inch": ("inch", "дюйм", "дюйма", "дюймов", '"', "″"),
    "um": ("um", "мкм"),
    "rub": ("rub", "руб", "руб.", "₽"),
    "rur": ("rur", "руб", "руб.", "₽"),
    "m2": ("m2", "м2", "m²", "м²"),
}

_IMPLIED_UNITS = {
    "area_m2": "м²",
    "declared_heated_area_m2": "м²",
    "diameter_mm": "мм",
    "duty_point_flow_l_h": "л/ч",
    "duty_point_head_m": "м",
    "installation_length_mm": "мм",
    "max_flow_l_h": "л/ч",
    "max_head_m": "м",
    "maximum_operating_temperature_c": "°C",
    "mounting_length_mm": "мм",
    "operating_pressure_bar": "бар",
    "operating_temperature_c": "°C",
    "power_kw": "кВт",
    "radiator_heating_pressure_bar": "бар",
    "secondary_diameter_mm": "мм",
}


def public_fact_label(predicate: str | None, *, fallback: str = "характеристика товара") -> str:
    """Return Russian text for a canonical predicate without leaking its key."""

    return _FACT_LABELS.get(str(predicate or ""), fallback)


def public_value(value: object, predicate: str | None = None) -> str:
    """Present a checked scalar while retaining its exact semantic value."""

    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        if isfinite(value) and value.is_integer():
            return str(int(value))
        return format(value, "g").replace(".", ",")

    raw = str(value).strip()
    if predicate == "circuits":
        return {"1": "один контур", "2": "два контура"}.get(raw, raw)
    return _VALUE_LABELS.get(raw.casefold(), raw)


def public_unit_suffix(value: object, unit: str | None) -> str:
    """Render a public unit once, without duplicating a unit already in value."""

    if not unit:
        return ""
    rendered_value = str(value).strip().casefold().rstrip(" .,:;")
    normalized_unit = str(unit).strip().casefold()
    public_unit = _PUBLIC_UNITS.get(normalized_unit, str(unit))
    aliases = tuple(
        dict.fromkeys(
            (
                normalized_unit,
                public_unit.casefold(),
                *_UNIT_ALIASES.get(normalized_unit, ()),
            )
        )
    )
    if any(rendered_value.endswith(alias) for alias in aliases):
        return ""
    return f" {public_unit}"


def format_public_fact_value(
    value: object,
    *,
    predicate: str | None = None,
    unit: str | None = None,
    imply_unit: bool = False,
) -> str:
    """Return value plus a Russian unit for a checked V2 fact.

    ``imply_unit`` is only for canonical contracts whose predicate itself fixes
    the unit; it never changes the underlying fact or source evidence.
    """

    if (
        predicate == "duty_point_flow_l_h"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value) >= 1000
    ):
        return f"{public_value(float(value) / 1000)} м³/ч"

    rendered = public_value(value, predicate)
    effective_unit = unit or (_IMPLIED_UNITS.get(predicate or "") if imply_unit else None)
    return f"{rendered}{public_unit_suffix(rendered, effective_unit)}"


def public_missing_predicate_label(scoped_predicate: str) -> str:
    """Present ``SKU:predicate`` missing-data records without English keys."""

    predicate = scoped_predicate.split(":", 1)[-1]
    return public_fact_label(predicate, fallback="характеристика соединения")
