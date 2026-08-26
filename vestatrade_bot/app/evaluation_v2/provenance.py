"""Pure provenance contracts for evaluating saved live dialogue runs.

The live harness records the exact testset, the canonical catalogue snapshot,
the executed scenarios and the models used by the bot.  Outcome evaluation may
only treat a saved transcript as comparable evidence when all of those fields
are present and match the evaluator's inputs.  Product count is retained for
diagnostics but is deliberately never used as catalogue identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


PROVENANCE_SCHEMA_VERSION = "1.0"
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_DYNAMIC_MODEL_NAMES = frozenset({"auto", "default", "free", "latest"})


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class SourceRunProvenance(_FrozenModel):
    """Validated identity of one saved, full or partial, live harness run."""

    schema_version: Literal["1.0"] = PROVENANCE_SCHEMA_VERSION
    source_manifest_schema_version: Literal["1"] = "1"
    testset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transcripts_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    catalog_product_count: int | None = Field(default=None, ge=0)
    catalog_source: str | None = Field(default=None, max_length=120)
    scenario_ids: tuple[str, ...]
    run_mode: Literal["live"] = "live"
    llm_provider: str = Field(min_length=1, max_length=80)
    bot_model: str = Field(min_length=3, max_length=200)
    bot_strong_model: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def validate_identity(self) -> "SourceRunProvenance":
        if not self.scenario_ids:
            raise ValueError("source run must contain at least one scenario")
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise ValueError("source run scenario ids must be unique")
        if any(not item.strip() for item in self.scenario_ids):
            raise ValueError("source run scenario ids must be non-empty")
        if not is_pinned_model_identifier(self.bot_model):
            raise ValueError("bot model must be a pinned model identifier")
        if not is_pinned_model_identifier(self.bot_strong_model):
            raise ValueError("strong bot model must be a pinned model identifier")
        return self

    @property
    def bot_models_for_independence(self) -> tuple[str, ...]:
        """Every distinct bot model family an independent judge must avoid."""

        return tuple(dict.fromkeys((self.bot_model, self.bot_strong_model)))


class SourceRunProvenanceValidation(_FrozenModel):
    """Fail-closed validation result with stable machine-readable diagnostics."""

    schema_version: Literal["1.0"] = PROVENANCE_SCHEMA_VERSION
    accepted: bool
    provenance: SourceRunProvenance | None = None
    reason_codes: tuple[str, ...] = ()
    requested_scenario_ids: tuple[str, ...] = ()
    missing_requested_scenario_ids: tuple[str, ...] = ()
    unexpected_requested_scenario_ids: tuple[str, ...] = ()
    missing_full_suite_scenario_ids: tuple[str, ...] = ()
    unexpected_full_suite_scenario_ids: tuple[str, ...] = ()
    exact_scenario_set_required: bool = False
    full_suite_required: bool = False
    full_suite_covered: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> "SourceRunProvenanceValidation":
        if self.accepted:
            if self.provenance is None or self.reason_codes:
                raise ValueError("accepted provenance requires a clean typed record")
        elif not self.reason_codes:
            raise ValueError("rejected provenance requires an explicit reason code")
        if self.provenance is not None and not self.accepted:
            raise ValueError("rejected provenance must not expose a consumable record")
        return self


def _stable_json_bytes(value: Any) -> bytes:
    """Match the Stage 6 harness' canonical UTF-8 JSON representation exactly."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def canonical_catalog_sha256(products: Iterable[Any]) -> str:
    """Return the order-independent catalogue fingerprint used by Stage 6.

    ``updated_at`` is object load time rather than a card business fact, so it
    is removed before hashing.  This implementation intentionally mirrors the
    harness algorithm rather than introducing a second serialization format.
    """

    canonical: list[dict[str, Any]] = []
    for product in products:
        if hasattr(product, "model_dump"):
            payload = product.model_dump(mode="json")
        elif hasattr(product, "dict"):
            payload = product.dict()
        elif isinstance(product, dict):
            payload = dict(product)
        else:
            payload = {"value": str(product)}
        payload.pop("updated_at", None)
        canonical.append(payload)
    canonical.sort(
        key=lambda item: (
            str(item.get("sku") or ""),
            str(item.get("name") or ""),
            _stable_json_bytes(item),
        )
    )
    return hashlib.sha256(_stable_json_bytes(canonical)).hexdigest()


