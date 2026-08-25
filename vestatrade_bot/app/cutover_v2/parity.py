"""Typed legacy/V2 parity checks that do not compare prose literally."""

from __future__ import annotations

from app.catalog_v2.contracts import ProductKind
from app.dialogue_v2.contracts import ConstraintStatus, ConstraintStrength
from app.models import ChatResponse, SessionState

from .contracts import ParityAssessment, ParityDifference, V2TurnCandidate


_SEVERITY_ORDER = {"none": 0, "p2": 1, "p1": 2, "p0": 3}


def assess_response_parity(
    legacy: ChatResponse | None,
    candidate: V2TurnCandidate | None,
    legacy_state: SessionState | None = None,
    legacy_product_kinds: dict[str, ProductKind] | None = None,
) -> ParityAssessment:
    if legacy is None or candidate is None or candidate.response is None:
        return ParityAssessment(
            status="unavailable",
            severity="p1",
            gate_blocking_reason_codes=("comparable_response_missing",),
        )

    v2 = candidate.response
    differences: list[ParityDifference] = []
    legacy_skus = tuple(item.sku for item in legacy.products)
    v2_skus = tuple(item.sku for item in v2.products)
    if legacy_skus != v2_skus:
        differences.append(
            ParityDifference(
                dimension="product_skus",
                severity="p1",
                legacy_value=legacy_skus,
                v2_value=v2_skus,
                reason_code="legacy_v2_product_set_differs",
            )
        )
    if len(v2_skus) != len(set(v2_skus)):
        differences.append(
            ParityDifference(
                dimension="product_identity",
                severity="p0",
                v2_value=v2_skus,
                reason_code="v2_duplicate_public_sku",
            )
        )
    if len(candidate.product_statuses) != len(v2.products):
        differences.append(
            ParityDifference(
                dimension="product_presentation_status",
                severity="p0",
                legacy_value=len(legacy.products),
                v2_value=len(candidate.product_statuses),
                reason_code="v2_product_status_cardinality_mismatch",
            )
        )
    if candidate.response_product_kinds and (
        len(candidate.response_product_kinds) != len(v2.products)
    ):
        differences.append(
            ParityDifference(
                dimension="product_kind",
                severity="p0",
                legacy_value=len(legacy.products),
                v2_value=len(candidate.response_product_kinds),
                reason_code="v2_product_kind_cardinality_mismatch",
            )
        )
    for sku, v2_kind in zip(
        v2_skus,
        candidate.response_product_kinds,
        strict=False,
    ):
        legacy_kind = (legacy_product_kinds or {}).get(sku)
        if legacy_kind is not None and legacy_kind != v2_kind:
            differences.append(
                ParityDifference(
                    dimension="product_kind",
                    severity="p0",
                    legacy_value=legacy_kind.value,
                    v2_value=v2_kind.value,
                    reason_code="legacy_v2_product_kind_differs",
                )
            )
    legacy_by_sku = {item.sku: item for item in legacy.products}
    for product in v2.products:
        legacy_product = legacy_by_sku.get(product.sku)
        if legacy_product is None:
            continue
        for field_name in ("price", "currency", "stock_status", "url"):
            legacy_value = getattr(legacy_product, field_name)
            v2_value = getattr(product, field_name)
            if legacy_value != v2_value:
                differences.append(
                    ParityDifference(
                        dimension=f"catalog_fact:{field_name}",
                        severity="p0",
                        legacy_value=legacy_value,
                        v2_value=v2_value,
                        reason_code=f"legacy_v2_{field_name}_differs_for_same_sku",
                    )
                )
    if legacy.need_handoff != v2.need_handoff:
        differences.append(
            ParityDifference(
                dimension="handoff",
                severity="p1",
                legacy_value=legacy.need_handoff,
                v2_value=v2.need_handoff,
                reason_code="legacy_v2_handoff_differs",
            )
        )
    if candidate.validation_status != "accepted":
        differences.append(
            ParityDifference(
                dimension="grounding",
                severity="p0",
                legacy_value="legacy_not_independently_grounded",
                v2_value=candidate.validation_status,
                reason_code="v2_grounding_not_accepted",
            )
        )
    if not v2.answer.strip():
        differences.append(
            ParityDifference(
                dimension="answer_presence",
                severity="p0",
                legacy_value=bool(legacy.answer.strip()),
                v2_value=False,
                reason_code="v2_empty_answer",
            )
        )

    if legacy_state is not None:
        for fact in candidate.state_after.constraints:
            if not fact.active or fact.strength != ConstraintStrength.HARD:
                continue
            if fact.name not in legacy_state.slots:
                continue
            legacy_value = legacy_state.slots.get(fact.name)
            if fact.status == ConstraintStatus.KNOWN and legacy_value != fact.value:
                differences.append(
                    ParityDifference(
                        dimension=f"hard_constraint:{fact.name}",
                        severity="p0",
                        legacy_value=str(legacy_value),
                        v2_value=str(fact.value),
                        reason_code="legacy_v2_hard_constraint_differs",
                    )
                )
            elif (
                fact.status != ConstraintStatus.KNOWN
                and legacy_value is not None
                and legacy_value != ""
            ):
                differences.append(
                    ParityDifference(
                        dimension=f"constraint_status:{fact.name}",
                        severity="p0",
                        legacy_value=str(legacy_value),
                        v2_value=fact.status.value,
                        reason_code="legacy_invented_value_for_non_known_fact",
                    )
                )

    if not differences:
        return ParityAssessment(
            status="parity",
            severity="none",
            compared_dimensions=(
                "product_skus",
                "catalog_facts",
                "product_kind",
                "product_presentation_status",
                "handoff",
                "grounding",
                "answer_presence",
                "hard_constraints",
                "constraint_statuses",
            ),
        )
    severity = max(
        (item.severity for item in differences),
        key=lambda item: _SEVERITY_ORDER[item],
    )
    return ParityAssessment(
        status="regression" if severity in {"p0", "p1"} else "acceptable_difference",
        severity=severity,
        compared_dimensions=(
            "product_skus",
            "catalog_facts",
            "product_kind",
            "product_presentation_status",
            "handoff",
            "grounding",
            "answer_presence",
            "hard_constraints",
            "constraint_statuses",
        ),
        differences=tuple(differences),
        gate_blocking_reason_codes=tuple(
            item.reason_code for item in differences if item.severity in {"p0", "p1"}
        ),
    )
