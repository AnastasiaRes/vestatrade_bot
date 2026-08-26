"""Fail-closed deterministic grounding validation for rendered answers."""

from __future__ import annotations

import math
import re

from .contracts import (
    AnswerPlan,
    AnswerSourceSnapshot,
    CandidateFactStatus,
    AnswerValidationResult,
    ClaimKind,
    KnowledgeStatus,
    ProductRecommendationRole,
    ProductPresentationStatus,
    RecommendationCriterion,
    RenderedAnswer,
    RenderedSegmentKind,
    ValidationViolation,
)


_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?")
_MIXED_ID_RE = re.compile(r"\b(?=[\w./-]*\d)(?=[\w./-]*[A-Za-zА-Яа-я])[\w./-]{3,}\b")
_PROMISE_RE = re.compile(
    r"\b(?:передан[аоы]?|отправлен[аоы]?|доставлен[аоы]?|"
    r"зарезервирован[аоы]?|оформлен[аоы]?)\b",
    re.IGNORECASE,
)
_PRODUCT_LINKED_CLAIM_KINDS = frozenset(
    {
        ClaimKind.PRODUCT_IDENTITY,
        ClaimKind.PRICE,
        ClaimKind.STOCK,
        ClaimKind.LINK,
        ClaimKind.PRODUCT_ATTRIBUTE,
    }
)


def _literal_values(plan: AnswerPlan) -> set[str]:
    values: set[str] = set()
    for claim in plan.claims:
        if claim.allowed_in_response and claim.value is not None:
            # A typed predicate may be rendered as a generic field label (for
            # example ``area_m2``).  It is provenance-checked as part of the
            # claim, so its literal is allowed like the value and unit.
            values.add(claim.predicate)
            values.add(str(claim.value))
            if claim.unit:
                values.add(claim.unit)
    for product in plan.products:
        values.update((product.sku, product.name))
    for item in plan.analog_differences:
        values.add(str(item.requested_value))
        if item.candidate_value is not None:
            values.add(str(item.candidate_value))
    for item in plan.limitations:
        if item.fact_name:
            values.add(item.fact_name)
    if plan.question is not None:
        values.add(plan.question.fact_name)
    if plan.next_step.fact_name:
        values.add(plan.next_step.fact_name)
    report = plan.next_step.candidate_fact_report
    if report is not None:
        values.add(report.fact_name)
        for item in report.items:
            values.update((item.sku, item.name, item.fact_name))
            if item.value is not None:
                values.add(str(item.value))
            if item.unit:
                values.add(item.unit)
    return {item for item in values if item}


def _numeric_spellings(value: str) -> set[str]:
    """Return equivalent decimal spellings without changing numeric meaning."""
    normalized = value.replace(",", ".")
    spellings = {normalized}
    try:
        number = float(normalized)
    except ValueError:
        return spellings
    if number.is_integer():
        spellings.add(str(int(number)))
    return spellings


def _allowed_ids(plan: AnswerPlan) -> set[str]:
    return {
        *(item.source_ref_id for item in plan.sources),
        *(item.claim_id for item in plan.claims),
        *(item.product_plan_id for item in plan.products),
        *(item.difference_id for item in plan.analog_differences),
        *(item.limitation_id for item in plan.limitations),
        *((plan.question.question_id,) if plan.question else ()),
        plan.next_step.next_step_id,
    }


def _required_ids(plan: AnswerPlan) -> set[str]:
    return {
        item_id
        for section in plan.sections
        if section.required
        for item_id in section.item_ids
    }


