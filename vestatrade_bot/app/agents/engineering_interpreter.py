from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.models import IntentResult, SessionState
from app.openrouter_client import OpenRouterClient

from .utils import normalize_text


ENGINEERING_INTERPRETER_PROMPT = """
Ты — первый слой понимания сообщений клиента компании «Веста Трейдинг».
Работай как инженер по водоснабжению, отоплению и водяному тёплому полу.
Твоя задача — до поиска товаров понять бытовую речь, связать короткий ответ с
последним вопросом бота и вернуть JSON только с исходными фактами клиента.

ЖЁСТКИЙ КОНТРАКТ ИЗВЛЕЧЕНИЯ:
- Не вычисляй, не конвертируй и не додумывай числа. Каждое число в slots
  должно быть прямо сказано клиентом в новом сообщении.
- Не возвращай в slots расчётные длины трубы, контуры, коллекторы,
  глубины из колец, конвертированный расход или напор.
- Не повторяй в slots старые факты из контекста. Контекст нужен только для
  толкования нового ответа.
- Для каждого slot верни короткую дословную цитату из нового сообщения в
  slot_evidence и источник current_message или pending_answer в slot_provenance.

КРИТИЧЕСКИЙ КОНТЕКСТ:
- Не сбрасывай текущую ветку без явной просьбы клиента сменить тему.
- Ответ одной цифрой или короткой фразой относится к последнему вопросу бота.
- Если бот спросил площадь тёплого пола, короткий ответ с числом и словом
  «метров» означает площадь тёплого пола, а не новую заявку на трубу.
- Подтверждай уже понятые данные и спрашивай только один следующий недостающий
  параметр. Не повторяй тот же вопрос, если клиент уже дал на него ответ.

БЫТОВЫЕ ЕДИНИЦЫ И ИСХОДНЫЕ ФАКТЫ:
- «Колодец на X колец» -> только well_ring_count=X.
- Если клиент явно назвал высоту одного кольца в метрах, сохрани её как
  ring_height_m. Явную глубину колодца в метрах сохрани как explicit_well_depth_m.
- «Столб воды X колец» -> только water_column_ring_count=X.
- Явный столб воды в метрах сохрани как explicit_water_column_depth_m, а явно
  названное расстояние от верха до воды — как explicit_water_level_depth_m.
- «Зеркало воды на X кольцах» без слов «от верха»/«от дна» неоднозначно:
  сохрани water_level_ring_count=X, water_level_reference="ambiguous" и попроси уточнить
  «X колец от верха до воды или X колец воды от дна?».
- Не возвращай well_depth_m, dynamic_water_level_m и water_column_depth_m:
  канонические глубины нормализует и рассчитывает следующий слой из raw-фактов.
- Расход в л/мин сохраняй как required_flow_l_min без конверсии. Статус единицы
  сохраняй как flow_unit_status: assumed, confirmed_per_minute или total_volume.
- Не возвращай required_flow_m3_h — его нормализует следующий слой.
- Число литров без времени не является расходом: не записывай required_flow_l_min,
  required_flow_m3_h или flow_unit_assumed; поставь flow_unit_status="assumed" и попроси
  уточнить единицу времени.
- Ведро, куб, бар, атмосферы и горизонтальная трасса не конвертируются этим агентом.

ВОДОСНАБЖЕНИЕ:
- Для колодца собери уровень воды, горизонтальную трассу, высоту подъёма,
  давление/расход и назначение. При уровне воды менее 8 м можно рассматривать
  поверхностный насос/станцию; глубже — погружной/колодезный насос.
- Для скважины собери общую глубину, статический и динамический уровни, дебит и
  внутренний диаметр обсадной трубы.
- Для ёмкости/дренажа уточни чистоту воды и необходимость поплавка.
- Не называй конкретный SKU, цену или наличие: это сделает следующий агент по
  реальному каталогу.

ВОДЯНОЙ ТЁПЛЫЙ ПОЛ:
- Извлекай только явно сказанную площадь в warm_floor_area_m2, тип пола и другие исходные факты.
- Следующий программный слой сам вычислит ориентир трубы area*6.5–area*7,
  число контуров ceil(area*6.5/80) и коллекторов ceil(contours/12).
- Ты не должен возвращать warm_floor_pipe_min_m, warm_floor_pipe_max_m,
  warm_floor_contours, warm_floor_collector_count и warm_floor_collector_outlets.
- Не выдавай ориентировочный расчёт за готовый проект и не подтверждай
  гидравлическую совместимость без расчётной рабочей точки.

ФОРМУЛЫ СЛЕДУЮЩЕГО СЛОЯ (не выполняй их сам):
- well_depth_m = well_ring_count*0.9; аналогично для уровня/столба воды из колец.
- required_flow_m3_h = required_flow_l_min*60/1000.
- required_head_m из бар, высоты и горизонтальной трассы рассчитывает только код.

Верни только JSON следующего вида:
{
  "handled": true,
  "continuation": true,
  "intent_type": "broad_category|attribute_request|unknown",
  "category": "pumps|pipes|boilers|water_heaters|other",
  "project_scope": "water|warm_floor|heating|general|null",
  "slots": {},
  "slot_evidence": {"slot_name": "дословный фрагмент нового сообщения"},
  "slot_provenance": {"slot_name": "current_message|pending_answer"},
  "assumptions": [],
  "missing_slot_keys": [],
  "needs_clarification": true,
  "clarifying_question": "один следующий вопрос или null",
  "ready_for_catalog_selection": false,
  "response_mode": "clarify|project_progress|catalog_search|none",
  "reply": "короткий ответ клиенту без SKU, цен и неподтверждённых товаров"
}

Если сообщение не относится к инженерной задаче и не продолжает инженерный
вопрос, верни handled=false. Числа в slots должны быть числами, не строками. В reply не пиши
рассчитанные или конвертированные числа — их добавит код после проверки.
""".strip()


