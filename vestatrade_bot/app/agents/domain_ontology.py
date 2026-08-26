"""Small semantic ontology for the LLM parser, independent of the feed rows.

Aliases describe language, not catalogue matching rules.  They are supplied to
the shadow interpreter as data so adding a product family does not require a
new dialogue-specific branch or regular expression.
"""

from __future__ import annotations

from typing import Any


PRODUCT_TYPE_ONTOLOGY: tuple[dict[str, Any], ...] = (
    {
        "canonical_type": "circulation_pump",
        "category": "pumps",
        "aliases": ["циркуляционный насос", "насос для циркуляции"],
    },
    {
        "canonical_type": "booster_pump",
        "category": "pumps",
        "aliases": ["повысительный насос", "насос повышения давления"],
    },
    {
        "canonical_type": "borehole_pump",
        "category": "pumps",
        "aliases": ["скважинный насос", "насос для скважины"],
    },
    {
        "canonical_type": "well_pump",
        "category": "pumps",
        "aliases": ["колодезный насос", "насос для колодца"],
    },
    {
        "canonical_type": "drainage_pump",
        "category": "pumps",
        "aliases": ["дренажный насос", "насос для откачки"],
    },
    {
        "canonical_type": "sewage_pump",
        "category": "pumps",
        "aliases": ["канализационный насос", "фекальный насос"],
    },
    {
        "canonical_type": "pipe",
        "category": "pipes",
        "aliases": ["труба", "трубопровод"],
    },
    {
        "canonical_type": "pex_pipe",
        "category": "pipes",
        "aliases": [
            "труба PEX",
            "труба PE-X",
            "труба из сшитого полиэтилена",
        ],
    },
    {
        "canonical_type": "sewer_pipe",
        "category": "sewer",
        "aliases": ["канализационная труба", "труба для канализации"],
    },
    {
        "canonical_type": "elbow",
        "category": "fittings",
        "aliases": ["отвод", "угольник", "колено"],
    },
    {
        "canonical_type": "tee",
        "category": "fittings",
        "aliases": ["тройник", "тройное соединение"],
    },
    {
        "canonical_type": "coupling",
        "category": "fittings",
        "aliases": ["муфта", "соединительная муфта"],
    },
    {
        "canonical_type": "reducer",
        "category": "fittings",
        "aliases": ["переход", "переходник", "переходная муфта"],
    },
    {
        "canonical_type": "ball_valve",
        "category": "valves",
        "aliases": ["шаровый кран", "кран"],
    },
    {
        "canonical_type": "check_valve",
        "category": "valves",
        "aliases": ["обратный клапан", "невозвратный клапан"],
    },
    {
        "canonical_type": "balancing_valve",
        "category": "valves",
        "aliases": ["балансировочный клапан", "балансировочный вентиль"],
    },
    {
        "canonical_type": "three_way_valve",
        "category": "valves",
        "aliases": ["трёхходовой клапан", "трехходовой клапан"],
    },
    {
        "canonical_type": "radiator",
        "category": "radiators",
        "aliases": ["радиатор", "батарея отопления"],
    },
    {
        "canonical_type": "radiator_valve",
        "category": "radiator_fittings",
        "aliases": ["радиаторный клапан", "клапан для радиатора"],
    },
    {
        "canonical_type": "thermostatic_head",
        "category": "radiator_fittings",
        "aliases": ["термоголовка", "термостатическая головка"],
    },
    {
        "canonical_type": "boiler",
        "category": "boilers",
        "aliases": ["котёл", "котел", "отопительный котёл"],
    },
    {
        "canonical_type": "gas_boiler",
        "category": "boilers",
        "aliases": ["газовый котёл", "газовый котел"],
    },
    {
        "canonical_type": "electric_boiler",
        "category": "boilers",
        "aliases": ["электрический котёл", "электрокотёл", "электрокотел"],
    },
    {
        "canonical_type": "water_heater",
        "category": "water_heaters",
        "aliases": ["водонагреватель", "бойлер"],
    },
    {
        "canonical_type": "hydraulic_accumulator",
        "category": "hydraulic_accumulators",
        "aliases": ["гидроаккумулятор", "гидробак", "мембранный бак"],
    },
    {
        "canonical_type": "water_filter",
        "category": "filters",
        "aliases": ["фильтр для воды", "водяной фильтр"],
    },
    {
        "canonical_type": "heating_control",
        "category": "controls",
        "aliases": ["термостат", "терморегулятор", "контроллер отопления"],
    },
    {
        "canonical_type": "meter",
        "category": "meters",
        "aliases": ["счётчик воды", "водомер", "теплосчётчик"],
    },
    {
        "canonical_type": "collector",
        "category": "fittings",
        "aliases": ["коллектор", "гребёнка", "гребенка"],
    },
    {
        "canonical_type": "sanitary_ware",
        "category": "sanitary_ware",
        "aliases": ["сантехника", "унитаз", "раковина"],
    },
    {
        "canonical_type": "installation_system",
        "category": "installation_systems",
        "aliases": ["инсталляция", "система инсталляции"],
    },
)