def is_pinned_model_identifier(model: str) -> bool:
    """Reject routing aliases; retain only explicit namespaced model ids."""

    normalized = str(model or "").strip().casefold()
    if not normalized or any(char.isspace() for char in normalized):
        return False
    if normalized.count("/") != 1 or "*" in normalized:
        return False
    namespace, name_with_variant = normalized.split("/", 1)
    if not namespace or not name_with_variant:
        return False
    name = name_with_variant.split(":", 1)[0]
    if not name:
        return False
    name_tokens = {item for item in re.split(r"[-_.]", name) if item}
    if name in _DYNAMIC_MODEL_NAMES or name_tokens & _DYNAMIC_MODEL_NAMES:
        return False
    # OpenRouter's own namespace denotes a router/alias rather than a pinned
    # foundation-model owner.  In particular, ``openrouter/auto`` must never
    # satisfy independent-judge provenance.
    if namespace == "openrouter":
        return False
    return True


def _normalized_expected_ids(
    values: Iterable[str],
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    normalized = tuple(str(item).strip() for item in values)
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if any(not item for item in normalized):
        raise ValueError(f"{field_name} must contain non-empty ids")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must contain unique ids")
    return normalized


def _invalid_result(
    reasons: list[str],
    *,
    requested: tuple[str, ...],
    missing_requested: tuple[str, ...] = (),
    unexpected_requested: tuple[str, ...] = (),
    missing_full: tuple[str, ...] = (),
    unexpected_full: tuple[str, ...] = (),
    require_full_suite: bool,
    require_exact_scenario_set: bool = False,
    full_suite_covered: bool = False,
) -> SourceRunProvenanceValidation:
    return SourceRunProvenanceValidation(
        accepted=False,
        reason_codes=tuple(dict.fromkeys(reasons)),
        requested_scenario_ids=requested,
        missing_requested_scenario_ids=missing_requested,
        unexpected_requested_scenario_ids=unexpected_requested,
        missing_full_suite_scenario_ids=missing_full,
        unexpected_full_suite_scenario_ids=unexpected_full,
        exact_scenario_set_required=require_exact_scenario_set,
        full_suite_required=require_full_suite,
        full_suite_covered=full_suite_covered,
    )


def validate_source_run_provenance(
    manifest: Mapping[str, Any] | Any,
    *,
    expected_testset_sha256: str,
    expected_catalog_sha256: str,
    expected_transcripts_sha256: str | None = None,
    requested_scenario_ids: Iterable[str],
    full_suite_scenario_ids: Iterable[str] = (),
    require_full_suite: bool = False,
    require_exact_scenario_set: bool = False,
    expected_bot_model: str | None = None,
    expected_bot_strong_model: str | None = None,
) -> SourceRunProvenanceValidation:
    """Parse and validate a Stage 6 source manifest without performing I/O.

    A subset of a valid source run may be evaluated: every requested scenario
    must be present.  A caller that passes the scenario ids parsed from the
    transcript artifact itself should also set ``require_exact_scenario_set``;
    this binds the manifest's scenario claim in both directions.  When
    ``require_full_suite`` is true, the manifest's set of scenarios must exactly
    match ``full_suite_scenario_ids`` even if the caller evaluates only a subset
    in this invocation.
    """

    if not _SHA256_RE.fullmatch(expected_testset_sha256):
        raise ValueError("expected_testset_sha256 must be a lowercase sha256")
    if not _SHA256_RE.fullmatch(expected_catalog_sha256):
        raise ValueError("expected_catalog_sha256 must be a lowercase sha256")
    if expected_transcripts_sha256 is not None and not _SHA256_RE.fullmatch(
        expected_transcripts_sha256
    ):
        raise ValueError("expected_transcripts_sha256 must be a lowercase sha256")
    requested = _normalized_expected_ids(
        requested_scenario_ids,
        field_name="requested_scenario_ids",
        allow_empty=False,
    )
    full_suite = _normalized_expected_ids(
        full_suite_scenario_ids,
        field_name="full_suite_scenario_ids",
        allow_empty=not require_full_suite,
    )

    reasons: list[str] = []
    if not isinstance(manifest, Mapping):
        return _invalid_result(
            ["source_manifest_not_object"],
            requested=requested,
            require_full_suite=require_full_suite,
            require_exact_scenario_set=require_exact_scenario_set,
        )

    raw_schema_version = manifest.get("schema_version")
    source_schema_version = str(raw_schema_version or "").strip()
    if not source_schema_version:
        reasons.append("source_manifest_schema_version_missing")
    elif source_schema_version != "1":
        reasons.append("source_manifest_schema_version_unsupported")

    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        reasons.append("source_manifest_inputs_missing")
        inputs = {}
    llm = manifest.get("llm")
    if not isinstance(llm, Mapping):
        reasons.append("source_manifest_llm_missing")
        llm = {}
    run = manifest.get("run")
    if not isinstance(run, Mapping):
        reasons.append("source_manifest_run_missing")
        run = {}
    artifacts = manifest.get("artifacts")
    if expected_transcripts_sha256 is not None and not isinstance(
        artifacts,
        Mapping,
    ):
        reasons.append("source_manifest_artifacts_missing")
        artifacts = {}
    elif not isinstance(artifacts, Mapping):
        artifacts = {}

    observed_testset = inputs.get("testset_sha256")
    if not isinstance(observed_testset, str) or not observed_testset.strip():
        reasons.append("source_manifest_testset_sha256_missing")
        observed_testset = ""
    elif not _SHA256_RE.fullmatch(observed_testset.strip()):
        reasons.append("source_manifest_testset_sha256_invalid")
        observed_testset = ""
    else:
        observed_testset = observed_testset.strip()
        if observed_testset != expected_testset_sha256:
            reasons.append("source_manifest_testset_sha256_mismatch")

    observed_catalog = inputs.get("catalog_sha256")
    if not isinstance(observed_catalog, str) or not observed_catalog.strip():
        reasons.append("source_manifest_catalog_sha256_missing")
        observed_catalog = ""
    elif not _SHA256_RE.fullmatch(observed_catalog.strip()):
        reasons.append("source_manifest_catalog_sha256_invalid")
        observed_catalog = ""
    else:
        observed_catalog = observed_catalog.strip()
        if observed_catalog != expected_catalog_sha256:
            reasons.append("source_manifest_catalog_sha256_mismatch")

    observed_transcripts: str | None = None
    if expected_transcripts_sha256 is not None:
        raw_transcripts = artifacts.get("transcripts_sha256")
        if not isinstance(raw_transcripts, str) or not raw_transcripts.strip():
            reasons.append("source_manifest_transcripts_sha256_missing")
        elif not _SHA256_RE.fullmatch(raw_transcripts.strip()):
            reasons.append("source_manifest_transcripts_sha256_invalid")
        else:
            observed_transcripts = raw_transcripts.strip()
            if observed_transcripts != expected_transcripts_sha256:
                reasons.append("source_manifest_transcripts_sha256_mismatch")

    raw_scenario_ids = inputs.get("scenario_ids")
    observed_scenarios: tuple[str, ...] = ()
    if not isinstance(raw_scenario_ids, (list, tuple)):
        reasons.append("source_manifest_scenario_ids_missing")
    elif not raw_scenario_ids:
        reasons.append("source_manifest_scenario_ids_empty")
    elif any(not isinstance(item, str) or not item.strip() for item in raw_scenario_ids):
        reasons.append("source_manifest_scenario_ids_invalid")
    else:
        observed_scenarios = tuple(item.strip() for item in raw_scenario_ids)
        if len(observed_scenarios) != len(set(observed_scenarios)):
            reasons.append("source_manifest_scenario_ids_duplicate")

    observed_set = set(observed_scenarios)
    missing_requested = tuple(sorted(set(requested) - observed_set))
    unexpected_requested = tuple(sorted(observed_set - set(requested)))
    if missing_requested:
        reasons.append("source_manifest_requested_scenario_missing")
    if require_exact_scenario_set and (missing_requested or unexpected_requested):
        reasons.append("source_manifest_transcript_scenario_set_mismatch")

    full_set = set(full_suite)
    missing_full = tuple(sorted(full_set - observed_set))
    unexpected_full = tuple(sorted(observed_set - full_set)) if full_suite else ()
    full_suite_covered = bool(full_suite) and not missing_full and not unexpected_full
    if require_full_suite and not full_suite_covered:
        reasons.append("source_manifest_full_suite_coverage_mismatch")

    run_mode = run.get("mode")
    if not isinstance(run_mode, str) or not run_mode.strip():
        reasons.append("source_manifest_run_mode_missing")
        run_mode = ""
    else:
        run_mode = run_mode.strip().casefold()
        if run_mode != "live":
            reasons.append("source_manifest_run_mode_not_live")

    llm_provider = llm.get("provider")
    if not isinstance(llm_provider, str) or not llm_provider.strip():
        reasons.append("source_manifest_llm_provider_missing")
        llm_provider = ""
    else:
        llm_provider = llm_provider.strip().casefold()
        if len(llm_provider) > 80:
            reasons.append("source_manifest_llm_provider_invalid")

    bot_model = llm.get("model")
    if not isinstance(bot_model, str) or not bot_model.strip():
        reasons.append("source_manifest_bot_model_missing")
        bot_model = ""
    else:
        bot_model = bot_model.strip()
        if len(bot_model) > 200:
            reasons.append("source_manifest_bot_model_invalid")
        elif not is_pinned_model_identifier(bot_model):
            reasons.append("source_manifest_bot_model_not_pinned")
        if expected_bot_model is not None and bot_model != expected_bot_model.strip():
            reasons.append("source_manifest_bot_model_mismatch")

    strong_model = llm.get("strong_model")
    if not isinstance(strong_model, str) or not strong_model.strip():
        reasons.append("source_manifest_bot_strong_model_missing")
        strong_model = ""
    else:
        strong_model = strong_model.strip()
        if len(strong_model) > 200:
            reasons.append("source_manifest_bot_strong_model_invalid")
        elif not is_pinned_model_identifier(strong_model):
            reasons.append("source_manifest_bot_strong_model_not_pinned")
        if (
            expected_bot_strong_model is not None
            and strong_model != expected_bot_strong_model.strip()
        ):
            reasons.append("source_manifest_bot_strong_model_mismatch")

    catalog_count = inputs.get("catalog_products")
    if catalog_count is not None and (
        isinstance(catalog_count, bool)
        or not isinstance(catalog_count, int)
        or catalog_count < 0
    ):
        reasons.append("source_manifest_catalog_product_count_invalid")
        catalog_count = None
    catalog_source = inputs.get("catalog_source")
    if catalog_source is not None and not isinstance(catalog_source, str):
        reasons.append("source_manifest_catalog_source_invalid")
        catalog_source = None
    elif isinstance(catalog_source, str) and len(catalog_source.strip()) > 120:
        reasons.append("source_manifest_catalog_source_invalid")
        catalog_source = None

    if reasons:
        return _invalid_result(
            reasons,
            requested=requested,
            missing_requested=missing_requested,
            unexpected_requested=unexpected_requested,
            missing_full=missing_full,
            unexpected_full=unexpected_full,
            require_full_suite=require_full_suite,
            require_exact_scenario_set=require_exact_scenario_set,
            full_suite_covered=full_suite_covered,
        )

    try:
        provenance = SourceRunProvenance(
            source_manifest_schema_version=source_schema_version,
            testset_sha256=observed_testset,
            catalog_sha256=observed_catalog,
            transcripts_sha256=observed_transcripts,
            catalog_product_count=catalog_count,
            catalog_source=(catalog_source.strip() if catalog_source else None),
            scenario_ids=observed_scenarios,
            run_mode="live",
            llm_provider=llm_provider,
            bot_model=bot_model,
            bot_strong_model=strong_model,
        )
    except ValidationError:
        return _invalid_result(
            ["source_manifest_typed_provenance_invalid"],
            requested=requested,
            missing_requested=missing_requested,
            unexpected_requested=unexpected_requested,
            missing_full=missing_full,
            unexpected_full=unexpected_full,
            require_full_suite=require_full_suite,
            require_exact_scenario_set=require_exact_scenario_set,
            full_suite_covered=full_suite_covered,
        )
    return SourceRunProvenanceValidation(
        accepted=True,
        provenance=provenance,
        requested_scenario_ids=requested,
        exact_scenario_set_required=require_exact_scenario_set,
        full_suite_required=require_full_suite,
        full_suite_covered=full_suite_covered,
    )


__all__ = [
    "PROVENANCE_SCHEMA_VERSION",
    "SourceRunProvenance",
    "SourceRunProvenanceValidation",
    "canonical_catalog_sha256",
    "is_pinned_model_identifier",
    "validate_source_run_provenance",
]
