"""Public API for Stage 7 goal-based evaluation."""

from .adapters import (
    TranscriptAdapterError,
    adapt_catalog_products,
    adapt_dialogue_record,
    load_dialogue_transcripts_jsonl,
    load_dialogue_transcripts_jsonl_bytes,
)
from .compare import compare_outcome_evaluations
from .compiler import (
    canonical_payload_sha256,
    canonical_testset_sha256,
    compile_outcome_contract,
    compile_outcome_contracts,
    validate_contract_provenance,
)
from .contracts import *  # noqa: F403
from .deterministic import (
    evaluate_machine_assessment,
    evaluate_machine_violations,
    finalize_outcome_evaluation,
)
from .evidence import build_evidence_binding, contract_sha256
from .judge import (
    MODEL_LINEAGE_REGISTRY_VERSION,
    OUTCOME_JUDGE_PROMPT_HASH,
    OUTCOME_JUDGE_PROMPT_VERSION,
    OutcomeJudge,
    judge_model_is_independent,
    unavailable_judge,
)
from .normalization import (
    APPROVED_NORMALIZATION_REGISTRY_SHA256,
    ContractNormalization,
    CriterionNormalization,
    apply_normalization_registry,
    apply_reviewed_normalization,
)
from .provenance import (
    PROVENANCE_SCHEMA_VERSION,
    SourceRunProvenance,
    SourceRunProvenanceValidation,
    canonical_catalog_sha256,
    is_pinned_model_identifier,
    validate_source_run_provenance,
)
from .reporting import (
    build_aggregate_summary,
    build_junit_xml,
    render_markdown_report,
    write_evaluation_artifacts,
)

__all__ = [
    "OUTCOME_JUDGE_PROMPT_HASH",
    "OUTCOME_JUDGE_PROMPT_VERSION",
    "MODEL_LINEAGE_REGISTRY_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "APPROVED_NORMALIZATION_REGISTRY_SHA256",
    "ContractNormalization",
    "CriterionNormalization",
    "OutcomeJudge",
    "SourceRunProvenance",
    "SourceRunProvenanceValidation",
    "TranscriptAdapterError",
    "adapt_catalog_products",
    "adapt_dialogue_record",
    "apply_normalization_registry",
    "apply_reviewed_normalization",
    "canonical_payload_sha256",
    "canonical_catalog_sha256",
    "canonical_testset_sha256",
    "build_aggregate_summary",
    "build_evidence_binding",
    "contract_sha256",
    "build_junit_xml",
    "compare_outcome_evaluations",
    "compile_outcome_contract",
    "compile_outcome_contracts",
    "evaluate_machine_assessment",
    "evaluate_machine_violations",
    "finalize_outcome_evaluation",
    "judge_model_is_independent",
    "is_pinned_model_identifier",
    "load_dialogue_transcripts_jsonl",
    "load_dialogue_transcripts_jsonl_bytes",
    "render_markdown_report",
    "unavailable_judge",
    "validate_contract_provenance",
    "validate_source_run_provenance",
    "write_evaluation_artifacts",
]
