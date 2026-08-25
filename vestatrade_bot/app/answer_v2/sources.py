"""Narrow source-preserving adapters for Stage 5 answer facts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.catalog_v2.contracts import CatalogProductSnapshot
from app.catalog_v2.contracts import CatalogPlanningResult
from app.commerce_v2.contracts import CommercePlanningResult
from app.dialogue_v2.contracts import DialogueStateV2
from app.models import Product

from .contracts import (
    AnswerSourceSnapshot,
    CatalogCandidateEvidence,
    CatalogAnswerProduct,
    CommerceWorkflowEvidence,
    ConstraintAnswerEvidence,
    ProductGoalEvidence,
    SolutionPlanEvidence,
    VerifiedCapabilityFact,
)


def build_answer_source_snapshot(
    products: Iterable[Product],
    catalog_snapshot: Iterable[CatalogProductSnapshot],
    *,
    capability_facts: Iterable[VerifiedCapabilityFact] = (),
) -> AnswerSourceSnapshot:
    """Expose only structured feed fields; descriptions and raw feed stay out."""

    source_products = tuple(products)
    classified = {item.sku: item for item in catalog_snapshot}
    records: list[CatalogAnswerProduct] = []
    revision_material: list[str] = []
    for product in source_products:
        identity = classified.get(product.sku)
        if identity is None:
            continue
        records.append(
            CatalogAnswerProduct(
                sku=product.sku,
                name=product.name,
                product_kind=identity.product_kind,
                role=identity.role,
                price=product.price,
                currency=product.currency if product.price is not None else None,
                stock_status=product.stock_status or None,
                stock_qty=product.stock_qty,
                url=product.url or None,
                updated_at=product.updated_at,
                facts=identity.facts,
            )
        )
        revision_material.append(
            "\x1f".join(
                (
                    product.sku,
                    product.updated_at,
                    str(product.price),
                    product.stock_status,
                    str(product.stock_qty),
                    str(product.url),
                )
            )
        )
    digest = hashlib.sha256(
        "\x1e".join(revision_material).encode("utf-8")
    ).hexdigest()
    return AnswerSourceSnapshot(
        source_revision=digest,
        products=tuple(records),
        capability_facts=tuple(capability_facts),
    )


def attach_turn_source_evidence(
    source_snapshot: AnswerSourceSnapshot,
    catalog_planning: CatalogPlanningResult | None,
    commerce_planning: CommercePlanningResult | None,
    dialogue_state: DialogueStateV2 | None = None,
) -> AnswerSourceSnapshot:
    """Attach only typed per-turn artifacts needed for independent validation."""

    candidates = tuple(
        CatalogCandidateEvidence(
            search_plan_id=search.plan_id,
            task_id=search.task_id,
            goal_id=search.goal_id,
            sku=candidate.sku,
            product_kind=candidate.product_kind,
            role=candidate.role,
            status=candidate.status,
            matched_hard_facts=candidate.matched_hard_facts,
            missing_hard_facts=candidate.missing_hard_facts,
            matched_soft_facts=candidate.matched_soft_facts,
            mismatched_soft_facts=candidate.mismatched_soft_facts,
            relaxations=candidate.relaxations,
        )
        for search in (catalog_planning.search_plans if catalog_planning else ())
        for candidate in search.candidate_assessments
    )
    solutions = (
        (
            SolutionPlanEvidence(
                solution_id=catalog_planning.solution_plan.solution_id,
                task_ids=catalog_planning.solution_plan.task_ids,
                unresolved_dependencies=(
                    catalog_planning.solution_plan.unresolved_dependencies
                ),
            ),
        )
        if catalog_planning is not None and catalog_planning.solution_plan is not None
        else ()
    )
    workflows = tuple(
        CommerceWorkflowEvidence(
            workflow_id=workflow.workflow_id,
            task_ids=workflow.task_ids,
            execution_status=workflow.execution_status.value,
            receipt_ref=workflow.external_receipt_ref,
            updated_turn=workflow.updated_turn,
        )
        for workflow in (commerce_planning.workflows if commerce_planning else ())
    )
    constraints = tuple(
        ConstraintAnswerEvidence(
            fact_id=fact.fact_id,
            name=fact.name,
            value=fact.value,
            unit=fact.unit,
            status=fact.status.value,
            task_id=fact.task_id,
            goal_id=fact.goal_id,
            source_turn=fact.source_turn,
        )
        for fact in (dialogue_state.constraints if dialogue_state else ())
        if fact.active
    )
    goals = tuple(
        ProductGoalEvidence(
            goal_id=goal.goal_id,
            canonical_type=goal.canonical_type,
            category=goal.category.value,
            role=goal.role.value,
            confirmed_turn=goal.confirmed_turn,
        )
        for goal in (dialogue_state.product_goals if dialogue_state else ())
    )
    return source_snapshot.model_copy(
        update=dict(
            catalog_candidates=candidates,
            solution_plans=solutions,
            commerce_workflows=workflows,
            constraints=constraints,
            product_goals=goals,
        )
    )
