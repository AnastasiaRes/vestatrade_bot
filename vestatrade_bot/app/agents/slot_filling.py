from __future__ import annotations

import re

from app.models import IntentResult, SessionState, SlotFillingResult

from .utils import merge_slots, normalize_text


class SlotFillingAgent:
    def fill(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> SlotFillingResult:
        previous_slots = dict(session.slots)
        slots = merge_slots(session.slots, intent.slots)
        category = intent.category
        text = normalize_text(message)

        if intent.intent_type in {"exact_sku", "link_request", "small_talk", "out_of_scope"}:
            return SlotFillingResult(slots=slots)
        if intent.intent_type == "complectation":
            return SlotFillingResult(slots=slots)
        if intent.intent_type == "stock_request" and category != "other":
            return SlotFillingResult(slots=slots)

        if category == "pipes" and slots.get("pipe_purpose") == "канализация":
            category = "sewer"

        if category == "pumps":
            self._infer_plain_circulation_parameters(text, slots)
            self._drop_parameter_shaped_sku(slots, intent)

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
        if category == "valves":
            return self._valves(slots, text)
        if category == "radiator_fittings":
            return self._radiator(slots)
        if category == "radiators":
            return self._radiators(slots)
        return SlotFillingResult(slots=slots)

    def _pipes(self, slots: dict) -> SlotFillingResult:
        if not slots.get("pipe_purpose"):
            if slots.get("diameter_mm"):
                question = (
                    f"Понял, труба {slots['diameter_mm']} мм. Для чего она: "
                    "для холодной или горячей воды, для отопления или для канализации?"
                )
            else:
                question = (
                    "Труба для чего: для холодной или горячей воды, для отопления "
                    "или для канализации? И какой диаметр в мм?"
                )
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=question,
            )
        missing = []
        if slots.get("pipe_purpose") == "водоснабжение" and not slots.get("water_temperature"):
            missing.append("холодная или горячая вода")
        if not slots.get("diameter_mm"):
            missing.append("диаметр в мм")
        if missing:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + " и ".join(missing[:2]) + ".",
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
        import re

        match = re.search(r"(?<!\d)(\d{1,3})(?:\s*)(?:метр|метров|метра)\b", text)
        if not match:
            return None
        value = int(match.group(1))
        if value <= 0:
            return None
        return value

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
        if not slots.get("pump_type"):
            if slots.get("pump_replacement"):
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
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "Понял, источник — колодец. Уточните глубину до воды, "
                            "высоту подъёма и нужный расход."
                        ),
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
            required = {
                "head_m": "напор (например 4 или 6 м)",
                "mounting_length_mm": "монтажную длину 130 или 180 мм",
            }
            missing = [label for key, label in required.items() if not slots.get(key)]
            has_any_core_param = len(missing) < len(required)
            if not has_any_core_param and slots.get("allow_basic_option"):
                return SlotFillingResult(slots=slots)
            if missing:
                if self._does_not_know_params(text):
                    if slots.get("pump_param_help_given"):
                        slots["allow_basic_option"] = True
                        slots["fallback_after_repeat"] = True
                        return SlotFillingResult(slots=slots)
                    slots["pump_param_help_given"] = True
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "Не страшно, монтажную длину можно посмотреть на старом насосе "
                            "или измерить расстояние между гайками подключения — часто бывает "
                            "130 или 180 мм. Напор обычно пишется в маркировке насоса: например "
                            "25-40 или 25-60. Если старого насоса нет, напишите площадь и задачу "
                            "системы — отопление, тёплый пол или водоснабжение."
                        ),
                    )
                if has_any_core_param:
                    return SlotFillingResult(
                        slots=slots,
                        needs_clarification=True,
                        question=(
                            "Для точного подбора циркуляционного насоса ещё уточните: "
                            + "; ".join(missing)
                            + ". По возможности также укажите присоединение (обычно 25 или 32); "
                            "либо просто пришлите полную маркировку старого насоса."
                        ),
                    )
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question=(
                        "Понял, нужен циркуляционный насос. Уточните присоединение, монтажную "
                        "длину и напор или пришлите полную маркировку старого насоса."
                    ),
                )
        return SlotFillingResult(slots=slots)

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

    def _boilers(self, slots: dict) -> SlotFillingResult:
        if not slots.get("boiler_type"):
            area = slots.get("area_m2")
            prefix = (
                f"Понял, подбираем котёл примерно на {float(area):g} м². "
                if area
                else ""
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
            prefix = ""
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

    def _valves(self, slots: dict, text: str) -> SlotFillingResult:
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
        has_form = bool(slots.get("body_form") or slots.get("union"))

        missing = []
        if not slots.get("application"):
            missing.append("для чего нужен кран: вода (холодная/горячая), отопление или радиатор")
        if not has_size:
            missing.append("размер: 1/2, 3/4 или диаметр в мм")
        if not missing and not has_form:
            return SlotFillingResult(slots=slots)
        if missing:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + " и ".join(missing[:2]) + ".",
            )
        return SlotFillingResult(slots=slots)

    def _radiator(self, slots: dict) -> SlotFillingResult:
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
        has_size = any(
            slots.get(key)
            for key in [
                "radiator_size_mm",
                "length_mm",
                "sections",
                "size_inch",
                "radiator_type_code",
            ]
        )
        if not has_size:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Уточните тип радиатора (панельный, биметаллический или "
                    "алюминиевый) и размер: высоту/межосевое расстояние, длину "
                    "или количество секций."
                ),
            )
        return SlotFillingResult(slots=slots)