def _claim_source_violation(
    plan: AnswerPlan,
    snapshot: AnswerSourceSnapshot,
) -> list[ValidationViolation]:
    violations: list[ValidationViolation] = []
    source_by_ref = {item.source_ref_id: item for item in plan.sources}
    for claim in plan.claims:
        if not claim.allowed_in_response:
            continue
        claim_sources = [
            source_by_ref.get(source_ref_id)
            for source_ref_id in claim.source_ref_ids
        ]
        if any(item is None for item in claim_sources):
            violations.append(
                ValidationViolation(code="claim_source_reference_missing", detail=claim.claim_id)
            )
            continue
        source_types = {item.source_type for item in claim_sources if item is not None}
        product = snapshot.product(claim.subject_ref)
        if claim.kind == ClaimKind.PRICE and (
            product is None
            or product.price != claim.value
            or product.currency != claim.unit
            or not any(item.value == "catalog_price" for item in source_types)
        ):
            violations.append(
                ValidationViolation(code="price_source_mismatch", detail=claim.claim_id)
            )
        elif claim.kind == ClaimKind.STOCK:
            expected = (
                product.stock_qty
                if product is not None and claim.predicate == "stock_qty"
                else product.stock_status if product is not None else None
            )
            if (
                product is None
                or expected != claim.value
                or not any(item.value == "catalog_stock" for item in source_types)
            ):
                violations.append(
                    ValidationViolation(code="stock_source_mismatch", detail=claim.claim_id)
                )
        elif claim.kind == ClaimKind.LINK and (
            product is None
            or product.url != claim.value
            or not any(item.value == "catalog_link" for item in source_types)
        ):
            violations.append(
                ValidationViolation(code="link_source_mismatch", detail=claim.claim_id)
            )
        elif claim.kind == ClaimKind.PRODUCT_IDENTITY and (
            product is None
            or product.name != claim.value
            or not any(item.value == "catalog_identity" for item in source_types)
        ):
            violations.append(
                ValidationViolation(code="identity_source_mismatch", detail=claim.claim_id)
            )
        elif claim.kind == ClaimKind.PRODUCT_ATTRIBUTE:
            if product is None or not any(
                item.name == claim.predicate
                and item.value == claim.value
                and item.unit == claim.unit
                for item in product.facts
            ):
                violations.append(
                    ValidationViolation(
                        code="catalog_attribute_source_mismatch",
                        detail=claim.claim_id,
                    )
                )
            elif not any(item.value == "catalog_attribute" for item in source_types):
                violations.append(
                    ValidationViolation(
                        code="catalog_attribute_source_type_missing",
                        detail=claim.claim_id,
                    )
                )
        elif claim.kind == ClaimKind.CUSTOMER_CONSTRAINT:
            constraint_sources = [
                item
                for item in claim_sources
                if item is not None and item.source_type.value == "constraint_fact"
            ]
            evidence = next(
                (
                    snapshot.constraint(item.source_id)
                    for item in constraint_sources
                    if snapshot.constraint(item.source_id) is not None
                ),
                None,
            )
            if (
                evidence is None
                or evidence.status != "known"
                or evidence.name != claim.predicate
                or evidence.value != claim.value
                or evidence.unit != claim.unit
            ):
                violations.append(
                    ValidationViolation(
                        code="constraint_source_mismatch",
                        detail=claim.claim_id,
                    )
                )
        elif claim.kind == ClaimKind.CAPABILITY_FACT:
            capability_sources = [
                item
                for item in claim_sources
                if item is not None and item.source_type.value == "capability_result"
            ]
            evidence = next(
                (
                    item
                    for item in snapshot.capability_facts
                    if any(source.source_id == item.fact_id for source in capability_sources)
                ),
                None,
            )
            if (
                evidence is None
                or not evidence.confirmed
                or evidence.name != claim.predicate
                or evidence.value != claim.value
                or evidence.unit != claim.unit
            ):
                violations.append(
                    ValidationViolation(
                        code="capability_source_mismatch",
                        detail=claim.claim_id,
                    )
                )
        elif claim.kind == ClaimKind.COMMERCE_STATUS:
            evidence = snapshot.commerce_workflow(claim.subject_ref)
            if evidence is None or evidence.execution_status != claim.value:
                violations.append(
                    ValidationViolation(
                        code="commerce_status_source_mismatch",
                        detail=claim.claim_id,
                    )
                )
            elif claim.value == "delivered":
                receipt_sources = [
                    item
                    for item in claim_sources
                    if item is not None and item.source_type.value == "commerce_receipt"
                ]
                if (
                    not evidence.receipt_ref
                    or not any(
                        item.source_id == evidence.receipt_ref for item in receipt_sources
                    )
                ):
                    violations.append(
                        ValidationViolation(
                            code="commerce_receipt_source_mismatch",
                            detail=claim.claim_id,
                        )
                    )
    return violations


