from __future__ import annotations

from app.models import IntentResult, SessionState, SlotFillingResult

from .utils import merge_slots, normalize_text


class SlotFillingAgent:
    def fill(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> SlotFillingResult:
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

        if category == "sewer":
            self._infer_sewer_followup_slots(text, slots)

        if category == "pipes":
            return self._pipes(slots)
        if category == "sewer":
            return self._sewer(slots)
        if category == "pumps":
            return self._pumps(slots)
        if category == "boilers":
            return self._boilers(slots)
        if category == "valves":
            return self._valves(slots, text)
        if category == "radiator_fittings":
            return self._radiator(slots)
        return SlotFillingResult(slots=slots)

    def _pipes(self, slots: dict) -> SlotFillingResult:
        if not slots.get("pipe_purpose"):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question=(
                    "Труба для чего: для холодной или горячей воды, для отопления "
                    "или для канализации? И какой диаметр в мм?"
                ),
            )
        missing = []
        if not slots.get("diameter_mm"):
            missing.append("диаметр в мм")
        if missing:
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Уточните " + " и ".join(missing[:2]) + ".",
            )
        return SlotFillingResult(slots=slots)

    def _sewer(self, slots: dict) -> SlotFillingResult:
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
            question = self._build_question(missing[:2])
            return SlotFillingResult(slots=slots, needs_clarification=True, question=question)
        return SlotFillingResult(slots=slots)

    def _infer_sewer_followup_slots(self, text: str, slots: dict) -> None:
        if slots.get("element_type") == "труба" and not slots.get("length_mm"):
            length = self._extract_mm_value(text, min_value=300)
            if length:
                slots["length_mm"] = length
        if not slots.get("diameter_mm"):
            diameter = self._extract_mm_value(text, min_value=32, max_value=250)
            if diameter:
                slots["diameter_mm"] = diameter

    def _extract_mm_value(
        self,
        text: str,
        min_value: int,
        max_value: int | None = None,
    ) -> int | None:
        import re

        for match in re.finditer(r"(?<!\d)(\d{2,5})(?:\s*мм|\s*м\b|\b)", text):
            value = int(match.group(1))
            if value < min_value:
                continue
            if max_value is not None and value > max_value:
                continue
            return value
        return None

    def _build_question(self, missing: list[str]) -> str:
        if missing == ["длина"]:
            return "Какая длина трубы нужна?"
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

    def _pumps(self, slots: dict) -> SlotFillingResult:
        if not slots.get("pump_type") and (
            slots.get("mounting_length_mm")
            or slots.get("head_m")
            or slots.get("connection_size")
            or slots.get("old_model")
        ):
            slots["pump_type"] = "циркуляционный"
        if not slots.get("pump_type"):
            if slots.get("pump_use") == "водоснабжение":
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question="Источник воды какой: скважина, колодец или центральный водопровод?",
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
            has_core_param = any(
                slots.get(key)
                for key in ["mounting_length_mm", "head_m", "connection_size", "old_model"]
            )
            if not has_core_param:
                return SlotFillingResult(
                    slots=slots,
                    needs_clarification=True,
                    question="Уточните монтажную длину и напор или модель старого насоса.",
                )
        return SlotFillingResult(slots=slots)

    def _boilers(self, slots: dict) -> SlotFillingResult:
        if not slots.get("boiler_type"):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="Котёл нужен газовый или электрический?",
            )
        if not slots.get("area_m2") and not slots.get("power_kw"):
            return SlotFillingResult(
                slots=slots,
                needs_clarification=True,
                question="На какую площадь подбираете котёл?",
            )
        return SlotFillingResult(slots=slots)

    def _valves(self, slots: dict, text: str) -> SlotFillingResult:
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
