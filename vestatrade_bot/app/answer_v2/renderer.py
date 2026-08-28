"""Constrained deterministic and optional LLM rendering of AnswerPlan."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any

from app.openrouter_client import OpenRouterClient
from app.dialogue_v2.contracts import InformationSourceKind

from .contracts import (
    AnswerClaim,
    AnswerPlan,
    CandidateFactStatus,
    ClaimKind,
    LimitationStatus,
    NaturalizationLayout,
    NaturalizationProposal,
    NextStepKind,
    ProductPresentationPlan,
    RenderedAnswer,
    RenderedAnswerResult,
    RenderedSegment,
    RenderedSegmentKind,
    TransitionStyle,
)


RENDERER_PROMPT_VERSION = "answer-renderer-v2.2"
RENDERER_PROMPT = """
Ты выбираешь только нейтральные связки между уже готовыми сегментами ответа
продавца-консультанта. Верни JSON по схеме NaturalizationLayout. Фактический
текст тебе не передаётся и создавать текст нельзя. Укажи не более одной
связки перед существующим segment_id, не ставь связку перед первым сегментом
и не меняй порядок сегментов. Допустимы только стили из схемы. Можно вернуть
пустой список transitions. Идентификатор плана возвращать не нужно: он
подставляется детерминированно вне модели.
""".strip()


_TRANSITION_TEXT = {
    TransitionStyle.ALSO: "Дополнительно:",
    TransitionStyle.IMPORTANT: "Важно:",
    TransitionStyle.THEREFORE: "Поэтому:",
    TransitionStyle.NEXT: "Далее:",
}
ALLOWED_TRANSITION_TEXTS = frozenset(_TRANSITION_TEXT.values())

_FACT_LABELS = {
    "angle_deg": "угол",
    "area_m2": "площадь",
    "boiler_type": "тип котла",
    "brand": "бренд",
    "center_distance_mm": "межосевое расстояние",
    "circuits": "количество контуров",
    "combustion_chamber": "камера сгорания",
    "contact_ref": "телефон или email для связи",
    "colour": "цвет",
    "color": "цвет",
    "control_thread": "резьба подключения управления",
    "coolant_type": "теплоноситель",
    "diameter_mm": "диаметр присоединения",
    "connection_diameter_mm": "диаметр присоединения",
    "connection_pattern": "тип резьбового соединения",
    "connection_size": "размер присоединения",
    "delivery_destination": "пункт назначения",
    "delivery_no_repack": "без переупаковки",
    "delivery_whole_bundles": "отгрузка целыми упаковками",
    "destination_region": "пункт назначения",
    "duty_point_flow_l_h": "расход в рабочей точке",
    "duty_point_head_m": "напор в рабочей точке",
    "filter_method": "тип фильтрации",
    "flue_solution": "вариант дымоудаления",
    "glycol_concentration_percent": "концентрация гликоля",
    "has_handle": "наличие рукоятки",
    "heat_output_w": "тепловая мощность",
    "heating_system_type": "тип системы отопления",
    "installation_location": "место установки",
    "length_mm": "длина",
    "material": "материал",
    "max_flow_l_h": "максимальная подача",
    "max_head_m": "максимальный напор",
    "mounting_length_mm": "монтажная длина",
    "micron_rating_um": "тонкость фильтрации",
    "minimum_power_kw": "минимальная мощность",
    "operating_pressure_bar": "рабочее давление",
    "operating_temperature_c": "рабочая температура",
    "pipe_service": "назначение трубы",
    "port_count": "количество присоединений",
    "power_kw": "мощность",
    "pressure_class": "класс давления",
    "product_selection": "выбранный товар",
    "pump_type": "тип насоса",
    "quantity": "запрошенное количество",
    "reinforcement": "тип армирования",
    "required_flow_m3_h": "требуемый расход",
    "requested_quantity_m": "требуемое количество",
    "secondary_diameter_mm": "второй диаметр",
    "sewer_scope": "область применения канализации",
    "site_url": "официальный сайт",
    "sku": "артикул",
    "style_match": "соответствие внешнему виду",
    "suction_depth_m": "глубина всасывания",
    "system_type": "схема системы",
    "thread_type": "тип резьбы",
    "valve_shape": "исполнение клапана",
    "washable": "промывная конструкция",
    "application": "назначение",
}

# Instructions are keyed only by declarative contract codes.  The renderer
# never inspects the user's wording to decide how a technical fact is learned.
_LEARN_METHOD_INSTRUCTIONS = {
    "calculate_heat_loss_or_read_project_power": (
        "Возьмите расчёт теплопотерь или проектную мощность отопления. Если их "
        "нет, расчёт выполняют по ограждающим конструкциям, климату, вентиляции "
        "и режиму здания; одну площадь нельзя считать окончательным расчётом."
    ),
    "decide_heating_only_or_dhw": (
        "Уточните задачу: котёл нужен только для отопления или также должен "
        "готовить горячую воду. Если горячую воду обеспечивает отдельный "
        "водонагреватель, укажите это отдельно."
    ),
    "estimate_required_system_head": (
        "Нужный напор берут из гидравлического расчёта самого неблагоприятного "
        "контура с учётом сопротивления труб, арматуры и оборудования. Высота "
        "дома сама по себе этот параметр для закрытой системы не заменяет."
    ),
    "identify_available_energy_source": (
        "Укажите реально доступный источник энергии: подключённый газ, "
        "электроснабжение с известными напряжением и выделенной мощностью либо "
        "возможность безопасно использовать твёрдое топливо."
    ),
    "identify_internal_or_external_sewer": (
        "Уточните, где проходит участок: внутри отапливаемого здания или снаружи, "
        "в грунте. Переход через стену или фундамент опишите как отдельный участок."
    ),
    "identify_pump_application": (
        "Опишите работу насоса: циркуляция отопления или ГВС, повышение давления, "
        "подача чистой воды из колодца или скважины, дренаж либо сточные воды."
    ),
    "identify_pipe_service": (
        "Укажите участок: холодное или горячее водоснабжение либо отопление; "
        "для канализации нужен отдельный тип трубы."
    ),
    "identify_pipe_operating_temperature": (
        "Укажите максимальную температуру воды или теплоносителя. Её можно взять "
        "из настроек источника тепла, проекта системы или паспорта оборудования."
    ),
    "identify_pipe_operating_pressure": (
        "Укажите максимальное рабочее давление системы. Проверьте его в проекте, "
        "на настройке автоматики или по показанию исправного манометра."
    ),
    "identify_required_water_treatment": (
        "Возьмите результаты анализа воды и укажите проблему, которую нужно "
        "устранить: механические примеси, железо, жёсткость, запах или растворённые "
        "вещества. По внешнему виду воды состав надёжно не определяют."
    ),
    "inspect_both_connection_threads": (
        "Осмотрите оба конца детали отдельно: резьба внутри отверстия — ВР, "
        "снаружи патрубка — НР. Передайте последовательность от входа к выходу "
        "и сверьте маркировку; не разбирайте горячее или находящееся под давлением соединение."
    ),
    "measure_old_pump_mounting_length": (
        "Монтажная длина определяет, встанет ли насос в существующий разрыв "
        "трубопровода без переделки соединений. Размеры в предварительных "
        "карточках относятся к кандидатам и не подтверждают размер старого насоса. "
        "Сначала проверьте монтажный размер на шильдике или в паспорте старого "
        "насоса. Если нужен замер, измеряйте вдоль оси трубопровода между ответными "
        "уплотнительными плоскостями разъёмных соединений — местами прилегания "
        "«американок», а не по корпусу или накидным гайкам. Не разбирайте горячую "
        "или находящуюся под давлением систему; если плоскости недоступны, нужен специалист."
    ),
    # Backward-compatible code used by early Stage-5 fixtures.
    "measure_between_union_faces": (
        "Монтажная длина определяет, встанет ли насос в существующий разрыв "
        "трубопровода без переделки соединений. Размеры в предварительных "
        "карточках относятся к кандидатам и не подтверждают размер старого насоса. "
        "Сначала проверьте монтажный размер на шильдике или в паспорте старого "
        "насоса. Если нужен замер, измеряйте вдоль оси трубопровода между ответными "
        "уплотнительными плоскостями разъёмных соединений — местами прилегания "
        "«американок», а не по корпусу или накидным гайкам. Не разбирайте горячую "
        "или находящуюся под давлением систему; если плоскости недоступны, нужен специалист."
    ),
    "measure_outer_or_nominal_diameter": (
        "Сначала прочитайте размер на маркировке трубы или детали. У пластиковой "
        "трубы наружный диаметр можно измерить штангенциркулем; у металлической "
        "резьбы наружный замер не равен дюймовому условному размеру, поэтому нужна "
        "маркировка или паспорт. Не разъединяйте работающий трубопровод ради замера."
    ),
    "measure_product_length": (
        "Сверьте длину в маркировке или карточке изделия; при замере укажите "
        "расстояние вдоль оси между торцами и отдельно сообщите, учитывали ли раструб."
    ),
    "measure_radiator_centers": (
        "Измерьте расстояние между осями верхнего и нижнего присоединений радиатора, "
        "а не полную высоту корпуса. Не ослабляйте пробки и соединения заполненной системы."
    ),
    "measure_second_connection_diameter": (
        "Сверьте маркировку и измерьте второй присоединительный конец или ответвление "
        "отдельно от основного. Укажите, какой размер относится к магистрали, а какой — к отводу."
    ),
    "measure_suction_depth": (
        "Нужна вертикальная разница между осью входа поверхностного насоса и "
        "минимальным динамическим уровнем воды при работе, а не общая глубина "
        "источника. Возьмите данные из паспорта скважины или акта прокачки; не "
        "спускайтесь в колодец для самостоятельного замера."
    ),
    "read_angle_marking": (
        "Проверьте обозначение угла на корпусе, упаковке или в паспорте детали. "
        "По приблизительному виду точный стандартный угол не подтверждают."
    ),
    "read_connection_marking": (
        "Перепишите размер и стандарт присоединения с корпуса, шильдика или паспорта. "
        "Не переводите наружный диаметр резьбы в дюймовый размер без таблицы производителя."
    ),
    "read_filter_cleaning_method": (
        "Проверьте в паспорте точной модели способ обслуживания: сменный элемент, "
        "ручная промывка, обратная промывка или самоочистка. По внешнему виду это "
        "надёжно не определяют."
    ),
    "read_filter_micron_rating": (
        "Перепишите тонкость фильтрации с маркировки элемента или паспорта точной "
        "модели. Название серии без значения тонкости подтверждением не считается."
    ),
    "read_valve_or_head_thread": (
        "Сверьте обозначение резьбы на корпусе клапана и в паспорте термоголовки. "
        "Нужно совпадение размера и стандарта; похожая посадка без маркировки "
        "совместимость не подтверждает."
    ),
    "read_valve_port_count": (
        "Посчитайте рабочие присоединения по схеме на корпусе или в паспорте, "
        "отдельно от сервисных и измерительных отверстий. Укажите назначение каждого порта."
    ),
}

_EXPECTED_UNIT_TEXT = {
    "%": "процентах (%)",
    "°": "градусах (°)",
    "°C": "градусах Цельсия (°C)",
    "бар": "барах (бар)",
    "Вт": "ваттах (Вт)",
    "кВт": "киловаттах (кВт)",
    "л/ч": "литрах в час (л/ч)",
    "м": "метрах (м)",
    "мкм": "микрометрах (мкм)",
    "мм": "миллиметрах (мм)",
}


def _learn_method_instruction(code: str | None) -> str | None:
    if not code:
        return None
    return _LEARN_METHOD_INSTRUCTIONS.get(
        code,
        "Проверьте значение по маркировке или паспорту точного изделия. Если для "
        "проверки требуется разбирать работающую систему, поручите это специалисту.",
    )


def _expected_unit_instruction(unit: str | None) -> str:
    if not unit:
        return ""
    label = _EXPECTED_UNIT_TEXT.get(unit, unit)
    return f" Ответ укажите в {label}."

_VALUE_LABELS = {
    "electric": "электрический",
    "female_female": "внутренняя/внутренняя резьба",
    "female_male": "внутренняя/наружная резьба",
    "male_female": "наружная/внутренняя резьба",
    "male_male": "наружная/наружная резьба",
    "gas": "газовый",
    "mechanical": "механическая очистка",
    "magnetic": "магнитная очистка",
    "reverse_osmosis": "обратный осмос",
    "carbon": "угольная очистка",
    "softening": "умягчение",
    "iron_removal": "обезжелезивание",
    "angle": "угловое исполнение",
    "straight": "прямое исполнение",
    "closed": "закрытая камера",
    "open": "открытая камера",
    "water": "вода",
    "steam": "пар",
    "glycol_unspecified": "гликолевый теплоноситель без уточнения типа",
    "external": "наружная",
    "internal": "внутренняя",
    "pex_a": "PEX-a",
    "pp_fiber": "полипропилен, армированный волокном",
    "pp_alux": "полипропилен, армированный алюминием",
    "ppr": "полипропилен PPR",
    "bimetal": "биметалл",
    "aluminium": "алюминий",
    "steel": "сталь",
    "circulation": "циркуляционный",
    "dhw_circulation": "циркуляционный для ГВС",
    "borehole": "скважинный",
    "drainage": "дренажный",
    "sewage": "канализационный",
    "pump_station": "насосная станция",
    "true": "да",
    "false": "нет",
    "in_stock": "в наличии",
    "out_of_stock": "нет в наличии",
    "preorder": "доступно под заказ",
    "unknown": "наличие не подтверждено",
    "white": "белый",
    "grey": "серый",
    "gray": "серый",
    "black": "чёрный",
    "chrome": "хром",
    "pex": "сшитый полиэтилен",
    "закр.камера": "закрытая камера",
    "откр.камера": "открытая камера",
    "НР-НР": "наружная/наружная резьба",
    "ВР-ВР": "внутренняя/внутренняя резьба",
    "ВР-НР": "внутренняя/наружная резьба",
    "propylene_glycol": "пропиленгликоль",
    "ethylene_glycol": "этиленгликоль",
    "cold_water": "холодная вода",
    "hot_water": "горячая вода",
    "heating": "отопление",
    "cold_water hot_water": "холодная и горячая вода",
    "cold_water heating": "холодная вода и отопление",
    "hot_water heating": "горячая вода и отопление",
    "cold_water hot_water heating": "холодная и горячая вода, отопление",
    "glass_fiber": "стекловолокно",
    "aluminium": "алюминий",
    "unreinforced": "без армирования",
}

_CANONICAL_VALUE_PREDICATES = frozenset(
    {
        "application",
        "boiler_type",
        "circuits",
        "combustion_chamber",
        "connection_pattern",
        "color",
        "colour",
        "coolant_type",
        "filter_method",
        "material",
        "pipe_service",
        "pump_type",
        "reinforcement",
        "sewer_scope",
        "stock_status",
        "valve_shape",
        "washable",
    }
)


def _fact_label(name: str) -> str:
    return _FACT_LABELS.get(name, "уточняемая характеристика")


def _value(value: object, predicate: str | None = None) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    raw = str(value)
    if predicate == "circuits":
        if raw == "1":
            return "один контур"
        if raw == "2":
            return "два контура"
    if predicate in _CANONICAL_VALUE_PREDICATES:
        return _VALUE_LABELS.get(raw, _VALUE_LABELS.get(raw.casefold(), raw))
    return raw


_UNIT_ALIASES = {
    "mm": ("mm", "мм"),
    "cm": ("cm", "см"),
    "m": ("m", "м", "метр", "метра", "метров"),
    "kw": ("kw", "квт"),
    "w": ("w", "вт"),
    "l/h": ("l/h", "л/ч"),
    "l/min": ("l/min", "л/мин"),
    "m3/h": ("m3/h", "м3/ч", "м³/ч"),
    "bar": ("bar", "бар"),
    "%": ("%",),
    "°c": ("°c", "°с"),
    "c": ("c", "°c", "°с"),
    "deg": ("deg", "°"),
    "inch": ("inch", "дюйм", "дюйма", "дюймов", '"', "″"),
    "um": ("um", "мкм"),
    "rub": ("rub", "руб", "руб.", "₽"),
    "rur": ("rur", "руб", "руб.", "₽"),
    "m²": ("m²", "м²"),
}

_PUBLIC_UNITS = {
    "mm": "мм",
    "cm": "см",
    "m": "м",
    "kw": "кВт",
    "w": "Вт",
    "l/h": "л/ч",
    "l/min": "л/мин",
    "m3/h": "м³/ч",
    "bar": "бар",
    "deg": "°",
    "inch": "дюйм",
    "um": "мкм",
    "c": "°C",
    "°c": "°C",
    "rub": "руб.",
    "rur": "руб.",
    "%": "%",
    "m²": "м²",
}


def _unit_suffix(value: object, unit: str | None) -> str:
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


def _claim_text(
    claim: AnswerClaim,
    product_names: dict[str, str] | None = None,
) -> str:
    value = _value(claim.value, claim.predicate)
    unit = _unit_suffix(claim.value, claim.unit)
    product_name = (product_names or {}).get(claim.subject_ref)
    product_ref = (
        f" для «{product_name}» (артикул {claim.subject_ref})"
        if product_name
        else ""
    )
    if claim.kind == ClaimKind.PRODUCT_IDENTITY:
        return f"Товар: {value}."
    if claim.kind == ClaimKind.PRICE:
        return f"Цена{product_ref}: {value}{unit}."
    if claim.kind == ClaimKind.STOCK:
        if claim.predicate == "stock_qty" and not claim.unit:
            return (
                f"Остаток по фиду: {value}; "
                "единица складского учёта в фиде не указана."
            )
        return f"Подтверждённый статус наличия: {value}{unit}."
    if claim.kind == ClaimKind.LINK:
        return f"Ссылка на товар: {value}."
    if claim.kind == ClaimKind.COMMERCE_STATUS:
        statuses = {
            "not_requested": "операция не запрошена",
            "prepared": "команда только подготовлена и не отправлена",
            "queued": "операция поставлена в очередь, получение не подтверждено",
            "local_draft_saved": "сохранён только локальный черновик",
            "delivered": "внешняя система подтвердила получение",
            "failed": "выполнение операции завершилось ошибкой",
            "delivery_unknown": "получение внешней системой не подтверждено",
            "cancelled": "операция отменена",
        }
        return f"Статус операции: {statuses.get(value, value)}."
    if claim.kind == ClaimKind.CAPABILITY_FACT and claim.predicate == "site_url":
        return (
            "Проверить актуальные условия и выбрать контакты нужного филиала "
            f"можно на официальном сайте: {value}."
        )
    return f"{_fact_label(claim.predicate).capitalize()}: {value}{unit}."


def _claim_critical_literals(claim: AnswerClaim) -> tuple[str, ...]:
    raw_value = str(claim.value)
    rendered_value = _value(claim.value, claim.predicate)
    rendered_unit = _unit_suffix(claim.value, claim.unit).strip()
    return tuple(
        dict.fromkeys(
            (
                *((raw_value,) if rendered_value == raw_value else ()),
                *(
                    (str(claim.unit),)
                    if claim.unit is not None and rendered_unit == str(claim.unit)
                    else ()
                ),
            )
        )
    )


def _product_claim_detail(claim: AnswerClaim) -> str | None:
    value = _value(claim.value, claim.predicate)
    unit = _unit_suffix(claim.value, claim.unit)
    if claim.kind == ClaimKind.PRODUCT_IDENTITY:
        return None
    if claim.kind == ClaimKind.PRICE:
        return f"цена — {value}{unit}"
    if claim.kind == ClaimKind.STOCK:
        if claim.predicate == "stock_qty" and not claim.unit:
            return (
                f"остаток по фиду — {value} "
                "(единица складского учёта в фиде не указана)"
            )
        return f"наличие — {value}{unit}"
    if claim.kind == ClaimKind.LINK:
        return f"ссылка — {value}"
    if claim.kind == ClaimKind.PRODUCT_ATTRIBUTE:
        return f"{_fact_label(claim.predicate)} — {value}{unit}"
    return None


def _product_segment(
    answer_plan: AnswerPlan,
    product: ProductPresentationPlan,
    claims_by_id: dict[str, AnswerClaim],
    item_order: dict[str, int],
) -> RenderedSegment:
    product_claims = tuple(
        claim
        for claim_id in product.claim_ids
        if (claim := claims_by_id.get(claim_id)) is not None
        and claim.allowed_in_response
    )
    detail_order = {
        ClaimKind.PRICE: 0,
        ClaimKind.STOCK: 1,
        ClaimKind.PRODUCT_ATTRIBUTE: 2,
        ClaimKind.LINK: 3,
        ClaimKind.PRODUCT_IDENTITY: 4,
    }
    ordered_claims = tuple(
        sorted(
            product_claims,
            key=lambda claim: (
                detail_order.get(claim.kind, len(detail_order)),
                product.claim_ids.index(claim.claim_id),
            ),
        )
    )
    details = tuple(
        detail
        for claim in ordered_claims
        if (detail := _product_claim_detail(claim)) is not None
    )
    qualifier = {
        "exact": "точное подтверждённое совпадение",
        "preliminary": "предварительный вариант",
        "analog": "аналог с отличиями",
        "alternative": "альтернативное решение",
        "unverified": "предварительный вариант",
    }[product.status.value]
    if product.missing_hard_facts:
        missing = ", ".join(
            f"«{_fact_label(fact_name)}»"
            for fact_name in product.missing_hard_facts
        )
        qualifier = f"{qualifier}; по фиду не подтверждены {missing}"
    identity = f"«{product.name}» (артикул {product.sku})"
    if "sku_resolution_unique_prefix" in product.reason_codes:
        qualifier = (
            "сокращённый артикул однозначно сопоставлен с этой полной "
            f"карточкой; {qualifier}"
        )
    if (
        product.recommendation_role is not None
        and product.recommendation_role.value == "primary"
    ):
        criterion = (
            product.recommendation_criterion.value
            if product.recommendation_criterion is not None
            else ""
        )
        if criterion == "only_exact_eligible":
            basis = (
                "это единственный подтверждённый кандидат с совпавшими "
                "обязательными параметрами"
            )
        elif criterion == "lowest_confirmed_price":
            basis = (
                "среди точных кандидатов с подтверждённой ценой он выбран "
                "по минимальной цене"
            )
            if (
                "stable_sku_tiebreak_among_equal_lowest_prices"
                in product.recommendation_reason_codes
            ):
                basis += (
                    "; при одинаковой минимальной цене использован стабильный "
                    "порядок артикулов"
                )
        else:
            basis = (
                "при отсутствии сопоставимой подтверждённой цены использован "
                "стабильный порядок артикулов; качество и популярность не "
                "оценивались"
            )
        text = f"Рекомендую {identity} — {qualifier}; {basis}"
    elif (
        product.recommendation_role is not None
        and product.recommendation_role.value == "alternative"
    ):
        text = f"{identity} — дополнительный точный вариант"
    else:
        text = f"{identity} — {qualifier}"
    text = f"{text}: {'; '.join(details)}." if details else f"{text}."

    entity_ids = (product.product_plan_id, *(item.claim_id for item in product_claims))
    anchor = min(entity_ids, key=lambda item_id: item_order.get(item_id, 10**9))
    source_ids = tuple(
        dict.fromkeys(
            (
                anchor,
                *entity_ids,
                *product.source_ref_ids,
                *(
                    source_ref_id
                    for claim in product_claims
                    for source_ref_id in claim.source_ref_ids
                ),
            )
        )
    )
    return RenderedSegment(
        segment_id=f"segment_{product.product_plan_id}",
        kind=RenderedSegmentKind.PRODUCT,
        source_ids=source_ids,
        text=text,
        critical_literals=tuple(
            dict.fromkeys(
                (
                    product.name,
                    product.sku,
                    *(
                        literal
                        for claim in product_claims
                        for literal in _claim_critical_literals(claim)
                    ),
                )
            )
        ),
    )


def deterministic_render(answer_plan: AnswerPlan) -> RenderedAnswer:
    segments: list[RenderedSegment] = []
    item_order = {
        item_id: index
        for index, item_id in enumerate(
            item_id
            for section in answer_plan.sections
            for item_id in section.item_ids
        )
    }
    claims_by_id = {claim.claim_id: claim for claim in answer_plan.claims}
    product_claim_ids = {
        claim_id
        for product in answer_plan.products
        for claim_id in product.claim_ids
    }
    product_names = {
        claim.subject_ref: str(claim.value)
        for claim in answer_plan.claims
        if claim.kind == ClaimKind.PRODUCT_IDENTITY and claim.value is not None
    }
    for claim in answer_plan.claims:
        if not claim.allowed_in_response or claim.claim_id in product_claim_ids:
            continue
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{claim.claim_id}",
                kind=RenderedSegmentKind.FACT,
                source_ids=(claim.claim_id, *claim.source_ref_ids),
                text=_claim_text(claim, product_names),
                critical_literals=(
                    ()
                    if claim.kind == ClaimKind.COMMERCE_STATUS
                    else _claim_critical_literals(claim)
                ),
            )
        )
    for product in answer_plan.products:
        segments.append(
            _product_segment(
                answer_plan,
                product,
                claims_by_id,
                item_order,
            )
        )
    for difference in answer_plan.analog_differences:
        requested = _value(difference.requested_value, difference.fact_name)
        actual = (
            _value(difference.candidate_value, difference.fact_name)
            if difference.candidate_value is not None
            else "не подтверждено"
        )
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{difference.difference_id}",
                kind=RenderedSegmentKind.LIMITATION,
                source_ids=(difference.difference_id, *difference.source_ref_ids),
                text=(
                    f"Отличие аналога по параметру «{_fact_label(difference.fact_name)}»: "
                    f"требовалось {requested}, у кандидата {actual}."
                ),
                critical_literals=tuple(
                    dict.fromkeys(
                        (
                            *(
                                (requested,)
                                if requested == str(difference.requested_value)
                                else ()
                            ),
                            *(
                                (actual,)
                                if difference.candidate_value is not None
                                and actual == str(difference.candidate_value)
                                else ()
                            ),
                        )
                    )
                ),
            )
        )
    labels = {
        LimitationStatus.UNKNOWN: "значение пока неизвестно",
        LimitationStatus.REFUSED: "значение не указано",
        LimitationStatus.DEFERRED: "уточнение отложено",
        LimitationStatus.CATALOGUE_MISSING: "в каталоге нет подтверждённого значения",
        LimitationStatus.UNVERIFIED: "значение нельзя подтвердить по данным каталога",
        LimitationStatus.UNSUPPORTED: "для точного решения недостаточно подтверждённых данных",
        LimitationStatus.CONFLICTING: "полученные данные противоречат друг другу",
        LimitationStatus.CAPABILITY_BOUNDARY: "выполнение действия пока не подтверждено",
    }
    has_confirmed_packaging_coordinates = bool(
        {"delivery_whole_bundles", "delivery_no_repack"}.intersection(
            claim.predicate
            for claim in answer_plan.claims
            if claim.allowed_in_response
        )
    )
    limitation_segments: dict[str, RenderedSegment] = {}
    stock_no_match_by_scope = {
        (item.task_id, item.goal_id): item
        for item in answer_plan.limitations
        if item.reason_code == "no_verified_in_stock_contract_match"
    }
    suppressed_stock_sources_by_no_match: dict[str, tuple[str, ...]] = {}
    for item in answer_plan.limitations:
        if item.reason_code != "verified_stock_source_missing":
            continue
        no_match = stock_no_match_by_scope.get((item.task_id, item.goal_id))
        if no_match is None:
            continue
        suppressed_stock_sources_by_no_match[no_match.limitation_id] = tuple(
            dict.fromkeys(
                (
                    *suppressed_stock_sources_by_no_match.get(
                        no_match.limitation_id, ()
                    ),
                    item.limitation_id,
                    *item.source_ref_ids,
                )
            )
        )
    for limitation in answer_plan.limitations:
        fact = (
            f" «{_fact_label(limitation.fact_name)}»"
            if limitation.fact_name
            else ""
        )
        if (
            limitation.reason_code == "verified_stock_source_missing"
            and stock_no_match_by_scope.get(
                (limitation.task_id, limitation.goal_id)
            )
            is not None
        ):
            # The typed no-match boundary below already explains the stock
            # result.  A second generic "unknown parameter" sentence would be
            # both redundant and misleading because stock is a catalogue
            # capability, not an engineering parameter.  Its required plan ID
            # is carried by the no-match segment below so grounding still sees
            # the typed direct-answer item as fulfilled.
            continue
        if limitation.reason_code == "ambiguous_sku_prefix":
            limitation_text = (
                "Указанное сокращение артикула соответствует нескольким товарам "
                "в текущем каталоге. Уточните полный артикул; случайный вариант "
                "я выбирать не буду."
            )
        elif limitation.reason_code == "no_verified_in_stock_contract_match":
            limitation_text = (
                "Среди товаров с подтверждённым наличием не найден вариант, "
                "у которого можно проверить совпадение всех обязательных "
                "параметров. Товары без подтверждённого остатка я не выдаю за "
                "доступные к покупке."
            )
        elif limitation.reason_code == "no_verified_contract_match":
            limitation_text = (
                "По подтверждённым требованиям в каталоге нет товара, у которого "
                "можно проверить совпадение всех обязательных параметров. "
                "Обязательные параметры я не ослаблял и неподтверждённый аналог "
                "не выдаю за подходящий."
            )
        elif limitation.reason_code == "verified_price_source_missing":
            limitation_text = (
                "Точную цену пока нельзя привязать к товару: сначала нужно "
                "определить подходящую модель. После подбора цена будет взята "
                "из её карточки в каталоге; без карточки я не буду подставлять "
                "цену."
            )
        elif limitation.reason_code == "verified_stock_source_missing":
            limitation_text = (
                "Наличие пока нельзя привязать к конкретному товару. После "
                "подбора оно будет проверено по остатку выбранной модели в каталоге."
            )
        elif limitation.reason_code == "verified_link_source_missing":
            limitation_text = (
                "Проверенную ссылку пока нельзя привязать к конкретному товару. "
                "После подбора она будет взята из карточки выбранной модели."
            )
        elif limitation.reason_code == "customer_fact_missing_for_exact_match":
            limitation_text = (
                f"Параметр{fact} пока не указан. Поэтому варианты "
                "предварительные: перед покупкой этот параметр нужно уточнить."
            )
        elif limitation.reason_code in {
            "commerce_blocked",
            "capability_unavailable",
            "delivery_policy_not_configured",
        }:
            limitation_text = (
                "Доставку, её стоимость и срок этот чат подтвердить не может. "
                "Их нужно проверить у сотрудника по выбранному товару, количеству "
                "и пункту назначения."
            )
            if (
                has_confirmed_packaging_coordinates
                and limitation.reason_code
                in {"capability_unavailable", "delivery_policy_not_configured"}
            ):
                limitation_text = (
                    f"{limitation_text} Чат также не подтверждает склад и город "
                    "отгрузки или сборку указанного количества целыми упаковками "
                    "без переупаковки."
                )
        else:
            limitation_text = (
                f"По параметру{fact} {labels[limitation.status]}. "
                "Я не буду подставлять значение без подтверждения."
            )
        existing_limitation_segment = limitation_segments.get(limitation_text)
        if existing_limitation_segment is not None:
            limitation_segments[limitation_text] = existing_limitation_segment.model_copy(
                update={
                    "source_ids": tuple(
                        dict.fromkeys(
                            (
                                *existing_limitation_segment.source_ids,
                                limitation.limitation_id,
                                *limitation.source_ref_ids,
                            )
                        )
                    )
                }
            )
            continue
        limitation_segments[limitation_text] = RenderedSegment(
            segment_id=f"segment_{limitation.limitation_id}",
            kind=RenderedSegmentKind.LIMITATION,
            source_ids=tuple(
                dict.fromkeys(
                    (
                        limitation.limitation_id,
                        *limitation.source_ref_ids,
                        *suppressed_stock_sources_by_no_match.get(
                            limitation.limitation_id, ()
                        ),
                    )
                )
            ),
            text=limitation_text,
            critical_literals=(),
        )
    segments.extend(limitation_segments.values())
    if answer_plan.question is not None:
        question = answer_plan.question
        question_text = (
            f"Уточните, пожалуйста, параметр «{_fact_label(question.fact_name)}» — "
            "он влияет на выбор."
        )
        learn_instruction = _learn_method_instruction(question.learn_method_code)
        if learn_instruction:
            question_text = f"{question_text} {learn_instruction}"
        question_text += _expected_unit_instruction(question.expected_unit)
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{question.question_id}",
                kind=RenderedSegmentKind.QUESTION,
                source_ids=(question.question_id, *question.source_ref_ids),
                text=question_text,
                critical_literals=(),
            )
        )
    next_labels = {
        NextStepKind.PROVIDE_DIRECT_ANSWER: "Выше — подтверждённые данные по вашему прямому вопросу.",
        NextStepKind.ASK_DECISION_FACT: "Для точного следующего шага достаточно одного уточнения.",
        NextStepKind.EXPLAIN_HOW_TO_FIND_FACT: "Подскажу, где посмотреть или как измерить недостающий параметр.",
        NextStepKind.SHOW_PRELIMINARY_OPTIONS: "Выше показаны предварительные варианты: перед покупкой проверьте отмеченные ограничения.",
        NextStepKind.RECOMMEND_ONE: "Выше выделен основной точный вариант; дополнительные карточки — точные альтернативы.",
        NextStepKind.CONTINUE_WITH_CONFIRMED_FACTS: "Подбор выполнен по уже подтверждённым данным.",
        NextStepKind.COMPARE_CANDIDATES: "Сравню подходящие варианты по параметрам, которые влияют на решение.",
        NextStepKind.PRESENT_ANALOG_DIFFERENCES: "Покажу аналоги и отдельно перечислю каждое отличие от исходного товара.",
        NextStepKind.OFFER_VERIFIABLE_EXTERNAL_STEP: "Предложу следующий шаг, выполнение которого можно проверить.",
        NextStepKind.STATE_CAPABILITY_BOUNDARY: (
            "Сейчас могу опираться только на подтверждённые данные; для остального "
            "понадобится уточнение или проверка сотрудником."
        ),
        NextStepKind.EXPLAIN_DECISION_RELEVANCE: "",
        NextStepKind.STATE_COMPATIBILITY_BOUNDARY: "",
        NextStepKind.STATE_INFORMATION_SOURCE_BOUNDARY: "",
        NextStepKind.STATE_INFORMATION_MEANING_BOUNDARY: "",
        NextStepKind.STATE_DETERMINATION_METHOD_BOUNDARY: "",
        NextStepKind.STATE_INFORMATION_VALUE_BOUNDARY: "",
        NextStepKind.REPORT_CANDIDATE_FACTS: "",
        NextStepKind.CLOSE_TASK: "По этой задаче всё подтверждённое уже собрано.",
        NextStepKind.WAIT_FOR_CUSTOMER: "Продолжу, когда вы будете готовы сообщить недостающие данные.",
    }
    next_step_text = next_labels[answer_plan.next_step.kind]
    if (
        answer_plan.next_step.kind
        == NextStepKind.OFFER_VERIFIABLE_EXTERNAL_STEP
        and "explicit_handoff_request" in answer_plan.next_step.reason_codes
    ):
        has_verified_site = any(
            claim.allowed_in_response
            and claim.kind == ClaimKind.CAPABILITY_FACT
            and claim.predicate == "site_url"
            for claim in answer_plan.claims
        )
        next_step_text = (
            "Я не могу напрямую передать этот чат или создать обращение "
            "менеджеру. Откройте указанную официальную ссылку и проверьте "
            "доступные там способы связи."
            if has_verified_site
            else (
                "Я не могу напрямую передать этот чат или создать обращение "
                "менеджеру. В подтверждённых данных этого ответа нет контакта, "
                "который я мог бы безопасно указать."
            )
        )
    no_match_reason_codes = {
        limitation.reason_code
        for limitation in answer_plan.limitations
        if limitation.reason_code
        in {
            "no_verified_contract_match",
            "no_verified_in_stock_contract_match",
        }
    }
    if (
        answer_plan.next_step.kind == NextStepKind.STATE_CAPABILITY_BOUNDARY
        and no_match_reason_codes
    ):
        # A typed no-match is terminal for the *current* hard requirements,
        # not for the customer's whole shopping task.  Give one actionable,
        # permission-preserving continuation instead of inviting an identical
        # search that can only repeat the same result.
        if "no_verified_in_stock_contract_match" in no_match_reason_codes:
            next_step_text = (
                "Повторный поиск с теми же условиями даст тот же результат. "
                "Чтобы продолжить, явно разрешите один из вариантов: показать "
                "точные товары без подтверждённого наличия либо изменить одно "
                "из обязательных технических требований. Без вашего разрешения "
                "я не буду ослаблять эти условия."
            )
        else:
            next_step_text = (
                "Повторный поиск по тем же подтверждённым требованиям даст тот "
                "же результат. Чтобы продолжить, укажите, какое одно обязательное "
                "требование допустимо изменить; без вашего явного разрешения я "
                "не буду ослаблять условия совместимости."
            )
    if answer_plan.next_step.kind == NextStepKind.EXPLAIN_DECISION_RELEVANCE:
        fact_label = _fact_label(answer_plan.next_step.fact_name or "параметр")
        if not answer_plan.next_step.contract_fact_recognized:
            next_step_text = (
                f"Для параметра «{fact_label}» нет подтверждённого правила, "
                "которое объясняет его влияние на выбор. Я не буду придумывать "
                "инженерную причину."
            )
        elif (
            answer_plan.next_step.fact_required_for_exact
            and answer_plan.next_step.fact_decision_changing
        ):
            next_step_text = (
                f"Параметр «{fact_label}» обязателен для точного подбора и может "
                "изменить выбор. Без его "
                "подтверждённого значения точную совместимость подтвердить нельзя."
            )
        elif answer_plan.next_step.fact_required_for_exact:
            next_step_text = (
                f"Параметр «{fact_label}» обязателен для точного подбора. Без его подтверждённого "
                "значения точную совместимость подтвердить нельзя."
            )
        elif answer_plan.next_step.fact_decision_changing:
            next_step_text = (
                f"Параметр «{fact_label}» может изменить выбор. Без его "
                "подтверждённого значения результат "
                "может быть только предварительным, а не подтверждением точной совместимости."
            )
        else:
            next_step_text = (
                f"Для параметра «{fact_label}» нет подтверждённого правила, что "
                "он обязателен для точного подбора или меняет выбор. Более сильную "
                "причину я не буду придумывать."
            )
    elif answer_plan.next_step.kind == NextStepKind.STATE_COMPATIBILITY_BOUNDARY:
        next_step_text = (
            "Точную совместимость по этому информационному запросу подтвердить "
            "нельзя. Для такого подтверждения должны совпасть все обязательные "
            "параметры подбора и подтверждённые характеристики "
            "конкретного товара; отсутствующее или непроверенное значение не "
            "считается совпадением."
        )
    elif answer_plan.next_step.kind == NextStepKind.STATE_INFORMATION_SOURCE_BOUNDARY:
        source_kind = answer_plan.next_step.source_kind
        if source_kind == InformationSourceKind.MANUFACTURER_DOCUMENTATION:
            next_step_text = (
                "В подключённых проверенных источниках нет запрошенного документа "
                "производителя. Карточка магазина и общий сайт не заменяют такой "
                "документ, поэтому я не буду выдавать их за источник производителя."
            )
        elif source_kind == InformationSourceKind.TECHNICAL_DOCUMENTATION:
            next_step_text = (
                "В подключённых проверенных источниках нет запрошенной технической "
                "документации или технического паспорта. Карточка магазина и общий "
                "сайт не заменяют документ производителя, поэтому я не буду "
                "выдавать их за такой источник."
            )
        elif source_kind == InformationSourceKind.CATALOG_PRODUCT_PAGE:
            next_step_text = (
                "В подключённых проверенных источниках нет проверенной ссылки на "
                "карточку точного товара. Я не буду создавать адрес или подменять "
                "его ссылкой на другой товар либо общей страницей каталога."
            )
        elif source_kind == InformationSourceKind.OFFICIAL_BUSINESS_SITE:
            next_step_text = (
                "В подключённых проверенных источниках нет проверенной ссылки на "
                "официальный сайт организации. Я не буду создавать адрес или "
                "подменять его непроверенной ссылкой."
            )
        else:
            next_step_text = (
                "В подключённых проверенных источниках нет запрошенного проверенного "
                "источника. Я не буду создавать ссылку или приписывать непроверенному "
                "материалу происхождение, которого источник не подтверждает."
            )
    elif answer_plan.next_step.kind == NextStepKind.STATE_INFORMATION_MEANING_BOUNDARY:
        fact_label = _fact_label(answer_plan.next_step.fact_name or "параметр")
        next_step_text = (
            f"В подключённых проверенных источниках нет отдельного подтверждённого "
            f"определения параметра «{fact_label}». Я не буду подменять значение "
            "термина инструкцией по измерению или предположением."
        )
    elif answer_plan.next_step.kind == NextStepKind.STATE_DETERMINATION_METHOD_BOUNDARY:
        fact_label = _fact_label(answer_plan.next_step.fact_name or "параметр")
        next_step_text = (
            f"Для параметра «{fact_label}» в проверенных правилах подбора нет "
            "проверенной инструкции по определению. Я не буду придумывать способ измерения."
        )
    elif answer_plan.next_step.kind == NextStepKind.STATE_INFORMATION_VALUE_BOUNDARY:
        fact_label = _fact_label(answer_plan.next_step.fact_name or "параметр")
        next_step_text = (
            f"Подтверждённого значения параметра «{fact_label}» в доступных "
            "проверенных источниках этого ответа нет. Я не буду подставлять "
            "значение или типичное значение без источника."
        )
    elif answer_plan.next_step.kind == NextStepKind.REPORT_CANDIDATE_FACTS:
        report = answer_plan.next_step.candidate_fact_report
        assert report is not None
        lines = [
            f"«{_fact_label(report.fact_name).capitalize()}» по ранее показанным моделям:"
        ]
        critical_literals: list[str] = []
        report_source_ids: list[str] = []
        for item in report.items:
            critical_literals.extend((item.sku, item.name))
            report_source_ids.extend(item.source_ref_ids)
            if item.status == CandidateFactStatus.CONFIRMED:
                rendered_value = _value(item.value, item.fact_name)
                rendered_unit = _unit_suffix(item.value, item.unit)
                lines.append(
                    f"— «{item.name}» (артикул {item.sku}) — "
                    f"{rendered_value}{rendered_unit}."
                )
                raw_value = str(item.value)
                if raw_value in lines[-1]:
                    critical_literals.append(raw_value)
            elif item.status == CandidateFactStatus.AMBIGUOUS:
                lines.append(
                    f"— «{item.name}» (артикул {item.sku}) — "
                    "в данных каталога указано неоднозначно; одно "
                    "значение не подставляю."
                )
            else:
                lines.append(
                    f"— «{item.name}» (артикул {item.sku}) — "
                    "однозначного значения в подключённых данных "
                    "каталога нет."
                )
        next_step_text = "\n".join(lines)
    elif (
        answer_plan.next_step.kind == NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
        and answer_plan.next_step.fact_name
    ):
        learn_instruction = _learn_method_instruction(
            answer_plan.next_step.learn_method_code
        )
        next_step_text = (
            (
                f"Параметр «{_fact_label(answer_plan.next_step.fact_name)}»: "
                "как измерить или проверить. "
                f"{learn_instruction}"
            )
            if learn_instruction
            else (
                "Проверьте параметр "
                f"«{_fact_label(answer_plan.next_step.fact_name)}» по маркировке "
                "или паспорту точного изделия."
            )
        )
        next_step_text += _expected_unit_instruction(
            answer_plan.next_step.expected_unit
        )
    segments.append(
        RenderedSegment(
            segment_id=f"segment_{answer_plan.next_step.next_step_id}",
            kind=RenderedSegmentKind.NEXT_STEP,
            source_ids=(
                answer_plan.next_step.next_step_id,
                *(
                    tuple(dict.fromkeys(report_source_ids))
                    if answer_plan.next_step.kind
                    == NextStepKind.REPORT_CANDIDATE_FACTS
                    else ()
                ),
            ),
            text=next_step_text,
            critical_literals=(
                tuple(dict.fromkeys(critical_literals))
                if answer_plan.next_step.kind
                == NextStepKind.REPORT_CANDIDATE_FACTS
                else ()
            ),
        )
    )
    # Product segments carry the source ids of every grouped price/stock/link
    # claim for grounding.  Those provenance ids must not make a product jump
    # into an earlier ``direct_answer`` or ``confirmed_facts`` section; public
    # product order is the bounded seller shortlist recorded by AnswerPlan.
    # Bind grouped products to their presentation id for layout, while all
    # other segment kinds retain the ordinary source-id lookup.
    by_item_id: dict[str, RenderedSegment] = {}
    for segment in segments:
        if segment.kind == RenderedSegmentKind.PRODUCT:
            product_plan_id = segment.segment_id.removeprefix("segment_")
            by_item_id[product_plan_id] = segment
            continue
        for source_id in segment.source_ids:
            by_item_id[source_id] = segment
    ordered_segments: list[RenderedSegment] = []
    seen_segment_ids: set[str] = set()
    for section in answer_plan.sections:
        for item_id in section.item_ids:
            segment = by_item_id.get(item_id)
            if segment is None or segment.segment_id in seen_segment_ids:
                continue
            seen_segment_ids.add(segment.segment_id)
            ordered_segments.append(segment)
    ordered = tuple(ordered_segments)
    return RenderedAnswer(
        plan_id=answer_plan.plan_id,
        renderer="deterministic",
        segments=ordered,
        text="\n".join(item.text for item in ordered),
    )


def _apply_naturalization_layout(
    fallback: RenderedAnswer,
    layout: NaturalizationLayout,
) -> RenderedAnswer:
    segment_ids = tuple(item.segment_id for item in fallback.segments)
    known_ids = set(segment_ids)
    first_id = segment_ids[0] if segment_ids else None
    seen: set[str] = set()
    by_target = {}
    for transition in layout.transitions:
        target = transition.before_segment_id
        if target not in known_ids:
            raise ValueError(f"unknown transition target: {target}")
        if target == first_id:
            raise ValueError("transition before first segment is not allowed")
        if target in seen:
            raise ValueError(f"duplicate transition target: {target}")
        seen.add(target)
        by_target[target] = transition.style

    rendered: list[RenderedSegment] = []
    for segment in fallback.segments:
        style = by_target.get(segment.segment_id)
        if style is not None:
            rendered.append(
                RenderedSegment(
                    segment_id=f"transition_{segment.segment_id}_{style.value}",
                    kind=RenderedSegmentKind.TRANSITION,
                    source_ids=(),
                    text=_TRANSITION_TEXT[style],
                )
            )
        # Factual, product, limitation, question and next-step segments are
        # copied byte-for-byte from the deterministic renderer. The LLM never
        # receives or rewrites their prose or protected literals.
        rendered.append(segment)
    return RenderedAnswer(
        plan_id=fallback.plan_id,
        renderer="llm",
        segments=tuple(rendered),
        text="\n".join(item.text for item in rendered),
    )


class ResponseRendererV2:
    def __init__(
        self,
        llm_client: OpenRouterClient | Any | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model = model

    def render(
        self,
        answer_plan: AnswerPlan,
        *,
        naturalize: bool = False,
        locale: str = "ru-RU",
    ) -> RenderedAnswerResult:
        started = monotonic()
        fallback = deterministic_render(answer_plan)
        if not naturalize:
            return RenderedAnswerResult(
                status="rendered",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                reason_codes=("deterministic_answer_renderer",),
                latency_ms=int((monotonic() - started) * 1000),
            )
        if self.llm_client is None:
            return RenderedAnswerResult(
                status="fallback",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                llm_requested=False,
                rejection_reason="response_llm_client_unavailable",
                reason_codes=("deterministic_fallback_selected",),
                latency_ms=int((monotonic() - started) * 1000),
            )
        model = self.model or self.llm_client.settings.llm_model_strong
        proposal_fallback = NaturalizationProposal(
            transitions=(),
        )
        payload = {
            "prompt_version": RENDERER_PROMPT_VERSION,
            "locale": locale,
            "segment_outline": [
                {
                    "segment_id": item.segment_id,
                    "kind": item.kind.value,
                }
                for item in fallback.segments
            ],
            "output_schema": NaturalizationProposal.model_json_schema(),
        }
        raw, transported = self.llm_client.complete_json(
            agent="ResponseRendererV2.shadow",
            messages=[
                {"role": "system", "content": RENDERER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            fallback=proposal_fallback.model_dump(mode="json"),
            model=model,
        )
        if not transported:
            return RenderedAnswerResult(
                status="fallback",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                llm_requested=True,
                model=model,
                rejection_reason=(
                    getattr(self.llm_client, "last_fallback_reason", None)
                    or "response_llm_transport_unavailable"
                ),
                reason_codes=("deterministic_fallback_selected",),
                latency_ms=int((monotonic() - started) * 1000),
            )
        try:
            proposal = NaturalizationProposal.model_validate(raw)
            layout = NaturalizationLayout(
                plan_id=answer_plan.plan_id,
                transitions=proposal.transitions,
            )
            rendered = _apply_naturalization_layout(fallback, layout)
        except Exception as exc:
            return RenderedAnswerResult(
                status="fallback",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                llm_requested=True,
                llm_output_accepted=False,
                model=model,
                rejection_reason=f"{type(exc).__name__}: {exc}"[:500],
                reason_codes=("malformed_response_renderer_output", "deterministic_fallback_selected"),
                latency_ms=int((monotonic() - started) * 1000),
            )
        return RenderedAnswerResult(
            status="rendered",
            rendered_answer=rendered,
            deterministic_fallback=fallback,
            llm_requested=True,
            llm_output_accepted=True,
            model=model,
            reason_codes=("structured_response_llm_output",),
            latency_ms=int((monotonic() - started) * 1000),
        )