def _plan_integrity_violations(
    plan: AnswerPlan,
    snapshot: AnswerSourceSnapshot,
) -> list[ValidationViolation]:
    violations: list[ValidationViolation] = []
    source_ids = {item.source_ref_id for item in plan.sources}
    source_by_id = {item.source_ref_id: item for item in plan.sources}
    entities = (
        *plan.claims,
        *plan.products,
        *plan.analog_differences,
        *plan.limitations,
        *((plan.question,) if plan.question is not None else ()),
        *(
            plan.next_step.candidate_fact_report.items
            if plan.next_step.candidate_fact_report is not None
            else ()
        ),
    )
    for entity in entities:
        for source_ref_id in entity.source_ref_ids:
            if source_ref_id not in source_ids:
                violations.append(
                    ValidationViolation(
                        code="plan_source_reference_missing",
                        detail=source_ref_id,
                    )
                )
    for claim in plan.claims:
        if claim.allowed_in_response and claim.knowledge_status != KnowledgeStatus.CONFIRMED:
            violations.append(
                ValidationViolation(code="unconfirmed_claim_asserted", detail=claim.claim_id)
            )
        if claim.allowed_in_response and claim.kind in _PRODUCT_LINKED_CLAIM_KINDS:
            linked_presentations = [
                product
                for product in plan.products
                if product.sku == claim.subject_ref
                and claim.claim_id in product.claim_ids
            ]
            if len(linked_presentations) != 1:
                violations.append(
                    ValidationViolation(
                        code="catalog_claim_without_single_product_presentation",
                        detail=claim.claim_id,
                    )
                )
    for product_plan in plan.products:
        product = snapshot.product(product_plan.sku)
        candidate = snapshot.candidate(
            product_plan.search_plan_id,
            product_plan.sku,
        )
        if product is None:
            violations.append(
                ValidationViolation(
                    code="presented_product_source_missing",
                    detail=product_plan.product_plan_id,
                )
            )
            continue
        if (
            product.name != product_plan.name
            or product.product_kind != product_plan.product_kind
            or product.role != product_plan.role
        ):
            violations.append(
                ValidationViolation(
                    code="presented_product_identity_mismatch",
                    detail=product_plan.product_plan_id,
                )
            )
        if candidate is None:
            violations.append(
                ValidationViolation(
                    code="candidate_assessment_source_missing",
                    detail=product_plan.product_plan_id,
                )
            )
            continue
        if candidate.status.value == "rejected":
            violations.append(
                ValidationViolation(
                    code="rejected_candidate_presented",
                    detail=product_plan.product_plan_id,
                )
            )
        if (
            candidate.product_kind != product_plan.product_kind
            or candidate.role != product_plan.role
            or candidate.task_id != product_plan.task_id
            or candidate.goal_id != product_plan.goal_id
        ):
            violations.append(
                ValidationViolation(
                    code="candidate_assessment_mismatch",
                    detail=product_plan.product_plan_id,
                )
            )
        if (
            candidate.status.value == "unverified"
            or candidate.missing_hard_facts
        ) and product_plan.status != ProductPresentationStatus.UNVERIFIED:
            violations.append(
                ValidationViolation(
                    code="unverified_candidate_mislabelled",
                    detail=product_plan.product_plan_id,
                )
            )
        if candidate.relaxations and product_plan.status != ProductPresentationStatus.ANALOG:
            violations.append(
                ValidationViolation(
                    code="analog_candidate_mislabelled",
                    detail=product_plan.product_plan_id,
                )
            )

    # A strict catalogue no-match and a verified exact/compatible analogue in
    # the same task scope cannot both be true.  Fail closed if stale catalogue
    # memory or cross-task assembly ever combines those mutually exclusive
    # outcomes in one answer plan.
    for limitation in plan.limitations:
        if limitation.reason_code != "no_verified_contract_match":
            continue
        contradictory = next(
            (
                product
                for product in plan.products
                if product.task_id == limitation.task_id
                and (
                    limitation.goal_id is None
                    or product.goal_id == limitation.goal_id
                )
                and product.status
                in {
                    ProductPresentationStatus.EXACT,
                    ProductPresentationStatus.ANALOG,
                }
            ),
            None,
        )
        if contradictory is not None:
            violations.append(
                ValidationViolation(
                    code="catalog_no_match_contradicts_verified_product",
                    detail=contradictory.product_plan_id,
                )
            )
    recommendation_groups: dict[
        tuple[str, str],
        list[object],
    ] = {}
    for product_plan in plan.products:
        if product_plan.recommendation_role is not None:
            recommendation_groups.setdefault(
                (product_plan.task_id, product_plan.search_plan_id),
                [],
            ).append(product_plan)

    recommendation_action_present = bool(
        plan.primary_action.value == "recommend_one"
        or (
            plan.secondary_action is not None
            and plan.secondary_action.value == "recommend_one"
        )
    )
    if recommendation_groups and not recommendation_action_present:
        violations.append(
            ValidationViolation(code="recommendation_without_typed_action")
        )
    if (
        recommendation_action_present
        and plan.products
        and not recommendation_groups
    ):
        violations.append(
            ValidationViolation(code="typed_recommendation_missing_primary")
        )

    for (task_id, search_plan_id), raw_group in recommendation_groups.items():
        group = sorted(
            raw_group,
            key=lambda item: item.recommendation_rank or 99,
        )
        primary = [
            item
            for item in group
            if item.recommendation_role == ProductRecommendationRole.PRIMARY
        ]
        alternatives = [
            item
            for item in group
            if item.recommendation_role == ProductRecommendationRole.ALTERNATIVE
        ]
        if len(primary) != 1:
            violations.append(
                ValidationViolation(
                    code="recommendation_primary_count_invalid",
                    detail=f"{task_id}:{search_plan_id}",
                )
            )
            continue
        if len(alternatives) > 2 or [
            item.recommendation_rank for item in group
        ] != list(range(1, len(group) + 1)):
            violations.append(
                ValidationViolation(
                    code="recommendation_alternative_count_or_rank_invalid",
                    detail=f"{task_id}:{search_plan_id}",
                )
            )
        if any(item.status != ProductPresentationStatus.EXACT for item in group):
            violations.append(
                ValidationViolation(
                    code="non_exact_product_recommended",
                    detail=f"{task_id}:{search_plan_id}",
                )
            )

        exact_candidates = []
        seen_candidate_skus: set[str] = set()
        for candidate in snapshot.catalog_candidates:
            if (
                candidate.task_id != task_id
                or candidate.search_plan_id != search_plan_id
                or candidate.status.value != "eligible"
                or candidate.mismatched_hard_facts
                or candidate.missing_hard_facts
                or not set(candidate.required_hard_facts).issubset(
                    candidate.matched_hard_facts
                )
                or candidate.relaxations
                or candidate.sku in seen_candidate_skus
                or snapshot.product(candidate.sku) is None
            ):
                continue
            seen_candidate_skus.add(candidate.sku)
            exact_candidates.append(candidate)

        priced = []
        unpriced = []
        for candidate in exact_candidates:
            product = snapshot.product(candidate.sku)
            if (
                product is not None
                and product.price is not None
                and not isinstance(product.price, bool)
                and math.isfinite(float(product.price))
                and product.price >= 0
                and product.currency
            ):
                priced.append((candidate, float(product.price), product.currency))
            else:
                unpriced.append(candidate)
        currencies = {item[2] for item in priced}
        if len(exact_candidates) <= 1:
            expected = exact_candidates
            expected_criterion = RecommendationCriterion.ONLY_EXACT_ELIGIBLE
            expected_reasons = ("only_exact_eligible_candidate",)
        elif priced and len(currencies) == 1:
            priced.sort(
                key=lambda item: (
                    item[1],
                    item[0].sku.casefold(),
                    item[0].sku,
                )
            )
            unpriced.sort(key=lambda item: (item.sku.casefold(), item.sku))
            expected = [item[0] for item in priced] + unpriced
            expected_criterion = RecommendationCriterion.LOWEST_CONFIRMED_PRICE
            lowest = priced[0][1]
            tied = sum(
                abs(item[1] - lowest) <= 1e-9
                for item in priced
            )
            expected_reasons = (
                "lowest_confirmed_price_among_priced_exact_candidates",
                *(
                    ("stable_sku_tiebreak_among_equal_lowest_prices",)
                    if tied > 1
                    else ()
                ),
            )
        else:
            expected = sorted(
                exact_candidates,
                key=lambda item: (item.sku.casefold(), item.sku),
            )
            expected_criterion = RecommendationCriterion.STABLE_SKU_TIEBREAK
            expected_reasons = (
                "stable_sku_tiebreak_without_comparable_confirmed_price",
            )

        if not expected:
            violations.append(
                ValidationViolation(
                    code="recommendation_has_no_exact_candidate_source",
                    detail=f"{task_id}:{search_plan_id}",
                )
            )
            continue
        if [item.sku for item in group] != [
            item.sku for item in expected[: len(group)]
        ]:
            violations.append(
                ValidationViolation(
                    code="recommendation_order_not_source_grounded",
                    detail=f"{task_id}:{search_plan_id}",
                )
            )
        primary_item = primary[0]
        if (
            primary_item.recommendation_criterion != expected_criterion
            or primary_item.recommendation_reason_codes != expected_reasons
        ):
            violations.append(
                ValidationViolation(
                    code="recommendation_reason_not_source_grounded",
                    detail=primary_item.product_plan_id,
                )
            )
        for alternative in alternatives:
            if (
                alternative.recommendation_criterion != expected_criterion
                or alternative.recommendation_reason_codes
                != ("exact_eligible_recommendation_alternative",)
            ):
                violations.append(
                    ValidationViolation(
                        code="recommendation_alternative_reason_invalid",
                        detail=alternative.product_plan_id,
                    )
                )
        for item in group:
            expected_source_id = item.recommendation_reason_codes[0]
            if not any(
                source_ref_id in source_by_id
                and source_by_id[source_ref_id].source_type.value == "policy_reason"
                and source_by_id[source_ref_id].field_name == "recommendation"
                and source_by_id[source_ref_id].source_id == expected_source_id
                for source_ref_id in item.source_ref_ids
            ):
                violations.append(
                    ValidationViolation(
                        code="recommendation_policy_source_missing",
                        detail=item.product_plan_id,
                    )
                )
    report = plan.next_step.candidate_fact_report
    if report is not None:
        for item in report.items:
            product = snapshot.product(item.sku)
            if (
                product is None
                or product.name != item.name
                or item.fact_name != report.fact_name
            ):
                violations.append(
                    ValidationViolation(
                        code="candidate_fact_report_identity_mismatch",
                        detail=item.item_id,
                    )
                )
                continue
            matching = tuple(
                fact for fact in product.facts if fact.name == item.fact_name
            )
            issues = tuple(
                issue
                for issue in product.fact_issues
                if issue.name == item.fact_name
            )
            if item.status == CandidateFactStatus.CONFIRMED and not any(
                fact.value == item.value and fact.unit == item.unit
                for fact in matching
            ):
                violations.append(
                    ValidationViolation(
                        code="candidate_fact_report_value_mismatch",
                        detail=item.item_id,
                    )
                )
            elif item.status == CandidateFactStatus.AMBIGUOUS and not (
                issues
                or len({(str(fact.value), fact.unit) for fact in matching}) > 1
            ):
                violations.append(
                    ValidationViolation(
                        code="candidate_fact_report_ambiguity_unproven",
                        detail=item.item_id,
                    )
                )
            elif item.status == CandidateFactStatus.MISSING and (matching or issues):
                violations.append(
                    ValidationViolation(
                        code="candidate_fact_report_missing_fact_mismatch",
                        detail=item.item_id,
                    )
                )
    return violations


