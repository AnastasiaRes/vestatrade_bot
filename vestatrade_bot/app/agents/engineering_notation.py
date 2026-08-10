from __future__ import annotations

import re
from typing import Any

from .numeric_semantics import extract_piece_length_mm, extract_temperature_c
from .utils import normalize_text


# This module deliberately does not rewrite abbreviations globally.  The same
# token can mean different things in different product families (``H`` is pump
# head but commonly a dimensional label in mixer cards; ``CV`` is both a valve
# coefficient and a radiator series).  Callers first resolve the active
# category, then apply only that category's notation.

PIPE_LIKE_CATEGORIES = {"pipes", "sewer", "fittings"}
THREADED_CATEGORIES = {
    "pipes",
    "sewer",
    "fittings",
    "valves",
    "radiator_fittings",
    "pumps",
    "hydraulic_accumulators",
    "filters",
}
ELECTRICAL_CATEGORIES = {
    "pumps",
    "boilers",
    "water_heaters",
    "controls",
}


def category_hint(value: str) -> tuple[str | None, float]:
    """Return a safe category hint from an engineering code or full term.

    Only distinctive notations are allowed to start a new product context.
    Short ambiguous tokens (``H``, ``CV``, ``СО``) are intentionally absent.
    """

    text = normalize_text(value)
    if re.search(r"\b(?:картридж|корпус\s+фильтр|фильтр\w*\s+для\s+вод|водоочист)\w*\b", text):
        return "filters", 0.92
    if re.search(r"\b(?:10|20)\s*(?:sl|bb)\b", text):
        return "filters", 0.9
    if re.search(r"\bro\b[^.!?]{0,24}(?:мембран|фильтр|систем|\b4040\b)", text):
        return "filters", 0.88
    if re.search(r"\b(?:htem|htb|htea|htu|kgem|kgb)\b", text):
        return "sewer", 0.94
    if re.search(
        r"\b(?:ht|kg)\b[^.!?]{0,15}(?:dn\s*\d+|\d{2,3}|труб|отвод|тройник|муфт)",
        text,
    ):
        return "sewer", 0.86
    if re.search(r"\b(?:рб|р\s*\.?\s*б\.?)\s*-?\s*\d{1,4}\s*(?:л|литр)", text):
        return "hydraulic_accumulators", 0.93
    if re.search(r"\bкнс\b|канализационн\w*\s+насосн\w*\s+(?:станц|установ)", text):
        return "pumps", 0.94
    control_match = re.search(
        r"\b(?:комнатн\w*\s+термостат|термостат\w*\s+комнатн|"
        r"термостат\w*\s+для\s+тепл\w*\s+пол|терморегулятор|сервопривод|"
        r"контроллер\s+(?:отоплен|тепл\w*\s+пол)|привод\s+клапан)\w*\b",
        text,
    )
    if control_match and not _match_is_negated(text, control_match.start(), control_match.end()):
        return "controls", 0.88
    if re.search(r"\b(?:rtl|нсу|пч)\b", text):
        return "controls", 0.84
    return None, 0.0


def _match_is_negated(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 35) : start]
    after = text[end : end + 30]
    return bool(
        re.search(r"\b(?:без|кроме|не\s+(?:нужен|нужна|нужно|нужны))\s*$", before)
        or re.match(r"\s+не\s+(?:нужен|нужна|нужно|нужны|требуется)\b", after)
    )


def extract_engineering_notation(value: str, category: str) -> dict[str, Any]:
    """Extract category-gated engineering notation into canonical slots."""

    text = normalize_text(value)
    slots: dict[str, Any] = {}

    _extract_common_dimensions(text, category, slots)
    _extract_electrical(text, category, slots)

    if category in {"pipes", "fittings"}:
        _extract_pipe_and_fitting_notation(text, category, slots)
    if category == "sewer":
        _extract_sewer_notation(text, slots)
    if category == "pumps":
        _extract_pump_notation(text, slots)
    if category in {"boilers", "water_heaters", "hydraulic_accumulators"}:
        _extract_heating_and_water_notation(text, category, slots)
    if category == "radiators":
        _extract_radiator_notation(text, slots)
    if category in {"valves", "radiator_fittings"}:
        _extract_valve_notation(text, slots)
    if category == "filters":
        _extract_filter_notation(text, slots)
    if category == "controls":
        _extract_control_notation(text, slots)

    return slots


