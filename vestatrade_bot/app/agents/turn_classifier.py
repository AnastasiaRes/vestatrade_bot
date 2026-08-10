"""Decide what the customer's turn actually *is* before the parameter funnel runs.

The dialogue is driven by a rule layer that, when a clarifying question is
pending, treats every incoming message as either an answer to that question or
as noise.  That works for "180 мм" and fails for everything a real buyer says
around it:

    — Для какого участка отопления нужна труба? …
    — а как подобрать диаметр?
    — Не буду подставлять случайный товар, пока этот параметр не известен. …

The customer asked a question and got the funnel back.  Recognising *intent of
the turn* — a request to be taught, a request for a definition, an answer to the
pending question — is the part a language model does well and a keyword table
does badly, so it is the part that moves here (same reasoning as
``slot_answer_resolver``).

What the model is allowed to produce stays deliberately small:

* one ``kind`` from a closed list, nothing else is accepted;
* an optional ``term`` copied verbatim from the customer's message;
* no product facts, no slots, no arithmetic — routing only.

Everything else keeps its current behaviour: an unrecognised or unavailable
answer degrades to ``unknown`` and the existing pipeline runs untouched, so the
bot still works with the LLM disabled or the provider down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .utils import normalize_text


logger = logging.getLogger(__name__)


# Kinds the router may act on. Anything outside this set is treated as
# ``unknown`` and left to the existing rule pipeline.
TURN_KINDS = (
    # «что значит ВР/ВР?», «что такое монтажная длина?»
    "terminology",
    # «как подобрать диаметр?», «как это узнать?», «как измерить напор?»
    "teaching",
    # «180 мм», «электрический», «на 100 квадратов» — ответ на заданный вопрос
    "answer_to_pending",
    # «нужен котёл на 100 м²», «покажи краны 1/2»
    "product_request",
    # «сколько стоит?», «а он в наличии?», «какой артикул?» — про показанный товар
    "fact_about_shown",
    # всё остальное
    "other",
)

CLASSIFIER_PROMPT = (
    "Ты классификатор реплик в чате магазина инженерной сантехники. "
    "Определи, ЧЕМ является последняя реплика клиента. Верни ТОЛЬКО JSON:\n"
    '{"kind": "<одно значение>", "term": "<термин из реплики или null>"}\n\n'
    "Допустимые значения kind:\n"
    "- terminology — клиент просит объяснить значение термина или сокращения "
    "(«что такое монтажная длина», «что значит ВР/ВР», «что такое полнопроходной»).\n"
    "- teaching — клиент просит научить, как получить или выбрать параметр "
    "(«как это узнать», «как подобрать диаметр», «как измерить напор», "
    "«как посчитать мощность»).\n"
    "- answer_to_pending — реплика отвечает на вопрос бота, даже коротко "
    "(«180 мм», «электрический», «для отопления», «не знаю»).\n"
    "- product_request — клиент просит подобрать или показать товар.\n"
    "- fact_about_shown — клиент спрашивает цену, наличие, артикул или "
    "характеристику уже показанного товара.\n"
    "- other — всё остальное.\n\n"
    "ВАЖНО: если сомневаешься между answer_to_pending и чем-то ещё — выбирай "
    "answer_to_pending: прерывать подбор дороже, чем не объяснить термин. "
    "Не придумывай товары, цены и характеристики: ты только классифицируешь. "
    "term заполняй только для kind=terminology, дословно словом из реплики клиента."
)


@dataclass
class TurnKind:
    """Result of classifying one customer turn."""

    kind: str = "unknown"
    term: str | None = None
    llm_requested: bool = False
    llm_used: bool = False
    rejection_reason: str | None = None

    @property
    def wants_explanation(self) -> bool:
        return self.kind in {"terminology", "teaching"}


class TurnClassifierAgent:
    """Ask the model what kind of turn this is; accept only a closed answer."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    def classify(
        self,
        *,
        message: str,
        pending_question: str | None,
        shown_products: list[str] | None = None,
    ) -> TurnKind:
        text = str(message or "").strip()
        if not text:
            return TurnKind(rejection_reason="empty message")

        context_lines = []
        if pending_question:
            context_lines.append(f"Последний вопрос бота: {pending_question}")
        if shown_products:
            context_lines.append(
                "Уже показанные товары: " + ", ".join(shown_products[:3])
            )
        context = "\n".join(context_lines) or "Бот пока ничего не спрашивал."

        messages = [
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user", "content": f"{context}\n\nРеплика клиента: {text}"},
        ]
        fallback: dict[str, Any] = {"kind": "other", "term": None}
        try:
            data, used = self.llm_client.complete_json(
                "TurnClassifierAgent",
                messages,
                fallback,
            )
        except Exception as exc:  # pragma: no cover - defensive integration guard
            logger.warning("Turn classification failed: %s", exc)
            return TurnKind(llm_requested=True, rejection_reason=str(exc))

        result = TurnKind(llm_requested=True, llm_used=bool(used))
        if not used or not isinstance(data, dict):
            result.rejection_reason = "LLM unavailable"
            return result
        if getattr(self.llm_client, "last_json_output_accepted", None) is False:
            result.rejection_reason = "LLM answer was not valid JSON"
            return result

        kind = normalize_text(str(data.get("kind") or "")).replace(" ", "_")
        if kind not in TURN_KINDS:
            result.rejection_reason = f"kind outside the closed list: {kind!r}"
            return result
        result.kind = kind

        term = data.get("term")
        if kind == "terminology" and isinstance(term, str) and term.strip():
            # The term must come from the customer, not from the model's memory.
            if normalize_text(term) in normalize_text(text):
                result.term = term.strip()[:60]
        return result