@dataclass
class EngineeringInterpretation:
    handled: bool = False
    continuation: bool = False
    intent_type: str | None = None
    category: str | None = None
    project_scope: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    missing_slot_keys: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    ready_for_catalog_selection: bool = False
    response_mode: str = "none"
    reply: str | None = None
    llm_requested: bool = False
    llm_used: bool = False
    output_accepted: bool = False
    fallback_reason: str | None = None
    # Appended to preserve positional compatibility of the pre-existing
    # dataclass constructor while exposing optional audit metadata.
    slot_evidence: dict[str, str] = field(default_factory=dict)
    slot_provenance: dict[str, str] = field(default_factory=dict)

    @property
    def should_reply_now(self) -> bool:
        return bool(
            self.output_accepted
            and self.reply
            and self.response_mode in {"clarify", "project_progress"}
        )


class EngineeringInterpreterAgent:
    """LLM-first semantic bridge between everyday language and typed slots."""

    _CATEGORIES = {"pumps", "pipes", "boilers", "water_heaters", "other"}
    _SCOPES = {"water", "warm_floor", "heating", "general"}
    _INTENTS = {"broad_category", "attribute_request", "unknown"}
    _MODES = {"clarify", "project_progress", "catalog_search", "none"}
    _STRING_SLOTS = {
        "water_source",
        "pump_use",
        "pump_type",
        "project_scope",
        "warm_floor_type",
        "water_quality",
        "pipe_material",
        "system_type",
    }
    _ENUM_STRING_SLOTS = {
        "water_level_reference": {"ambiguous", "from_top", "from_bottom"},
        "flow_unit_status": {"assumed", "confirmed_per_minute", "total_volume"},
    }
    _BOOL_SLOTS = {
        "needs_float_switch",
        "has_warm_floor",
    }
    # These values are calculations or normalized representations owned by the
    # deterministic engineering layer.  They are never accepted from the LLM,
    # even when the returned JSON is syntactically valid.
    _DERIVED_SLOTS = {
        "well_depth_m",
        "water_level_depth_m",
        "water_column_depth_m",
        "dynamic_water_level_m",
        "required_head_m",
        "required_flow_m3_h",
        "warm_floor_pipe_min_m",
        "warm_floor_pipe_max_m",
        "warm_floor_contours",
        "warm_floor_collector_count",
        "warm_floor_collector_outlets",
        "flow_unit_assumed",
        "ring_height_assumed",
    }
    _ALLOWED_PROVENANCE = {"current_message", "pending_answer"}
    _NUMERIC_LIMITS: dict[str, tuple[float, float]] = {
        "well_ring_count": (1, 100),
        "well_depth_m": (0.1, 300),
        "water_level_ring_count": (0.1, 100),
        "water_column_ring_count": (0.1, 100),
        "ring_height_m": (0.1, 5),
        "explicit_well_depth_m": (0.1, 300),
        "explicit_water_level_depth_m": (0.1, 300),
        "explicit_water_column_depth_m": (0.1, 300),
        "water_column_depth_m": (0.1, 300),
        "static_water_level_m": (0.1, 300),
        "dynamic_water_level_m": (0.1, 300),
        "lift_height_m": (0, 300),
        "horizontal_run_m": (0, 5000),
        "required_pressure_bar": (0.1, 25),
        "required_head_m": (0.1, 300),
        "required_flow_l_min": (0.1, 10000),
        "required_flow_m3_h": (0.001, 600),
        "well_yield_m3_h": (0.001, 600),
        "casing_diameter_mm": (20, 1000),
        "warm_floor_area_m2": (1, 10000),
        "area_m2": (1, 10000),
        "warm_floor_pipe_min_m": (1, 100000),
        "warm_floor_pipe_max_m": (1, 100000),
        "warm_floor_contours": (1, 1000),
        "warm_floor_collector_count": (1, 100),
        "warm_floor_collector_outlets": (1, 24),
    }

    _ENGINEERING_MARKERS = (
        "насос",
        "скваж",
        "колод",
        "кольц",
        "зеркал",
        "дебит",
        "расход",
        "напор",
        "давлен",
        "водоснаб",
        "отоплен",
        "котел",
        "котёл",
        "труб",
        "тепл",
        "тёпл",
        "коллектор",
        "контур",
        "литр",
        "куб",
    )

    def __init__(self, llm_client: OpenRouterClient) -> None:
        self.llm_client = llm_client

    def should_interpret(
        self,
        message: str,
        baseline: IntentResult,
        session: SessionState,
    ) -> bool:
        if baseline.intent_type in {
            "exact_sku",
            "link_request",
            "complectation",
            "handoff_request",
            "out_of_scope",
        }:
            return False
        if (
            session.pending_question
            or session.slots.get("project_scope")
            or session.slots.get("scope_funnel")
        ):
            return True
        if baseline.category in {"pumps", "pipes", "boilers", "water_heaters"}:
            return True
        text = normalize_text(message)
        return any(marker in text for marker in self._ENGINEERING_MARKERS)

    def interpret(
        self,
        message: str,
        baseline: IntentResult,
        session: SessionState,
    ) -> EngineeringInterpretation:
        fallback = {
            "handled": False,
            "continuation": False,
            "intent_type": None,
            "category": None,
            "project_scope": None,
            "slots": {},
            "assumptions": [],
            "missing_slot_keys": [],
            "needs_clarification": False,
            "clarifying_question": None,
            "ready_for_catalog_selection": False,
            "response_mode": "none",
            "reply": None,
        }
        context = {
            "active_category": session.category,
            "current_slots": session.slots,
            "project_context": session.project_context,
            "pending_question": session.pending_question,
            "pending_category": session.pending_category,
            "pending_slot_keys": session.pending_slot_keys,
            "recent_dialog": session.history[-6:],
            "baseline_intent": {
                "intent_type": baseline.intent_type,
                "category": baseline.category,
                "slots": baseline.slots,
            },
        }
        messages = [
            {"role": "system", "content": ENGINEERING_INTERPRETER_PROMPT},
            {
                "role": "user",
                "content": (
                    "Контекст текущего диалога:\n"
                    + json.dumps(context, ensure_ascii=False, default=str)
                    + "\n\nНовое сообщение клиента: "
                    + message
                ),
            },
        ]
        data, used = self.llm_client.complete_json(
            "EngineeringInterpreterAgent",
            messages,
            fallback,
        )
        accepted_signal = getattr(self.llm_client, "last_json_output_accepted", None)
        if used and accepted_signal is False:
            retry_messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Предыдущий ответ не разобрался как JSON. Повтори анализ того же "
                        "сообщения и верни только один валидный JSON-объект строго по схеме, "
                        "без Markdown и пояснений вокруг JSON."
                    ),
                }
            ]
            retry_data, retry_used = self.llm_client.complete_json(
                "EngineeringInterpreterAgent.retry",
                retry_messages,
                fallback,
            )
            if retry_used:
                data = retry_data
                used = True
            accepted_signal = getattr(
                self.llm_client,
                "last_json_output_accepted",
                accepted_signal,
            )
        output_accepted = bool(
            used and data.get("handled") and (accepted_signal is not False)
        )
        cleaned_slots = self._clean_slots(
            data.get("slots"),
            message=message,
            pending_slot_keys=session.pending_slot_keys,
            slot_evidence=data.get("slot_evidence"),
            slot_provenance=data.get("slot_provenance"),
        )
        cleaned_evidence = self._clean_slot_evidence(
            data.get("slot_evidence"),
            message=message,
            allowed_keys=cleaned_slots,
        )
        cleaned_provenance = self._clean_slot_provenance(
            data.get("slot_provenance"),
            allowed_keys=cleaned_slots,
        )
        result = EngineeringInterpretation(
            handled=bool(data.get("handled")),
            continuation=bool(data.get("continuation")),
            intent_type=(
                data.get("intent_type")
                if data.get("intent_type") in self._INTENTS
                else None
            ),
            category=(
                data.get("category")
                if data.get("category") in self._CATEGORIES
                else None
            ),
            project_scope=(
                data.get("project_scope")
                if data.get("project_scope") in self._SCOPES
                else None
            ),
            slots=cleaned_slots,
            slot_evidence=cleaned_evidence,
            slot_provenance=cleaned_provenance,
            assumptions=self._clean_string_list(data.get("assumptions"), limit=6),
            missing_slot_keys=self._clean_string_list(
                data.get("missing_slot_keys"), limit=8
            ),
            needs_clarification=bool(data.get("needs_clarification")),
            clarifying_question=self._clean_text(data.get("clarifying_question"), 320),
            ready_for_catalog_selection=bool(data.get("ready_for_catalog_selection")),
            response_mode=(
                data.get("response_mode")
                if data.get("response_mode") in self._MODES
                else "none"
            ),
            reply=self._clean_reply(data.get("reply"), message=message),
            llm_requested=True,
            llm_used=bool(used),
            output_accepted=output_accepted,
        )
        if used and not output_accepted:
            result.fallback_reason = "engineering interpretation JSON was not accepted"
        elif not used:
            result.fallback_reason = getattr(
                self.llm_client,
                "last_fallback_reason",
                None,
            )
        return result

    def _clean_slots(
        self,
        raw: Any,
        *,
        message: str = "",
        pending_slot_keys: list[str] | tuple[str, ...] | None = None,
        slot_evidence: Any = None,
        slot_provenance: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, Any] = {}
        for key, value in raw.items():
            if key in self._DERIVED_SLOTS:
                continue
            if not self._slot_metadata_is_grounded(
                key,
                message=message,
                slot_evidence=slot_evidence,
                slot_provenance=slot_provenance,
            ):
                continue
            if key in self._STRING_SLOTS and isinstance(value, str):
                text = self._clean_text(value, 120)
                if text and self._string_slot_is_grounded(
                    key,
                    text,
                    message=message,
                ):
                    cleaned[key] = text
                continue
            allowed_values = self._ENUM_STRING_SLOTS.get(key)
            if allowed_values and isinstance(value, str):
                normalized = normalize_text(value).replace(" ", "_")
                if normalized in allowed_values and self._enum_slot_is_grounded(
                    key,
                    normalized,
                    message=message,
                ):
                    cleaned[key] = normalized
                continue
            if key in self._BOOL_SLOTS and isinstance(value, bool):
                if self._bool_slot_is_grounded(key, value, message=message):
                    cleaned[key] = value
                continue
            limits = self._NUMERIC_LIMITS.get(key)
            if (
                limits
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                number = float(value)
                if limits[0] <= number <= limits[1] and self._numeric_slot_is_grounded(
                    key,
                    number,
                    message=message,
                    pending_slot_keys=pending_slot_keys or (),
                ):
                    cleaned[key] = (
                        int(number) if number.is_integer() else round(number, 4)
                    )
        return cleaned

    @staticmethod
    def _string_slot_is_grounded(key: str, value: str, *, message: str) -> bool:
        text = normalize_text(message)
        normalized_value = normalize_text(value)
        canonical_value = normalized_value.replace(" ", "_")
        if not text:
            return True
        if key == "water_source":
            markers = {
                "колодец": "колод",
                "скважина": "скваж",
                "емкость": "емкост",
                "ёмкость": "емкост",
                "бочка": "боч",
            }
            marker = next(
                (needle for name, needle in markers.items() if name in normalized_value),
                None,
            )
            if marker == "колод" and re.search(r"\bкол(?:ьц|ец)\w*", text) and any(
                word in text for word in ["зеркал", "столб", "вод"]
            ):
                return True
            return bool(marker and marker in text)
        if key == "project_scope":
            if canonical_value == "warm_floor":
                return "пол" in text and "тепл" in text
            if canonical_value == "water":
                return any(word in text for word in ["вод", "колод", "скваж"])
            if canonical_value == "heating":
                return any(word in text for word in ["отоплен", "кот", "радиатор"])
            return any(word in text for word in ["дом", "проект", "систем"])
        if key == "warm_floor_type":
            return (
                "водян" in text if "водян" in normalized_value else "электр" in text
            )
        if key == "pump_type":
            markers = [
                "циркуляц",
                "погруж",
                "поверхност",
                "дренаж",
                "колодез",
                "скважин",
                "насосн",
            ]
            return any(marker in normalized_value and marker in text for marker in markers)
        if key == "pump_use":
            markers = ["отоплен", "водоснаб", "полив", "откач", "давлен"]
            return any(marker in normalized_value and marker in text for marker in markers)
        if key == "water_quality":
            return any(marker in text for marker in ["чист", "гряз", "фекал", "песок", "ил"])
        if key == "pipe_material":
            return any(marker in text for marker in ["pex", "pe-rt", "pert", "ppr", "металлопласт"])
        if key == "system_type":
            return any(marker in text for marker in ["радиатор", "тепл", "отоплен"])
        return normalized_value in text

    @staticmethod
    def _bool_slot_is_grounded(key: str, value: bool, *, message: str) -> bool:
        text = normalize_text(message)
        if not text:
            return True
        if key == "needs_float_switch":
            return "поплав" in text
        if key == "has_warm_floor":
            return "пол" in text and any(marker in text for marker in ["тепл", "тёпл"])
        return False

    @staticmethod
    def _enum_slot_is_grounded(key: str, value: str, *, message: str) -> bool:
        text = normalize_text(message)
        if not text:
            return True
        if key == "water_level_reference":
            if value == "from_top":
                return bool(re.search(r"от\s+верх|от\s+поверхност", text))
            if value == "from_bottom":
                return bool(re.search(r"от\s+дна|столб\w*\s+вод", text))
            return bool(re.search(r"зеркал", text)) and not bool(
                re.search(r"от\s+(?:верх|поверхност|дна)", text)
            )
        if key == "flow_unit_status":
            has_per_minute = bool(
                re.search(
                    r"(?:\bл\s*/\s*мин\b|\bл\s+(?:в\s+)?минут\w*|литр\w*\s+(?:в\s+)?минут\w*)",
                    text,
                )
            )
            if value == "confirmed_per_minute":
                return has_per_minute
            if value == "total_volume":
                return bool(re.search(r"всего|общ\w*\s+объем|объем\w*\s+всего", text))
            return "литр" in text and not has_per_minute
        return True

    def _slot_metadata_is_grounded(
        self,
        key: str,
        *,
        message: str,
        slot_evidence: Any,
        slot_provenance: Any,
    ) -> bool:
        """Reject a slot when the model supplied fabricated evidence/source.

        Metadata remains optional for compatibility with older local models,
        but once supplied it must point to the current user turn.  Numeric
        values still have an independent grounding check below.
        """

        if isinstance(slot_provenance, dict) and key in slot_provenance:
            source = normalize_text(str(slot_provenance.get(key) or "")).replace(
                " ", "_"
            )
            if source not in self._ALLOWED_PROVENANCE:
                return False
        if isinstance(slot_evidence, dict) and key in slot_evidence:
            evidence = self._clean_text(slot_evidence.get(key), 240)
            if not evidence or normalize_text(evidence) not in normalize_text(message):
                return False
        return True

    def _numeric_slot_is_grounded(
        self,
        key: str,
        value: float,
        *,
        message: str,
        pending_slot_keys: list[str] | tuple[str, ...],
    ) -> bool:
        text = normalize_text(message)
        if not text:
            # Preserve compatibility for direct helper calls.  The production
            # path always passes the current message.
            return True
        if not any(
            math.isclose(value, stated, rel_tol=0.0, abs_tol=0.0001)
            for stated in self._stated_numbers(text)
        ):
            return False

        pending = set(pending_slot_keys)
        if key == "required_flow_l_min":
            # A bare amount of litres is not a flow.  Even a pending flow
            # question cannot manufacture the missing time unit.
            return bool(
                re.search(
                    r"(?:\bл\s*/\s*мин\b|\bл\s+(?:в\s+)?минут\w*|литр\w*\s+(?:в\s+)?минут\w*)",
                    text,
                )
            )
        if key == "required_pressure_bar":
            return bool(re.search(r"\b(?:бар\w*|атм\w*)\b", text))
        if key == "well_yield_m3_h":
            return "дебит" in text and self._has_m3_per_hour_unit(text)
        if key == "casing_diameter_mm":
            return "мм" in text and bool(re.search(r"диаметр|обсад|труб", text))
        if key == "static_water_level_m":
            return "статич" in text and self._has_metre_unit(text)
        if key == "lift_height_m":
            return key in pending or bool(
                re.search(r"поднят|подъем|подъём|высот", text)
            )
        if key == "horizontal_run_m":
            return key in pending or bool(
                re.search(
                    r"до\s+(?:дом|полив|точк)|от\s+(?:колод|скваж)|расстоян|трасс", text
                )
            )
        if key == "well_ring_count":
            return bool(re.search(r"\bкол(?:ьц|ец)\w*", text)) and "столб" not in text
        if key == "water_level_ring_count":
            return bool(re.search(r"\bкол(?:ьц|ец)\w*", text)) and bool(
                re.search(r"зеркал|до\s+вод", text)
            )
        if key == "water_column_ring_count":
            return bool(re.search(r"\bкол(?:ьц|ец)\w*", text)) and bool(
                re.search(r"столб|от\s+дна", text)
            )
        if key == "ring_height_m":
            return bool(re.search(r"\bкол(?:ьц|ец)\w*", text)) and self._has_metre_unit(text)
        if key == "explicit_well_depth_m":
            return "колод" in text and "глубин" in text and self._has_metre_unit(text)
        if key == "explicit_water_level_depth_m":
            return bool(re.search(r"от\s+(?:верх|кра)|глубин\w*\s+до\s+вод", text))
        if key == "explicit_water_column_depth_m":
            return bool(re.search(r"столб\w*\s+вод|от\s+дна", text))
        if key == "warm_floor_area_m2":
            return key in pending or bool(
                re.search(r"тепл\w*\s+пол|теплого\s+пол", text)
            )
        if key == "area_m2":
            return key in pending or bool(
                re.search(r"площад|кв\.?\s*м|м\s*(?:2|²)|квадрат", text)
            )
        return True

    @staticmethod
    def _has_m3_per_hour_unit(text: str) -> bool:
        return bool(re.search(r"м\s*(?:3|³)\s*/\s*ч|куб\w*\s+(?:в\s+)?час", text))

    @staticmethod
    def _has_metre_unit(text: str) -> bool:
        return bool(re.search(r"\d(?:[\d,.]*)\s*(?:м\b|метр\w*)", text))

    @classmethod
    def _stated_numbers(cls, text: str) -> list[float]:
        numbers = [
            float(token.replace(",", "."))
            for token in re.findall(r"(?<![a-zа-я\d])\d+(?:[,.]\d+)?", text)
        ]
        word_values = {
            "ноль": 0,
            "один": 1,
            "одна": 1,
            "одно": 1,
            "два": 2,
            "две": 2,
            "двух": 2,
            "три": 3,
            "трех": 3,
            "четыре": 4,
            "четырех": 4,
            "пять": 5,
            "пяти": 5,
            "шесть": 6,
            "шести": 6,
            "семь": 7,
            "семи": 7,
            "восемь": 8,
            "восьми": 8,
            "девять": 9,
            "девяти": 9,
            "десять": 10,
            "десяти": 10,
            "одиннадцать": 11,
            "двенадцать": 12,
            "тринадцать": 13,
            "четырнадцать": 14,
            "пятнадцать": 15,
            "шестнадцать": 16,
            "семнадцать": 17,
            "восемнадцать": 18,
            "девятнадцать": 19,
            "двадцать": 20,
            "тридцать": 30,
            "сорок": 40,
            "пятьдесят": 50,
            "шестьдесят": 60,
            "семьдесят": 70,
            "восемьдесят": 80,
            "девяносто": 90,
            "сто": 100,
            "двести": 200,
            "триста": 300,
            "четыреста": 400,
            "пятьсот": 500,
            "шестьсот": 600,
            "семьсот": 700,
            "восемьсот": 800,
            "девятьсот": 900,
        }
        current = 0
        for token in text.split() + [""]:
            normalized_token = token.strip(".,-/")
            if normalized_token in word_values:
                current += word_values[normalized_token]
            elif normalized_token in {"тысяча", "тысячи", "тысяч"}:
                current = max(current, 1) * 1000
            elif current:
                numbers.append(float(current))
                current = 0
        return numbers

    def _clean_slot_evidence(
        self,
        raw: Any,
        *,
        message: str,
        allowed_keys: dict[str, Any],
    ) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        normalized_message = normalize_text(message)
        cleaned: dict[str, str] = {}
        for key in allowed_keys:
            evidence = self._clean_text(raw.get(key), 240)
            if evidence and normalize_text(evidence) in normalized_message:
                cleaned[key] = evidence
        return cleaned

    def _clean_slot_provenance(
        self,
        raw: Any,
        *,
        allowed_keys: dict[str, Any],
    ) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, str] = {}
        for key in allowed_keys:
            source = normalize_text(str(raw.get(key) or "")).replace(" ", "_")
            if source in self._ALLOWED_PROVENANCE:
                cleaned[key] = source
        return cleaned

    @staticmethod
    def _clean_string_list(raw: Any, *, limit: int) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [str(value).strip()[:160] for value in raw[:limit] if str(value).strip()]

    @staticmethod
    def _clean_text(raw: Any, limit: int) -> str | None:
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        return text[:limit] if text else None

    def _clean_reply(self, raw: Any, *, message: str = "") -> str | None:
        reply = self._clean_text(raw, 1400)
        if not reply:
            return None
        # This agent has no catalogue. Any product identity/price/link is unsafe
        # and must fall through to the grounded catalogue consultant.
        unsafe = bool(
            re.search(
                r"https?://|\bарт(?:икул)?\.?\s*[:№]?|\d[\d ]*\s*(?:₽|руб)", reply, re.I
            )
        )
        if unsafe:
            return None

        normalized_reply = normalize_text(reply)
        normalized_message = normalize_text(message)
        if normalized_message:
            stated_numbers = self._stated_numbers(normalized_message)
            reply_numbers = self._stated_numbers(normalized_reply)
            if any(
                not any(
                    math.isclose(number, stated, rel_tol=0.0, abs_tol=0.0001)
                    for stated in stated_numbers
                )
                for number in reply_numbers
            ):
                return None
            # Do not let the wording field smuggle in a unit conversion while
            # the structured slot was correctly rejected.
            if re.search(
                r"\bл\s*/\s*мин\b|литр\w*\s+(?:в\s+)?минут", normalized_reply
            ) and not re.search(
                r"\bл\s*/\s*мин\b|литр\w*\s+(?:в\s+)?минут",
                normalized_message,
            ):
                return None
            if self._has_m3_per_hour_unit(
                normalized_reply
            ) and not self._has_m3_per_hour_unit(normalized_message):
                return None
        return reply