# This vocabulary is guidance for the semantic model, not a catalogue search
# table.  It keeps fact names stable when the same characteristic is phrased
# differently and lets product modifiers become typed constraints on the same
# turn.  Values still require exact evidence from the current message.
CONSTRAINT_FACT_ONTOLOGY: dict[str, tuple[dict[str, Any], ...]] = {
    "circulation_pump": (
        {
            "name": "diameter_mm",
            "meaning": "nominal pump connection diameter",
            "aliases": ["присоединение", "подключение", "условный проход"],
        },
        {
            "name": "max_head_m",
            "meaning": "explicit maximum/designation pump head, not a working-point head",
            "aliases": ["максимальный напор", "напор в обозначении насоса"],
        },
        {
            "name": "max_flow_l_h",
            "meaning": "explicit maximum pump flow, not a working-point flow",
            "aliases": ["максимальный расход", "максимальная подача"],
        },
        {
            "name": "duty_point_head_m",
            "meaning": "required working-point head at a stated flow; needs a Q-H curve",
            "aliases": ["напор в рабочей точке", "при расходе нужен напор"],
        },
        {
            "name": "duty_point_flow_l_h",
            "meaning": "required working-point flow at a stated head; needs a Q-H curve",
            "aliases": ["расход в рабочей точке", "при напоре нужен расход", "м³/ч при"],
        },
        {
            "name": "mounting_length_mm",
            "meaning": "pump installation length, not pipe length",
            "aliases": ["монтажная длина", "длина насоса"],
        },
        {
            "name": "coolant_type",
            "meaning": "circulated liquid",
            "aliases": ["теплоноситель", "вода", "гликоль", "антифриз"],
            # Closed categorical values are also consumed by the deterministic
            # semantic validator.  The aliases are linguistic evidence for the
            # value, never inferred product/catalogue defaults.
            "closed_values": [
                {
                    "value": "water",
                    "aliases": [
                        "water",
                        "вода",
                        "на воде",
                        "для воды",
                        "водный теплоноситель",
                    ],
                },
                {
                    "value": "propylene_glycol",
                    "aliases": [
                        "propylene glycol",
                        "пропиленгликоль",
                        "пропиленовый гликоль",
                    ],
                },
                {
                    "value": "ethylene_glycol",
                    "aliases": [
                        "ethylene glycol",
                        "этиленгликоль",
                        "этиленовый гликоль",
                    ],
                },
                {
                    "value": "glycol_unspecified",
                    "aliases": ["glycol", "гликоль", "антифриз"],
                },
            ],
        },
        {
            "name": "glycol_concentration_percent",
            "meaning": "explicit glycol concentration",
            "aliases": ["процент гликоля", "концентрация гликоля"],
        },
    ),
    "boiler": (
        {
            "name": "boiler_type",
            "meaning": "energy or fuel type",
            "aliases": ["газовый", "электрический", "твердотопливный"],
            "closed_values": [
                {
                    "value": "gas",
                    "aliases": ["gas", "natural gas", "газ", "газовый"],
                },
                {
                    "value": "electric",
                    "aliases": [
                        "electric",
                        "electricity",
                        "электрический",
                        "электрокотёл",
                        "электрокотел",
                    ],
                },
                {
                    "value": "solid_fuel",
                    "aliases": [
                        "solid fuel",
                        "твердотопливный",
                        "твёрдотопливный",
                    ],
                },
            ],
        },
        {
            "name": "power_kw",
            "meaning": "explicit boiler power in kW",
            "aliases": ["мощность", "кВт"],
        },
        {
            "name": "circuits",
            "meaning": "one heating circuit or heating plus DHW",
            "aliases": ["одноконтурный", "двухконтурный", "отопление и ГВС"],
            "closed_values": [
                {
                    "value": 1,
                    "equivalent_values": [False],
                    "aliases": [
                        "1",
                        "one",
                        "один",
                        "одноконтурный",
                        "один контур",
                        "только отопление",
                        "только для отопления",
                        "single circuit",
                        "heating only",
                    ],
                },
                {
                    "value": 2,
                    "equivalent_values": [True],
                    "aliases": [
                        "2",
                        "two",
                        "два",
                        "двухконтурный",
                        "два контура",
                        "отопление и ГВС",
                        "dual circuit",
                    ],
                },
            ],
        },
        {
            "name": "combustion_chamber",
            "meaning": "open or closed combustion chamber",
            "aliases": ["открытая камера", "закрытая камера"],
            "closed_values": [
                {
                    "value": "open",
                    "aliases": ["open", "открытая", "открытая камера"],
                },
                {
                    "value": "closed",
                    "aliases": ["closed", "закрытая", "закрытая камера"],
                },
            ],
        },
    ),
    "pex_pipe": (
        {
            "name": "diameter_mm",
            "meaning": "explicit outer pipe diameter",
            "aliases": ["PEX 16", "16x2", "диаметр"],
        },
        {
            "name": "requested_quantity_m",
            "meaning": "customer quantity in metres, not product length",
            "aliases": ["нужно метров", "количество метров"],
        },
        {
            "name": "delivery_destination",
            "meaning": "destination for delivery check",
            "aliases": ["доставка в", "объект в"],
        },
    ),
    "water_filter": (
        {
            "name": "filter_method",
            "meaning": "filtration or cleaning method",
            "aliases": ["механический", "сетчатый", "магнитный"],
            "closed_values": [
                {
                    "value": "mechanical",
                    "aliases": [
                        "mechanical",
                        "механический",
                        "сетчатый",
                        "грязевик",
                    ],
                },
                {
                    "value": "magnetic",
                    "aliases": ["magnetic", "магнитный"],
                },
                {
                    "value": "reverse_osmosis",
                    "aliases": ["reverse osmosis", "обратный осмос"],
                },
                {
                    "value": "carbon",
                    "aliases": ["carbon", "угольный"],
                },
                {
                    "value": "softening",
                    "aliases": ["softening", "умягчение", "умягчающий"],
                },
                {
                    "value": "iron_removal",
                    "aliases": [
                        "iron removal",
                        "обезжелезивание",
                        "обезжелезивающий",
                    ],
                },
            ],
        },
        {
            "name": "washable",
            "meaning": "explicit washable or self-cleaning construction",
            "aliases": ["промывной", "самопромывной", "самоочищающийся"],
            "closed_values": [
                {
                    "value": True,
                    "aliases": [
                        "true",
                        "washable",
                        "backwashable",
                        "flushable",
                        "промывной",
                        "самопромывной",
                        "самоочищающийся",
                    ],
                },
                {
                    "value": False,
                    "aliases": [
                        "false",
                        "not washable",
                        "non washable",
                        "без промывки",
                        "не промывной",
                        "непромывной",
                    ],
                },
            ],
        },
        {
            "name": "connection_size",
            "meaning": "nominal connection size",
            "aliases": ["присоединение", "резьба"],
        },
        {
            "name": "micron_rating_um",
            "meaning": "explicit filtration fineness",
            "aliases": ["мкм", "микронность"],
        },
    ),
    "ball_valve": (
        {
            "name": "connection_size",
            "meaning": "nominal thread size such as G1/2",
            "aliases": ["резьба", "G1/2", "размер подключения"],
        },
        {
            "name": "connection_pattern",
            "meaning": "male/female pattern for both ports",
            "aliases": ["ВР-ВР", "ВР-НР", "НР-НР"],
            "closed_values": [
                {
                    "value": "female_female",
                    "aliases": [
                        "female female",
                        "ВР-ВР",
                        "внутренняя/внутренняя",
                    ],
                },
                {
                    "value": "female_male",
                    "aliases": [
                        "female male",
                        "ВР-НР",
                        "внутренняя/наружная",
                    ],
                },
                {
                    "value": "male_female",
                    "aliases": [
                        "male female",
                        "НР-ВР",
                        "наружная/внутренняя",
                    ],
                },
                {
                    "value": "male_male",
                    "aliases": [
                        "male male",
                        "НР-НР",
                        "наружная/наружная",
                    ],
                },
            ],
        },
        {
            "name": "port_count",
            "meaning": "explicit total number of valve ports",
            "aliases": ["трёхходовой", "три присоединения", "два входа и один выход"],
        },
        {
            "name": "inlet_count",
            "meaning": "explicit number of inlet ports",
            "aliases": ["вход", "входа"],
        },
        {
            "name": "outlet_count",
            "meaning": "explicit number of outlet ports",
            "aliases": ["выход", "выхода"],
        },
    ),
}
CONSTRAINT_FACT_ONTOLOGY["gas_boiler"] = CONSTRAINT_FACT_ONTOLOGY["boiler"]
CONSTRAINT_FACT_ONTOLOGY["electric_boiler"] = CONSTRAINT_FACT_ONTOLOGY["boiler"]


