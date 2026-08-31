"""Narrow source-preserving adapters for Stage 5 answer facts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from app.catalog_v2.contracts import CatalogFact, CatalogProductSnapshot, FactProvenance
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


def _commercial_price_facts(product: Product) -> tuple[CatalogFact, ...]:
    """Expose only an explicit price basis from a feed description.

    The raw description remains outside the V2 source snapshot.  A typed fact
    is emitted only where the feed itself says that its catalogue price is per
    linear metre; this is the sole safe basis for a metre × price calculation.
    """

    description = str(product.description or "")
    normalized = " ".join(description.casefold().replace("ё", "е").split())
    per_metre = bool(
        re.search(r"цен\w*\s+(?:одного\s+)?(?:погонн\w*\s+)?метр\w*", normalized)
        or "приведена цена погонного метра" in normalized
    )
    if not per_metre:
        return ()
    return (
        CatalogFact(
            name="price_unit",
            value="m",
            provenance=FactProvenance(
                source="description",
                source_field="description",
                raw_value="цена погонного метра",
                parser="commercial_price_basis_v1",
            ),
        ),
    )


def build_verified_business_capability_facts(
    business_facts: object | None,
) -> tuple[VerifiedCapabilityFact, ...]:
    """Expose only stable, explicitly configured public verification channels."""

    if business_facts is None:
        return ()
    site_url = str(getattr(business_facts, "site_url", "") or "").strip()
    if not site_url.startswith(("https://", "http://")):
        return ()
    revision = str(
        getattr(business_facts, "facts_verified_on", "")
        or "business_config_unversioned"
    )
    return (
        VerifiedCapabilityFact(
            fact_id="business_site_url",
            name="site_url",
            value=site_url,
            source="data/business_config.json",
            source_revision=revision,
            confirmed=True,
        ),
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
        facts = (*identity.facts, *_commercial_price_facts(product))
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
                image_url=product.image_url or None,
                updated_at=product.updated_at,
                document_scope=tuple(
                    dict.fromkeys(document.filename for document in product.documents)
                ),
                facts=facts,
                fact_issues=identity.fact_issues,
                flow_head_points=identity.flow_head_points,
            )
        )
        revision_material.append(
            "\x1f".join(
                (
                    product.sku,
                    product.name,
                    product.updated_at,
                    str(product.price),
                    str(product.currency),
                    product.stock_status,
                    str(product.stock_qty),
                    str(product.url),
                    str(product.image_url),
                    identity.product_kind.value,
                    identity.role.value,
                    *(
                        "\x1d".join(
                            (
                                fact.name,
                                str(fact.value),
                                str(fact.unit),
                                fact.provenance.source,
                                fact.provenance.source_field,
                                fact.provenance.raw_value,
                                fact.provenance.parser,
                                str(fact.provenance.source_document),
                                str(fact.provenance.source_section),
                            )
                        )
                        for fact in facts
                    ),
                    *(
                        "\x1d".join(
                            (
                                issue.name,
                                issue.provenance.source,
                                issue.provenance.source_field,
                                issue.provenance.raw_value,
                                issue.provenance.parser,
                                str(issue.provenance.source_document),
                                str(issue.provenance.source_section),
                            )
                        )
                        for issue in identity.fact_issues
                    ),
                    *(
                        "\x1d".join(
                            (
                                str(point.flow_l_h),
                                str(point.head_m),
                                point.provenance.source,
                                point.provenance.source_field,
                                point.provenance.raw_value,
                                point.provenance.parser,
                                str(point.provenance.source_document),
                                str(point.provenance.source_section),
                            )
                        )
                        for point in identity.flow_head_points
                    ),
                    *(
                        "\x1d".join(
                            (
                                document.filename,
                                document.document_kind,
                                document.binding_scope,
                                str(document.binding_value),
                                hashlib.sha256(
                                    document.text.encode("utf-8")
                                ).hexdigest(),
                            )
                        )
                        for document in product.documents
                    ),
                )
            )
        )
    verified_capabilities = tuple(capability_facts)
    revision_material.extend(
        "\x1f".join(
            (
                item.fact_id,
                item.name,
                str(item.value),
                item.source,
                item.source_revision,
                str(item.confirmed),
            )
        )
        for item in verified_capabilities
    )
    digest = hashlib.sha256(
        "\x1e".join(revision_material).encode("utf-8")
    ).hexdigest()
    return AnswerSourceSnapshot(
        source_revision=digest,
        products=tuple(records),
        capability_facts=verified_capabilities,
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
            required_hard_facts=tuple(
                constraint.name for constraint in search.hard_constraints
            ),
            matched_hard_facts=candidate.matched_hard_facts,
            mismatched_hard_facts=candidate.mismatched_hard_facts,
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