def extract_contextual_short_answer(
    value: str,
    category: str,
    pending_question: str | None,
    pending_slot_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Interpret a compact answer only against the question the bot asked.

    A bare ``24`` must not mean litres everywhere.  It is volume only while the
    bot is waiting for a tank/water-heater volume; the same principle is used
    for pump connection, temperature and pressure.
    """

    text = normalize_text(value)
    pending = normalize_text(pending_question)
    expected = set(pending_slot_keys or [])
    slots: dict[str, Any] = {}

    if category in {"pipes", "sewer"} and (
        "length_mm" in expected
        or "длина одной" in pending
        or "длина одного" in pending
        or "длина трубы" in pending
        or "длина отрезка" in pending
    ):
        piece_length_mm = extract_piece_length_mm(text, allow_bare=True)
        if piece_length_mm is not None:
            slots["length_mm"] = piece_length_mm
        return slots

    # Pressure is role-sensitive: the same bare ``1 бар`` means inlet
    # pressure after an inlet question and target pressure after an outlet
    # question.  Bind it to the structured pending slot before the generic
    # pump parser has a chance to treat every unqualified pressure as target.
    pressure_match = re.fullmatch(
        r"\s*(?:давлен\w*\s*)?(\d{1,3}(?:[,.]\d+)?)\s*"
        r"(?:бар(?:а|ов)?|bar|атм(?:осфер\w*)?)\s*",
        text,
    )
    if category == "pumps" and pressure_match:
        pressure = float(pressure_match.group(1).replace(",", "."))
        if (
            "inlet_pressure_bar" in expected
            or any(
                marker in pending
                for marker in ["на вход", "сейчас", "исходн", "имеетс"]
            )
        ):
            if 0 <= pressure <= 25:
                slots["inlet_pressure_bar"] = pressure
            return slots
        if (
            "required_pressure_bar" in expected
            or any(
                marker in pending
                for marker in ["после насос", "получить", "нужно", "требуем"]
            )
        ):
            if 0 < pressure <= 25:
                slots["required_pressure_bar"] = pressure
            return slots

    number_match = re.fullmatch(
        r"\s*(\d{1,4}(?:[,.]\d+)?)\s*"
        r"(?:м\b|метр(?:а|ов)?)?\s*",
        text,
    )
    if not number_match:
        return slots
    number = float(number_match.group(1).replace(",", "."))

    waits_for_volume = bool(
        {"volume_l", "tank_volume_l"}.intersection(expected)
        or any(marker in pending for marker in ["объем", "объём", "литр"])
    )
    if category in {"hydraulic_accumulators", "water_heaters"} and waits_for_volume:
        if 1 <= number <= 5000:
            slots["volume_l"] = int(number) if number.is_integer() else number
        return slots

    if category == "pumps":
        if (
            "water_level_depth_m" in expected
            or "explicit_water_level_depth_m" in expected
            or (
                "глубин" in pending
                and "от верха" in pending
                and "вод" in pending
            )
        ):
            if 0 < number <= 300:
                slots["explicit_water_level_depth_m"] = number
                slots["water_level_reference"] = "from_top"
            return slots
        if (
            "horizontal_run_m" in expected
            or "расстоян" in pending
            or "до дома" in pending
            or "до полива" in pending
        ):
            if 0 < number <= 5000:
                slots["horizontal_run_m"] = number
            return slots
        if "lift_height_m" in expected or any(
            marker in pending for marker in ["высот", "поднять воду", "верхней точки"]
        ):
            if 0 <= number <= 300:
                slots["lift_height_m"] = number
            return slots
        if "connection_size" in expected or "присоедин" in pending or "условн" in pending:
            if number in {15, 20, 25, 32, 40, 50, 65, 80, 100}:
                slots["connection_size"] = int(number)
            return slots
        if "mounting_length_mm" in expected or "монтажн" in pending:
            if number in {130, 180}:
                slots["mounting_length_mm"] = int(number)
            return slots
        if {"required_head_m", "head_m"}.intersection(expected) or "напор" in pending:
            if 0 < number <= 300:
                slots["required_head_m"] = number
                slots["required_head_calculated"] = False
            return slots

    if category in {"pipes", "valves", "radiator_fittings"}:
        if "operating_temperature_c" in expected or "температур" in pending:
            if -80 <= number <= 300:
                slots["operating_temperature_c"] = number
            return slots
        if "operating_pressure_bar" in expected or "давлен" in pending:
            if 0 < number <= 1000:
                slots["operating_pressure_bar"] = number
            return slots
    return slots


def _extract_common_dimensions(text: str, category: str, slots: dict[str, Any]) -> None:
    if category in PIPE_LIKE_CATEGORIES | {"valves", "radiator_fittings"}:
        dn = re.search(r"\b(?:dn|ду|дн)\s*-?\s*(\d{1,3})\b", text)
        if not dn:
            dn = re.search(
                r"(?:условн\w*\s+(?:диаметр|проход)|номинальн\w*\s+диаметр)"
                r"[^\d]{0,12}(\d{1,3})(?:\s*мм)?\b",
                text,
            )
        if dn:
            slots["nominal_diameter_dn"] = int(dn.group(1))
            slots["diameter_mm"] = int(dn.group(1))

        diameter = re.search(r"(?:ø|⌀|\bф)\s*-?\s*(\d{1,3})(?:\s*мм)?\b", text)
        if diameter:
            slots["diameter_mm"] = int(diameter.group(1))

        pn_matches = list(
            re.finditer(r"\b(?:pn|ру)\s*-?\s*(\d{1,3}(?:[,.]\d+)?)\b", text)
        )
        pn = next(
            (
                match
                for match in reversed(pn_matches)
                if not re.search(
                    r"(?:\bне\b|\bбез\b)\s*$",
                    text[max(0, match.start() - 16) : match.start()],
                )
            ),
            None,
        )
        if not pn:
            pn = re.search(
                r"(?:номинальн\w*|условн\w*)\s+давлен\w*[^\d]{0,12}"
                r"(\d{1,3}(?:[,.]\d+)?)\s*(?:бар|bar)?\b",
                text,
            )
        if pn:
            slots["pressure_class_bar"] = float(pn.group(1).replace(",", "."))

    if category in PIPE_LIKE_CATEGORIES:
        sdr = re.search(r"\bsdr\s*-?\s*(\d{1,2}(?:[,.]\d+)?)\b", text)
        if sdr:
            slots["sdr"] = float(sdr.group(1).replace(",", "."))

        dimensions = re.search(
            r"(?<!\d)(\d{2,3})\s*[xх×*]\s*(\d{1,2}(?:[,.]\d{1,2})?)"
            r"(?:\s*мм)?(?!\d)",
            text,
        )
        if dimensions:
            diameter = int(dimensions.group(1))
            wall = float(dimensions.group(2).replace(",", "."))
            # В трубной записи 20×2 второе число — толщина стенки.
            # Ограничение отсекает габариты вида 100×50.
            if 0 < wall <= 30 and wall < diameter:
                slots["diameter_mm"] = diameter
                slots["wall_thickness_mm"] = wall

    if category in {"pipes", "valves", "radiator_fittings"}:
        temperature_c = extract_temperature_c(text)
        if temperature_c is not None and -80 <= temperature_c <= 300:
            slots["operating_temperature_c"] = temperature_c

        pressure = re.search(
            r"(?:\b(?:p|давлен\w*)\s*(?:=|:)?\s*)"
            r"(\d{1,4}(?:[,.]\d+)?)\s*(?:бар|bar)?\b",
            text,
        )
        if not pressure:
            pressure = re.search(r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:бар|bar)\b", text)
        if pressure:
            slots["operating_pressure_bar"] = float(
                pressure.group(1).replace(",", ".")
            )

    if category in THREADED_CATEGORIES:
        metric_thread = re.search(
            r"(?<![a-zа-я])m\s*(\d{1,3})\s*[xх×]\s*(\d+(?:[,.]\d+)?)",
            text,
        )
        if metric_thread:
            pitch = metric_thread.group(2).replace(",", ".")
            slots["metric_thread"] = f"M{int(metric_thread.group(1))}x{pitch}"
        thread_standard = re.search(
            r"(?<![a-zа-я])(?P<standard>g|rp|rc|r)\s*"
            r"(?P<size>1\s+1/4|1\s+1/2|3/4|1/2|3/8|1/4|2|1)(?![\d/])",
            text,
        )
        if thread_standard:
            slots["thread_standard"] = thread_standard.group("standard")
            slots["size_inch"] = re.sub(r"\s+", "", thread_standard.group("size"))
        elif re.search(r"\bbspp\b|цилиндрическ\w*\s+трубн\w*\s+резьб", text):
            slots["thread_standard"] = "g"
        elif re.search(r"\bbspt\b|коническ\w*\s+трубн\w*\s+резьб", text):
            slots["thread_standard"] = "r"

        has_pair = bool(
            re.search(
                r"\b(?:вр|вн|нр|нар)\.?\s*[-/х]\s*(?:вр|вн|нр|нар)\.?\b",
                text,
            )
            or re.search(r"\b(?:ff|fm|mf|mm)\b", text)
        )
        if not has_pair:
            if re.search(r"\b(?:вр|вн)\.?\b|внутренн\w*\s+резьб", text):
                slots["thread_gender"] = "female"
            elif re.search(r"\b(?:нр|нар)\.?\b|наружн\w*\s+резьб", text):
                slots["thread_gender"] = "male"


def _extract_pipe_and_fitting_notation(
    text: str,
    category: str,
    slots: dict[str, Any],
) -> None:
    if category == "pipes":
        if re.search(r"\b(?:pprc|ppr|pp-r|ппр)\b|полипропилен", text):
            slots["pipe_material"] = "ppr"
        elif re.search(r"\b(?:pe-rt|pert|пе-рт)\b|термостойк\w*\s+полиэтилен", text):
            slots["pipe_material"] = "pe-rt"
        elif re.search(r"\b(?:pex|pe-x[abc]?|ре-?х[abc]?)\b|сшит\w*\s+полиэтилен", text):
            slots["pipe_material"] = "pex"
        elif re.search(
            r"\b(?:м\s*[/.-]\s*п|мп)\b|металлопласт|pe-x\s*[-/]\s*al\s*[-/]\s*pe-x",
            text,
        ):
            slots["pipe_material"] = "металлопластик"
        elif re.search(r"\b(?:пнд|hdpe|пэ\s*-?\s*100|pe\s*-?\s*100)\b", text):
            slots["pipe_material"] = "пэ100"
        elif re.search(r"\b(?:пвх|pvc)\b", text):
            slots["pipe_material"] = "pvc"

        oxygen_barrier_mentioned = bool(
            re.search(
                r"\bevoh\b|(?:кислородн|антидиффузионн)\w*\s+(?:барьер|слой)",
                text,
            )
        )
        if oxygen_barrier_mentioned:
            oxygen_barrier_rejected = bool(
                re.search(
                    r"\bбез\s+(?:слоя\s+)?(?:evoh|кислородн\w*\s+барьер\w*|"
                    r"антидиффузионн\w*\s+сло\w*)\b|"
                    r"\b(?:evoh|кислородн\w*\s+барьер\w*)[^.!?]{0,18}"
                    r"\bне\s+(?:нужен|требуется)\b",
                    text,
                )
            )
            slots["oxygen_barrier"] = not oxygen_barrier_rejected
        if re.search(
            r"\b(?:al|aluminium|alux)\b|"
            r"армир\w*\s+алюминием\b|"
            r"алюминиев\w*\s+(?:слой|фольг\w*)",
            text,
        ):
            slots["reinforcement"] = "алюминий"
        elif re.search(r"\b(?:gf|fb|fiber)\b|стекловолок", text):
            slots["reinforcement"] = "стекловолокно"

        if any(marker in text for marker in ["внутри здания", "в помещении", "внутри помещения"]):
            slots["pipe_service"] = "разводка внутри дома"
        if re.search(r"\b(?:для|в|на)\s+со\b", text):
            slots["pipe_purpose"] = "отопление"
        if re.search(r"\b(?:втп|тп)\b", text):
            slots["pipe_purpose"] = "отопление"
            slots["pipe_service"] = "петля тёплого пола"

    if category == "fittings":
        if re.search(r"\bppsu\b|полифенилсульфон", text):
            slots["fitting_material"] = "ppsu"
        elif re.search(r"\bpvdf\b|поливинилиденфторид", text):
            slots["fitting_material"] = "pvdf"

    if category in {"pipes", "fittings"}:
        profile = re.search(
            r"\b(th|u|v|m|f)\s*[- ]?(?:профиль|проф\.?)(?!\w)|"
            r"\b(?:профиль|проф\.?)\s*[- ]?(th|u|v|m|f)\b",
            text,
        )
        if profile:
            slots["press_profile"] = profile.group(1) or profile.group(2)

        if re.search(r"\bepdm\b|этиленпропилен", text):
            slots["seal_material"] = "epdm"
        elif re.search(r"\b(?:ptfe|фум)\b|фторопласт", text):
            slots["seal_material"] = "ptfe"


def _extract_sewer_notation(text: str, slots: dict[str, Any]) -> None:
    code = re.search(r"\b(htem|htb|htea|htu|kgem|kgb)\b", text)
    if code:
        value = code.group(1)
        slots["sewer_system_code"] = value
        if value.startswith("ht"):
            slots["sewer_scope"] = "внутренняя"
        elif value.startswith("kg"):
            slots["sewer_scope"] = "наружная"
        element_by_code = {
            "htem": "труба",
            "htb": "отвод",
            "htea": "тройник",
            "htu": "муфта",
            "kgem": "труба",
        }
        if value in element_by_code:
            slots["element_type"] = element_by_code[value]
    else:
        simple_code = re.search(r"\b(ht|kg)\b", text)
        if simple_code:
            value = simple_code.group(1)
            slots["sewer_system_code"] = value
            slots["sewer_scope"] = "внутренняя" if value == "ht" else "наружная"

    if re.search(r"\b(?:сн|sn)\s*-?\s*(4|8|10|12|16)\b", text):
        match = re.search(r"\b(?:сн|sn)\s*-?\s*(4|8|10|12|16)\b", text)
        assert match is not None
        slots["ring_stiffness_sn"] = int(match.group(1))

    material = re.search(r"\b(?:pvc|пвх)\b", text)
    if material:
        slots["pipe_material"] = "pvc"
    elif re.search(r"\b(?:pp|пп)\b|полипропилен", text):
        slots["pipe_material"] = "pp"


def _extract_pump_notation(text: str, slots: dict[str, Any]) -> None:
    if re.search(r"\bкнс\b|канализационн\w*\s+насосн\w*\s+(?:станц|установ)", text):
        slots["pump_type"] = "канализационная насосная установка"
        slots["pump_use"] = "канализация"
    elif re.search(r"\b(?:насос\w*\s+цн|цн\s*насос)\b", text):
        slots["pump_type"] = "центробежный"
    flow = re.search(
        r"(?<![a-zа-я])q(?!\s*max)\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*"
        r"(м3/ч|м³/ч|л/мин|л/ч|л/с)",
        text,
    )
    if flow:
        slots["required_flow_m3_h"] = _flow_to_m3_h(flow.group(1), flow.group(2))

    maximum_flow = re.search(
        r"(?<![a-zа-я])q\s*max\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*"
        r"(м3/ч|м³/ч|л/мин|л/ч|л/с)",
        text,
    )
    if maximum_flow:
        slots["maximum_flow_m3_h"] = _flow_to_m3_h(
            maximum_flow.group(1), maximum_flow.group(2)
        )

    head = re.search(
        r"(?<![a-zа-я])h(?!\s*max)\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*"
        r"(?:м\b|метр\w*|м\.?\s*в\.?\s*ст\.?)?",
        text,
    )
    if head:
        slots["required_head_m"] = float(head.group(1).replace(",", "."))
        slots["required_head_calculated"] = False

    maximum_head = re.search(
        r"(?<![a-zа-я])h\s*max\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*"
        r"(?:м\b|метр\w*|м\.?\s*в\.?\s*ст\.?)?",
        text,
    )
    if maximum_head:
        slots["maximum_head_m"] = float(maximum_head.group(1).replace(",", "."))

    natural_head = re.search(
        r"(?:точк\w*\s+разбор\w*|потребител\w*|верхн\w*\s+точк\w*)"
        r"[^.!?]{0,24}(?:выше|поднят\w*)[^\d]{0,12}"
        r"(\d+(?:[,.]\d+)?)\s*(?:м|метр)",
        text,
    )
    if not natural_head:
        natural_head = re.search(
            r"перепад\w*\s+высот\w*[^\d]{0,12}"
            r"(\d+(?:[,.]\d+)?)\s*(?:м|метр)",
            text,
        )
    if natural_head:
        slots["lift_height_m"] = float(natural_head.group(1).replace(",", "."))

    connection = re.search(
        r"(?:присоедин\w*|условн\w*\s+проход\w*|\bdn)"
        r"[^\d]{0,12}(15|20|25|32|40|50|65|80|100)\b",
        text,
    )
    if connection:
        slots["connection_size"] = int(connection.group(1))

    p1 = re.search(r"\bp\s*1\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*(квт|вт|kw|w)\b", text)
    if p1:
        slots["input_power_w"] = _power_to_w(p1.group(1), p1.group(2))
    p2 = re.search(r"\bp\s*2\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*(квт|вт|kw|w)\b", text)
    if p2:
        slots["shaft_power_w"] = _power_to_w(p2.group(1), p2.group(2))


def _extract_heating_and_water_notation(
    text: str,
    category: str,
    slots: dict[str, Any],
) -> None:
    if category == "boilers":
        if re.search(r"\b(?:2\s*[кk]|2к)\b", text):
            slots["contours"] = "двухконтурный"
            slots["allow_alternatives"] = False
        elif re.search(r"\b(?:1\s*[кk]|1к)\b", text):
            slots["contours"] = "одноконтурный"
            slots["allow_alternatives"] = False

        chimney = re.search(
            r"(?:дымоход\w*|коаксиал\w*)[^\d]{0,18}"
            r"(\d{2,3})\s*[/xх×]\s*(\d{2,3})",
            text,
        )
        if chimney:
            slots["chimney_size"] = f"{int(chimney.group(1))}/{int(chimney.group(2))}"
            slots["needs_chimney"] = True
        if re.search(r"\b(?:ng|природн\w*\s+газ)\b", text):
            slots["gas_type"] = "природный"
        elif re.search(r"\b(?:lpg|сжиженн\w*\s+газ|пропан)\b", text):
            slots["gas_type"] = "сжиженный"

    if category == "water_heaters":
        if re.search(r"\b(?:сух\w*\s+тэн\w*|сухой\s+нагревательн\w*\s+элемент)\b", text):
            slots["heating_element_type"] = "сухой"
        elif re.search(r"\b(?:мокр\w*\s+тэн\w*|погружн\w*\s+тэн\w*)\b", text):
            slots["heating_element_type"] = "мокрый"

    if category == "hydraulic_accumulators":
        if re.search(r"\b(?:га|г\s*\.?\s*а\.?)\s*-?\s*\d", text):
            slots["tank_application"] = "водоснабжение"
        elif re.search(r"\b(?:рб|расширительн\w*\s+бак)\b", text):
            slots["tank_application"] = "отопление"


def _extract_radiator_notation(text: str, slots: dict[str, Any]) -> None:
    if "биметалл" in text:
        slots["radiator_type"] = "биметаллический"
    elif "алюмин" in text:
        slots["radiator_type"] = "алюминиевый"
    elif "панельн" in text:
        slots["radiator_type"] = "панельный"
    elif "стальн" in text:
        slots["radiator_type"] = "стальной"

    center = re.search(
        r"(?:межосев\w*|м\s*[/.-]?\s*о)\D{0,12}"
        r"(\d{2,4})(?:\s*мм)?",
        text,
    )
    if center:
        slots["radiator_size_mm"] = int(center.group(1))

    height = re.search(
        r"высот\w*\D{0,12}(\d{2,4})(?:\s*мм)?",
        text,
    )
    if height:
        slots["radiator_height_mm"] = int(height.group(1))

    panel_type = re.search(
        r"\bтип\s*(10|11|20|21|22|30|33)\b|"
        r"\b(?:c|cv|vc|vk)\s*(10|11|20|21|22|30|33)(?=[-\s]|$)",
        text,
    )
    if panel_type:
        slots["radiator_panel_type"] = int(panel_type.group(1) or panel_type.group(2))

    delta_t = re.search(r"(?:δ|∆|d)\s*t\s*(?:=|:)?\s*(\d{1,3})\b", text)
    if delta_t:
        slots["rating_delta_t_c"] = int(delta_t.group(1))

    output = re.search(
        r"(?:теплоотдач\w*|мощност\w*)[^\d]{0,15}"
        r"(\d+(?:[,.]\d+)?)\s*(?:вт|w)\b",
        text,
    )
    if output:
        slots["heat_output_w"] = float(output.group(1).replace(",", "."))

    if re.search(r"\b(?:нижн\w*|донн\w*)\s+подключ", text):
        slots["radiator_connection"] = "нижнее"
    elif re.search(r"\bбоков\w*\s+подключ", text):
        slots["radiator_connection"] = "боковое"


def _extract_valve_notation(text: str, slots: dict[str, Any]) -> None:
    coefficient = re.search(
        r"(?<![a-zа-я])(kvs|kv|cv)\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\b",
        text,
    )
    if coefficient:
        slots["flow_coefficient_kind"] = coefficient.group(1)
        slots["flow_coefficient"] = float(coefficient.group(2).replace(",", "."))
    else:
        full_coefficient = re.search(
            r"(?:коэффициент\w*\s+)?пропускн\w*\s+способност\w*"
            r"[^\d]{0,12}(\d+(?:[,.]\d+)?)\b",
            text,
        )
        if full_coefficient:
            slots["flow_coefficient_kind"] = "kvs"
            slots["flow_coefficient"] = float(
                full_coefficient.group(1).replace(",", ".")
            )

    differential = re.search(
        r"(?:δ|∆|d)\s*p\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*(?:бар|bar)\b|"
        r"перепад\w*\s+давлен\w*[^\d]{0,12}(\d+(?:[,.]\d+)?)\s*(?:бар|bar)\b",
        text,
    )
    if differential:
        slots["differential_pressure_bar"] = float(
            (differential.group(1) or differential.group(2)).replace(",", ".")
        )

    ways = re.search(r"(?<!\d)(2|3)\s*/\s*2(?!\d)", text)
    if ways:
        slots["valve_ways"] = int(ways.group(1))

    state = _normal_state(text)
    if state:
        slots["normal_state"] = state


def _extract_filter_notation(text: str, slots: dict[str, Any]) -> None:
    form = re.search(r"\b(10|20)\s*(sl|bb)\b", text)
    if form:
        slots["filter_format"] = f"{form.group(1)}{form.group(2)}"
    else:
        big_blue = re.search(
            r"(?:\b(?:big\s*blue|биг\s*блю)\b\D{0,10}(10|20)\b|"
            r"\b(10|20)\b\D{0,10}(?:big\s*blue|биг\s*блю)\b)",
            text,
        )
        slim_line = re.search(
            r"(?:\b(?:slim\s*line|слим\s*лайн)\b\D{0,10}(10|20)\b|"
            r"\b(10|20)\b\D{0,10}(?:slim\s*line|слим\s*лайн)\b)",
            text,
        )
        if big_blue:
            slots["filter_format"] = f"{big_blue.group(1) or big_blue.group(2)}bb"
        elif slim_line:
            slots["filter_format"] = f"{slim_line.group(1) or slim_line.group(2)}sl"

    microns = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:мкм|µm|μm|микрон\w*)", text)
    if microns:
        slots["filtration_microns"] = float(microns.group(1).replace(",", "."))

    technology_aliases = {
        "ro": r"\bro\b|обратн\w*\s+осмос",
        "uf": r"\buf\b|ультрафильтрац",
        "gac": r"\bgac\b|гранулированн\w*\s+уг(?:ол|л)\w*",
        "cto": r"\bcto\b|прессованн\w*\s+уг(?:ол|л)\w*",
        "cbc": r"\bcbc\b|карбон[- ]?блок",
        "pp": r"\bpp\b|полипропиленов\w*\s+(?:картридж|волокн)",
        "mechanical": r"механическ\w*|осадочн\w*(?:\s+фильтр)?",
        "carbon": r"угольн\w*|активированн\w*\s+угол",
    }
    for canonical, pattern in technology_aliases.items():
        if re.search(pattern, text):
            slots["filter_technology"] = canonical
            break

    if re.search(r"\bкартридж\w*\b", text):
        slots["filter_element_type"] = "картридж"
    elif re.search(r"\bкорпус\w*\s+фильтр", text):
        slots["filter_element_type"] = "корпус"
    elif re.search(r"\bфильтр\w*\b", text):
        slots["filter_element_type"] = "фильтр"

    if "гвс" in text or "горяч" in text:
        slots["water_temperature"] = "горячая"
    elif "хвс" in text or "холод" in text:
        slots["water_temperature"] = "холодная"


def _extract_control_notation(text: str, slots: dict[str, Any]) -> None:
    state = _normal_state(text)
    if state:
        slots["normal_state"] = state

    signal = re.search(r"\b0\s*[-–]\s*10\s*(?:v|в)\b", text)
    if signal:
        slots["control_signal"] = "0-10v"

    if re.search(r"\brtl\b|ограничител\w*\s+температур\w*\s+обрат", text):
        slots["control_kind"] = "rtl"
    elif re.search(r"\bнсу\b|насосно[- ]?смесительн\w*\s+узел", text):
        slots["control_kind"] = "насосно-смесительный узел"
    elif re.search(r"\bпч\b|преобразовател\w*\s+частот", text):
        slots["control_kind"] = "частотный преобразователь"
    elif re.search(r"\bсервопривод\w*\b|привод\w*\s+клапан", text):
        slots["control_kind"] = "сервопривод"
    elif re.search(r"\b(?:термостат|терморегулятор)\w*\b", text):
        slots["control_kind"] = "термостат"


def _extract_electrical(text: str, category: str, slots: dict[str, Any]) -> None:
    if category not in ELECTRICAL_CATEGORIES:
        return

    ip = re.search(r"\bip\s*(x?\s*\d{1,2})\b", text)
    if ip:
        slots["ip_rating"] = "ip" + re.sub(r"\s+", "", ip.group(1))

    phase = re.search(r"\b([13])\s*(?:ф|фаз)\w*\b", text)
    if not phase:
        phase = re.search(r"(?<!\d)([13])\s*~", text)
    if not phase:
        word_phase = re.search(r"\b(одно|трех|трёх)фазн\w*\b", text)
        if word_phase:
            slots["phase_count"] = 1 if word_phase.group(1) == "одно" else 3
    else:
        slots["phase_count"] = int(phase.group(1))

    voltage = re.search(r"(?<!\d)(12|24|220|230|380|400)\s*(?:v|в|вольт)\b", text)
    if voltage:
        slots["voltage_v"] = int(voltage.group(1))

    current = re.search(
        r"(?:(12|24|220|230|380|400)\s*(?:v|в)\s*(ac|dc)\b|"
        r"\b(ac|dc)\s*(12|24|220|230|380|400)\s*(?:v|в))",
        text,
    )
    if current:
        slots["current_type"] = current.group(2) or current.group(3)

    if re.search(r"\b(?:узо|устройство\s+защитн\w*\s+отключен)\w*\b", text):
        slots["requires_rcd"] = True


def _normal_state(text: str) -> str | None:
    if re.search(
        r"\b(?:нз|nc)\b|(?<!\w)н\.з\.(?!\w)|"
        r"\bнормальн\w*\s+закрыт\w*|\bнорм\.?\s*закр\w*",
        text,
    ):
        return "нормально закрытый"
    # Plain Russian ``но`` is a conjunction and must never be expanded.  The
    # Cyrillic abbreviation is accepted only with dots or next to the device.
    if (
        re.search(
            r"\bno\b|(?<!\w)н\.о\.(?!\w)|"
            r"\bнормальн\w*\s+открыт\w*|\bнорм\.?\s*откр\w*",
            text,
        )
        or re.search(r"\bно\s+(?:сервопривод|привод|клапан)\w*\b", text)
        or re.search(r"\b(?:сервопривод|привод|клапан)\w*\s+но\b", text)
        or re.search(
            r"\b(?:сервопривод|привод|клапан|термостат)\w*"
            r"(?:\s+\w+){0,2}\s+но(?=\s*(?:\d|$))",
            text,
        )
    ):
        return "нормально открытый"
    return None


def _flow_to_m3_h(number: str, unit: str | None) -> float:
    value = float(number.replace(",", "."))
    normalized_unit = normalize_text(unit)
    if "л/мин" in normalized_unit:
        value = value * 60.0 / 1000.0
    elif "л/ч" in normalized_unit:
        value /= 1000.0
    elif "л/с" in normalized_unit:
        value *= 3.6
    return round(value, 4)


def _power_to_w(number: str, unit: str) -> float:
    value = float(number.replace(",", "."))
    if normalize_text(unit) in {"квт", "kw"}:
        value *= 1000
    return value
