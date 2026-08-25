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


def semantic_ontology_payload() -> dict[str, Any]:
    return {
        "product_types": [dict(item) for item in PRODUCT_TYPE_ONTOLOGY],
        "role_meanings": {
            "target": "primary product the customer asks to find or select",
            "context": "system or object that only constrains the target",
            "existing": "product explicitly described as already installed or owned",
            "accessory": "additional item requested for another product",
            "alternative": "replacement or analogue requested for a source item",
        },
    }
