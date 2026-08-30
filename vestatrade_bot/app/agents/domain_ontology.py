"""Small semantic ontology for the LLM parser, independent of the feed rows.

Aliases describe language, not catalogue matching rules.  They are supplied to
the shadow interpreter as data so adding a product family does not require a
new dialogue-specific branch or regular expression.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


PRODUCT_TYPE_ONTOLOGY: tuple[dict[str, Any], ...] = (
    {
        "canonical_type": "circulation_pump",
        "category": "pumps",
        "aliases": [
            "циркуляционный насос",
            "насос для циркуляции",
            "циркуляционник",
            "насос на отопление",
            "насос для радиаторного контура",
        ],
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
        "aliases": [
            "труба",
            "трубопровод",
            "полипропиленовая труба",
            "полипропилен",
            "ППР",
            "PPR",
            "ппэровская",
        ],
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
        "aliases": [
            "канализационная труба",
            "труба для канализации",
            "каналия",
            "труба для стоков",
            "вывод стоков",
        ],
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
        "aliases": [
            "шаровый кран",
            "кран",
            "кран BASE",
            "VALTEC BASE",
            "шаровый BASE",
        ],
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
_COLD_WATER_SERVICE_ALIASES = (
    "cold water",
    "cold water supply",
    "domestic cold water",
    "холодная вода",
    "холодной воды",
    "для холодной воды",
    "для холодной",
    "холодная",
    "холодное водоснабжение",
    "холодного водоснабжения",
    "холодному водоснабжению",
    "холодным водоснабжением",
    "холодном водоснабжении",
    "система холодного водоснабжения",
    "системы холодного водоснабжения",
    "хвс",
)
_HOT_WATER_SERVICE_ALIASES = (
    "hot water",
    "hot water supply",
    "domestic hot water",
    "горячая вода",
    "горячей воды",
    "для горячей воды",
    "для горячей",
    "горячая",
    "горячее водоснабжение",
    "горячего водоснабжения",
    "горячему водоснабжению",
    "горячим водоснабжением",
    "горячем водоснабжении",
    "система горячего водоснабжения",
    "системы горячего водоснабжения",
    "гвс",
)


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
            "aliases": [
                "монтажная длина",
                "длина насоса",
                "по монтажу",
                "между присоединениями",
                "длина между присоединениями",
                "между патрубками",
                "монтажный размер",
                "длина монтажа",
                "по длине монтажа",
            ],
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
            "name": "area_m2",
            "meaning": "heated building area stated by the customer, in square metres",
            "aliases": [
                "площадь",
                "отапливаемая площадь",
                "квадратные метры",
                "квадраты",
                "м2",
                "м²",
            ],
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
                        "горячая вода",
                        "горячей воды",
                        "для горячей воды",
                        "горячее водоснабжение",
                        "нужна горячая вода",
                        "нужна ещё горячая вода",
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
    "radiator": (
        {
            "name": "center_distance_mm",
            "meaning": "radiator centre-to-centre connection distance",
            "aliases": [
                "межосевое расстояние",
                "межосевой размер",
                "между осями",
            ],
        },
        {
            "name": "material",
            "meaning": "radiator body material",
            "aliases": ["материал", "материал радиатора"],
            "closed_values": [
                {
                    "value": "биметалл",
                    "aliases": [
                        "биметалл",
                        "биметаллический",
                        "биметаллическая",
                    ],
                },
                {
                    "value": "aluminium",
                    "aliases": ["aluminium", "aluminum", "алюминий", "алюминиевый"],
                },
                {
                    "value": "сталь",
                    "aliases": ["сталь", "стальной", "стальная"],
                },
            ],
        },
    ),
    "pipe": (
        {
            "name": "diameter_mm",
            "meaning": "explicit outer pipe diameter",
            "aliases": ["диаметр", "наружный диаметр", "размер трубы"],
        },
        {
            "name": "pipe_service",
            "meaning": "declared pipe service, without guessing temperature or pressure",
            "aliases": [
                "назначение трубы",
                "для холодной воды",
                "для горячей воды",
                "для холодной",
                "для горячей",
                "для отопления",
                "application",
                "application_type",
                "water_type",
            ],
            "closed_values": [
                {
                    "value": "cold_water",
                    "aliases": [*_COLD_WATER_SERVICE_ALIASES],
                },
                {
                    "value": "hot_water",
                    "aliases": [*_HOT_WATER_SERVICE_ALIASES],
                },
                {
                    "value": "heating",
                    "aliases": [
                        "heating",
                        "отопление",
                        "для отопления",
                "на батареи",
                "для батарей",
                "радиаторная магистраль",
                "радиаторная разводка",
                "радиаторный контур",
                "отопительный контур",
                "контур с радиаторами",
                "контур радиаторов",
                    ],
                },
            ],
        },
        {
            "name": "operating_temperature_c",
            "meaning": "maximum or working temperature the pipe must withstand",
            "aliases": [
                "operating_temperature_c",
                "max_operating_temperature_c",
                "maximum_operating_temperature_c",
                "working_temperature_c",
                "рабочая температура",
                "максимальная температура",
                "максимальная рабочая температура",
                "температура",
            ],
        },
        {
            "name": "operating_pressure_bar",
            "meaning": "maximum or working pressure the pipe must withstand",
            "aliases": [
                "operating_pressure_bar",
                "max_operating_pressure_bar",
                "maximum_operating_pressure_bar",
                "working_pressure_bar",
                "рабочее давление",
                "максимальное давление",
                "максимальное рабочее давление",
                "давление",
            ],
        },
        {
            "name": "reinforcement",
            "meaning": "explicit pipe reinforcement type or its explicit absence",
            "aliases": [
                "reinforcement",
                "reinforcement_material",
                "армирование",
                "армирована",
                "армированная",
                "армированный",
            ],
            "closed_values": [
                {
                    "value": "glass_fiber",
                    "aliases": [
                        "glass_fiber",
                        "glass fiber",
                        "стекловолокно",
                        "стекловолокном",
                        "со стеклом",
                        "волокно стекла",
                        "волокном стекла",
                        "стекловолоконное армирование",
                        "стекловолоконным армированием",
                        "PP-FIBER",
                        "PP FIBER",
                    ],
                },
                {
                    "value": "aluminium",
                    "aliases": [
                        "aluminium",
                        "aluminum",
                        "алюминий",
                        "алюминием",
                        "фольга",
                        "фольгой",
                        "PP-ALUX",
                        "PP ALUX",
                    ],
                },
                {
                    "value": "unreinforced",
                    "aliases": [
                        "unreinforced",
                        "без армирования",
                        "неармированная",
                        "неармированный",
                    ],
                },
            ],
        },
    ),
    "pex_pipe": (
        {
            "name": "diameter_mm",
            "meaning": "explicit outer pipe diameter",
            "aliases": ["PEX 16", "16x2", "диаметр", "размер трубы"],
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
        {
            "name": "pipe_service",
            "meaning": "declared pipe service, without guessing temperature or pressure",
            "aliases": [
                "назначение трубы",
                "для холодной воды",
                "для горячей воды",
                "для холодной",
                "для горячей",
                "для отопления",
                "application",
                "application_type",
                "water_type",
            ],
            "closed_values": [
                {
                    "value": "cold_water",
                    "aliases": [*_COLD_WATER_SERVICE_ALIASES],
                },
                {
                    "value": "hot_water",
                    "aliases": [*_HOT_WATER_SERVICE_ALIASES],
                },
                {
                    "value": "heating",
                    "aliases": ["heating", "отопление", "для отопления"],
                },
            ],
        },
        {
            "name": "operating_temperature_c",
            "meaning": "maximum or working temperature the pipe must withstand",
            "aliases": [
                "operating_temperature_c",
                "max_operating_temperature_c",
                "maximum_operating_temperature_c",
                "working_temperature_c",
                "рабочая температура",
                "максимальная температура",
                "максимальная рабочая температура",
                "температура",
            ],
        },
        {
            "name": "operating_pressure_bar",
            "meaning": "maximum or working pressure the pipe must withstand",
            "aliases": [
                "operating_pressure_bar",
                "max_operating_pressure_bar",
                "maximum_operating_pressure_bar",
                "working_pressure_bar",
                "рабочее давление",
                "максимальное давление",
                "максимальное рабочее давление",
                "давление",
            ],
        },
        {
            "name": "reinforcement",
            "meaning": "explicit pipe reinforcement type or its explicit absence",
            "aliases": [
                "reinforcement",
                "reinforcement_material",
                "армирование",
                "армирована",
                "армированная",
                "армированный",
            ],
            "closed_values": [
                {
                    "value": "glass_fiber",
                    "aliases": [
                        "glass_fiber",
                        "glass fiber",
                        "стекловолокно",
                        "стекловолокном",
                        "PP-FIBER",
                        "PP FIBER",
                    ],
                },
                {
                    "value": "aluminium",
                    "aliases": [
                        "aluminium",
                        "aluminum",
                        "алюминий",
                        "алюминием",
                        "фольга",
                        "фольгой",
                        "PP-ALUX",
                        "PP ALUX",
                    ],
                },
                {
                    "value": "unreinforced",
                    "aliases": [
                        "unreinforced",
                        "без армирования",
                        "неармированная",
                        "неармированный",
                    ],
                },
            ],
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
            "aliases": [
                "ВР-ВР",
                "ВР-НР",
                "НР-НР",
            ],
            "closed_values": [
                {
                    "value": "female_female",
                    "aliases": [
                        "female female",
                        "ВР-ВР",
                        "ВР/ВР",
                        "вн-вн",
                        "внутренняя/внутренняя",
                        "обе резьбы внутренние",
                        "внутренняя резьба с обеих сторон",
                        "ВР с двух сторон",
                        "ВР с обеих сторон",
                        "оба присоединения с внутренней резьбой",
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
        {
            "name": "handle_type",
            "meaning": "explicit handle wording in the valve title",
            "aliases": ["ручка", "рукоятка", "бабочка", "рычаг"],
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


# Action language belongs to the same versioned semantic registry as products,
# predicates and categorical values.  Downstream planners remain authoritative
# for capability readiness; these aliases only preserve what the customer
# explicitly asked for.
ACTION_ALIAS_ONTOLOGY: tuple[dict[str, Any], ...] = (
    {
        "action": "fact",
        # Bare interrogatives are not an action.  In particular, "какой
        # котёл смотреть" starts a selection and must not be promoted to a
        # direct product fact before a product/predicate exists.
        "aliases": [
            "какая характеристика",
            "какое значение",
            "сколько миллиметров",
            "характеристика",
        ],
    },
    {
        "action": "compare",
        "aliases": [
            "сравни",
            "сравните",
            "чем отличается",
            "чем они отличаются",
            "в чем разница",
            "в чём разница",
            "какие отличия",
            "есть отличия",
            "что лучше",
        ],
    },
    {
        "action": "calculate",
        # This is a total-price phrase, unlike a bare price check.  The
        # calculation executor still requires an explicit quantity and a
        # grounded product scope before it can act.
        "aliases": [
            "посчитай",
            "рассчитай",
            "сколько выйдет",
            "сколько будет стоить",
            "итоговая стоимость",
        ],
    },
    {
        "action": "rationale",
        "aliases": ["почему именно", "обоснуй", "объясни выбор"],
    },
    {
        "action": "compatibility",
        "aliases": [
            "совместим",
            "совместимость",
            "подойдёт ли",
            "подойдет ли",
            "подойдут ли",
            "подойдёт к",
            "подойдет к",
            "подойдут к",
            "подходит к",
            "можно соединить",
            "можно ли соединить",
            "можно состыковать",
            "состыкуется",
            "стыкуется с",
            "сочетается с",
            "будут работать вместе",
        ],
    },
    {
        "action": "project",
        "aliases": ["собери проект", "собрать котельную", "комплект на объект"],
    },
    {
        "action": "show",
        "aliases": [
            "покажи",
            "покажы",
            "покажите",
            "что есть",
            "что можно взять",
            "что можно купить",
            "можно варианты",
            "выдай варианты",
            "подбери доступные",
            "подбери доступные позиции",
            "покажи доступные",
            "покажи доступные позиции",
            "покажи наличие",
            "что доступно",
            "какие есть в наличии",
            "какие позиции подходят",
            "выведи подходящие позиции",
            "какие трубы есть",
        ],
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
        "action_aliases": [dict(item) for item in ACTION_ALIAS_ONTOLOGY],
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


def semantic_ontology_version() -> str:
    """Stable digest used by semantic deltas and diagnostic telemetry."""

    encoded = json.dumps(
        semantic_ontology_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def action_aliases(action: str) -> tuple[str, ...]:
    """Return the one canonical action vocabulary used by prompt and anchors."""

    return tuple(
        str(alias)
        for definition in ACTION_ALIAS_ONTOLOGY
        if definition.get("action") == action
        for alias in definition.get("aliases", ())
    )


def canonical_product_type(value: object) -> tuple[str, str] | None:
    """Resolve one exact ontology product alias to its canonical identity.

    The resolver is intentionally exact after punctuation/spacing
    normalization.  It is shared semantic vocabulary, not fuzzy catalogue
    search, and therefore cannot turn an unrelated phrase into a product.
    """

    normalized = " ".join(
        token
        for token in re.sub(
            r"[^0-9a-zа-яё]+",
            " ",
            str(value or "").casefold().replace("_", " "),
            flags=re.IGNORECASE,
        ).split()
        if token
    )
    if not normalized:
        return None
    for definition in PRODUCT_TYPE_ONTOLOGY:
        canonical = str(definition.get("canonical_type") or "")
        candidates = (canonical, *(definition.get("aliases") or ()))
        for candidate in candidates:
            candidate_normalized = " ".join(
                token
                for token in re.sub(
                    r"[^0-9a-zа-яё]+",
                    " ",
                    str(candidate).casefold().replace("_", " "),
                    flags=re.IGNORECASE,
                ).split()
                if token
            )
            if normalized == candidate_normalized:
                return canonical, str(definition.get("category") or "other")
    return None


def fact_aliases(product_kind: str, predicate: str) -> tuple[str, ...]:
    """Return predicate aliases from the versioned semantic ontology."""

    return tuple(
        str(alias)
        for definition in CONSTRAINT_FACT_ONTOLOGY.get(product_kind, ())
        if definition.get("name") == predicate
        for alias in definition.get("aliases", ())
    )


def closed_value_aliases(
    product_kind: str,
    predicate: str,
    value: object,
) -> tuple[str, ...]:
    """Return aliases for one closed fact value without a parallel dictionary."""

    return tuple(
        str(alias)
        for definition in CONSTRAINT_FACT_ONTOLOGY.get(product_kind, ())
        if definition.get("name") == predicate
        for closed_value in definition.get("closed_values", ())
        if closed_value.get("value") == value
        for alias in closed_value.get("aliases", ())
    )