def validate_rendered_answer(
    answer_plan: AnswerPlan,
    rendered_answer: RenderedAnswer,
    source_snapshot: AnswerSourceSnapshot,
) -> AnswerValidationResult:
    violations: list[ValidationViolation] = []
    if rendered_answer.plan_id != answer_plan.plan_id:
        violations.append(ValidationViolation(code="rendered_plan_id_mismatch"))
    allowed_ids = _allowed_ids(answer_plan)
    referenced = {
        source_id
        for segment in rendered_answer.segments
        for source_id in segment.source_ids
    }
    unknown = tuple(sorted(referenced - allowed_ids))
    for source_id in unknown:
        violations.append(
            ValidationViolation(code="unknown_render_source", detail=source_id)
        )
    missing = tuple(sorted(_required_ids(answer_plan) - referenced))
    for item_id in missing:
        violations.append(
            ValidationViolation(code="required_plan_item_missing", detail=item_id)
        )

    question_segments = [
        item for item in rendered_answer.segments if item.kind == RenderedSegmentKind.QUESTION
    ]
    next_segments = [
        item for item in rendered_answer.segments if item.kind == RenderedSegmentKind.NEXT_STEP
    ]
    if len(question_segments) > 1:
        violations.append(ValidationViolation(code="multiple_questions_rendered"))
    if answer_plan.question is None and question_segments:
        violations.append(ValidationViolation(code="unplanned_question_rendered"))
    if len(next_segments) != 1:
        violations.append(ValidationViolation(code="next_step_count_invalid"))

    if rendered_answer.text != "\n".join(
        item.text for item in rendered_answer.segments
    ):
        violations.append(ValidationViolation(code="rendered_text_segment_mismatch"))
    segment_ids = [item.segment_id for item in rendered_answer.segments]
    if len(segment_ids) != len(set(segment_ids)):
        violations.append(ValidationViolation(code="duplicate_rendered_segment_id"))

    # All content-bearing segments are deterministic. The response LLM may
    # insert only allow-listed neutral transitions; it never rewrites facts.
    from .renderer import ALLOWED_TRANSITION_TEXTS, deterministic_render

    expected_segments = deterministic_render(answer_plan).segments
    actual_content = tuple(
        item
        for item in rendered_answer.segments
        if item.kind != RenderedSegmentKind.TRANSITION
    )
    if len(actual_content) != len(expected_segments):
        violations.append(ValidationViolation(code="content_segment_count_mismatch"))
    else:
        for expected, actual in zip(expected_segments, actual_content, strict=True):
            if actual != expected:
                violations.append(
                    ValidationViolation(
                        code="protected_content_segment_changed",
                        segment_id=actual.segment_id,
                    )
                )
    transitions = [
        item
        for item in rendered_answer.segments
        if item.kind == RenderedSegmentKind.TRANSITION
    ]
    if len(transitions) > 8:
        violations.append(ValidationViolation(code="too_many_transition_segments"))
    for transition in transitions:
        if (
            transition.text not in ALLOWED_TRANSITION_TEXTS
            or transition.source_ids
            or transition.critical_literals
        ):
            violations.append(
                ValidationViolation(
                    code="invalid_transition_segment",
                    segment_id=transition.segment_id,
                )
            )

    allowed_literals = _literal_values(answer_plan)
    allowed_numbers = {
        spelling
        for literal in allowed_literals
        for number in _NUMBER_RE.findall(literal)
        for spelling in _numeric_spellings(number)
    }
    allowed_urls = {
        match.rstrip(".,;)")
        for literal in allowed_literals
        for match in _URL_RE.findall(literal)
    }
    allowed_mixed = {
        match.casefold()
        for literal in allowed_literals
        for match in _MIXED_ID_RE.findall(literal)
    }
    extra_literals: set[str] = set()
    for segment in rendered_answer.segments:
        for critical in segment.critical_literals:
            normalized_critical_numbers = _NUMBER_RE.findall(critical)
            critical_allowed = critical in allowed_literals or (
                len(normalized_critical_numbers) == 1
                and normalized_critical_numbers[0] == critical
                and bool(_numeric_spellings(critical) & allowed_numbers)
            )
            if not critical_allowed:
                extra_literals.add(critical)
                violations.append(
                    ValidationViolation(
                        code="unapproved_critical_literal",
                        segment_id=segment.segment_id,
                        detail=critical,
                    )
                )
            if critical not in segment.text:
                violations.append(
                    ValidationViolation(
                        code="protected_literal_missing_from_segment",
                        segment_id=segment.segment_id,
                        detail=critical,
                    )
                )
        for number in _NUMBER_RE.findall(segment.text):
            normalized = number.replace(",", ".")
            if normalized not in allowed_numbers:
                extra_literals.add(number)
        for url in _URL_RE.findall(segment.text):
            clean = url.rstrip(".,;)")
            if clean not in allowed_urls:
                extra_literals.add(clean)
        for token in _MIXED_ID_RE.findall(segment.text):
            if token.casefold() not in allowed_mixed:
                extra_literals.add(token)
    for literal in sorted(extra_literals):
        violations.append(
            ValidationViolation(code="extra_critical_literal", detail=literal)
        )

    delivered = any(
        item.kind == ClaimKind.COMMERCE_STATUS
        and item.allowed_in_response
        and str(item.value) == "delivered"
        and any(
            source.source_ref_id in item.source_ref_ids
            and source.source_type.value == "commerce_receipt"
            for source in answer_plan.sources
        )
        for item in answer_plan.claims
    )
    if _PROMISE_RE.search(rendered_answer.text) and not delivered:
        violations.append(
            ValidationViolation(code="unverified_commerce_promise")
        )
    violations.extend(_claim_source_violation(answer_plan, source_snapshot))
    violations.extend(_plan_integrity_violations(answer_plan, source_snapshot))

    unique = tuple(
        {
            (item.code, item.segment_id, item.detail): item for item in violations
        }.values()
    )
    return AnswerValidationResult(
        status="accepted" if not unique else "rejected",
        plan_id=answer_plan.plan_id,
        accepted_segment_ids=(
            tuple(item.segment_id for item in rendered_answer.segments)
            if not unique
            else ()
        ),
        violations=unique,
        unknown_reference_ids=unknown,
        extra_critical_literals=tuple(sorted(extra_literals)),
        missing_required_item_ids=missing,
        reason_codes=(
            ("grounded_answer_validated",)
            if not unique
            else ("grounded_answer_rejected",)
        ),
    )
