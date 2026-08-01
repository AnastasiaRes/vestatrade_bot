from __future__ import annotations

from math import ceil
from typing import Any


DEFAULT_WELL_RING_HEIGHT_M = 0.9

# These values are never authoritative when they come from a language model.
# They are produced from the raw, user-supplied facts below by deterministic
# code so a syntactically valid but numerically wrong JSON response cannot
# poison the session.
DERIVED_ENGINEERING_SLOTS = {
    "well_depth_m",
    "water_level_depth_m",
    "dynamic_water_level_m",
    "water_column_depth_m",
    "required_flow_m3_h",
    "required_head_m",
    "calculated_static_head_m",
    "warm_floor_pipe_min_m",
    "warm_floor_pipe_max_m",
    "warm_floor_contours",
    "warm_floor_collector_count",
}


def normalize_engineering_slots(raw_slots: dict[str, Any]) -> dict[str, Any]:
    """Return canonical engineering facts with all safe derived values rebuilt.

    The function intentionally performs no hydraulic design.  It only applies
    explicit household-unit conversions and the agreed warm-floor sizing
    formula.  Values such as a final pump head remain an engineer/user input.
    """

    slots = dict(raw_slots)
    _normalize_warm_floor(slots)
    _normalize_well_rings(slots)
    _normalize_flow(slots)
    _normalize_required_head(slots)
    return slots


def _normalize_warm_floor(slots: dict[str, Any]) -> None:
    area = _positive_float(slots.get("warm_floor_area_m2"))
    scope = str(slots.get("project_scope") or slots.get("scope_funnel") or "")
    if area is None and scope == "warm_floor":
        area = _positive_float(slots.get("area_m2"))
    if area is None:
        return

    area_value = _compact_number(area)
    slots["warm_floor_area_m2"] = area_value
    # ``area_m2`` remains a compatibility view of the active warm-floor goal;
    # project memory keeps it scoped and will not share it with a boiler goal.
    if scope == "warm_floor":
        slots["area_m2"] = area_value
    slots["warm_floor_pipe_min_m"] = round(area * 6.5)
    slots["warm_floor_pipe_max_m"] = round(area * 7.0)
    contours = ceil(area * 6.5 / 80.0)
    slots["warm_floor_contours"] = contours
    slots["warm_floor_collector_count"] = ceil(contours / 12.0)


def _normalize_well_rings(slots: dict[str, Any]) -> None:
    ring_height = _positive_float(slots.get("ring_height_m"))
    if ring_height is None:
        ring_height = DEFAULT_WELL_RING_HEIGHT_M

    explicit_well_depth = _positive_float(slots.get("explicit_well_depth_m"))
    well_rings = _positive_float(slots.get("well_ring_count"))
    if explicit_well_depth is not None:
        slots["well_depth_m"] = round(explicit_well_depth, 3)
    elif well_rings is not None:
        slots["well_depth_m"] = round(well_rings * ring_height, 3)
        if slots.get("ring_height_m") is None:
            slots["ring_height_assumed"] = True

    reference = str(slots.get("water_level_reference") or "").strip().lower()
    if reference and reference != "ambiguous":
        slots.pop("water_level_reference_question_asked", None)
    level_rings = _positive_float(slots.get("water_level_ring_count"))
    column_rings = _positive_float(slots.get("water_column_ring_count"))
    well_depth = _positive_float(slots.get("well_depth_m"))
    explicit_level_depth = _positive_float(
        slots.get("explicit_water_level_depth_m")
    )
    explicit_column_depth = _positive_float(
        slots.get("explicit_water_column_depth_m")
    )

    if explicit_level_depth is not None:
        slots["water_level_reference"] = "from_top"
        slots["water_level_depth_m"] = round(explicit_level_depth, 3)
        slots["dynamic_water_level_m"] = round(explicit_level_depth, 3)
        if well_depth is not None and well_depth >= explicit_level_depth:
            slots["water_column_depth_m"] = round(
                well_depth - explicit_level_depth,
                3,
            )
        return
    if explicit_column_depth is not None:
        slots["water_level_reference"] = "from_bottom"
        slots["water_column_depth_m"] = round(explicit_column_depth, 3)
        slots.pop("dynamic_water_level_m", None)
        if well_depth is not None and well_depth >= explicit_column_depth:
            slots["water_level_depth_m"] = round(
                well_depth - explicit_column_depth,
                3,
            )
        return

    if reference == "ambiguous" and level_rings is not None:
        # Do not silently guess whether the customer counted from the top or
        # from the bottom.  Remove stale values left by an earlier guess.
        slots.pop("water_level_depth_m", None)
        slots.pop("water_column_depth_m", None)
        slots.pop("dynamic_water_level_m", None)
        return

    if reference == "from_top" and level_rings is not None:
        level_depth = round(level_rings * ring_height, 3)
        slots["water_level_depth_m"] = level_depth
        # Compatibility for the established pump-selection filters.  The raw
        # LLM value is never trusted; this value is rebuilt from the ring count.
        slots["dynamic_water_level_m"] = level_depth
        if well_depth is not None and well_depth >= level_depth:
            column_depth = round(well_depth - level_depth, 3)
            slots["water_column_depth_m"] = column_depth
            slots["water_column_ring_count"] = _compact_number(
                column_depth / ring_height
            )
    elif reference == "from_bottom" and level_rings is not None:
        slots.pop("dynamic_water_level_m", None)
        column_depth = round(level_rings * ring_height, 3)
        slots["water_column_ring_count"] = _compact_number(level_rings)
        slots["water_column_depth_m"] = column_depth
        if well_depth is not None and well_depth >= column_depth:
            slots["water_level_depth_m"] = round(well_depth - column_depth, 3)
    elif column_rings is not None:
        column_depth = round(column_rings * ring_height, 3)
        slots["water_column_depth_m"] = column_depth
        if well_depth is not None and well_depth >= column_depth:
            slots["water_level_depth_m"] = round(well_depth - column_depth, 3)


