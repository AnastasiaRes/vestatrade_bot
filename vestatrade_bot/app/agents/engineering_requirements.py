from __future__ import annotations

from typing import Any

from app.models import (
    IntentResult,
    PendingQuestionState,
    SessionState,
    SlotFillingResult,
    model_to_dict,
)

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
        "hydraulic_accumulators",
        "filters",
        "controls",
        "valves",
        "sewer",
        "radiator_fittings",
        "radiators",
        "fittings",
    }

    # There are deliberately almost no globally shared engineering facts.
    # In particular, ``area_m2`` is category-owned: a boiler's house area is
    # not a warm-floor area and neither value belongs to a pump selection.
    SHARED_KEYS = {
        "project",
    }

    COMMERCIAL_KEYS = {
        "budget_rub",
        "max_price",
        "min_price",
        "in_stock",
    }

    SCOPE_KEYS: dict[str, set[str]] = {
        "heating": {
            "heat_sources",
            "has_gas",
            "has_electricity",
            "needs_hot_water",
        },
        "warm_floor": {
            "has_warm_floor",
            "warm_floor_area_m2",
            "warm_floor_type",
            "warm_floor_heat_source",
            "floor_insulation_ready",
            "warm_floor_automation_needed",
            "warm_floor_pipe_min_m",
            "warm_floor_pipe_max_m",
            "warm_floor_contours",
            "warm_floor_collector_count",
            "engineering_assumptions",
        },
    }

    # Backwards-compatible name for integrations that imported the old
    # constant.  Its meaning is now intentionally narrow.
    GLOBAL_KEYS = SHARED_KEYS

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
            "pressure_class_bar",
            "nominal_diameter_dn",
            "sdr",
            "wall_thickness_mm",
            "oxygen_barrier",
            "reinforcement",
            "thread_standard",
            "thread_gender",
            "press_profile",
            "seal_material",
            "installation_method",
            "required_flow_m3_h",
            "horizontal_run_m",
        },
        "pumps": {
            "engineering_assumptions",
            "pump_use",
            "pump_type",
            "pump_selection_mode",
            "pump_selection_mode_explicit",
            "old_model",
            "head_m",
            "required_head_m",
            "calculated_static_head_m",
            "geometric_lift_m",
            "horizontal_loss_allowance_m",
            "outlet_pressure_head_m",
            "required_head_calculated",
            "head_includes_outlet_pressure",
            "required_flow_m3_h",
            "required_flow_l_min",
            "stated_volume_l",
            "flow_unit_assumed",
            "flow_unit_status",
            "deferred_slot_keys",
            "mounting_length_mm",
            "connection_size",
            "water_source",
            "well_depth_m",
            "explicit_well_depth_m",
            "well_ring_count",
            "water_level_ring_count",
            "water_level_reference",
            "water_level_reference_question_asked",
            "water_level_depth_m",
            "explicit_water_level_depth_m",
            "water_column_ring_count",
            "water_column_depth_m",
            "explicit_water_column_depth_m",
            "ring_height_m",
            "ring_height_assumed",
            "static_water_level_m",
            "dynamic_water_level_m",
            "well_yield_m3_h",
            "casing_diameter_mm",
            "lift_height_m",
            "horizontal_run_m",
            "required_pressure_bar",
            "inlet_pressure_bar",
            "water_quality",
            "solids_mm",
            "system_type",
            "maximum_head_m",
            "maximum_flow_m3_h",
            "input_power_w",
            "shaft_power_w",
            "thread_standard",
            "thread_gender",
            "ip_rating",
            "phase_count",
            "voltage_v",
            "current_type",
            "floors",
        },
        "boilers": {
            "boiler_type",
            "boiler_water_heater_pair",
            "boiler_water_heater_relation",
            "contours",
            "combustion_chamber",
            "needs_chimney",
            "chimney_type",
            "chimney_size",
            "power_kw",
            "area_m2",
            "voltage_v",
            "boiler_requirement",
            "needs_hot_water",
            "has_warm_floor",
            "system_type",
            "phase_count",
            "ip_rating",
            "gas_type",
            "current_type",
            "floors",
            "heat_sources",
            "has_gas",
            "has_electricity",
        },
        "water_heaters": {
            "heater_type",
            "energy_source",
            "volume_l",
            "mounting",
            "orientation",
            "voltage_v",
            "required_flow_l_min",
            "phase_count",
            "ip_rating",
            "heating_element_type",
            "current_type",
        },
        "hydraulic_accumulators": {
            "tank_application",
            "volume_l",
            "orientation",
            "size_inch",
            "connection_size",
            "operating_pressure_bar",
            "pressure_class_bar",
            "nominal_diameter_dn",
            "thread_standard",
            "thread_gender",
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
            "pressure_class_bar",
            "nominal_diameter_dn",
            "thread_standard",
            "thread_gender",
            "flow_coefficient_kind",
            "flow_coefficient",
            "valve_ways",
            "normal_state",
            "differential_pressure_bar",
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
            "sewer_system_code",
            "ring_stiffness_sn",
            "pipe_material",
            "wall_thickness_mm",
        },
        "radiator_fittings": {
            "connection_form",
            "diameter_mm",
            "size_inch",
            "thermostatic_head",
            "union",
            "thread_standard",
            "thread_gender",
            "flow_coefficient_kind",
            "flow_coefficient",
            "valve_ways",
            "normal_state",
            "differential_pressure_bar",
        },
        "radiators": {
            "radiator_type",
            "radiator_size_mm",
            "radiator_height_mm",
            "length_mm",
            "sections",
            "size_inch",
            "area_m2",
            "heat_load_w",
            "connection_form",
            "radiator_panel_type",
            "rating_delta_t_c",
            "heat_output_w",
            "radiator_connection",
        },
        "fittings": {
            "fitting_system",
            "element_type",
            "diameter_mm",
            "secondary_diameter_mm",
            "size_inch",
            "thread_type",
            "pipe_material",
            "pressure_class_bar",
            "nominal_diameter_dn",
            "thread_standard",
            "thread_gender",
            "press_profile",
            "fitting_material",
            "seal_material",
        },
        "filters": {
            "filter_format",
            "filtration_microns",
            "filter_technology",
            "filter_element_type",
            "water_temperature",
            "size_inch",
            "thread_standard",
            "thread_gender",
        },
        "controls": {
            "control_kind",
            "normal_state",
            "control_signal",
            "voltage_v",
            "phase_count",
            "ip_rating",
            "current_type",
        },
    }

    # Warm-floor calculations live in the warm-floor scope and may be restored
    # only together with that scope.  They are not global pipe/boiler facts.

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
            self._restore_referenced_context(
                message,
                category,
                session,
                explicit_slots=intent.slots,
            )
        result = self.slot_filling.fill(message, intent, session)
        if category in self.CATEGORIES:
            self.remember(category, result.slots, session)
        return result

    @staticmethod
    def _present(value: Any) -> bool:
        return value not in (None, "", [], {})

    def _scope_from_slots(
        self,
        slots: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> str | None:
        scope = slots.get("project_scope") or slots.get("scope_funnel")
        if scope:
            return str(scope)
        active_goal = (context or {}).get("active_goal")
        active = ((context or {}).get("goals") or {}).get(active_goal, {})
        if active.get("scope"):
            return str(active["scope"])
        return None

    @staticmethod
    def _goal_variant(category: str, slots: dict[str, Any]) -> str | None:
        if category == "pumps":
            source = normalize_text(str(slots.get("water_source") or ""))
            if "колод" in source or source == "well":
                return "well"
            if "скваж" in source or source == "borehole":
                return "borehole"
            if any(
                marker in source
                for marker in ["емкост", "боч", "бак", "tank", "barrel"]
            ):
                return "tank"
            pump_use = normalize_text(str(slots.get("pump_use") or ""))
            if "отоплен" in pump_use:
                return "heating"
            if "давлен" in pump_use:
                return "pressure"
        if category == "boilers":
            boiler_type = normalize_text(str(slots.get("boiler_type") or ""))
            if "электр" in boiler_type or boiler_type == "electric":
                return "electric"
            if "газ" in boiler_type or boiler_type == "gas":
                return "gas"
        if category in {"pipes", "controls"}:
            scope = normalize_text(
                str(slots.get("project_scope") or slots.get("scope_funnel") or "")
            )
            if scope == "warm_floor" or slots.get("has_warm_floor") is True:
                return "warm_floor"
        return None

    def goal_id_for(self, category: str, slots: dict[str, Any]) -> str:
        variant = self._goal_variant(category, slots)
        return f"{category}:{variant}" if variant else category

    def _normalise_context(self, session: SessionState) -> dict[str, Any]:
        """Return the v2 goal memory, migrating the old category map lazily."""

        context = dict(session.project_context or {})
        goals = {
            str(key): dict(value)
            for key, value in dict(context.get("goals") or {}).items()
            if isinstance(value, dict)
        }
        category_last_goal = dict(context.get("category_last_goal") or {})
        legacy_categories = dict(context.get("categories") or {})
        shared_by_scope = dict(context.get("shared_by_scope") or {})
        for category, values in legacy_categories.items():
            if not isinstance(values, dict):
                continue
            scope = values.get("project_scope") or values.get("scope_funnel")
            allowed = (
                self.SHARED_KEYS
                | self.COMMERCIAL_KEYS
                | self.CATEGORY_KEYS.get(category, set())
            )
            migrated_slots = {
                key: value
                for key, value in values.items()
                if key in allowed and self._present(value)
            }
            if scope:
                scope_values = {
                    key: value
                    for key, value in values.items()
                    if key in self.SCOPE_KEYS.get(str(scope), set())
                    and self._present(value)
                }
                shared_by_scope[str(scope)] = merge_slots(
                    dict(shared_by_scope.get(str(scope)) or {}),
                    scope_values,
                )
            goal_id = str(
                category_last_goal.get(category) or self.goal_id_for(category, values)
            )
            goals.setdefault(
                goal_id,
                {
                    "category": category,
                    "scope": scope,
                    "slots": migrated_slots,
                    "pending": None,
                },
            )
            category_last_goal.setdefault(category, goal_id)

        active_category = context.get("active_category") or session.category
        active_goal = context.get("active_goal")
        if not active_goal and active_category:
            active_goal = category_last_goal.get(active_category)

        context.update(
            {
                "version": 2,
                "goals": goals,
                "category_last_goal": category_last_goal,
                "active_goal": active_goal,
                "active_category": active_category,
                "categories": legacy_categories,
                "known_facts": {
                    key: value
                    for key, value in dict(context.get("known_facts") or {}).items()
                    if key in self.SHARED_KEYS and self._present(value)
                },
                "shared_by_scope": shared_by_scope,
            }
        )
        return context

    def _pending_payload(self, session: SessionState) -> dict[str, Any] | None:
        pending = session.sync_pending_question_state()
        return model_to_dict(pending) if pending else None

    def _snapshot_pending_on_active_goal(
        self,
        context: dict[str, Any],
        session: SessionState,
    ) -> None:
        active_goal = context.get("active_goal")
        goals = dict(context.get("goals") or {})
        if active_goal and active_goal in goals:
            goal = dict(goals[active_goal])
            goal["pending"] = self._pending_payload(session)
            goals[active_goal] = goal
            context["goals"] = goals

    def remember(
        self,
        category: str,
        slots: dict[str, Any],
        session: SessionState,
    ) -> None:
        if category not in self.CATEGORIES:
            return
        context = self._normalise_context(session)
        goals = dict(context.get("goals") or {})
        active_goal_id = context.get("active_goal")
        active_goal = dict(goals.get(active_goal_id) or {})
        requested_goal_id = self.goal_id_for(category, slots)
        if active_goal.get("category") == category:
            if ":" in requested_goal_id and requested_goal_id != active_goal_id:
                goal_id = requested_goal_id
                active_goal = dict(goals.get(goal_id) or {})
            else:
                goal_id = str(active_goal_id)
        else:
            goal_id = requested_goal_id
            active_goal = dict(goals.get(goal_id) or {})

        scope = self._scope_from_slots(slots, context)
        previous = dict(active_goal.get("slots") or {})
        allowed = (
            self.SHARED_KEYS
            | self.COMMERCIAL_KEYS
            | self.CATEGORY_KEYS.get(category, set())
        )
        incoming = {
            key: value
            for key, value in slots.items()
            if key in allowed and self._present(value)
        }
        goal_slots = merge_slots(previous, incoming)

        scope_values: dict[str, Any] = {}
        if scope:
            scope_allowed = self.SCOPE_KEYS.get(scope, set())
            scope_values = {
                key: value
                for key, value in slots.items()
                if key in scope_allowed and self._present(value)
            }
            shared_by_scope = dict(context.get("shared_by_scope") or {})
            shared_by_scope[scope] = merge_slots(
                dict(shared_by_scope.get(scope) or {}),
                scope_values,
            )
            context["shared_by_scope"] = shared_by_scope

        active_goal = {
            "category": category,
            "scope": scope,
            "slots": goal_slots,
            "pending": self._pending_payload(session),
        }
        goals[goal_id] = active_goal
        context["goals"] = goals
        category_last_goal = dict(context.get("category_last_goal") or {})
        category_last_goal[category] = goal_id
        context["category_last_goal"] = category_last_goal
        context["active_goal"] = goal_id
        context["active_category"] = category

        # Compatibility view used by existing diagnostics and tests.  It is a
        # view of the category's latest goal, never a union of unrelated goals.
        categories = dict(context.get("categories") or {})
        categories[category] = merge_slots(goal_slots, scope_values)
        context["categories"] = categories
        context["known_facts"] = {
            key: value
            for key, value in merge_slots(
                dict(context.get("known_facts") or {}),
                {key: value for key, value in slots.items() if key in self.SHARED_KEYS},
            ).items()
            if self._present(value)
        }
        session.project_context = context

    def activate_goal(
        self,
        message: str,
        category: str,
        session: SessionState,
        *,
        explicit_slots: dict[str, Any] | None = None,
        returning: bool | None = None,
    ) -> dict[str, Any]:
        """Switch active engineering goal without leaking the old slot map.

        A normal explicit switch starts a fresh goal.  A turn containing a
        return marker restores the last goal for that category, including its
        machine-readable pending question.  Current-turn explicit facts always
        win over restored facts.
        """

        explicit = dict(explicit_slots or {})
        context = self._normalise_context(session)
        old_category = session.category or context.get("active_category")
        if old_category in self.CATEGORIES and session.slots:
            self.remember(str(old_category), dict(session.slots), session)
            context = self._normalise_context(session)
        else:
            self._snapshot_pending_on_active_goal(context, session)

        is_return = (
            any(marker in normalize_text(message) for marker in self.RETURN_MARKERS)
            if returning is None
            else returning
        )
        goals = dict(context.get("goals") or {})
        category_last_goal = dict(context.get("category_last_goal") or {})
        if is_return:
            selector = dict(explicit)
            text = normalize_text(message)
            if category == "pumps":
                if "колод" in text:
                    selector.setdefault("water_source", "колодец")
                    explicit.setdefault("water_source", "колодец")
                elif "скваж" in text:
                    selector.setdefault("water_source", "скважина")
                    explicit.setdefault("water_source", "скважина")
                elif any(marker in text for marker in ["емкост", "боч", "бак"]):
                    selector.setdefault("water_source", "ёмкость")
                    explicit.setdefault("water_source", "ёмкость")
                elif "водопровод" in text or (
                    "центральн" in text and "вод" in text
                ):
                    selector.setdefault("water_source", "центральный водопровод")
                    selector.setdefault("pump_use", "повышение давления")
                    explicit.setdefault("water_source", "центральный водопровод")
                    explicit.setdefault("pump_use", "повышение давления")
                elif "повысит" in text or "давлен" in text:
                    selector.setdefault("pump_use", "повышение давления")
                    explicit.setdefault("pump_use", "повышение давления")
            elif category == "boilers":
                if "электр" in text:
                    selector.setdefault("boiler_type", "электрический")
                    explicit.setdefault("boiler_type", "электрический")
                elif "газ" in text:
                    selector.setdefault("boiler_type", "газовый")
                    explicit.setdefault("boiler_type", "газовый")
            elif category == "pipes" and "тепл" in text and "пол" in text:
                selector.setdefault("project_scope", "warm_floor")
                selector.setdefault("has_warm_floor", True)
                explicit.setdefault("project_scope", "warm_floor")
                explicit.setdefault("has_warm_floor", True)
            referenced_goal_id = self.goal_id_for(category, selector)
            explicit_variant = ":" in referenced_goal_id
            goal_id = str(
                referenced_goal_id
                if explicit_variant
                else category_last_goal.get(category)
                or referenced_goal_id
            )
            goal = dict(goals.get(goal_id) or {})
            restored = dict(goal.get("slots") or {})
            scope = goal.get("scope") or self._scope_from_slots(explicit)
            if scope:
                restored = merge_slots(
                    dict((context.get("shared_by_scope") or {}).get(scope) or {}),
                    restored,
                )
                restored.setdefault("project_scope", scope)
                restored.setdefault("scope_funnel", scope)
            session.slots = merge_slots(restored, explicit)
            pending_data = goal.get("pending")
            if isinstance(pending_data, dict) and pending_data.get("question_id"):
                pending = PendingQuestionState(**pending_data)
                session.set_pending_question_state(
                    text=pending.text,
                    expected_slots=pending.expected_slots,
                    question_id=pending.question_id,
                    category=pending.category,
                    intent_type=pending.intent_type,
                    attempts=pending.attempts,
                )
                reconciled_pending = session.sync_pending_question_state()
                goal["pending"] = (
                    model_to_dict(reconciled_pending)
                    if reconciled_pending
                    else None
                )
                goals[goal_id] = goal
                if reconciled_pending:
                    session.slots["_pending_just_restored"] = True
            else:
                session.clear_pending_question_state()
            if goal_id not in goals:
                allowed = (
                    self.SHARED_KEYS
                    | self.COMMERCIAL_KEYS
                    | self.CATEGORY_KEYS.get(category, set())
                )
                goals[goal_id] = {
                    "category": category,
                    "scope": scope,
                    "slots": {
                        key: value
                        for key, value in explicit.items()
                        if key in allowed and self._present(value)
                    },
                    "pending": None,
                }
        else:
            goal_id = self.goal_id_for(category, explicit)
            # A newly selected category starts outside the previous branch's
            # scope unless the current turn names its own scope explicitly.
            # Otherwise a well-pump goal can inherit ``warm_floor`` merely
            # because the user switched tasks from a floor calculation.
            scope = self._scope_from_slots(explicit)
            session.slots = explicit
            session.clear_pending_question_state()
            goals[goal_id] = {
                "category": category,
                "scope": scope,
                "slots": {},
                "pending": None,
            }

        context["goals"] = goals
        category_last_goal[category] = goal_id
        context["category_last_goal"] = category_last_goal
        context["active_goal"] = goal_id
        context["active_category"] = category
        session.project_context = context
        session.category = category
        return dict(session.slots)

    def set_pending_question(
        self,
        session: SessionState,
        *,
        question_id: str,
        text: str,
        expected_slots: list[str],
        category: str | None = None,
        intent_type: str | None = None,
        repeated: bool = False,
    ) -> PendingQuestionState:
        previous = session.pending_question_state
        attempts = (
            previous.attempts + 1
            if repeated and previous and previous.question_id == question_id
            else 0
        )
        pending = session.set_pending_question_state(
            text=text,
            expected_slots=expected_slots,
            question_id=question_id,
            category=category,
            intent_type=intent_type,
            attempts=attempts,
        )
        context = self._normalise_context(session)
        self._snapshot_pending_on_active_goal(context, session)
        session.project_context = context
        return pending

    def clear_pending_question(self, session: SessionState) -> None:
        session.clear_pending_question_state()
        context = self._normalise_context(session)
        self._snapshot_pending_on_active_goal(context, session)
        session.project_context = context

    def sync_pending_question(
        self, session: SessionState
    ) -> PendingQuestionState | None:
        pending = session.sync_pending_question_state()
        context = self._normalise_context(session)
        self._snapshot_pending_on_active_goal(context, session)
        session.project_context = context
        return pending

    def _restore_referenced_context(
        self,
        message: str,
        category: str,
        session: SessionState,
        *,
        explicit_slots: dict[str, Any] | None = None,
    ) -> None:
        text = normalize_text(message)
        if not any(marker in text for marker in self.RETURN_MARKERS):
            return
        self.activate_goal(
            message,
            category,
            session,
            explicit_slots=explicit_slots,
            returning=True,
        )

    def summary(self, session: SessionState) -> str:
        context = self._normalise_context(session)
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