# Canonical facts whose values are continuous physical magnitudes and may
# therefore be represented by an explicitly stated numeric interval.  Discrete
# cardinalities such as ``circuits``, ``port_count``, ``inlet_count`` and
# ``outlet_count`` are intentionally absent: a range is not a valid categorical
# value for them.  This public ontology is shared by semantic grounding and the
# deterministic state layer; it contains schema names, never customer phrases.
RANGE_CAPABLE_CONSTRAINT_FACTS: frozenset[str] = frozenset(
    {
        "angle_deg",
        "center_distance_mm",
        "diameter_mm",
        "duty_point_flow_l_h",
        "duty_point_head_m",
        "glycol_concentration_percent",
        "heat_output_w",
        "length_mm",
        "max_flow_l_h",
        "max_head_m",
        "micron_rating_um",
        "mounting_length_mm",
        "operating_pressure_bar",
        "operating_temperature_c",
        "power_kw",
        "requested_quantity_m",
        "secondary_diameter_mm",
        "suction_depth_m",
    }
)


# Capability constraints are not engineering compatibility facts.  Some of
# them nevertheless express durable, product-scoped seller requirements (for
# example "show only items currently in stock").  The ontology says whether a
# capability coordinate must survive as typed dialogue state; catalogue
# readiness still ignores it as a technical product characteristic.
CAPABILITY_CONSTRAINT_ONTOLOGY: tuple[dict[str, Any], ...] = (
    {
        "canonical_name": "stock_availability",
        "name_aliases": [
            "availability",
            "availability_status",
            "stock_availability",
            "stock_available",
            "stock_status",
            "in_stock",
            "is_in_stock",
            "inventory_availability",
        ],
        "action": "check_stock",
        "action_evidence_aliases": [
            "в наличии",
            "из наличия",
            "наличие",
            "наличия",
            "наличию",
            "наличием",
            "на складе",
            "остаток",
            "доступен",
            "доступна",
            "доступно",
            "доступные",
            "доступный",
            "in stock",
            "available",
            "stock",
        ],
        # A persistent required/excluded stock condition is accepted only
        # when its own exact evidence contains one of these declarative
        # availability coordinates.  Negative forms are included because
        # they can explicitly relax a previous in-stock-only requirement;
        # generic existential words such as "есть" are intentionally absent.
        "constraint_evidence_aliases": [
            "в наличии",
            "из наличия",
            "наличие",
            "наличия",
            "наличию",
            "наличием",
            "без наличия",
            "нет в наличии",
            "на складе",
            "остаток",
            "остатки",
            "доступен",
            "доступна",
            "доступно",
            "доступные",
            "доступный",
            "недоступен",
            "недоступна",
            "недоступно",
            "отсутствует",
            "отсутствуют",
            "отсутствующий",
            "отсутствующие",
            "in stock",
            "out of stock",
            "available",
            "unavailable",
            "availability",
            "stock",
        ],
        # These coordinates express a durable selection condition, unlike a
        # neutral request to report stock.  They are deliberately separate
        # from the broader action vocabulary above.
        "required_evidence_aliases": [
            "из наличия",
            "только из наличия",
            "только в наличии",
            "именно в наличии",
            "обязательно в наличии",
            "доступный сейчас",
            "доступные сейчас",
            "in-stock only",
            "only in stock",
            "must be in stock",
        ],
        "negative_state_evidence_aliases": [
            "нет в наличии",
            "не в наличии",
            "без наличия",
            "недоступен",
            "недоступна",
            "недоступно",
            "недоступные",
            "отсутствует",
            "отсутствуют",
            "отсутствующий",
            "отсутствующие",
            "out of stock",
            "unavailable",
        ],
        "positive_values": [
            True,
            1,
            "true",
            "yes",
            "available",
            "in_stock",
            "in stock",
            "в наличии",
            "есть в наличии",
        ],
        "retain_as_typed_requirement": True,
    },
)


def semantic_ontology_payload() -> dict[str, Any]:
    return {
        "product_types": [dict(item) for item in PRODUCT_TYPE_ONTOLOGY],
        "constraint_vocabulary": {
            product_type: [dict(item) for item in definitions]
            for product_type, definitions in CONSTRAINT_FACT_ONTOLOGY.items()
        },
        "capability_constraints": [
            dict(item) for item in CAPABILITY_CONSTRAINT_ONTOLOGY
        ],
        "range_capable_constraint_facts": sorted(
            RANGE_CAPABLE_CONSTRAINT_FACTS
        ),
        "role_meanings": {
            "target": "primary product the customer asks to find or select",
            "context": "system or object that only constrains the target",
            "existing": "product explicitly described as already installed or owned",
            "accessory": "additional item requested for another product",
            "alternative": "replacement or analogue requested for a source item",
        },
    }
