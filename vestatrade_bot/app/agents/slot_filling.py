from __future__ import annotations

import re

from app.models import IntentResult, SessionState, SlotFillingResult

from .engineering_calculations import normalize_engineering_slots
from .numeric_semantics import extract_total_length_m as parse_total_length_m
from .slot_answer_resolver import bind_local_refusals
from .utils import merge_slots, normalize_text


class SlotFillingAgent:
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
            return self._radiators(slots)
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
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Для новой разводки от стояка диаметр не угадывают по цвету трубы. "
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
        missing = []
        if not slots.get("fitting_system"):
            missing.append("система: PPR или канализация")
        if not slots.get("element_type"):
            missing.append("тип: муфта, угольник, тройник или переходник")
        if not slots.get("diameter_mm") and not slots.get("size_inch"):
            missing.append("размер в мм или дюймах")
        if missing:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + " и ".join(missing[:2]) + ".",
            )
        return SlotFillingResult(slots=slots)

    def _sewer(self, slots: dict, text: str) -> SlotFillingResult:
        slots.setdefault("pipe_purpose", "канализация")
        missing = []
        if not slots.get("sewer_scope"):
            missing.append("внутренняя или наружная канализация")
        if not slots.get("element_type"):
            missing.append("что нужно: труба, отвод, тройник или муфта")
        if not slots.get("diameter_mm"):
            missing.append("диаметр")
        if slots.get("element_type") == "труба" and not slots.get("length_mm"):
            missing.append("длина")
        if missing:
            question = self._build_question(missing[:2], slots=slots, text=text)
            return SlotFillingResult(slots=slots, needs_clarification=True, question=question)
        return SlotFillingResult(slots=slots)

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
                slots.setdefault("pump_selection_mode", "замена")
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

            missing = []
            if not (slots.get("head_m") or slots.get("required_head_m")):
                missing.append("напор (например 4 или 6 м)")
            if not slots.get("mounting_length_mm"):
                missing.append("монтажную длину 130 или 180 мм")
            if not slots.get("connection_size"):
                missing.append("присоединение (например 25 или 32)")
            has_any_core_param = any(
                slots.get(key)
                for key in ("head_m", "required_head_m", "mounting_length_mm", "connection_size")
            )
            if missing:
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
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            known_prefix
                            + marking_note
                            + "Для замены не хватает только: "
                            + "; ".join(missing)
                            + ". Монтажную длину измеряют между плоскостями "
                            "подключений; присоединение часто указано как DN25/DN32."
                        ),
                    )
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
                                "случайный насос. Можно прислать маркировку старого насоса "
                                "для замены либо расчёт системы/фото шильдика; монтажную "
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
                    missing_for_new_selection = list(missing)
                    if not slots.get("required_flow_m3_h"):
                        missing_for_new_selection.append("расчётный расход в м³/ч")
                    if not slots.get("system_type"):
                        missing_for_new_selection.append(
                            "схема системы: радиаторы, тёплый пол или оба контура"
                        )
                    flow_note = (
                        " Расход не угадываю: его можно взять из расчёта системы "
                        "или определить со специалистом по тепловой нагрузке и перепаду температур."
                        if "required_flow_m3_h" in deferred
                        else ""
                    )
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            known_prefix
                            + "Для точного нового подбора не хватает только: "
                            + "; ".join(missing_for_new_selection)
                            + "."
                            + flow_note
                        ),
                    )
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Понял, нужен циркуляционный насос. Это замена старого или новый "
                        "подбор? Для замены пришлите маркировку, присоединение и монтажную "
                        "длину. Для нового подбора нужны расчётный расход (м³/ч), напор (м) "
                        "и схема системы; монтажный размер всё равно нужно сверить."
                    ),
                )
            if (
                slots.get("pump_selection_mode") == "новый подбор"
                and (
                    not slots.get("required_flow_m3_h")
                    or not slots.get("system_type")
                )
            ):
                missing_duty = []
                if not slots.get("required_flow_m3_h"):
                    missing_duty.append("расчётный расход в м³/ч")
                if not slots.get("system_type"):
                    missing_duty.append(
                        "схема системы: радиаторы, тёплый пол или оба контура"
                    )
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Монтажные параметры понял. Для нового подбора ещё уточните: "
                        + "; ".join(missing_duty)
                        + ". Если это замена по уже заданной маркировке, так и напишите — "
                        "тогда подберу по ней."
                    ),
                )
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "скважинный":
            missing = []
            if not (
                slots.get("dynamic_water_level_m")
                or slots.get("static_water_level_m")
            ):
                missing.append("динамический уровень воды")
            if not slots.get("lift_height_m"):
                missing.append("высоту от уровня воды до верхней точки")
            if not slots.get("horizontal_run_m"):
                missing.append("длину горизонтальной трассы")
            if not slots.get("required_pressure_bar"):
                missing.append("нужное давление в доме")
            if not slots.get("required_flow_m3_h"):
                missing.append("требуемый расход")
            if (
                not slots.get("required_head_m")
                and not slots.get("discharge_diameter_mm")
            ):
                missing.append(
                    "внутренний диаметр напорной трубы; для ПНД можно наружный диаметр и SDR"
                )
            if missing:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Одной глубины скважины недостаточно. Чтобы я рассчитал "
                        "расчётный напор, величину потерь в трассе и рабочую точку, уточните: "
                        + "; ".join(missing[:3])
                        + (
                            ". Затем проверим остальные данные и рабочую точку насоса."
                            if len(missing) > 3
                            else "."
                        )
                    ),
                )
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "дренажный":
            missing = []
            if not slots.get("water_quality"):
                missing.append("какая вода: чистая, грязная или фекальная")
            if (
                slots.get("water_quality") in {"грязная", "фекальная"}
                and slots.get("solids_mm") is None
            ):
                missing.append("максимальный размер частиц в воде")
            if slots.get("required_head_m") is None and slots.get("lift_height_m") is None:
                missing.append("вертикальный подъём до точки сброса")
            if slots.get("required_head_m") is None and slots.get("horizontal_run_m") is None:
                missing.append("длину горизонтального отвода")
            if (
                slots.get("required_head_m") is None
                and slots.get("discharge_diameter_mm") is None
            ):
                missing.append("внутренний диаметр шланга или напорной трубы")
            if not slots.get("required_flow_m3_h"):
                missing.append("нужную производительность")
            if missing:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Для дренажного насоса уточните: "
                        + "; ".join(missing[:3])
                        + ". По этим данным я рассчитаю предварительный напор; "
                        "модель затем нужно проверить по Q–H-кривой."
                    ),
                )
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "канализационная насосная установка":
            missing = []
            if not slots.get("connected_fixtures"):
                missing.append(
                    "какие приборы подключаются: унитаз, раковина, душ, ванна или техника"
                )
            if slots.get("lift_height_m") is None:
                missing.append("вертикальный подъём от установки до точки сброса")
            if slots.get("horizontal_run_m") is None:
                missing.append("длину горизонтального напорного участка")
            if not slots.get("diameter_mm"):
                missing.append("диаметр существующей или проектной напорной трубы")
            if missing:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "КНС/санитарный насос нельзя выбирать только по слову «санузел»: "
                        "нужно проверить допустимые стоки, число входов и рабочую точку. "
                        "Уточните: "
                        + "; ".join(missing[:3])
                        + "."
                    ),
                )
            return SlotFillingResult(slots=slots)

        if slots.get("pump_type") == "повысительный":
            if not slots.get("water_source"):
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Уточните источник слабого напора: центральный водопровод, "
                        "скважина или колодец? Причины и схема повышения давления "
                        "для них различаются."
                    ),
                )
            if slots.get("inlet_pressure_bar") is None:
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
                    )
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Какое давление сейчас на входе из центрального водопровода, в барах?"
                    ),
                )
            if slots.get("required_pressure_bar") is None:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Какое давление нужно получить после насоса, в барах?"
                    ),
                )
            if not slots.get("required_flow_m3_h"):
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Какой нужен расход при одновременном водоразборе, "
                        "например в литрах в минуту?"
                    ),
                )
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
        if water_level is None and not ambiguous_level:
            question = "Уточните глубину от верха колодца до поверхности воды."
        elif not slots.get("horizontal_run_m") and "horizontal_run_m" not in deferred:
            question = "Какое расстояние по горизонтали от колодца до дома или полива?"
        elif slots.get("lift_height_m") is None:
            question = (
                "Есть ли дополнительный перепад высоты от уровня земли у колодца "
                "до точки полива? Если участок ровный — ответьте «0 метров»; "
                "иначе укажите, на сколько метров точка выше."
            )
        elif not slots.get("required_flow_m3_h"):
            question = "Какой нужен расход: сколько литров в минуту?"
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
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Остался отложенный обязательный параметр: расстояние от колодца "
                    "до дома или полива. Без него не учитываются потери в трассе, поэтому "
                    "окончательный вариант насоса пока не подтверждаю."
                ),
            )
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
            area = slots.get("area_m2")
            prefix = (
                f"Понял, подбираем котёл примерно на {float(area):g} м². "
                if area
                else pair_prefix
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    prefix + "Газовый или электрический?"
                    if prefix
                    else "Котёл нужен газовый или электрический и на какую площадь?"
                ),
            )
        if not slots.get("area_m2") and not slots.get("power_kw"):
            prefix = pair_prefix
            if slots.get("contours") == "двухконтурный":
                prefix = "Понял, нужен двухконтурный котёл — с горячей водой. "
            elif slots.get("contours") == "одноконтурный":
                prefix = "Понял, одноконтурный — только отопление. "
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=prefix + "На какую площадь подбираете котёл?",
            )
        if slots.get("boiler_type") == "газовый" and not slots.get("contours"):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Котёл нужен только для отопления или ещё для горячей воды?"
                ),
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
        elif "горяч" in text or "холодн" in text or "вода" in text or "воды" in text or "водоснаб" in text:
            slots["application"] = "вода"

        has_size = bool(
            slots.get("diameter_mm") or slots.get("size_inch") or slots.get("connection_size")
        )

        if slots.get("installation_context") == "перед унитазом":
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "По описанию это обычно угловой запорный кран перед гибкой "
                    "подводкой бачка. Полдюйма может относиться только к одной стороне: "
                    "уточните, какая резьба выходит из стены (наружная или внутренняя) "
                    "и какой размер гайки подводки — 1/2 или 3/8. Лучше сверить "
                    "маркировку шланга или прислать фото соединений."
                ),
            )

        missing = []
        if not slots.get("application"):
            missing.append("для чего нужен кран: вода (холодная/горячая), отопление или радиатор")
        if not has_size:
            missing.append("размер: 1/2, 3/4 или диаметр в мм")
        if missing:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + " и ".join(missing[:2]) + ".",
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
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Для крана на отопление/горячую воду укажите максимальную "
                    "рабочую температуру и давление — размер резьбы сам по себе "
                    "не подтверждает применимость."
                ),
            )
        return SlotFillingResult(slots=slots)

    def _radiator(self, slots: dict) -> SlotFillingResult:
        if slots.get("thermostatic_head") is True:
            if not slots.get("metric_thread") and not slots.get("size_inch"):
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Уточните модель термостатического клапана или резьбу "
                        "под термоголовку, например M30x1,5."
                    ),
                )
            return SlotFillingResult(slots=slots)
        missing = []
        if not slots.get("connection_form"):
            missing.append("прямое или угловое подключение")
        if not slots.get("diameter_mm") and not slots.get("size_inch"):
            missing.append("размер 1/2 или 3/4")
        if "thermostatic_head" not in slots:
            missing.append("регулировать температуру (термоголовка) или просто перекрывать поток")
        if missing:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Подскажите для радиатора: " + "; ".join(missing[:3]) + "."
                ),
            )
        return SlotFillingResult(slots=slots)

    def _radiators(self, slots: dict) -> SlotFillingResult:
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
        if not has_size:
            missing = []
            if not has_type:
                missing.append("тип (панельный, биметаллический или алюминиевый)")
            missing.append(
                "размер/межосевое расстояние, количество секций или требуемую теплоотдачу"
            )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Уточните для радиатора: " + "; ".join(missing) + "."
                ),
            )
        return SlotFillingResult(slots=slots)
