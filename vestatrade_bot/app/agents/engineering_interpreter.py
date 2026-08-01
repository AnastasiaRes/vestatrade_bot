from __future__ import annotations

import json
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
последним вопросом бота, перевести данные в технические единицы и вернуть JSON.

КРИТИЧЕСКИЙ КОНТЕКСТ:
- Не сбрасывай текущую ветку без явной просьбы клиента сменить тему.
- Ответ одной цифрой или короткой фразой относится к последнему вопросу бота.
- Если бот спросил площадь тёплого пола, «240 метров» означает 240 м² тёплого
  пола, а не новую заявку на трубу.
- Подтверждай уже понятые данные и спрашивай только один следующий недостающий
  параметр. Не повторяй тот же вопрос, если клиент уже дал на него ответ.

БЫТОВЫЕ ЕДИНИЦЫ:
- Для Ж/Б колец по умолчанию используй КС 10.9: 1 кольцо = 0,9 м. Всегда
  называй это допущением, если точный размер кольца не был указан.
- «Колодец на X колец» -> well_ring_count=X, well_depth_m=X*0.9.
- «Зеркало воды на X кольцах» -> water_level_ring_count=X,
  dynamic_water_level_m=X*0.9. Если клиент сам ранее определил эту фразу иначе,
  следуй определению клиента и отметь допущение.
- «Столб воды X колец» -> water_column_ring_count=X,
  water_column_depth_m=X*0.9.
- «100 литров» без времени: предварительно считай 100 л/мин = 6 м³/ч,
  запиши flow_unit_assumed=true и обязательно попроси подтвердить, что речь о
  литрах в минуту, а не об общем объёме.
- Ведро = 10 л; куб = 1 м³ = 1000 л.
- 1 бар ≈ 10 м напора. 10 м горизонтального участка можно использовать как
  грубую оценку 1 м дополнительного напора, обязательно обозначая это ориентиром.

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
- Для PEX/PE-RT 16x2 при шаге 15 см ориентир трубы 6,5–7 м на 1 м².
- Ориентировочная длина одного контура 80 м; инженерный предел обычно 80–100 м.
- Число контуров = ceil(площадь*6.5/80).
- Один коллектор — максимум 12 выходов; при большем числе контуров предложи два.
- Для 240 м² ориентир: 1560–1680 м трубы, около 20 контуров, два коллектора
  примерно 10+10. Затем спроси один параметр: водяной ли пол от котла и готов ли
  пирог/утеплитель.
- Не выдавай ориентировочный расчёт за готовый проект и не подтверждай
  гидравлическую совместимость без расчётной рабочей точки.

Верни только JSON следующего вида:
{
  "handled": true,
  "continuation": true,
  "intent_type": "broad_category|attribute_request|unknown",
  "category": "pumps|pipes|boilers|water_heaters|other",
  "project_scope": "water|warm_floor|heating|general|null",
  "slots": {},
  "assumptions": [],
  "missing_slot_keys": [],
  "needs_clarification": true,
  "clarifying_question": "один следующий вопрос или null",
  "ready_for_catalog_selection": false,
  "response_mode": "clarify|project_progress|catalog_search|none",
  "reply": "короткий ответ клиенту без SKU, цен и неподтверждённых товаров"
}

Если сообщение не относится к инженерной задаче и не продолжает инженерный
вопрос, верни handled=false. Числа в slots должны быть числами, не строками.
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
    _BOOL_SLOTS = {
        "flow_unit_assumed",
        "ring_height_assumed",
        "needs_float_switch",
        "has_warm_floor",
    }
    _NUMERIC_LIMITS: dict[str, tuple[float, float]] = {
        "well_ring_count": (1, 100),
        "well_depth_m": (0.1, 300),
        "water_level_ring_count": (0.1, 100),
        "water_column_ring_count": (0.1, 100),
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
        if session.pending_question or session.slots.get("project_scope") or session.slots.get(
            "scope_funnel"
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
            used
            and data.get("handled")
            and (accepted_signal is not False)
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
                data.get("category") if data.get("category") in self._CATEGORIES else None
            ),
            project_scope=(
                data.get("project_scope")
                if data.get("project_scope") in self._SCOPES
                else None
            ),
            slots=self._clean_slots(data.get("slots")),
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
            reply=self._clean_reply(data.get("reply")),
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

    def _clean_slots(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, Any] = {}
        for key, value in raw.items():
            if key in self._STRING_SLOTS and isinstance(value, str):
                text = self._clean_text(value, 120)
                if text:
                    cleaned[key] = text
                continue
            if key in self._BOOL_SLOTS and isinstance(value, bool):
                cleaned[key] = value
                continue
            limits = self._NUMERIC_LIMITS.get(key)
            if limits and isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                if limits[0] <= number <= limits[1]:
                    cleaned[key] = int(number) if number.is_integer() else round(number, 4)
        return cleaned

    @staticmethod
    def _clean_string_list(raw: Any, *, limit: int) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [
            str(value).strip()[:160]
            for value in raw[:limit]
            if str(value).strip()
        ]

    @staticmethod
    def _clean_text(raw: Any, limit: int) -> str | None:
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        return text[:limit] if text else None

    def _clean_reply(self, raw: Any) -> str | None:
        reply = self._clean_text(raw, 1400)
        if not reply:
            return None
        # This agent has no catalogue. Any product identity/price/link is unsafe
        # and must fall through to the grounded catalogue consultant.
        unsafe = bool(
            re.search(r"https?://|\bарт(?:икул)?\.?\s*[:№]?|\d[\d ]*\s*(?:₽|руб)", reply, re.I)
        )
        return None if unsafe else reply
