"""Declarative, fail-closed migration registry loading."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from app.answer_v2.contracts import AnswerPlanStatus
from app.catalog_v2.contracts import ProductKind
from app.dialogue_v2.contracts import NextActionKind, TaskAct

from .contracts import (
    MigrationCell,
    MigrationReadinessRow,
    MigrationRegistry,
    RolloutStage,
)


_SUPPORTED_KINDS = tuple(
    kind for kind in ProductKind if kind != ProductKind.UNSUPPORTED
)


def default_registry() -> MigrationRegistry:
    """Ship only a shadow cell; live delivery needs reviewed external evidence."""

    return MigrationRegistry(
        registry_id="stage6a_builtin_shadow",
        revision="stage6a-builtin-shadow-v1",
        cells=(
            MigrationCell(
                cell_id="exact_catalog_facts_shadow",
                task_acts=(
                    TaskAct.CHECK_PRICE,
                    TaskAct.CHECK_STOCK,
                    TaskAct.GET_LINK,
                ),
                product_kinds=_SUPPORTED_KINDS,
                allowed_answer_statuses=(
                    AnswerPlanStatus.READY,
                    AnswerPlanStatus.PARTIAL,
                ),
                allowed_next_actions=(NextActionKind.ANSWER_DIRECT_QUESTION,),
                stage=RolloutStage.SHADOW,
                canary_percent=0,
                external_actions_allowed=False,
                existing_sessions_allowed=False,
                require_single_exact_product=True,
                reason_codes=("builtin_registry_is_shadow_only",),
            ),
        ),
    )


class RegistryLoadResult(MigrationRegistry):
    valid: bool = True
    source: str = "builtin"
    error: str | None = None

    def registry(self) -> MigrationRegistry:
        return MigrationRegistry(
            registry_id=self.registry_id,
            revision=self.revision,
            cells=self.cells,
        )


def _invalid_registry(source: str, error: str) -> RegistryLoadResult:
    return RegistryLoadResult(
        registry_id="invalid_registry_fail_closed",
        revision="invalid",
        cells=(),
        valid=False,
        source=source,
        error=error[:500],
    )


def load_registry(path: Path | None) -> RegistryLoadResult:
    if path is None:
        registry = default_registry()
        return RegistryLoadResult(
            **registry.model_dump(),
            valid=True,
            source="builtin",
        )
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        registry = MigrationRegistry.model_validate(payload)
        actual_revision = hashlib.sha256(raw).hexdigest()
        registry = registry.model_copy(update={"revision": actual_revision})
        return RegistryLoadResult(
            **registry.model_dump(),
            valid=True,
            source=str(path),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        return _invalid_registry(str(path), f"{type(exc).__name__}: {exc}")


def build_migration_readiness_matrix(
    registry: MigrationRegistry,
    *,
    catalog_revision: str | None,
) -> tuple[MigrationReadinessRow, ...]:
    """Expand declarative cells into a machine-readable release matrix."""

    rows: list[MigrationReadinessRow] = []
    for cell in registry.cells:
        blocked: list[str] = []
        if cell.stage != RolloutStage.INTERNAL_CANARY:
            blocked.append(f"rollout_stage_{cell.stage.value}")
        if not cell.gate_artifact_ref:
            blocked.append("gate_artifact_missing")
        if (
            cell.required_catalog_revision is not None
            and cell.required_catalog_revision != catalog_revision
        ):
            blocked.append("catalog_revision_mismatch")
        for task_act in cell.task_acts:
            for product_kind in cell.product_kinds:
                for contract_version in cell.product_contract_versions:
                    for answer_status in cell.allowed_answer_statuses:
                        rows.append(
                            MigrationReadinessRow(
                                cell_id=cell.cell_id,
                                task_act=task_act,
                                product_kind=product_kind,
                                product_contract_version=contract_version,
                                catalog_revision=catalog_revision,
                                answer_status=answer_status,
                                rollout_stage=cell.stage,
                                canary_eligible=not blocked,
                                blocked_reason_codes=tuple(blocked),
                            )
                        )
    return tuple(rows)
