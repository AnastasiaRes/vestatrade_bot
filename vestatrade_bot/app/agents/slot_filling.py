from __future__ import annotations

import re

from app.models import IntentResult, SessionState, SlotFillingResult

from .engineering_calculations import normalize_engineering_slots
from .numeric_semantics import extract_total_length_m as parse_total_length_m
from .selection_contracts import (
    GENERIC_RADIATOR_FITTING_CONTRACT,
    RADIATOR_VALVE_CONTRACT,
    THERMOSTATIC_HEAD_CONTRACT,
    THREADED_BALL_VALVE_CONTRACT,
    VALVE_BASE_CONTRACT,
    missing_requirements,
    observable_selection_guidance,
)
from .slot_answer_resolver import bind_local_refusals
from .trade_vocabulary import is_system_agnostic_element
from .utils import mentions_water_application, merge_slots, normalize_text


class SlotFillingAgent:
    _UNKNOWN_PARAMETER_PATTERNS: dict[str, dict[str, re.Pattern[str]]] = {
        "pipes": {
            "operating_temperature_c": re.compile(
                r"\b(?:температур|нагрев|режим)\w*"
            ),
            "operating_pressure_bar": re.compile(
                r"\b(?:давлен|опрессов|бар)\w*"
            ),
            "diameter_mm": re.compile(r"\b(?:диаметр|размер|маркиров|надпис)\w*"),
            "pipe_material": re.compile(r"\b(?:материал|тип|систем)\w*"),
        },
        "fittings": {
            "fitting_system": re.compile(r"\b(?:систем|материал\w*\s+труб)\w*"),
            "element_type": re.compile(r"\b(?:фитинг|детал|элемент)\w*"),
            "diameter_mm": re.compile(r"\b(?:диаметр|размер|маркиров|надпис)\w*"),
            "secondary_diameter_mm": re.compile(
                r"\b(?:втор\w*\s+диаметр|диаметр\w*\s+(?:переход|ответв)|"
                r"ответвлен|боков\w*\s+(?:выход|отвод)|с\s+какого\s+на\s+какой)\w*"
            ),
            "size_inch": re.compile(r"\b(?:дюйм|размер|резьб)\w*"),
        },
        "sewer": {
            "sewer_scope": re.compile(r"\b(?:внутрен|наружн|место\s+проклад)\w*"),
            "element_type": re.compile(r"\b(?:труб|отвод|тройник|муфт|детал)\w*"),
            "diameter_mm": re.compile(r"\b(?:dn|дн|диаметр|размер|маркиров|надпис)\w*"),
            "secondary_diameter_mm": re.compile(
                r"\b(?:втор\w*\s+диаметр|диаметр\w*\s+(?:переход|ответв)|"
                r"ответвлен|боков\w*\s+(?:выход|отвод)|с\s+какого\s+на\s+какой)\w*"
            ),
            "length_mm": re.compile(r"\b(?:длин|отрез|участ)\w*"),
        },
        "pumps": {
            "head_m": re.compile(r"\b(?:напор|рабоч\w*\s+точк)\w*"),
            "required_flow_m3_h": re.compile(
                r"\b(?:расход|производительност|подач)\w*"
            ),
            "mounting_length_mm": re.compile(
                r"\b(?:монтажн\w*\s+длин|межосев|длин\w*\s+насос)\w*"
            ),
            "connection_size": re.compile(
                r"\b(?:присоедин|подключен|dn|размер\w*\s+резьб)\w*"
            ),
            "old_model": re.compile(r"\b(?:маркировк|модел|шильдик)\w*"),
            "system_type": re.compile(r"\b(?:схем|тип\w*\s+систем)\w*"),
            "dynamic_water_level_m": re.compile(
                r"\b(?:динамическ\w*\s+уров|уров\w*\s+вод)\w*"
            ),
            "lift_height_m": re.compile(
                r"\b(?:высот\w*\s+под[ъь]?ем|перепад\w*\s+высот|"
                r"вертикальн\w*\s+под[ъь]?ем)\w*"
            ),
            "horizontal_run_m": re.compile(
                r"\b(?:длин\w*\s+трасс|горизонтальн\w*\s+(?:трасс|отвод)|"
                r"расстоян\w*\s+по\s+горизонтал)\w*"
            ),
            "required_pressure_bar": re.compile(
                r"\b(?:нужн\w*\s+давлен|давлен\w*\s+после\s+насос)\w*"
            ),
            "inlet_pressure_bar": re.compile(
                r"\b(?:входн\w*\s+давлен|давлен\w*\s+на\s+вход|"
                r"давлен\w*\s+в\s+водопровод)\w*"
            ),
            "discharge_diameter_mm": re.compile(
                r"\b(?:диаметр\w*\s+(?:шланг|напорн\w*\s+труб)|"
                r"напорн\w*\s+труб\w*)\w*"
            ),
            "water_quality": re.compile(
                r"\b(?:качеств\w*\s+вод|чист\w*\s+или\s+грязн|тип\w*\s+сток)\w*"
            ),
            "solids_mm": re.compile(
                r"\b(?:размер\w*\s+частиц|частиц|включен)\w*"
            ),
            "connected_fixtures": re.compile(
                r"\b(?:подключаем\w*\s+прибор|сантехнич\w*\s+прибор|"
                r"что\s+подключ)\w*"
            ),
        },
        "radiator_fittings": {
            "metric_thread": re.compile(r"\b(?:резьб|присоедин)\w*"),
            "valve_model": re.compile(r"\b(?:модел|маркиров)\w*"),
            "valve_brand": re.compile(r"\b(?:марк|бренд|производител)\w*"),
            "connection_form": re.compile(r"\b(?:подключен|прям|углов)\w*"),
            "size_inch": re.compile(r"\b(?:размер|дюйм)\w*"),
        },
        "radiators": {
            "heating_system_type": re.compile(r"\b(?:систем|отоплен)\w*"),
            "radiator_type": re.compile(r"\b(?:тип|материал)\w*"),
            "area_m2": re.compile(r"\b(?:площад|квадрат)\w*"),
            "operating_pressure_bar": re.compile(r"\b(?:давлен|опрессов)\w*"),
        },
        "boilers": {
            "boiler_type": re.compile(r"\b(?:тип|газ|электр)\w*"),
            "area_m2": re.compile(r"\b(?:площад|квадрат)\w*"),
            "contours": re.compile(r"\b(?:контур|гвс|горяч\w*\s+вод)\w*"),
            "needs_hot_water": re.compile(r"\b(?:гвс|горяч\w*\s+вод)\w*"),
        },
    }
    _REMAINING_UNKNOWN_SLOTS: dict[str, tuple[str, ...]] = {
        "pipes": (
            "operating_temperature_c",
            "operating_pressure_bar",
            "diameter_mm",
            "pipe_material",
        ),
        "fittings": (
            "fitting_system",
            "element_type",
            "diameter_mm",
            "secondary_diameter_mm",
            "size_inch",
        ),
        "sewer": (
            "sewer_scope",
            "element_type",
            "diameter_mm",
            "secondary_diameter_mm",
            "length_mm",
        ),
        "pumps": (
            "head_m",
            "required_flow_m3_h",
            "mounting_length_mm",
            "connection_size",
            "old_model",
            "system_type",
            "dynamic_water_level_m",
            "lift_height_m",
            "horizontal_run_m",
            "required_pressure_bar",
            "inlet_pressure_bar",
            "discharge_diameter_mm",
            "water_quality",
            "solids_mm",
            "connected_fixtures",
        ),
        "radiator_fittings": (
            "metric_thread",
            "valve_model",
            "valve_brand",
            "connection_form",
            "size_inch",
        ),
        "radiators": (
            "heating_system_type",
            "radiator_type",
            "area_m2",
            "operating_pressure_bar",
        ),
        "boilers": ("boiler_type", "area_m2", "contours", "needs_hot_water"),
    }

    @staticmethod
    def _observable_unknown_result(
        category: str,
        slots: dict,
        missing_slots: list[str],
    ) -> SlotFillingResult | None:
        """Replace an unknown technical value with an observable route.

        The refusal remains category-local in ``deferred_slot_keys``.  Search
        may later continue on the other known facts, while this step gives the
        customer a practical way to resolve the same compatibility constraint.
        """

        guidance = observable_selection_guidance(
            category,
            missing_slots,
            slots.get("deferred_slot_keys") or [],
        )
        if guidance is None:
            return None
        question, expected_slots = guidance
        return SlotFillingResult(
            slots=slots,
            needs_clarification=True,
            question=question,
            expected_slots=expected_slots,
            blocking=True,
        )

    @classmethod
    def _remember_selection_refusals(
        cls,
        category: str,
        text: str,
        slots: dict,
    ) -> None:
        """Persist explicit unknowns even when they occur in the first turn.

        Pending-question binding handles replies to the bot.  This companion
        path handles the equally common opening ``резьбу и модель не знаю``.
        Both write the same typed ``deferred_slot_keys`` state, so later
        observation, preliminary search and caveats do not depend on wording.
        """

        patterns = cls._UNKNOWN_PARAMETER_PATTERNS.get(category)
        if not patterns:
            return
        refused = bind_local_refusals(text, patterns)
        remaining_unknown = bool(
            re.search(
                r"\b(?:остальн\w*|проч\w*\s+параметр\w*|"
                r"паспортн\w*\s+данн\w*)[^.?!]{0,28}"
                r"(?:не\s+знаю|неизвестн\w*|нет|не\s+чита\w*)\b",
                text,
            )
        )
        if remaining_unknown:
            refused.extend(
                key
                for key in cls._REMAINING_UNKNOWN_SLOTS.get(category, ())
                if slots.get(key) in (None, "", [], {})
            )
        if not refused:
            return
        deferred = {str(key) for key in slots.get("deferred_slot_keys") or []}
        deferred.update(refused)
        slots["deferred_slot_keys"] = sorted(deferred)

    @staticmethod
    def _normalize_observable_selection_slots(
        category: str,
        text: str,
        slots: dict,
        previous_slots: dict | None = None,
    ) -> None:
        """Make direct physical observations authoritative at the final gate.

        The semantic interpreter may help with colloquial wording, but it may
        not turn ``наружный диаметр`` into outdoor sewer or overlook a joining
        method that deterministically identifies PPR.  Rechecking those facts
        here keeps the contract stable even when an LLM interpretation was
        accepted earlier in the turn.
        """

        if category == "fittings" and "нагрев" in text and any(
            marker in text for marker in ("труб", "пластик", "бел")
        ):
            slots["fitting_system"] = "ppr"
        if category == "fittings" and any(
            marker in text
            for marker in ("поворот", "повернуть", "под углом", "90-градус")
        ):
            slots["element_type"] = "угольник"
            slots["product_kind"] = "elbow"
        if category == "fittings":
            explicit_angle = re.search(
                r"(?<!\d)(15|22|30|45|60|67|87|88|90)\s*"
                r"(?:°|[-–—]?\s*градус\w*)",
                text,
            )
            if explicit_angle:
                slots["angle_deg"] = int(explicit_angle.group(1))
            if re.search(
                r"\bбез\s+(?:переход\w*\s+на\s+)?резьб\w*\b",
                text,
            ):
                slots["combined_metal"] = False
            if (
                re.search(r"\b(?:две|два)\s+(?:\w+\s+){0,2}труб\w*\b", text)
                or re.search(
                    r"\b(?:с\s+)?обеих\s+сторон\b[^.!?]{0,28}\bтруб\w*\b",
                    text,
                )
            ):
                slots["fitting_end_form"] = "socket_socket"

        if category in {"fittings", "sewer"}:
            element = normalize_text(str(slots.get("element_type") or ""))
            coupling = normalize_text(str(slots.get("coupling_type") or ""))
            needs_pair = bool(
                "тройник" in element
                or "переход" in element
                or "редукц" in element
                or "переход" in coupling
                or "редукц" in coupling
            )
            explicitly_equal = bool(
                re.search(r"\bравнопроходн\w*\b", text)
                or re.search(
                    r"\b(?:оба|все|основн\w*\s+и\s+(?:боков\w*\s+)?"
                    r"ответвлен\w*)[^.!?]{0,24}\b(?:одинаков|одного\s+диаметр)\w*",
                    text,
                )
                or re.search(
                    r"\b(?:ответвлен|боков\w*\s+(?:выход|отвод))\w*"
                    r"[^.!?]{0,18}\b(?:так\w*\s+же|того\s+же|равн\w*)\b",
                    text,
                )
            )
            if (
                needs_pair
                and explicitly_equal
                and slots.get("diameter_mm") is not None
                and slots.get("secondary_diameter_mm") is None
            ):
                # Equal tees/reducers are still a two-ended contract.  The
                # second size may only be copied after the customer explicitly
                # says the ends/branch are equal; the bot never assumes it.
                slots["secondary_diameter_mm"] = slots["diameter_mm"]
            explicit_second = re.search(
                r"\b(?:втор\w*\s+диаметр|диаметр\w*\s+ответвлен|"
                r"ответвлен\w*|боков\w*\s+(?:выход|отвод))\w*"
                r"[^\d]{0,18}(\d{2,3})\s*(?:мм)?\b",
                text,
            )
            if (
                needs_pair
                and explicit_second
                and previous_slots
                and previous_slots.get("diameter_mm") is not None
                and slots.get("secondary_diameter_mm") is None
            ):
                # A follow-up «второй диаметр — 32 мм» must fill the branch/end
                # size, not overwrite the already confirmed main diameter.
                slots["diameter_mm"] = previous_slots["diameter_mm"]
                slots["secondary_diameter_mm"] = int(explicit_second.group(1))

        if category == "sewer":
            previous_slots = previous_slots or {}
            explicit_external_location = any(
                marker in text
                for marker in (
                    "в земле",
                    "на улице",
                    "во дворе",
                    "на участке",
                    "наружная канализация",
                    "наружной канализации",
                )
            )
            explicit_internal_observation = bool(
                any(
                    marker in text
                    for marker in (
                        "внутри квартир",
                        "в квартире",
                        "под мойк",
                        "под раковин",
                        "в ванной",
                        "в сануз",
                    )
                )
                or (
                    "сер" in text
                    and any(marker in text for marker in ("труб", "канализац"))
                )
            )
            outside_measurement = bool(
                re.search(
                    r"\b(?:наружн\w*\s+(?:размер|диаметр|замер)\w*|"
                    r"наружк\w*[^.!?]{0,16}\d{2,3})\b",
                    text,
                )
            )
            if explicit_external_location:
                slots["sewer_scope"] = "наружная"
            elif explicit_internal_observation or (
                outside_measurement
                and previous_slots.get("sewer_scope") == "внутренняя"
            ):
                slots["sewer_scope"] = "внутренняя"

        if category == "boilers":
            rejects_hot_water = bool(
                re.search(r"\bбез\s+(?:горяч\w*\s+вод\w*|гвс)\b", text)
                or re.search(r"\bтолько\s+(?:для\s+)?отоплен\w*", text)
            )
            mentions_hot_water = bool(
                "гвс" in text or ("горяч" in text and "вод" in text)
            )
            if rejects_hot_water:
                slots["needs_hot_water"] = False
                slots["contours"] = "одноконтурный"
            elif mentions_hot_water:
                slots["needs_hot_water"] = True
                slots["contours"] = "двухконтурный"

        if category == "radiator_fittings" and (
            slots.get("product_kind") == "thermostatic_head"
            or slots.get("thermostatic_head") is True
        ):
            if re.search(r"\b(?:valtec|валтек)\b", text) and any(
                marker in text for marker in ("клапан", "корпус", "маркиров", "надпис")
            ):
                slots["valve_brand"] = "VALTEC"
            uncertain_metric_thread = bool(
                re.search(
                    r"\b(?:не\s+знаю|не\s+уверен\w*|может|вроде|кажется)\b"
                    r"[^.!?]{0,45}\b[mм]\s*\d{1,3}",
                    text,
                )
                or re.search(
                    r"\b[mм]\s*\d{1,3}[^.!?]{0,20}\b(?:или|либо)\b"
                    r"[^.!?]{0,20}\b[mм]?\s*\d{1,3}",
                    text,
                )
            )
            if uncertain_metric_thread:
                # An either/or observation is not a filter.  Clear every
                # connection-shaped field an interpreter could derive from
                # ``standard thread, maybe M20 or M30``; retaining even one of
                # them can turn an honest unknown into a hard no-match.
                for key in (
                    "metric_thread",
                    "size_inch",
                    "connection_size",
                    "thread_type",
                    "connection_form",
                    "body_form",
                    "diameter_mm",
                    "name_tokens",
                ):
                    slots.pop(key, None)
                slots["thermostatic_head"] = True
                slots["product_kind"] = "thermostatic_head"
                deferred = {str(key) for key in slots.get("deferred_slot_keys") or []}
                deferred.add("metric_thread")
                slots["deferred_slot_keys"] = sorted(deferred)
        if category == "radiator_fittings" and any(
            marker in text for marker in ("фото", "фотограф", "снимок")
        ):
            slots["photo_requested"] = True

    @staticmethod
    def _prune_resolved_selection_refusals(slots: dict) -> None:
        deferred = {str(key) for key in slots.get("deferred_slot_keys") or []}
        if not deferred:
            return
        deferred = {
            key
            for key in deferred
            if slots.get(key) in (None, "", [], {})
        }
        if any(slots.get(key) not in (None, "", [], {}) for key in (
            "diameter_mm",
            "size_inch",
            "connection_size",
        )):
            deferred.difference_update({"diameter_mm", "size_inch", "connection_size"})
        if deferred:
            slots["deferred_slot_keys"] = sorted(deferred)
        else:
            slots.pop("deferred_slot_keys", None)

    def fill(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> SlotFillingResult:
        previous_slots = dict(session.slots)
        slots = normalize_engineering_slots(
            merge_slots(session.slots, intent.slots)
        )
        category = intent.category
        text = normalize_text(message)

        if intent.intent_type in {"exact_sku", "link_request", "small_talk", "out_of_scope"}:
            return SlotFillingResult(slots=slots)
        if intent.intent_type == "complectation":
            return SlotFillingResult(slots=slots)
        if (
            intent.intent_type == "stock_request"
            and category not in {
                "other",
                "water_heaters",
                "hydraulic_accumulators",
            }
        ):
            return SlotFillingResult(slots=slots)

        if category == "pipes" and slots.get("pipe_purpose") == "канализация":
            category = "sewer"

        self._normalize_observable_selection_slots(
            category,
            text,
            slots,
            previous_slots=previous_slots,
        )
        self._remember_selection_refusals(category, text, slots)
        self._prune_resolved_selection_refusals(slots)

        if category == "pumps":
            self._infer_plain_circulation_parameters(text, slots)
            self._drop_parameter_shaped_sku(slots, intent)

        if category == "water_heaters":
            self._reconcile_water_heater_negations(
                text,
                slots,
                previous_slots=previous_slots,
                current_slots=intent.slots,
            )

        if category == "sewer":
            self._infer_sewer_followup_slots(
                text,
                slots,
                previous_slots=previous_slots,
                current_slots=intent.slots,
            )

        if category == "pipes":
            return self._pipes(slots)
        if category == "fittings":
            return self._fittings(slots)
        if category == "sewer":
            return self._sewer(slots, text)
        if category == "pumps":
            return self._pumps(slots, text)
        if category == "boilers":
            return self._boilers(slots)
        if category == "water_heaters":
            return self._water_heaters(slots)
        if category == "hydraulic_accumulators":
            return self._hydraulic_accumulators(slots)
        if category == "filters":
            return self._filters(slots)
        if category == "controls":
            return self._controls(slots)
        if category == "valves":
            return self._valves(slots, text)
        if category == "radiator_fittings":
            return self._radiator(slots)
        if category == "radiators":
            return self._radiators(
                slots,
                require_compatibility_context=(
                    session.pending_selection_mode == "recommend"
                ),
            )
        return SlotFillingResult(slots=slots)

    def _filters(self, slots: dict) -> SlotFillingResult:
        element = normalize_text(str(slots.get("filter_element_type") or ""))
        if not element and not any(
            slots.get(key)
            for key in ["filter_format", "filter_technology", "filtration_microns"]
        ):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Что именно нужно: корпус фильтра, сменный картридж или система "
                    "водоочистки? Укажите источник воды и задачу/результат анализа; "
                    "для замены — типоразмер, например 10SL, 10BB или 20BB."
                ),
            )
        if element == "картридж" and not slots.get("filter_format"):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Какой типоразмер картриджа нужен: 10SL, 20SL, 10BB или 20BB? "
                    "Также укажите назначение картриджа или требуемую тонкость в мкм."
                ),
            )
        if element == "картридж" and not (
            slots.get("filter_technology")
            or slots.get("filtration_microns") is not None
        ):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Для чего нужен картридж: механическая очистка, угольная очистка "
                    "или другая задача? Для механического укажите тонкость фильтрации в мкм."
                ),
            )
        return SlotFillingResult(slots=slots)

    def _controls(self, slots: dict) -> SlotFillingResult:
        kind = normalize_text(str(slots.get("control_kind") or ""))
        if not kind:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Что требуется: комнатный термостат/терморегулятор, сервопривод "
                    "клапана или контроллер? Укажите систему и модель совместимого узла."
                ),
            )
        if kind == "сервопривод" and not slots.get("voltage_v"):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Какое питание сервопривода — 24 или 230 В? Если это замена, "
                    "укажите также НЗ/NC или НО/NO, сигнал управления и присоединение."
                ),
            )
        return SlotFillingResult(slots=slots)

    def _pipes(self, slots: dict) -> SlotFillingResult:
        warm_floor_scope = bool(
            slots.get("project_scope") == "warm_floor"
            or slots.get("scope_funnel") == "warm_floor"
            or slots.get("has_warm_floor") is True
        )
        concrete_warm_floor_pipe = bool(
            warm_floor_scope
            and slots.get("pipe_material")
            and slots.get("diameter_mm")
            and slots.get("wall_thickness_mm")
            and slots.get("total_length_m")
        )
        if warm_floor_scope:
            slots.setdefault("pipe_purpose", "отопление")
            slots.setdefault("pipe_service", "петля тёплого пола")
        if concrete_warm_floor_pipe:
            # A customer who already specified PE-RT 16x2 and the total metreage
            # is buying a concrete pipe, not asking us to design the whole floor.
            # Area/contour design can be offered separately after the exact search.
            return SlotFillingResult(slots=slots)
        if warm_floor_scope and not (
            slots.get("warm_floor_area_m2") or slots.get("area_m2")
        ):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Какая площадь тёплого пола в м²? По ней рассчитаю метраж "
                    "трубы и количество контуров."
                ),
            )
        if not slots.get("pipe_purpose"):
            if slots.get("diameter_mm"):
                question = (
                    f"Понял, труба {slots['diameter_mm']} мм. Для чего она: "
                    "для холодной или горячей воды, для отопления или для канализации? "
                    "Где именно она будет проложена?"
                )
            else:
                question = (
                    "Труба для чего: для холодной или горячей воды, для отопления "
                    "или для канализации? Уточните участок системы и диаметр; если "
                    "диаметр нужно рассчитать — расход и длину трассы."
                )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=question,
            )

        purpose = normalize_text(str(slots.get("pipe_purpose") or ""))
        service = normalize_text(str(slots.get("pipe_service") or ""))
        if "отоплен" in purpose and not service:
            diameter_prefix = (
                f"Диаметр {slots['diameter_mm']} мм записал. "
                if slots.get("diameter_mm")
                else ""
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    diameter_prefix
                    + "Для какого участка отопления нужна труба: петля тёплого пола, "
                    "радиаторная разводка/магистраль или обвязка котла? Также укажите "
                    "максимальную температуру и рабочее давление системы."
                ),
            )

        if "водоснаб" in purpose and not slots.get("water_temperature"):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Это ХВС (холодная вода) или ГВС (горячая вода)? "
                    "Уточните участок: внутри дома, "
                    "подземный ввод от скважины/колодца или рециркуляция ГВС, "
                    "и укажите расчётный диаметр."
                ),
            )

        if (
            normalize_text(str(slots.get("water_temperature") or "")) == "горячая"
            and not service
            and normalize_text(str(slots.get("pipe_material") or "")) == "ppr"
            and not slots.get("diameter_mm")
        ):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "По описанию это PPR (полипропиленовая труба под раструбную сварку). "
                    "Для начала уточните участок: обычная разводка от стояка к кранам "
                    "в квартире, рециркуляция ГВС или ввод в дом? Остальные размеры и "
                    "режимы соберём следующим шагом — угадывать диаметр не нужно."
                ),
            )

        hot_or_heating = bool(
            "отоплен" in purpose
            or normalize_text(str(slots.get("water_temperature") or "")) == "горячая"
        )
        if (
            normalize_text(str(slots.get("water_temperature") or "")) == "горячая"
            and not service
        ):
            diameter_suffix = (
                f" Диаметр {slots['diameter_mm']} мм уже записал."
                if slots.get("diameter_mm")
                else " Также укажите расчётный диаметр."
            )
            # The question asks for three things at once, so a customer who
            # answers one of them and sees the identical text come back assumes
            # nothing was heard and types it again.  Name what is already
            # recorded and ask only for what is still missing.
            recorded = []
            if slots.get("operating_temperature_c"):
                recorded.append(
                    f"температура {float(slots['operating_temperature_c']):g} °C"
                )
            if slots.get("operating_pressure_bar"):
                recorded.append(
                    f"давление {float(slots['operating_pressure_bar']):g} бар"
                )
            missing = []
            if not slots.get("operating_temperature_c"):
                missing.append("максимальную температуру")
            if not slots.get("operating_pressure_bar"):
                missing.append("рабочее давление")
            prefix = f"Записал: {', '.join(recorded)}. " if recorded else ""
            ask_params = (
                f" Укажите также {' и '.join(missing)}." if missing else ""
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    prefix
                    + "Для какого участка ГВС нужна труба: обычная разводка внутри "
                    "дома, рециркуляция или ввод?"
                    + ask_params
                    + diameter_suffix
                ),
            )
        if (
            "водоснаб" in purpose
            and service == "разводка внутри дома"
            and not slots.get("diameter_mm")
        ):
            material_prefix = (
                "По описанию это PPR (полипропиленовая труба под раструбную сварку). "
                "Для начала не нужно угадывать диаметр по внешнему виду. "
                if normalize_text(str(slots.get("pipe_material") or "")) == "ppr"
                else ""
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    material_prefix
                    + "Для новой разводки от стояка диаметр не угадывают по цвету трубы. "
                    "Перечислите точки водоразбора (душ, раковина, кухня, унитаз), "
                    "какие из них могут работать одновременно, и примерную длину до "
                    "самой дальней точки. Давление и максимальную температуру лучше "
                    "взять из проекта/у управляющей организации, а не назначать на глаз."
                ),
            )
        # Спрашиваем только то, что действительно не названо и от чего клиент не
        # отказался. Раньше текст был жёсткой строкой с обоими параметрами, и
        # клиент, назвавший температуру, слышал просьбу назвать её снова.
        deferred_keys = set(slots.get("deferred_slot_keys") or [])
        param_labels = (
            ("operating_temperature_c", "максимальную температуру теплоносителя/воды"),
            ("operating_pressure_bar", "рабочее давление"),
        )
        still_needed = [
            label
            for key, label in param_labels
            if not slots.get(key) and key not in deferred_keys
        ]
        if hot_or_heating and still_needed:
            known = []
            if slots.get("operating_temperature_c"):
                known.append(
                    f"температура {float(slots['operating_temperature_c']):g} °C"
                )
            if slots.get("operating_pressure_bar"):
                known.append(
                    f"давление {float(slots['operating_pressure_bar']):g} бар"
                )
            prefix = f"Понял: {', '.join(known)}. " if known else ""
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    prefix
                    + "Укажите недостающие расчётные параметры: "
                    + " и ".join(still_needed)
                    + ". По одному слову «горячая» или "
                    "«отопление» безопасно выбрать конкретную трубу нельзя."
                    + (
                        " Диаметр вы не обязаны угадывать: для новой разводки его "
                        "определяют по одновременному расходу, длине и допустимым потерям "
                        "давления; для замены можно прислать маркировку или измерить "
                        "наружный диаметр существующей трубы."
                        if not slots.get("diameter_mm")
                        and "diameter_mm" in deferred_keys
                        else (
                            " Также нужен расчётный диаметр."
                            if not slots.get("diameter_mm")
                            else ""
                        )
                    )
                ),
            )

        cold_water = bool(
            "водоснаб" in purpose
            and normalize_text(str(slots.get("water_temperature") or "")) == "холодная"
        )
        if cold_water and not service:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Где пойдёт ХВС: внутри дома или под землёй от "
                    "скважины/колодца? Для подземного ввода укажите длину трассы, "
                    "требуемый расход и рабочее давление."
                ),
            )

        if not slots.get("diameter_mm"):
            if "diameter_mm" in deferred_keys:
                question = (
                    "Диаметр не буду угадывать. Если меняете существующую трубу, "
                    "пришлите маркировку или измерьте её наружный диаметр штангенциркулем. "
                    "Для новой разводки нужны одновременный расход по точкам, длина и "
                    "схема трассы, допустимые потери давления. Это замена существующей "
                    "трубы или новая разводка?"
                )
            elif slots.get("required_flow_m3_h") and slots.get("horizontal_run_m"):
                question = (
                    "Диаметр ещё не задан. Для его расчёта дополнительно нужны допустимые "
                    "потери давления и схема трассы; без гидравлического расчёта диаметр "
                    "угадывать не буду. Если он уже рассчитан, напишите размер в мм."
                )
            else:
                question = (
                    "Какой расчётный диаметр нужен? Если его ещё нет, укажите расход, "
                    "длину и схему трассы — по одному назначению диаметр не выбирают."
                )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=question,
            )

        # The question below offers the laying method as an alternative when the
        # material is undecided.  Gating only on ``pipe_material`` refused the
        # very answer it invited, so «скрытая» re-asked the same question.
        if (
            hot_or_heating
            and not slots.get("pipe_material")
            and not slots.get("installation_method")
        ):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Какой материал/система соединения уже заложены: металлопластик "
                    "(для 16 мм сначала проверю VALTEC), PPR, PEX/PE-RT, сталь или "
                    "другое? Если материал не выбран, укажите способ прокладки — "
                    "открытая, скрытая или петля тёплого пола."
                ),
            )
        return SlotFillingResult(slots=slots)

    def _fittings(self, slots: dict) -> SlotFillingResult:
        missing: list[str] = []
        if (
            not slots.get("fitting_system")
            and not is_system_agnostic_element(slots.get("element_type"))
            # Комбинированные фитинги — резьбовое соединение полимера с
            # металлом; в канализации их практически нет, и вопрос о системе
            # только мешает.
            and not slots.get("combined_metal")
        ):
            missing.append("fitting_system")
        if not slots.get("element_type"):
            missing.append("element_type")
        if not slots.get("diameter_mm") and not slots.get("size_inch"):
            missing.extend(["diameter_mm", "size_inch"])
        if self._fitting_requires_secondary_diameter(slots) and not slots.get(
            "secondary_diameter_mm"
        ):
            missing.append("secondary_diameter_mm")
        if missing:
            assisted = self._observable_unknown_result("fittings", slots, missing)
            if assisted is not None:
                return assisted
            labels = {
                "fitting_system": "система: PPR или канализация",
                "element_type": "тип: муфта, угольник, тройник или переходник",
                "diameter_mm": "размер в мм или дюймах",
                "size_inch": "размер в мм или дюймах",
                "secondary_diameter_mm": (
                    "второй диаметр: для перехода — конечный размер, "
                    "для тройника — размер ответвления (если он равен основному, так и напишите)"
                ),
            }
            visible_keys: list[str] = []
            for key in missing:
                if labels[key] not in [labels[item] for item in visible_keys]:
                    visible_keys.append(key)
                if len(visible_keys) >= 2:
                    break
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + " и ".join(labels[key] for key in visible_keys) + ".",
                expected_slots=list(dict.fromkeys(missing)),
                blocking=True,
            )
        return SlotFillingResult(slots=slots)

    def _sewer(self, slots: dict, text: str) -> SlotFillingResult:
        slots.setdefault("pipe_purpose", "канализация")
        missing: list[str] = []
        if not slots.get("sewer_scope"):
            missing.append("sewer_scope")
        if not slots.get("element_type"):
            missing.append("element_type")
        if not slots.get("diameter_mm"):
            missing.append("diameter_mm")
        if slots.get("element_type") == "труба" and not slots.get("length_mm"):
            missing.append("length_mm")
        if self._fitting_requires_secondary_diameter(slots) and not slots.get(
            "secondary_diameter_mm"
        ):
            missing.append("secondary_diameter_mm")
        if missing:
            assisted = self._observable_unknown_result("sewer", slots, missing)
            if assisted is not None:
                return assisted
            labels = {
                "sewer_scope": "внутренняя или наружная канализация",
                "element_type": "что нужно: труба, отвод, тройник или муфта",
                "diameter_mm": "диаметр",
                "length_mm": "длина",
                "secondary_diameter_mm": (
                    "диаметр ответвления/второй стороны; если он такой же, "
                    "напишите «равный основному»"
                ),
            }
            question = self._build_question(
                [labels[key] for key in missing[:2]],
                slots=slots,
                text=text,
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=question,
                expected_slots=list(dict.fromkeys(missing)),
                blocking=True,
            )
        return SlotFillingResult(slots=slots)

    @staticmethod
    def _fitting_requires_secondary_diameter(slots: dict) -> bool:
        """Whether the explicitly requested part has two catalogue sizes."""

        element = normalize_text(str(slots.get("element_type") or ""))
        coupling = normalize_text(str(slots.get("coupling_type") or ""))
        return bool(
            "тройник" in element
            or "переход" in element
            or "редукц" in element
            or "переход" in coupling
            or "редукц" in coupling
        )

    def _infer_sewer_followup_slots(
        self,
        text: str,
        slots: dict,
        previous_slots: dict | None = None,
        current_slots: dict | None = None,
    ) -> None:
        previous_slots = previous_slots or {}
        current_slots = current_slots or {}
        element_type = normalize_text(
            str(slots.get("element_type") or previous_slots.get("element_type") or "")
        )
        if "муфт" in element_type:
            if "соедин" in text:
                slots["coupling_type"] = "соединительная"
            elif "переход" in text or "редукц" in text:
                slots["coupling_type"] = "переходная"
            elif "ремонт" in text or "надвиж" in text:
                slots["coupling_type"] = "ремонтная"

        angle = self._extract_sewer_angle(text, element_type, previous_slots)
        if angle is not None:
            slots["angle_deg"] = angle
            previous_diameter = previous_slots.get("diameter_mm")
            current_diameter = current_slots.get("diameter_mm")
            if (
                previous_diameter
                and current_diameter
                and int(current_diameter) == int(angle)
                and not self._explicitly_states_diameter(text, int(angle))
            ):
                # ``отвод 110`` → ``внутренняя, 90`` means DN110 at a
                # right angle; the generic number extractor sees 90 as a new
                # diameter, so restore the already confirmed DN.
                slots["diameter_mm"] = previous_diameter

        if (
            not slots.get("element_type")
            and slots.get("sewer_scope")
            and slots.get("diameter_mm")
            and not any(word in text for word in ["отвод", "тройник", "муфта"])
        ):
            slots["element_type"] = "труба"
        if slots.get("element_type") == "труба" and not slots.get("length_mm"):
            total_length = self._extract_total_length_m(text)
            if total_length:
                slots["total_length_m"] = total_length
        if slots.get("element_type") == "труба" and not slots.get("length_mm"):
            length = self._extract_mm_value(text, min_value=300)
            if length:
                slots["length_mm"] = length
        if not slots.get("diameter_mm"):
            diameter = self._extract_mm_value(text, min_value=32, max_value=250)
            if diameter:
                slots["diameter_mm"] = diameter

    def _extract_sewer_angle(
        self,
        text: str,
        element_type: str,
        previous_slots: dict,
    ) -> int | None:
        if "отвод" not in element_type:
            return None
        explicit = re.search(
            r"(?<!\d)(\d{1,3})\s*(?:градус\w*|град\b)",
            text,
        )
        if explicit:
            value = int(explicit.group(1))
            return value if 5 <= value <= 90 else None

        values = [int(value) for value in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", text)]
        standard_angles = {15, 22, 30, 45, 67, 87, 88, 90}
        previous_diameter = previous_slots.get("diameter_mm")
        if previous_diameter and len(values) == 1 and values[0] in standard_angles:
            if not self._explicitly_states_diameter(text, values[0]):
                return values[0]
        if len(values) >= 2 and values[-1] in standard_angles:
            return values[-1]
        return None

    def _explicitly_states_diameter(self, text: str, value: int) -> bool:
        return bool(
            re.search(
                rf"(?:диаметр\w*|\bdn|\bd\s*|ø)\D{{0,8}}{value}\b|"
                rf"\b{value}\s*мм\b",
                text,
            )
        )

    def _extract_total_length_m(self, text: str) -> int | None:
        value = parse_total_length_m(text)
        return int(value) if value is not None and float(value).is_integer() else value

    def _extract_mm_value(
        self,
        text: str,
        min_value: int,
        max_value: int | None = None,
    ) -> int | None:
        import re

        for match in re.finditer(r"(?<!\d)(\d{2,5})(?:\s*мм|\s*м\b|\b)", text):
            tail = text[match.end(1) : match.end(1) + 12]
            # Угол, температура, объём или секции — это не размер.
            if re.match(
                r"\s*(?:м\b|метр|градус|°|литр|л\b|секц|м2|м²|квадрат)",
                tail,
            ):
                continue
            value = int(match.group(1))
            if value < min_value:
                continue
            if max_value is not None and value > max_value:
                continue
            return value
        return None

    def _build_question(self, missing: list[str], slots: dict | None = None, text: str = "") -> str:
        slots = slots or {}
        if missing == ["длина"]:
            diameter = slots.get("diameter_mm")
            total_length = slots.get("total_length_m")
            if diameter and self._mentions_number(text, int(diameter)) and "метр" not in text:
                return (
                    f"Диаметр {diameter} мм уже понял. Уточните длину одного отрезка трубы: "
                    "500, 1000, 1500 или 2000 мм?"
                )
            if total_length:
                details = []
                if slots.get("sewer_scope"):
                    details.append(str(slots["sewer_scope"]))
                if diameter:
                    details.append(f"диаметр {diameter} мм")
                prefix = f"Понял: {', '.join(details)}. " if details else ""
                return (
                    f"{prefix}{total_length} м — это общий метраж. В карточке длина указана для одного "
                    "отрезка трубы. Какая длина одной трубы нужна: 500, 1000, 1500 или 2000 мм?"
                )
            return "Какая длина одного отрезка трубы нужна: 500, 1000, 1500 или 2000 мм?"
        if missing == ["диаметр"]:
            return "Какой диаметр нужен?"
        if missing == ["внутренняя или наружная канализация"]:
            return "Канализация внутренняя или наружная?"
        if missing == ["что нужно: труба, отвод, тройник или муфта"]:
            return "Что нужно: труба, отвод, тройник или муфта?"
        if missing == ["внутренняя или наружная канализация", "длина"]:
            return "Внутренняя или наружная канализация? И какая длина трубы нужна?"
        if missing == ["внутренняя или наружная канализация", "что нужно: труба, отвод, тройник или муфта"]:
            return "Канализация внутренняя или наружная? И что нужно: труба, отвод, тройник или муфта?"
        return "Уточните: " + "; ".join(missing) + "."

    def _mentions_number(self, text: str, number: int) -> bool:
        return bool(re.search(rf"(?<!\d){number}(?!\d)", text))

    def _drop_parameter_shaped_sku(self, slots: dict, intent: IntentResult) -> None:
        if intent.intent_type == "exact_sku":
            return
        raw_sku = normalize_text(str(slots.get("sku") or ""))
        if not raw_sku:
            return
        if re.fullmatch(
            r"\d{2,3}\s*[/.-]\s*\d{1,2}(?:\s*[- ]\s*(?:130|180))?",
            raw_sku,
        ):
            # A weak intent-model may copy the already known pump notation
            # ``25/6-130`` into the SKU field on an ordinary follow-up.  It is a
            # parameter tuple, not a catalog article, and must not turn the next
            # search into a failed exact-SKU lookup.
            slots.pop("sku", None)

    def _infer_plain_circulation_parameters(self, text: str, slots: dict) -> None:
        """Recognise common compact/space-separated circulation-pump shorthand."""
        match = re.search(
            r"(?<!\d)(25|32)\s+(4|6|8)\s+(130|180)(?!\d)",
            text,
        )
        if not match:
            # Customers frequently omit the slash while typing on a phone:
            # ``нсос 256 130`` means 25/6 with a 130 mm mounting length.
            match = re.search(
                r"(?<!\d)(25|32)(4|6|8)\s+(130|180)(?!\d)",
                text,
            )
        if not match:
            return
        connection, head, mounting = (int(value) for value in match.groups())
        slots.setdefault("connection_size", connection)
        slots.setdefault("head_m", float(head))
        slots.setdefault("mounting_length_mm", mounting)
        slots.setdefault("pump_type", "циркуляционный")

    def _pumps(self, slots: dict, text: str) -> SlotFillingResult:
        explicitly_unknown = self._remember_explicit_pump_refusals(slots, text)
        deferred = self._prune_filled_pump_deferred_slots(slots)
        # The purpose answer offered by this very funnel must be actionable.
        # Previously ``откачка воды`` was stored only as ``pump_use`` while the
        # next branch required ``pump_type`` and repeated the same question.
        # Keep this defensive conversion here as well as in IntentRouter: it
        # also protects sessions restored from an older version of the app.
        pump_use = normalize_text(str(slots.get("pump_use") or ""))
        water_source = normalize_text(str(slots.get("water_source") or ""))
        if not slots.get("pump_type") and ("откач" in pump_use or "дренаж" in pump_use):
            slots["pump_type"] = "дренажный"
        if (
            not slots.get("pump_type")
            and pump_use in {"водоснабжение", "подача воды", "повышение давления"}
            and ("центральн" in water_source or "водопровод" in water_source)
        ):
            # With a central main the source is already known.  The remaining
            # engineering task is pressure boosting; asking for the source
            # again traps short natural answers in the previous funnel step.
            slots["pump_type"] = "повысительный"
            slots["pump_use"] = "повышение давления"
        if (
            not slots.get("pump_type")
            and slots.get("water_source") == "скважина"
            and slots.get("well_depth_m")
        ):
            slots["pump_type"] = "скважинный"
        if not slots.get("pump_type") and (
            slots.get("mounting_length_mm")
            or slots.get("head_m")
            or slots.get("connection_size")
            or slots.get("old_model")
        ):
            slots["pump_type"] = "циркуляционный"
        if (
            slots.get("water_source") == "колодец"
            and normalize_text(str(slots.get("pump_use") or ""))
            in {"водоснабжение", "подача воды", "полив"}
        ):
            return self._well_water_supply(slots)
        if not slots.get("pump_type"):
            if slots.get("pump_replacement"):
                if "old_model" in deferred:
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "Понял, маркировка старого насоса неизвестна — не буду "
                            "просить её повторно. Где он работал: в системе отопления, "
                            "скважине/колодце, на откачке воды или на повышении давления? "
                            "По назначению определю тип насоса и дальше попрошу только "
                            "измеримые параметры подключения."
                        ),
                    )
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Для замены напишите модель или маркировку старого насоса и размер: "
                        "монтажную длину 130/180 мм; если видно — также напор 25/4 или 25/6."
                    ),
                )
            if (
                (slots.get("brand") or slots.get("reference_brand"))
                and slots.get("cheap")
                and slots.get("product_kind") == "насос"
            ):
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Уточните модель старого насоса или маркировку: например UPS 25-40/25-60, "
                        "монтажную длину 130/180 мм и присоединение."
                    ),
                )
            if slots.get("pump_use") == "повышение давления":
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "При слабом напоре уточните источник воды: центральный водопровод, "
                        "скважина или колодец? И где нужно повысить напор — в доме или для полива?"
                    ),
                )
            if slots.get("pump_use") == "водоснабжение":
                if slots.get("water_source") == "скважина":
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "Понял, источник — скважина. Уточните её глубину в метрах; "
                            "для точного расчёта затем понадобятся уровень воды, высота "
                            "подъёма и нужный расход."
                        ),
                    )
                if slots.get("water_source") == "колодец":
                    known: list[str] = []
                    if slots.get("well_ring_count") and slots.get("well_depth_m"):
                        rings_text = f"{float(slots['well_ring_count']):g}".replace(".", ",")
                        depth_text = f"{float(slots['well_depth_m']):g}".replace(".", ",")
                        known.append(
                            f"колодец {rings_text} кольца "
                            f"(~{depth_text} м при высоте кольца 0,9 м)"
                        )
                    if slots.get("dynamic_water_level_m"):
                        water_level_text = (
                            f"{float(slots['dynamic_water_level_m']):g}".replace(".", ",")
                        )
                        known.append(
                            f"глубина до воды ~{water_level_text} м"
                        )
                    if slots.get("required_flow_m3_h"):
                        known.append(
                            f"расход ~{float(slots['required_flow_m3_h']):g} м³/ч"
                        )
                    prefix = "Принял: " + "; ".join(known) + ". " if known else ""
                    if slots.get("flow_unit_assumed"):
                        return SlotFillingResult(
                            slots=slots,
                            needs_clarification=True,
                            question=(
                                prefix
                                + "100 литров предварительно понял как 100 л/мин (6 м³/ч). "
                                "Подтвердите: это литры в минуту или общий объём?"
                            ),
                        )
                    if not (
                        slots.get("dynamic_water_level_m")
                        or slots.get("static_water_level_m")
                    ):
                        question = "Уточните глубину от верха колодца до поверхности воды."
                    elif not slots.get("horizontal_run_m"):
                        question = "Какое расстояние по горизонтали от колодца до дома или полива?"
                    elif slots.get("lift_height_m") is None:
                        question = "На какую высоту выше поверхности воды нужно поднять воду?"
                    elif not slots.get("required_flow_m3_h"):
                        question = "Какой нужен расход: сколько литров в минуту?"
                    else:
                        return SlotFillingResult(slots=slots)
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=prefix + question,
                    )
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question="Источник воды какой: скважина, колодец или центральный водопровод?",
                )
            if slots.get("pump_use") == "полив":
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Для полива насос подбирают по источнику воды. Из бочки, ёмкости "
                        "или для откачки воды обычно смотрят дренажный; из скважины — "
                        "скважинный; из колодца или для подачи в дом — поверхностный насос "
                        "или насосную станцию. Циркуляционный насос нужен для отопления. "
                        "Откуда берём воду для полива?"
                    ),
                )
            if slots.get("application") == "дача":
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Для дачи насос нужен для водоснабжения из скважины/колодца, "
                        "для полива, повышения давления или откачки воды?"
                    ),
                )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Для какой задачи нужен насос: отопление, водоснабжение/полив, "
                    "повышение давления или откачка воды?"
                ),
            )
        if slots.get("pump_type") == "циркуляционный":
            has_explicit_duty = bool(
                slots.get("head_m")
                and slots.get("mounting_length_mm")
                and slots.get("connection_size")
            )
            if slots.get("old_model") or slots.get("pump_replacement"):
                # Replacement evidence is authoritative even if an earlier
                # turn provisionally classified a 25/6 marking as a new
                # selection.
                slots["pump_selection_mode"] = "замена"
            elif has_explicit_duty:
                # The customer supplied a concrete pump notation/dimensions.
                # Treat them as an explicit specification, not as a hydraulic
                # calculation performed by the bot.
                if (
                    slots.get("pump_selection_mode") != "замена"
                    and not slots.get("pump_selection_mode_explicit")
                ):
                    slots["pump_selection_mode"] = "по заданным параметрам"
            else:
                slots.setdefault("pump_selection_mode", "новый подбор")

            core_requirements = (
                (
                    "head_m",
                    ("head_m", "required_head_m"),
                    "напор (например 4 или 6 м)",
                ),
                (
                    "mounting_length_mm",
                    ("mounting_length_mm",),
                    "монтажную длину 130 или 180 мм",
                ),
                (
                    "connection_size",
                    ("connection_size",),
                    "присоединение (например 25 или 32)",
                ),
            )
            missing_core = [
                requirement
                for requirement in core_requirements
                if not any(slots.get(key) is not None for key in requirement[1])
            ]
            askable_core = [
                requirement
                for requirement in missing_core
                if not self._pump_requirement_deferred(deferred, *requirement[1])
            ]
            has_any_core_param = any(
                slots.get(key) is not None
                for key in (
                    "head_m",
                    "required_head_m",
                    "mounting_length_mm",
                    "connection_size",
                )
            )
            if missing_core:
                known_prefix = self._circulation_pump_known_prefix(slots)
                if slots.get("pump_selection_mode") == "замена":
                    marking_unknown = "old_model" in deferred
                    marking_note = (
                        "Маркировку оставил неизвестной и больше её не повторяю. "
                        if marking_unknown
                        else (
                            "Если маркировка читается, можно вместо замеров прислать её целиком. "
                            if not slots.get("old_model")
                            else ""
                        )
                    )
                    if askable_core:
                        return SlotFillingResult(
                            slots=slots,
                            needs_clarification=True,
                            question=(
                                known_prefix
                                + marking_note
                                + "Для замены не хватает только: "
                                + "; ".join(item[2] for item in askable_core)
                                + ". Монтажную длину измеряют между плоскостями "
                                "подключений; присоединение часто указано как DN25/DN32."
                            ),
                            expected_slots=[item[0] for item in askable_core],
                            blocking=True,
                        )
                    if has_any_core_param:
                        # The user explicitly cannot supply the remaining
                        # dimensions. Retrieval may still show catalogue
                        # candidates filtered by the confirmed dimensions, but
                        # the deferred-slot caveat makes the result non-final.
                        slots["preliminary_selection"] = True
                        return SlotFillingResult(slots=slots)
                # A bare «не знаю» means the customer needs help with the
                # whole question.  A named refusal (for example only the flow)
                # must not erase the head and dimensions supplied in the same
                # message.
                if (
                    self._does_not_know_params(text)
                    and not explicitly_unknown
                    and not has_any_core_param
                ):
                    if slots.get("pump_param_help_given"):
                        return SlotFillingResult(
                            slots=slots,
                            needs_clarification=True,
                            question=(
                                "Без расчётного расхода и напора не буду предлагать "
                                "случайный насос. Перепишите маркировку старого насоса "
                                "для замены либо данные из расчёта системы и с шильдика; "
                                "к сожалению, этот чат пока не принимает фотографии. Монтажную "
                                "длину измеряют между плоскостями подключений."
                            ),
                        )
                    slots["pump_param_help_given"] = True
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "Не страшно, монтажную длину можно посмотреть на старом насосе "
                            "или измерить между присоединительными плоскостями корпуса, "
                            "не включая накидные гайки и переходники — часто бывает 130 или "
                            "180 мм. Напор обычно пишется в маркировке насоса: например "
                            "25-40 или 25-60. Если старого насоса нет, для нового подбора "
                            "нужны расчётный расход (м³/ч), напор (м) и схема: радиаторы, "
                            "тёплый пол или комбинированная система."
                        ),
                    )
                if has_any_core_param:
                    missing_for_new_selection = [
                        (item[0], item[2]) for item in askable_core
                    ]
                    if (
                        not slots.get("required_flow_m3_h")
                        and not self._pump_requirement_deferred(
                            deferred,
                            "required_flow_m3_h",
                            "required_flow_l_min",
                        )
                    ):
                        missing_for_new_selection.append(
                            ("required_flow_m3_h", "расчётный расход в м³/ч")
                        )
                    if (
                        not slots.get("system_type")
                        and "system_type" not in deferred
                    ):
                        missing_for_new_selection.append(
                            (
                                "system_type",
                                "схема системы: радиаторы, тёплый пол или оба контура",
                            )
                        )
                    flow_note = (
                        " Расход не угадываю: его можно взять из расчёта системы "
                        "или определить со специалистом по тепловой нагрузке и перепаду температур."
                        if "required_flow_m3_h" in deferred
                        else ""
                    )
                    if missing_for_new_selection:
                        return SlotFillingResult(
                            slots=slots,
                            needs_clarification=True,
                            question=(
                                known_prefix
                                + "Для точного нового подбора не хватает только: "
                                + "; ".join(
                                    label for _, label in missing_for_new_selection
                                )
                                + "."
                                + flow_note
                            ),
                            expected_slots=[
                                key for key, _ in missing_for_new_selection
                            ],
                            blocking=True,
                        )
                    slots["preliminary_selection"] = True
                    return SlotFillingResult(slots=slots)
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Понял, нужен циркуляционный насос. Это замена старого или новый "
                        "подбор? Для замены пришлите маркировку, присоединение и монтажную "
                        "длину. Для нового подбора нужны расчётный расход (м³/ч), напор (м) "
                        "и схема системы; монтажный размер всё равно нужно сверить."
                    ),
                    expected_slots=[
                        "pump_selection_mode",
                        "old_model",
                        "head_m",
                        "mounting_length_mm",
                        "connection_size",
                        "required_flow_m3_h",
                        "system_type",
                    ],
                    blocking=True,
                )
            if (
                slots.get("pump_selection_mode") == "новый подбор"
                and (
                    not slots.get("required_flow_m3_h")
                    or not slots.get("system_type")
                )
            ):
                missing_duty: list[tuple[str, str]] = []
                if (
                    not slots.get("required_flow_m3_h")
                    and not self._pump_requirement_deferred(
                        deferred,
                        "required_flow_m3_h",
                        "required_flow_l_min",
                    )
                ):
                    missing_duty.append(
                        ("required_flow_m3_h", "расчётный расход в м³/ч")
                    )
                if not slots.get("system_type") and "system_type" not in deferred:
                    missing_duty.append(
                        (
                            "system_type",
                            "схема системы: радиаторы, тёплый пол или оба контура",
                        )
                    )
                if missing_duty:
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "Монтажные параметры понял. Для нового подбора ещё уточните: "
                            + "; ".join(label for _, label in missing_duty)
                            + ". Если это замена по уже заданной маркировке, так и напишите — "
                            "тогда подберу по ней."
                        ),
                        expected_slots=[key for key, _ in missing_duty],
                        blocking=True,
                    )
                slots["preliminary_selection"] = True
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "скважинный":
            missing: list[tuple[str, tuple[str, ...], str]] = []
            if not (
                slots.get("dynamic_water_level_m")
                or slots.get("static_water_level_m")
            ):
                missing.append(
                    (
                        "dynamic_water_level_m",
                        ("dynamic_water_level_m", "static_water_level_m"),
                        "динамический уровень воды",
                    )
                )
            if not slots.get("lift_height_m"):
                missing.append(
                    (
                        "lift_height_m",
                        ("lift_height_m",),
                        "высоту от уровня воды до верхней точки",
                    )
                )
            if not slots.get("horizontal_run_m"):
                missing.append(
                    (
                        "horizontal_run_m",
                        ("horizontal_run_m",),
                        "длину горизонтальной трассы",
                    )
                )
            if not slots.get("required_pressure_bar"):
                missing.append(
                    (
                        "required_pressure_bar",
                        ("required_pressure_bar",),
                        "нужное давление в доме",
                    )
                )
            if not slots.get("required_flow_m3_h"):
                missing.append(
                    (
                        "required_flow_m3_h",
                        ("required_flow_m3_h", "required_flow_l_min"),
                        "требуемый расход",
                    )
                )
            if (
                not slots.get("required_head_m")
                and not slots.get("discharge_diameter_mm")
            ):
                missing.append(
                    (
                        "discharge_diameter_mm",
                        ("required_head_m", "discharge_diameter_mm"),
                        "внутренний диаметр напорной трубы; для ПНД можно наружный диаметр и SDR",
                    )
                )
            askable = [
                requirement
                for requirement in missing
                if not self._pump_requirement_deferred(
                    deferred,
                    *requirement[1],
                )
            ]
            if askable:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Одной глубины скважины недостаточно. Чтобы я рассчитал "
                        "расчётный напор, величину потерь в трассе и рабочую точку, уточните: "
                        + "; ".join(item[2] for item in askable[:3])
                        + (
                            ". Затем проверим остальные данные и рабочую точку насоса."
                            if len(askable) > 3
                            else "."
                        )
                    ),
                    expected_slots=[item[0] for item in askable[:3]],
                    blocking=True,
                )
            if missing:
                slots["preliminary_selection"] = True
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "дренажный":
            missing: list[tuple[str, tuple[str, ...], str]] = []
            if not slots.get("water_quality"):
                missing.append(
                    (
                        "water_quality",
                        ("water_quality",),
                        "какая вода: чистая, грязная или фекальная",
                    )
                )
            if (
                slots.get("water_quality") in {"грязная", "фекальная"}
                and slots.get("solids_mm") is None
            ):
                missing.append(
                    (
                        "solids_mm",
                        ("solids_mm",),
                        "наблюдаемый тип включений/максимальный размер частиц в воде",
                    )
                )
            if slots.get("required_head_m") is None and slots.get("lift_height_m") is None:
                missing.append(
                    (
                        "lift_height_m",
                        ("required_head_m", "lift_height_m"),
                        "вертикальный подъём до точки сброса",
                    )
                )
            if slots.get("required_head_m") is None and slots.get("horizontal_run_m") is None:
                missing.append(
                    (
                        "horizontal_run_m",
                        ("required_head_m", "horizontal_run_m"),
                        "длину горизонтального отвода",
                    )
                )
            if (
                slots.get("required_head_m") is None
                and slots.get("discharge_diameter_mm") is None
            ):
                missing.append(
                    (
                        "discharge_diameter_mm",
                        ("required_head_m", "discharge_diameter_mm"),
                        "внутренний диаметр шланга или напорной трубы",
                    )
                )
            if not slots.get("required_flow_m3_h"):
                missing.append(
                    (
                        "required_flow_m3_h",
                        ("required_flow_m3_h", "required_flow_l_min"),
                        "нужную производительность",
                    )
                )
            askable = [
                requirement
                for requirement in missing
                if not self._pump_requirement_deferred(
                    deferred,
                    *requirement[1],
                )
            ]
            if askable:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Для дренажного насоса уточните: "
                        + "; ".join(item[2] for item in askable[:3])
                        + ". По этим данным я рассчитаю предварительный напор; "
                        "модель затем нужно проверить по Q–H-кривой."
                    ),
                    expected_slots=[item[0] for item in askable[:3]],
                    blocking=True,
                )
            if missing:
                slots["preliminary_selection"] = True
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "канализационная насосная установка":
            discharge = slots.get("discharge_diameter_mm") or slots.get("diameter_mm")
            if discharge is not None and float(discharge) >= 75:
                # 110 mm next to a toilet normally describes the gravity inlet,
                # not the smaller pressure outlet of a sanitary station.  Do
                # not use it as a hard discharge filter and reject every valid
                # catalogue unit.
                slots["gravity_inlet_diameter_mm"] = float(discharge)
                slots.pop("discharge_diameter_mm", None)
                slots.pop("diameter_mm", None)
                slots["preliminary_selection"] = True
            missing: list[tuple[str, str]] = []
            if not slots.get("connected_fixtures"):
                missing.append(
                    (
                        "connected_fixtures",
                        "какие приборы подключаются: унитаз, раковина, душ, ванна или техника",
                    )
                )
            if slots.get("lift_height_m") is None:
                missing.append(
                    (
                        "lift_height_m",
                        "вертикальный подъём от установки до точки сброса",
                    )
                )
            if slots.get("horizontal_run_m") is None:
                missing.append(
                    (
                        "horizontal_run_m",
                        "длину горизонтального напорного участка",
                    )
                )
            if not (slots.get("diameter_mm") or slots.get("discharge_diameter_mm")):
                # The outlet is a property of the candidate station.  Without
                # it we can still show a preliminary shortlist from fixtures
                # and lift/run, then tell the customer which pressure pipe the
                # selected model requires.
                slots["preliminary_selection"] = True
            askable = [item for item in missing if item[0] not in deferred]
            if askable:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "КНС/санитарный насос нельзя выбирать только по слову «санузел»: "
                        "нужно проверить допустимые стоки, число входов и рабочую точку. "
                        "Уточните: "
                        + "; ".join(label for _, label in askable[:3])
                        + "."
                    ),
                    expected_slots=[key for key, _ in askable[:3]],
                    blocking=True,
                )
            if missing:
                slots["preliminary_selection"] = True
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "повысительный":
            if not slots.get("water_source") and "water_source" not in deferred:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Уточните источник слабого напора: центральный водопровод, "
                        "скважина или колодец? Причины и схема повышения давления "
                        "для них различаются."
                    ),
                    expected_slots=["water_source"],
                    blocking=True,
                )
            if (
                slots.get("inlet_pressure_bar") is None
                and "inlet_pressure_bar" not in deferred
            ):
                if slots.get("symptom") or any(
                    marker in text
                    for marker in ["еле теч", "слабо теч", "плохо теч", "манометр"]
                ):
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "До покупки насоса исключите местное ограничение: сравните напор "
                            "на всех точках, проверьте, полностью ли открыт вводной кран, и "
                            "очистите доступные сетки-аэраторы и фильтр грубой очистки без "
                            "разборки опломбированных узлов. Затем попросите сантехника или "
                            "водоснабжающую организацию измерить динамическое давление на "
                            "вводе именно в слабый вечерний период. Какое давление получится "
                            "на вводе, в барах?"
                        ),
                        expected_slots=["inlet_pressure_bar"],
                        blocking=True,
                    )
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Какое давление сейчас на входе из центрального водопровода, в барах?"
                    ),
                    expected_slots=["inlet_pressure_bar"],
                    blocking=True,
                )
            if (
                slots.get("required_pressure_bar") is None
                and "required_pressure_bar" not in deferred
            ):
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Какое давление нужно получить после насоса, в барах?"
                    ),
                    expected_slots=["required_pressure_bar"],
                    blocking=True,
                )
            if (
                not slots.get("required_flow_m3_h")
                and not self._pump_requirement_deferred(
                    deferred,
                    "required_flow_m3_h",
                    "required_flow_l_min",
                )
            ):
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Какой нужен расход при одновременном водоразборе, "
                        "например в литрах в минуту?"
                    ),
                    expected_slots=["required_flow_m3_h"],
                    blocking=True,
                )
            if any(
                key in deferred
                for key in (
                    "water_source",
                    "inlet_pressure_bar",
                    "required_pressure_bar",
                    "required_flow_m3_h",
                )
            ):
                slots["preliminary_selection"] = True
        return SlotFillingResult(slots=slots)

    def _well_water_supply(self, slots: dict) -> SlotFillingResult:
        deferred = {
            str(key)
            for key in slots.get("deferred_slot_keys") or []
            if slots.get(str(key)) in (None, "", [], {})
        }
        if deferred:
            slots["deferred_slot_keys"] = sorted(deferred)
        else:
            slots.pop("deferred_slot_keys", None)
        known: list[str] = []
        if slots.get("well_ring_count") and slots.get("well_depth_m"):
            rings_text = f"{float(slots['well_ring_count']):g}".replace(".", ",")
            depth_text = f"{float(slots['well_depth_m']):g}".replace(".", ",")
            ring_height_text = f"{float(slots.get('ring_height_m') or 0.9):g}".replace(
                ".",
                ",",
            )
            known.append(
                f"колодец {rings_text} кольца "
                f"(~{depth_text} м при высоте кольца {ring_height_text} м)"
            )
        water_level = (
            slots.get("water_level_depth_m")
            or slots.get("dynamic_water_level_m")
            or slots.get("static_water_level_m")
        )
        water_level_deferred = bool(
            {
                "water_level_depth_m",
                "dynamic_water_level_m",
                "static_water_level_m",
            }.intersection(deferred)
        )
        if water_level is not None:
            level_text = f"{float(water_level):g}".replace(".", ",")
            known.append(f"глубина до воды ~{level_text} м")
        if slots.get("water_column_depth_m") is not None:
            column_text = f"{float(slots['water_column_depth_m']):g}".replace(
                ".", ","
            )
            known.append(f"столб воды ~{column_text} м")
        if slots.get("required_flow_m3_h"):
            known.append(f"расход ~{float(slots['required_flow_m3_h']):g} м³/ч")
        if slots.get("required_head_m") and slots.get("required_head_calculated"):
            head_text = f"{float(slots['required_head_m']):g}".replace(".", ",")
            known.append(f"расчётный требуемый напор ~{head_text} м")
        prefix = "Принял: " + "; ".join(known) + ". " if known else ""

        ambiguous_level = slots.get("water_level_reference") == "ambiguous"
        if ambiguous_level and not slots.get("water_level_reference_question_asked"):
            ring_count = float(slots.get("water_level_ring_count") or 0)
            ring_text = f"{ring_count:g}".replace(".", ",")
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    prefix
                    + f"Уточните только направление отсчёта: {ring_text} кольца "
                    "считаются от верха колодца до поверхности воды или это "
                    f"{ring_text} кольца воды от дна до поверхности?"
                ),
            )
        if slots.get("flow_unit_assumed"):
            flow_l_min = float(slots.get("required_flow_l_min") or 0)
            flow_m3_h = float(slots.get("required_flow_m3_h") or 0)
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    prefix
                    + f"{flow_l_min:g} литров предварительно понял как "
                    f"{flow_l_min:g} л/мин ({flow_m3_h:g} м³/ч). "
                    "Подтвердите: это литры в минуту или общий объём?"
                ),
            )
        expected_slot: str | None = None
        if water_level is None and not ambiguous_level and not water_level_deferred:
            question = "Уточните глубину от верха колодца до поверхности воды."
            expected_slot = "water_level_depth_m"
        elif not slots.get("horizontal_run_m") and "horizontal_run_m" not in deferred:
            question = "Какое расстояние по горизонтали от колодца до дома или полива?"
            expected_slot = "horizontal_run_m"
        elif slots.get("lift_height_m") is None and "lift_height_m" not in deferred:
            question = (
                "Есть ли дополнительный перепад высоты от уровня земли у колодца "
                "до точки полива? Если участок ровный — ответьте «0 метров»; "
                "иначе укажите, на сколько метров точка выше."
            )
            expected_slot = "lift_height_m"
        elif (
            not slots.get("required_flow_m3_h")
            and not self._pump_requirement_deferred(
                deferred,
                "required_flow_m3_h",
                "required_flow_l_min",
            )
        ):
            question = "Какой нужен расход: сколько литров в минуту?"
            expected_slot = "required_flow_m3_h"
        elif ambiguous_level:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Осталось определить положение воды. Сейчас известно число "
                    "колец, но неизвестно, отсчитывалось оно сверху или снизу; "
                    "без этого глубину до воды и тип насоса не подтверждаю."
                ),
            )
        elif deferred:
            # The customer has explicitly said that the remaining field(s)
            # are unavailable.  Re-asking one of them under a different label
            # is still the same loop.  Let the catalogue layer show a clearly
            # caveated preliminary shortlist from the confirmed facts.
            slots["preliminary_selection"] = True
            return SlotFillingResult(slots=slots)
        else:
            # The installation family follows from suction depth; the customer
            # is not asked to make an engineering choice on our behalf.
            if not slots.get("pump_type"):
                slots["pump_type"] = (
                    "поверхностный" if float(water_level) < 8.0 else "колодезный"
                )
            slots["pump_type_decision"] = (
                "уровень воды менее 8 м — возможен поверхностный насос/станция"
                if float(water_level) < 8.0
                else "уровень воды 8 м или глубже — нужен погружной колодезный насос"
            )
            return SlotFillingResult(slots=slots)
        return SlotFillingResult(
            slots=slots,
            needs_clarification=True,
            question=prefix + question,
            expected_slots=[expected_slot] if expected_slot else [],
            blocking=True,
        )

    def _does_not_know_params(self, text: str) -> bool:
        markers = [
            "не знаю",
            "незнаю",
            "не понимаю",
            "без понятия",
            "не в курсе",
            "не помню",
            "не могу сказать",
            "нет данных",
            "данных нет",
            "неизвестно",
            "неизвестны",
            "неизвестен",
            "неизвестна",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _pump_parameter_is_present(slots: dict, key: str) -> bool:
        """Whether a deferred pump fact has since received a real answer.

        ``head_m`` is a pump-marking capability while ``required_head_m`` is a
        calculated duty point, but either one resolves a generic dialogue
        question about the head.  Flow has the same raw/canonical pair.  This
        equivalence is deliberately limited to dialogue deferrals; catalogue
        filtering keeps the engineering meanings separate.
        """

        equivalents = {
            "head_m": ("head_m", "required_head_m"),
            "required_head_m": ("head_m", "required_head_m"),
            "required_flow_m3_h": ("required_flow_m3_h", "required_flow_l_min"),
            "required_flow_l_min": ("required_flow_m3_h", "required_flow_l_min"),
        }
        keys = equivalents.get(key, (key,))
        return any(
            candidate in slots
            and slots[candidate] is not None
            and slots[candidate] not in ("", [], {})
            for candidate in keys
        )

    def _prune_filled_pump_deferred_slots(self, slots: dict) -> set[str]:
        """Remove stale refusal markers once the customer supplies the value."""

        deferred = {
            str(key)
            for key in slots.get("deferred_slot_keys") or []
            if not self._pump_parameter_is_present(slots, str(key))
        }
        if deferred:
            slots["deferred_slot_keys"] = sorted(deferred)
        else:
            slots.pop("deferred_slot_keys", None)
            slots.pop("preliminary_selection", None)
        return deferred

    @staticmethod
    def _explicitly_unknown_pump_fields(text: str) -> set[str]:
        """Return only pump fields explicitly tied to an uncertainty phrase.

        Scoping to a comma/disjunctive clause is important for mixed replies:
        ``расход не знаю, напор 6 м, длина 180 мм`` refuses only the
        flow.  A whole-message ``не знаю`` check used to discard the two
        valid facts and answer as though the head were also absent.
        """

        field_patterns = {
            "required_flow_m3_h": re.compile(
                r"\b(?:расход|производительност|подач)\w*\b"
            ),
            "head_m": re.compile(r"\bнапор\w*\b"),
            "mounting_length_mm": re.compile(
                r"\b(?:монтажн\w*\s+длин\w*|межосев\w*|длин\w*)\b"
            ),
            "connection_size": re.compile(
                r"\b(?:присоедин\w*|подключен\w*|диаметр\w*|dn)\b"
            ),
            "old_model": re.compile(
                r"\b(?:маркировк\w*|модел\w*|шильдик\w*)\b"
            ),
            "system_type": re.compile(r"\b(?:схем\w*|тип\w*\s+систем\w*)\b"),
            "dynamic_water_level_m": re.compile(
                r"\b(?:динамическ\w*\s+уров\w*|уров\w*\s+вод\w*)\b"
            ),
            "lift_height_m": re.compile(
                r"\b(?:высот\w*\s+под[ъь]?ем\w*|перепад\w*\s+высот\w*|"
                r"вертикальн\w*\s+под[ъь]?ем\w*)\b"
            ),
            "horizontal_run_m": re.compile(
                r"\b(?:длин\w*\s+трасс\w*|горизонтальн\w*\s+"
                r"(?:трасс\w*|отвод\w*)|расстоян\w*\s+по\s+горизонтал\w*)\b"
            ),
            "required_pressure_bar": re.compile(
                r"\b(?:нужн\w*\s+давлен\w*|давлен\w*\s+после\s+насос\w*)\b"
            ),
            "inlet_pressure_bar": re.compile(
                r"\b(?:входн\w*\s+давлен\w*|давлен\w*\s+на\s+вход\w*|"
                r"давлен\w*\s+в\s+водопровод\w*)\b"
            ),
            "discharge_diameter_mm": re.compile(
                r"\b(?:диаметр\w*\s+(?:шланг\w*|напорн\w*\s+труб\w*)|"
                r"напорн\w*\s+труб\w*)\b"
            ),
            "water_quality": re.compile(
                r"\b(?:качеств\w*\s+вод\w*|чист\w*\s+или\s+грязн\w*|"
                r"тип\w*\s+сток\w*)\b"
            ),
            "solids_mm": re.compile(
                r"\b(?:размер\w*\s+частиц\w*|частиц\w*|включен\w*)\b"
            ),
            "connected_fixtures": re.compile(
                r"\b(?:подключаем\w*\s+прибор\w*|сантехнич\w*\s+прибор\w*|"
                r"что\s+подключ\w*)\b"
            ),
        }
        return set(bind_local_refusals(text, field_patterns))

    def _remember_explicit_pump_refusals(self, slots: dict, text: str) -> set[str]:
        unknown = self._explicitly_unknown_pump_fields(text)
        if not unknown:
            return set()
        deferred = {str(key) for key in slots.get("deferred_slot_keys") or []}
        deferred.update(unknown)
        slots["deferred_slot_keys"] = sorted(deferred)
        return unknown

    @staticmethod
    def _pump_requirement_deferred(deferred: set[str], *keys: str) -> bool:
        """Whether any canonical representation of a pump fact was refused."""

        aliases = {
            "head_m": {"head_m", "required_head_m"},
            "required_head_m": {"head_m", "required_head_m"},
            "required_flow_m3_h": {
                "required_flow_m3_h",
                "required_flow_l_min",
            },
            "required_flow_l_min": {
                "required_flow_m3_h",
                "required_flow_l_min",
            },
        }
        candidates: set[str] = set()
        for key in keys:
            candidates.update(aliases.get(key, {key}))
        return bool(candidates.intersection(deferred))

    @staticmethod
    def _circulation_pump_known_prefix(slots: dict) -> str:
        known: list[str] = []
        head = slots.get("head_m") or slots.get("required_head_m")
        if head:
            known.append(f"напор {float(head):g} м")
        if slots.get("mounting_length_mm"):
            known.append(
                f"монтажная длина {int(slots['mounting_length_mm'])} мм"
            )
        if slots.get("connection_size"):
            known.append(f"присоединение {slots['connection_size']}")
        return "Записал: " + "; ".join(known) + ". " if known else ""

    def _boilers(self, slots: dict) -> SlotFillingResult:
        pair_relation = normalize_text(
            str(slots.get("boiler_water_heater_relation") or "")
        )
        if slots.get("boiler_water_heater_pair") and not pair_relation:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Вы имеете в виду два отдельных прибора — котёл для отопления "
                    "и отдельный водонагреватель — или котёл со встроенным бойлером?"
                ),
                expected_slots=["boiler_water_heater_relation"],
                blocking=True,
            )
        pair_prefix = ""
        if pair_relation == "отдельные приборы":
            pair_prefix = (
                "Понял, нужны два отдельных прибора. Сначала уточним котёл, "
                "затем отдельно подберём водонагреватель. "
            )
        elif pair_relation == "встроенный бойлер":
            pair_prefix = "Понял, нужен котёл со встроенным бойлером. "
            slots["boiler_requirement"] = "с бойлером"
        if not slots.get("boiler_type"):
            assisted = self._observable_unknown_result(
                "boilers", slots, ["boiler_type"]
            )
            if assisted is not None:
                return assisted
            area = slots.get("area_m2")
            prefix = (
                f"Понял, подбираем котёл примерно на {float(area):g} м². "
                if area
                else pair_prefix
            )
            source_question = (
                "Газа нет — какой источник выбираете: электричество, "
                "твёрдое топливо или другой?"
                if slots.get("gas_available") is False
                or slots.get("has_gas") is False
                else "Газовый или электрический, либо твердотопливный?"
            )
            if not area and not pair_prefix:
                source_question += " И на какую площадь нужен котёл?"
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=prefix + source_question,
                expected_slots=["boiler_type", "area_m2"],
                blocking=True,
            )
        if not slots.get("area_m2") and not slots.get("power_kw"):
            assisted = self._observable_unknown_result(
                "boilers", slots, ["area_m2", "power_kw"]
            )
            if assisted is not None:
                return assisted
            prefix = pair_prefix
            if slots.get("contours") == "двухконтурный":
                prefix = "Понял, нужен двухконтурный котёл — с горячей водой. "
            elif slots.get("contours") == "одноконтурный":
                prefix = "Понял, одноконтурный — только отопление. "
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=prefix + "На какую площадь подбираете котёл?",
                expected_slots=["area_m2", "power_kw"],
                blocking=True,
            )
        if slots.get("boiler_type") == "газовый" and not slots.get("contours"):
            assisted = self._observable_unknown_result(
                "boilers", slots, ["contours", "needs_hot_water"]
            )
            if assisted is not None:
                return assisted
            chimney_note = (
                "Старый кирпичный дымоход обязательно учитывают, но заранее "
                "считать его совместимым или обязательной заменой на коаксиальный "
                "нельзя: это зависит от камеры сгорания и требований паспорта "
                "выбранного котла, а состояние и тягу проверяет специалист. "
                if slots.get("needs_chimney")
                else ""
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    chimney_note
                    + "Котёл нужен только для отопления или ещё для горячей воды?"
                ),
                expected_slots=["contours", "needs_hot_water"],
                blocking=True,
            )
        if (
            slots.get("boiler_type") == "электрический"
            and slots.get("needs_voltage_clarification")
            and not slots.get("voltage_v")
        ):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Какое питание доступно для котла: 220 или 380 В?",
                expected_slots=["voltage_v"],
                blocking=True,
            )
        return SlotFillingResult(slots=slots)

    def _water_heaters(self, slots: dict) -> SlotFillingResult:
        # Normalise aliases defensively for sessions created by an LLM or an
        # older deployment.  ``heater_type`` is the canonical key used by the
        # router and search layer.
        if not slots.get("heater_type"):
            for alias in ["water_heater_type", "heating_type"]:
                if slots.get(alias):
                    slots["heater_type"] = slots[alias]
                    break

        heater_type = normalize_text(str(slots.get("heater_type") or ""))
        if "косвен" in heater_type or "indirect" in heater_type:
            slots["heater_type"] = "косвенного нагрева"
            slots.setdefault("energy_source", "косвенный")
        elif "проточ" in heater_type or "instant" in heater_type or "tankless" in heater_type:
            slots["heater_type"] = "проточный"
        elif "накоп" in heater_type or "storage" in heater_type:
            slots["heater_type"] = "накопительный"

        if not slots.get("heater_type"):
            known_volume = slots.get("volume_l")
            prefix = (
                f"Понял, нужен водонагреватель объёмом {float(known_volume):g} л. "
                if known_volume
                else ""
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    prefix
                    + "Какой тип нужен: накопительный, проточный или бойлер "
                    "косвенного нагрева?"
                ),
            )

        missing: list[str] = []
        if not slots.get("energy_source"):
            missing.append("источник нагрева: электрический или газовый")
        if (
            slots.get("heater_type") != "проточный"
            and not slots.get("volume_l")
        ):
            missing.append("объём в литрах")
        if missing:
            if len(missing) == 1:
                question = f"Уточните {missing[0]}."
            else:
                question = (
                    "Уточните источник нагрева — электрический или газовый — "
                    "и нужный объём в литрах."
                )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=question,
            )
        return SlotFillingResult(slots=slots)

    def _hydraulic_accumulators(self, slots: dict) -> SlotFillingResult:
        """Collect vessel sizing facts without confusing it with a pump."""
        application = normalize_text(str(slots.get("tank_application") or ""))
        if not application:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Бак нужен как гидроаккумулятор для водоснабжения/насоса или "
                    "как расширительный бак для закрытой системы отопления? Это разные "
                    "назначения, подменять одно другим нельзя."
                ),
            )
        if slots.get("volume_l") is None:
            if "водоснаб" in application:
                question = (
                    "Какой расчётный объём гидроаккумулятора нужен в литрах? Если он ещё "
                    "не рассчитан, укажите тип и мощность насоса, требуемый расход, "
                    "давления включения/отключения и допустимое число пусков — по одной "
                    "цене безопасно выбирать объём нельзя."
                )
            else:
                question = (
                    "Какой расчётный объём расширительного бака нужен в литрах? Для "
                    "расчёта нужны общий объём теплоносителя, минимальная/максимальная "
                    "температура и рабочие давления системы."
                )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=question,
            )
        return SlotFillingResult(slots=slots)

    def _reconcile_water_heater_negations(
        self,
        text: str,
        slots: dict,
        *,
        previous_slots: dict,
        current_slots: dict,
    ) -> None:
        """Remove a rejected value when no replacement was supplied this turn."""
        rejected_types = []
        if re.search(r"\bне\s+проточн\w*\b", text):
            rejected_types.append("проточный")
        if re.search(r"\bне\s+накопительн\w*\b", text):
            rejected_types.append("накопительный")
        if re.search(r"\bне\s+косвенн\w*\b", text):
            rejected_types.append("косвенного нагрева")
        if (
            "heater_type" not in current_slots
            and normalize_text(str(previous_slots.get("heater_type") or ""))
            in rejected_types
        ):
            slots.pop("heater_type", None)

        previous_source = normalize_text(
            str(previous_slots.get("energy_source") or "")
        )
        if (
            "energy_source" not in current_slots
            and (
                (re.search(r"\bне\s+электр\w*\b", text) and "электр" in previous_source)
                or (re.search(r"\bне\s+газов\w*\b", text) and "газ" in previous_source)
            )
        ):
            slots.pop("energy_source", None)

        rejected_volume = re.search(
            r"\bне\s+(\d{1,4}(?:[,.]\d+)?)"
            r"(?:\s*(?:л\b|литр(?:а|ов)?\b))?",
            text,
        )
        if (
            "volume_l" not in current_slots
            and rejected_volume
            and previous_slots.get("volume_l") is not None
        ):
            previous_volume = float(previous_slots["volume_l"])
            rejected_value = float(rejected_volume.group(1).replace(",", "."))
            if previous_volume == rejected_value:
                slots.pop("volume_l", None)

        rejected_mounting = []
        if re.search(r"\bне\s+настенн\w*\b", text):
            rejected_mounting.append("настенный")
        if re.search(r"\bне\s+напольн\w*\b", text):
            rejected_mounting.append("напольный")
        if re.search(r"\bне\s+под\s+мойк\w*\b", text):
            rejected_mounting.append("под мойкой")
        if re.search(r"\bне\s+над\s+мойк\w*\b", text):
            rejected_mounting.append("над мойкой")
        if (
            "mounting" not in current_slots
            and normalize_text(str(previous_slots.get("mounting") or ""))
            in rejected_mounting
        ):
            slots.pop("mounting", None)

        rejected_orientations = []
        if re.search(r"\bне\s+вертикальн\w*\b", text):
            rejected_orientations.append("вертикальный")
        if re.search(r"\bне\s+горизонтальн\w*\b", text):
            rejected_orientations.append("горизонтальный")
        if re.search(r"\bне\s+универсальн\w*\b", text):
            rejected_orientations.append("универсальный")
        if (
            "orientation" not in current_slots
            and normalize_text(str(previous_slots.get("orientation") or ""))
            in rejected_orientations
        ):
            slots.pop("orientation", None)

    def _valves(self, slots: dict, text: str) -> SlotFillingResult:
        if "унитаз" in text and any(
            marker in text for marker in ["перекры", "ручк", "подвод", "перед"]
        ):
            slots.setdefault("valve_kind", "угловой кран")
            slots.setdefault("application", "вода")
            slots.setdefault("water_temperature", "холодная")
            slots.setdefault("installation_context", "перед унитазом")
        if "дренаж" in text and "кран" in text:
            slots["valve_kind"] = "дренажный кран"
        elif "обратн" in text and "клапан" in text:
            slots["valve_kind"] = "обратный клапан"
        elif "шаров" in text or "кран" in text:
            # In the product funnel an unqualified isolation ``кран`` means a
            # ball valve.  Drain cocks and check valves require an explicit ask.
            slots["valve_kind"] = "шаровый кран"
        elif "вентил" in text:
            slots["valve_kind"] = "вентиль"
        elif "клапан" in text:
            slots["valve_kind"] = "клапан"

        if "радиатор" in text:
            slots["application"] = "радиатор"
        elif "отоплен" in text:
            slots["application"] = "отопление"
        elif "горяч" in text or "холодн" in text or mentions_water_application(text):
            slots["application"] = "вода"

        if slots.get("installation_context") == "перед унитазом":
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "По описанию это обычно угловой запорный кран перед гибкой "
                    "подводкой бачка. Полдюйма может относиться только к одной стороне: "
                    "уточните, какая резьба выходит из стены (наружная или внутренняя) "
                    "и какой размер гайки подводки — 1/2 или 3/8. Лучше сверить "
                    "маркировку шланга и описать соединения словами. К сожалению, "
                    "этот чат пока не принимает фотографии."
                ),
            )

        contract_slots = dict(slots)
        # A distinctive catalogue family/model plus an explicit port topology
        # is an identity lookup, not an attempt to infer application safety.
        # It is safe to show that grounded card while leaving applicability to
        # a later question.  Generic requests ("кран 1/2") still have to state
        # both application and thread pair before any alternatives are shown.
        if (
            contract_slots.get("name_tokens")
            and contract_slots.get("thread_type")
        ):
            contract_slots.setdefault("application", "catalogue_identity")

        contracts = [VALVE_BASE_CONTRACT]
        product_kind = str(slots.get("product_kind") or "").strip().lower()
        valve_kind = normalize_text(str(slots.get("valve_kind") or ""))
        # A lone inch size on a ball valve does not describe both ends.  FF,
        # FM and MM are different, non-interchangeable SKUs, so retrieval must
        # wait for the pair.  DN/flanged/polymer valves are intentionally not
        # forced through this threaded-small-valve contract.
        if slots.get("size_inch") and not slots.get("union") and (
            product_kind == "ball_valve" or "шаров" in valve_kind
        ):
            contracts.append(THREADED_BALL_VALVE_CONTRACT)
        missing = missing_requirements(contract_slots, *contracts)
        if missing:
            visible_missing = missing[:2]
            missing_slots = list(
                dict.fromkeys(
                    slot
                    for requirement in visible_missing
                    for slot in requirement.any_of
                )
            )
            assisted = self._observable_unknown_result(
                "valves", slots, missing_slots
            )
            if assisted is not None:
                return assisted
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + " и ".join(
                    requirement.prompt for requirement in visible_missing
                ) + ".",
                expected_slots=missing_slots,
                blocking=True,
            )
        hot_or_heating = bool(
            normalize_text(str(slots.get("application") or "")) == "отопление"
            or normalize_text(str(slots.get("water_temperature") or ""))
            == "горячая"
        )
        if hot_or_heating and (
            not slots.get("operating_temperature_c")
            or not slots.get("operating_pressure_bar")
        ):
            missing_regime = [
                key
                for key in ("operating_temperature_c", "operating_pressure_bar")
                if not slots.get(key)
            ]
            assisted = self._observable_unknown_result(
                "valves", slots, missing_regime
            )
            if assisted is not None:
                return assisted
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Для крана на отопление/горячую воду укажите максимальную "
                    "рабочую температуру и давление — размер резьбы сам по себе "
                    "не подтверждает применимость."
                ),
                expected_slots=missing_regime,
                blocking=True,
            )
        return SlotFillingResult(slots=slots)

    def _radiator(self, slots: dict) -> SlotFillingResult:
        product_kind = str(slots.get("product_kind") or "").strip().lower()

        if product_kind == "thermostatic_head" or (
            not product_kind and slots.get("thermostatic_head") is True
        ):
            missing = missing_requirements(slots, THERMOSTATIC_HEAD_CONTRACT)
            if not missing:
                return SlotFillingResult(slots=slots)
            missing_slots = list(missing[0].any_of)
            assisted = self._observable_unknown_result(
                "radiator_fittings", slots, missing_slots
            )
            if assisted is not None:
                return assisted
            photo_note = (
                " К сожалению, загрузка фотографий в этом чате пока не "
                "поддерживается. Перепишите маркировку и опишите посадочное место "
                "словами — я продолжу подбор по этим данным."
                if slots.get("photo_requested")
                else ""
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + missing[0].prompt + "." + photo_note,
                expected_slots=missing_slots,
                blocking=True,
            )

        if product_kind in {
            "thermostatic_valve",
            "radiator_shutoff_valve",
        }:
            # The product kind already answers the regulate-vs-shutoff choice.
            # Do not ask it again merely because the legacy
            # ``thermostatic_head`` boolean is absent.
            contract_slots = dict(slots)
            contract_slots["control_mode"] = (
                "регулировать"
                if product_kind == "thermostatic_valve"
                else "перекрывать"
            )
            missing = missing_requirements(
                contract_slots,
                RADIATOR_VALVE_CONTRACT,
            )
        else:
            contract_slots = dict(slots)
            if "thermostatic_head" in slots or slots.get("radiator_action"):
                contract_slots["control_mode"] = (
                    "регулировать"
                    if slots.get("thermostatic_head") is True
                    else "перекрывать"
                )
            missing = missing_requirements(
                contract_slots,
                GENERIC_RADIATOR_FITTING_CONTRACT,
            )
        if missing:
            visible_missing = missing[:3]
            missing_slots = list(
                dict.fromkeys(
                    slot
                    for requirement in visible_missing
                    for slot in requirement.any_of
                )
            )
            assisted = self._observable_unknown_result(
                "radiator_fittings", slots, missing_slots
            )
            if assisted is not None:
                return assisted
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Подскажите для радиатора: "
                    + "; ".join(
                        requirement.prompt for requirement in visible_missing
                    )
                    + "."
                ),
                expected_slots=missing_slots,
                blocking=True,
            )
        return SlotFillingResult(slots=slots)

    def _radiators(
        self,
        slots: dict,
        *,
        require_compatibility_context: bool = False,
    ) -> SlotFillingResult:
        # A capability browse (``which models tolerate at least 16 bar``) is
        # intentionally not a final room sizing.  Pressure is enough to show
        # grounded candidates; dimensions and heat output are chosen only
        # after the customer selects the required format.
        if slots.get("capability_browse") and slots.get("operating_pressure_bar"):
            return SlotFillingResult(slots=slots)
        has_type = bool(slots.get("radiator_type"))
        has_size = any(
            slots.get(key)
            for key in [
                "radiator_size_mm",
                "radiator_height_mm",
                "length_mm",
                "sections",
                "size_inch",
                "radiator_panel_type",
                "area_m2",
                "heat_load_w",
                "heat_output_w",
            ]
        )
        if require_compatibility_context:
            missing_prompts: list[str] = []
            expected_slots: list[str] = []
            if not slots.get("heating_system_type"):
                missing_prompts.append(
                    "система отопления центральная или автономная"
                )
                expected_slots.append("heating_system_type")
            if missing_prompts:
                assisted = self._observable_unknown_result(
                    "radiators", slots, expected_slots
                )
                if assisted is not None:
                    return assisted
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Для совместимого подбора уточните: "
                        + "; ".join(missing_prompts)
                        + ". Для центрального отопления материал нельзя выбирать "
                        "только по площади: важны рабочее давление, гидроудары и "
                        "качество теплоносителя."
                    ),
                    expected_slots=expected_slots,
                    blocking=True,
                )
            if (
                normalize_text(str(slots.get("heating_system_type") or ""))
                == "центральное"
                and not slots.get("operating_pressure_bar")
                and "operating_pressure_bar"
                not in set(slots.get("deferred_slot_keys") or [])
            ):
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Для центрального отопления подскажите рабочее или "
                        "опрессовочное давление из данных управляющей организации. "
                        "Если его сейчас нет, так и напишите: тогда покажу только "
                        "предварительные варианты без обещания совместимости. "
                        "Тип радиатора (панельный, биметаллический или алюминиевый) "
                        "можно назвать как предпочтение, но если вы его не знаете, "
                        "угадывать материал вместо вас не буду."
                    ),
                    expected_slots=["operating_pressure_bar"],
                    blocking=True,
                )
        if not has_size:
            missing = []
            if not has_type:
                missing.append("тип (панельный, биметаллический или алюминиевый)")
            missing.append(
                "размер/межосевое расстояние, количество секций или требуемую теплоотдачу"
            )
            size_slots = [
                *([] if has_type else ["radiator_type"]),
                "area_m2",
                "radiator_size_mm",
                "radiator_height_mm",
                "length_mm",
                "sections",
                "heat_load_w",
                "heat_output_w",
            ]
            assisted = self._observable_unknown_result(
                "radiators", slots, size_slots
            )
            if assisted is not None:
                return assisted
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Уточните для радиатора: " + "; ".join(missing) + "."
                ),
                expected_slots=size_slots,
            )
        return SlotFillingResult(slots=slots)
