"""Constrained deterministic and optional LLM rendering of AnswerPlan."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any

from app.openrouter_client import OpenRouterClient

from .contracts import (
    AnswerClaim,
    AnswerPlan,
    ClaimKind,
    LimitationStatus,
    NaturalizationLayout,
    NaturalizationProposal,
    NextStepKind,
    RenderedAnswer,
    RenderedAnswerResult,
    RenderedSegment,
    RenderedSegmentKind,
    TransitionStyle,
)


RENDERER_PROMPT_VERSION = "answer-renderer-v2.2"
RENDERER_PROMPT = """
Ты выбираешь только нейтральные связки между уже готовыми сегментами ответа
продавца-консультанта. Верни JSON по схеме NaturalizationLayout. Фактический
текст тебе не передаётся и создавать текст нельзя. Укажи не более одной
связки перед существующим segment_id, не ставь связку перед первым сегментом
и не меняй порядок сегментов. Допустимы только стили из схемы. Можно вернуть
пустой список transitions. Идентификатор плана возвращать не нужно: он
подставляется детерминированно вне модели.
""".strip()


_TRANSITION_TEXT = {
    TransitionStyle.ALSO: "Дополнительно:",
    TransitionStyle.IMPORTANT: "Важно:",
    TransitionStyle.THEREFORE: "Поэтому:",
    TransitionStyle.NEXT: "Далее:",
}
ALLOWED_TRANSITION_TEXTS = frozenset(_TRANSITION_TEXT.values())


def _value(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _claim_text(claim: AnswerClaim) -> str:
    value = _value(claim.value)
    unit = f" {claim.unit}" if claim.unit else ""
    if claim.kind == ClaimKind.PRODUCT_IDENTITY:
        return f"Товар: {value}."
    if claim.kind == ClaimKind.PRICE:
        return f"Цена: {value}{unit}."
    if claim.kind == ClaimKind.STOCK:
        return f"Подтверждённый статус наличия: {value}{unit}."
    if claim.kind == ClaimKind.LINK:
        return f"Ссылка на товар: {value}."
    if claim.kind == ClaimKind.COMMERCE_STATUS:
        statuses = {
            "not_requested": "операция не запрошена",
            "prepared": "команда только подготовлена и не отправлена",
            "queued": "операция поставлена в очередь, получение не подтверждено",
            "local_draft_saved": "сохранён только локальный черновик",
            "delivered": "внешняя система подтвердила получение",
            "failed": "выполнение операции завершилось ошибкой",
            "delivery_unknown": "получение внешней системой не подтверждено",
            "cancelled": "операция отменена",
        }
        return f"Статус операции: {statuses.get(value, value)}."
    return f"{claim.predicate}: {value}{unit}."


def deterministic_render(answer_plan: AnswerPlan) -> RenderedAnswer:
    segments: list[RenderedSegment] = []
    for claim in answer_plan.claims:
        if not claim.allowed_in_response:
            continue
        literal = _value(claim.value)
        critical = (literal, *(() if not claim.unit else (claim.unit,)))
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{claim.claim_id}",
                kind=RenderedSegmentKind.FACT,
                source_ids=(claim.claim_id, *claim.source_ref_ids),
                text=_claim_text(claim),
                critical_literals=critical,
            )
        )
    for product in answer_plan.products:
        qualifier = {
            "exact": "точное подтверждённое совпадение",
            "preliminary": "предварительный вариант",
            "analog": "аналог с отличиями",
            "alternative": "альтернативное решение",
            "unverified": "вариант с непроверенными по фиду параметрами",
        }[product.status.value]
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{product.product_plan_id}",
                kind=RenderedSegmentKind.PRODUCT,
                source_ids=(product.product_plan_id, *product.source_ref_ids),
                text=f"{product.name}, SKU {product.sku}: {qualifier}.",
                critical_literals=(product.name, product.sku),
            )
        )
    for difference in answer_plan.analog_differences:
        requested = _value(difference.requested_value)
        actual = (
            _value(difference.candidate_value)
            if difference.candidate_value is not None
            else "не подтверждено"
        )
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{difference.difference_id}",
                kind=RenderedSegmentKind.LIMITATION,
                source_ids=(difference.difference_id, *difference.source_ref_ids),
                text=(
                    f"Отличие аналога по {difference.fact_name}: "
                    f"требовалось {requested}, у кандидата {actual}."
                ),
                critical_literals=tuple(
                    item
                    for item in (requested, actual)
                    if item != "не подтверждено"
                ),
            )
        )
    labels = {
        LimitationStatus.UNKNOWN: "пользователь не знает значение",
        LimitationStatus.REFUSED: "пользователь отказался сообщать значение",
        LimitationStatus.DEFERRED: "параметр отложен",
        LimitationStatus.CATALOGUE_MISSING: "параметр отсутствует в данных фида",
        LimitationStatus.UNVERIFIED: "параметр нельзя подтвердить",
        LimitationStatus.UNSUPPORTED: "точное решение не поддержано текущими данными",
        LimitationStatus.CONFLICTING: "данные противоречат друг другу",
        LimitationStatus.CAPABILITY_BOUNDARY: "выполнение операции не подтверждено",
    }
    for limitation in answer_plan.limitations:
        fact = f" ({limitation.fact_name})" if limitation.fact_name else ""
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{limitation.limitation_id}",
                kind=RenderedSegmentKind.LIMITATION,
                source_ids=(limitation.limitation_id, *limitation.source_ref_ids),
                text=f"Ограничение{fact}: {labels[limitation.status]}.",
                critical_literals=(() if limitation.fact_name is None else (limitation.fact_name,)),
            )
        )
    if answer_plan.question is not None:
        question = answer_plan.question
        segments.append(
            RenderedSegment(
                segment_id=f"segment_{question.question_id}",
                kind=RenderedSegmentKind.QUESTION,
                source_ids=(question.question_id, *question.source_ref_ids),
                text=f"Уточните, пожалуйста, параметр {question.fact_name}?",
                critical_literals=(question.fact_name,),
            )
        )
    next_labels = {
        NextStepKind.PROVIDE_DIRECT_ANSWER: "Следующий шаг: использовать подтверждённый прямой ответ.",
        NextStepKind.ASK_DECISION_FACT: "Следующий шаг: дождаться одного параметра, который меняет решение.",
        NextStepKind.EXPLAIN_HOW_TO_FIND_FACT: "Следующий шаг: объяснить, как определить неизвестный параметр.",
        NextStepKind.SHOW_PRELIMINARY_OPTIONS: "Следующий шаг: показать только предварительные варианты.",
        NextStepKind.CONTINUE_WITH_CONFIRMED_FACTS: "Следующий шаг: продолжить по подтверждённым данным.",
        NextStepKind.COMPARE_CANDIDATES: "Следующий шаг: сравнить проверяемых кандидатов.",
        NextStepKind.PRESENT_ANALOG_DIFFERENCES: "Следующий шаг: показать отличия совместимого аналога.",
        NextStepKind.OFFER_VERIFIABLE_EXTERNAL_STEP: "Следующий шаг: предложить только проверяемое внешнее действие.",
        NextStepKind.STATE_CAPABILITY_BOUNDARY: "Следующий шаг: честно обозначить границу возможности.",
        NextStepKind.CLOSE_TASK: "Следующий шаг: корректно закрыть текущую задачу.",
        NextStepKind.WAIT_FOR_CUSTOMER: "Следующий шаг: дождаться решения пользователя.",
    }
    segments.append(
        RenderedSegment(
            segment_id=f"segment_{answer_plan.next_step.next_step_id}",
            kind=RenderedSegmentKind.NEXT_STEP,
            source_ids=(answer_plan.next_step.next_step_id,),
            text=next_labels[answer_plan.next_step.kind],
        )
    )
    by_item_id = {
        segment.source_ids[0]: segment
        for segment in segments
        if segment.source_ids
    }
    ordered = tuple(
        by_item_id[item_id]
        for section in answer_plan.sections
        for item_id in section.item_ids
        if item_id in by_item_id
    )
    return RenderedAnswer(
        plan_id=answer_plan.plan_id,
        renderer="deterministic",
        segments=ordered,
        text="\n".join(item.text for item in ordered),
    )


def _apply_naturalization_layout(
    fallback: RenderedAnswer,
    layout: NaturalizationLayout,
) -> RenderedAnswer:
    segment_ids = tuple(item.segment_id for item in fallback.segments)
    known_ids = set(segment_ids)
    first_id = segment_ids[0] if segment_ids else None
    seen: set[str] = set()
    by_target = {}
    for transition in layout.transitions:
        target = transition.before_segment_id
        if target not in known_ids:
            raise ValueError(f"unknown transition target: {target}")
        if target == first_id:
            raise ValueError("transition before first segment is not allowed")
        if target in seen:
            raise ValueError(f"duplicate transition target: {target}")
        seen.add(target)
        by_target[target] = transition.style

    rendered: list[RenderedSegment] = []
    for segment in fallback.segments:
        style = by_target.get(segment.segment_id)
        if style is not None:
            rendered.append(
                RenderedSegment(
                    segment_id=f"transition_{segment.segment_id}_{style.value}",
                    kind=RenderedSegmentKind.TRANSITION,
                    source_ids=(),
                    text=_TRANSITION_TEXT[style],
                )
            )
        # Factual, product, limitation, question and next-step segments are
        # copied byte-for-byte from the deterministic renderer. The LLM never
        # receives or rewrites their prose or protected literals.
        rendered.append(segment)
    return RenderedAnswer(
        plan_id=fallback.plan_id,
        renderer="llm",
        segments=tuple(rendered),
        text="\n".join(item.text for item in rendered),
    )


class ResponseRendererV2:
    def __init__(
        self,
        llm_client: OpenRouterClient | Any | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model = model

    def render(
        self,
        answer_plan: AnswerPlan,
        *,
        naturalize: bool = False,
        locale: str = "ru-RU",
    ) -> RenderedAnswerResult:
        started = monotonic()
        fallback = deterministic_render(answer_plan)
        if not naturalize:
            return RenderedAnswerResult(
                status="rendered",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                reason_codes=("deterministic_answer_renderer",),
                latency_ms=int((monotonic() - started) * 1000),
            )
        if self.llm_client is None:
            return RenderedAnswerResult(
                status="fallback",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                llm_requested=False,
                rejection_reason="response_llm_client_unavailable",
                reason_codes=("deterministic_fallback_selected",),
                latency_ms=int((monotonic() - started) * 1000),
            )
        model = self.model or self.llm_client.settings.llm_model_strong
        proposal_fallback = NaturalizationProposal(
            transitions=(),
        )
        payload = {
            "prompt_version": RENDERER_PROMPT_VERSION,
            "locale": locale,
            "segment_outline": [
                {
                    "segment_id": item.segment_id,
                    "kind": item.kind.value,
                }
                for item in fallback.segments
            ],
            "output_schema": NaturalizationProposal.model_json_schema(),
        }
        raw, transported = self.llm_client.complete_json(
            agent="ResponseRendererV2.shadow",
            messages=[
                {"role": "system", "content": RENDERER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            fallback=proposal_fallback.model_dump(mode="json"),
            model=model,
        )
        if not transported:
            return RenderedAnswerResult(
                status="fallback",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                llm_requested=True,
                model=model,
                rejection_reason=(
                    getattr(self.llm_client, "last_fallback_reason", None)
                    or "response_llm_transport_unavailable"
                ),
                reason_codes=("deterministic_fallback_selected",),
                latency_ms=int((monotonic() - started) * 1000),
            )
        try:
            proposal = NaturalizationProposal.model_validate(raw)
            layout = NaturalizationLayout(
                plan_id=answer_plan.plan_id,
                transitions=proposal.transitions,
            )
            rendered = _apply_naturalization_layout(fallback, layout)
        except Exception as exc:
            return RenderedAnswerResult(
                status="fallback",
                rendered_answer=fallback,
                deterministic_fallback=fallback,
                llm_requested=True,
                llm_output_accepted=False,
                model=model,
                rejection_reason=f"{type(exc).__name__}: {exc}"[:500],
                reason_codes=("malformed_response_renderer_output", "deterministic_fallback_selected"),
                latency_ms=int((monotonic() - started) * 1000),
            )
        return RenderedAnswerResult(
            status="rendered",
            rendered_answer=rendered,
            deterministic_fallback=fallback,
            llm_requested=True,
            llm_output_accepted=True,
            model=model,
            reason_codes=("structured_response_llm_output",),
            latency_ms=int((monotonic() - started) * 1000),
        )