def _normalize_flow(slots: dict[str, Any]) -> None:
    litres_per_minute = _positive_float(slots.get("required_flow_l_min"))
    status = str(slots.get("flow_unit_status") or "").strip().lower()
    if status == "total_volume":
        if litres_per_minute is not None:
            slots["stated_volume_l"] = _compact_number(litres_per_minute)
        slots.pop("required_flow_l_min", None)
        slots.pop("required_flow_m3_h", None)
        slots.pop("flow_unit_assumed", None)
        return
    if litres_per_minute is None:
        return
    if slots.get("flow_unit_assumed") is True and not status:
        status = "assumed"
        slots["flow_unit_status"] = status
    if status in {"assumed", "confirmed_per_minute"} or slots.get(
        "flow_unit_assumed"
    ) is True:
        slots["required_flow_m3_h"] = round(litres_per_minute * 60.0 / 1000.0, 4)


def _normalize_required_head(slots: dict[str, Any]) -> None:
    """Build a transparent preliminary head from confirmed well geometry."""

    source = str(slots.get("water_source") or "").strip().lower()
    if "колод" not in source and source != "well":
        return
    explicit_head = _positive_float(slots.get("head_m"))
    if explicit_head is not None:
        slots["required_head_m"] = round(explicit_head, 3)
        slots["required_head_calculated"] = False
        return
    existing_head = _positive_float(slots.get("required_head_m"))
    if existing_head is not None and slots.get("required_head_calculated") is not True:
        return

    water_level = _positive_float(
        slots.get("water_level_depth_m")
        or slots.get("dynamic_water_level_m")
        or slots.get("static_water_level_m")
    )
    horizontal = _nonnegative_float(slots.get("horizontal_run_m"))
    lift = _nonnegative_float(slots.get("lift_height_m"))
    if water_level is None or horizontal is None or lift is None:
        if slots.get("required_head_calculated") is True:
            slots.pop("required_head_m", None)
            slots.pop("calculated_static_head_m", None)
            slots.pop("head_includes_outlet_pressure", None)
        return

    static_head = water_level + lift + horizontal / 10.0
    pressure_bar = _nonnegative_float(slots.get("required_pressure_bar"))
    required_head = static_head + (pressure_bar or 0.0) * 10.0
    slots["calculated_static_head_m"] = round(static_head, 3)
    slots["required_head_m"] = round(required_head, 3)
    slots["required_head_calculated"] = True
    slots["head_includes_outlet_pressure"] = pressure_bar is not None


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _compact_number(number: float) -> int | float:
    return int(number) if float(number).is_integer() else round(number, 4)
