from __future__ import annotations

from typing import Any

from app.models import IntentResult, SessionState, SlotFillingResult

from .slot_filling import SlotFillingAgent
from .utils import merge_slots, normalize_text


class EngineeringRequirementsAgent:
    """Deterministic pre-flight for engineering selections.

    A free-form LLM is useful for explaining choices, but it must not decide
    whether enough hydraulic/compatibility data has been collected.  This
    agent owns that gate and keeps a structured, per-category memory that
    survives changes of the active product category.
    """

    CATEGORIES = {
        "pipes",
        "pumps",
        "boilers",
        "water_heaters",
        "valves",
        "sewer",
        "radiator_fittings",
        "radiators",
        "fittings",
    }

    GLOBAL_KEYS = {
        "area_m2",
        "floors",
        "budget_rub",
        "max_price",
        "min_price",
        "in_stock",
        "project",
        "project_scope",
        "water_source",
        "system_type",
        "heat_sources",
        "has_gas",
        "has_electricity",
    }

    CATEGORY_KEYS: dict[str, set[str]] = {
        "pipes": {
            "pipe_purpose",
            "pipe_service",
            "water_temperature",
            "pipe_material",
            "diameter_mm",
            "total_length_m",
            "operating_temperature_c",
            "operating_pressure_bar",
            "installation_method",
            "required_flow_m3_h",
            "horizontal_run_m",
        },
        "pumps": {
            "pump_use",
            "pump_type",
            "pump_selection_mode",
            "pump_selection_mode_explicit",
            "old_model",
            "head_m",
            "required_head_m",
            "required_flow_m3_h",
            "mounting_length_mm",
            "connection_size",
            "water_source",
            "well_depth_m",
            "static_water_level_m",
            "dynamic_water_level_m",
            "lift_height_m",
            "horizontal_run_m",
            "required_pressure_bar",
            "inlet_pressure_bar",
            "water_quality",
            "solids_mm",
            "system_type",
        },
        "boilers": {
            "boiler_type",
            "contours",
            "power_kw",
            "area_m2",
            "voltage_v",
            "boiler_requirement",
        },
        "water_heaters": {
            "heater_type",
            "energy_source",
            "volume_l",
            "mounting",
            "orientation",
            "voltage_v",
            "required_flow_l_min",
        },
        "valves": {
            "application",
            "water_temperature",
            "valve_kind",
            "diameter_mm",
            "size_inch",
            "connection_size",
            "thread_type",
            "body_form",
            "union",
            "operating_temperature_c",
            "operating_pressure_bar",
        },
        "sewer": {
            "sewer_scope",
            "element_type",
            "diameter_mm",
            "secondary_diameter_mm",
            "length_mm",
            "total_length_m",
            "angle_deg",
            "coupling_type",
        },
        "radiator_fittings": {
            "connection_form",
            "diameter_mm",
            "size_inch",
            "thermostatic_head",
            "union",
        },
        "radiators": {
            "radiator_type",
            "radiator_size_mm",
            "length_mm",
            "sections",
            "size_inch",
            "area_m2",
            "heat_load_w",
            "connection_form",
        },
        "fittings": {
            "fitting_system",
            "element_type",
            "diameter_mm",
            "secondary_diameter_mm",
            "size_inch",
            "thread_type",
            "pipe_material",
        },
    }

    RETURN_MARKERS = (
        "вернемся",
        "вернёмся",
        "как обсуждали",
        "как говорили",
        "к предыдущ",
        "те же",
        "тот же",
        "продолжим",
        "по прежн",
    )

    def __init__(self, slot_filling: SlotFillingAgent | None = None) -> None:
        self.slot_filling = slot_filling or SlotFillingAgent()

    def assess(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> SlotFillingResult:
        category = intent.category if intent.category != "other" else session.category
        if category in self.CATEGORIES:
            self._restore_referenced_context(message, category, session)
        result = self.slot_filling.fill(message, intent, session)
        if category in self.CATEGORIES:
            self.remember(category, result.slots, session)
        return result

    def remember(
        self,
        category: str,
        slots: dict[str, Any],
        session: SessionState,
    ) -> None:
        context = dict(session.project_context or {})
        categories = dict(context.get("categories") or {})
        previous = dict(categories.get(category) or {})
        allowed = self.GLOBAL_KEYS | self.CATEGORY_KEYS.get(category, set())
        incoming = {
            key: value
            for key, value in slots.items()
            if key in allowed and value not in (None, "", [], {})
        }
        categories[category] = merge_slots(previous, incoming)
        context["categories"] = categories
        context["active_category"] = category
        context["known_facts"] = {
            key: value
            for key, value in merge_slots(
                dict(context.get("known_facts") or {}),
                {key: value for key, value in slots.items() if key in self.GLOBAL_KEYS},
            ).items()
            if value not in (None, "", [], {})
        }
        session.project_context = context

    def _restore_referenced_context(
        self,
        message: str,
        category: str,
        session: SessionState,
    ) -> None:
        text = normalize_text(message)
        if not any(marker in text for marker in self.RETURN_MARKERS):
            return
        saved = (
            (session.project_context or {}).get("categories", {}).get(category, {})
        )
        if saved:
            session.slots = merge_slots(saved, session.slots)

    def summary(self, session: SessionState) -> str:
        context = session.project_context or {}
        categories = context.get("categories") or {}
        parts: list[str] = []
        known = context.get("known_facts") or {}
        if known:
            parts.append(
                "общие факты: "
                + ", ".join(f"{key}={value}" for key, value in known.items())
            )
        for category, values in categories.items():
            if not values:
                continue
            facts = ", ".join(f"{key}={value}" for key, value in values.items())
            parts.append(f"{category}: {facts}")
        return "; ".join(parts)
