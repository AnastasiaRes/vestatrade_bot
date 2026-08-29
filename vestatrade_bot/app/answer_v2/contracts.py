"""Immutable contracts for grounded Stage 5 shadow answers."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.catalog_v2.contracts import (
    CandidateStatus,
    CatalogFact,
    CatalogFactIssue,
    CatalogProductRole,
    CatalogRelaxation,
    ProductKind,
)
from app.dialogue_v2.contracts import (
    InformationOutputRelation,
    InformationPurpose,
    InformationSourceKind,
    InformationSubjectScope,
    NextActionKind,
    RequestedInformationOutput,
    ResponseStrategyKind,
)


ANSWER_SCHEMA_VERSION = "1.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


Scalar = str | int | float | bool


class AnswerPlanStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BOUNDARY = "boundary"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


class KnowledgeStatus(str, Enum):
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"
    CATALOGUE_MISSING = "catalogue_missing"
    USER_UNKNOWN = "user_unknown"
    USER_REFUSED = "user_refused"
    USER_DEFERRED = "user_deferred"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"


class SourceType(str, Enum):
    CONSTRAINT_FACT = "constraint_fact"
    PRODUCT_GOAL = "product_goal"
    CATALOG_IDENTITY = "catalog_identity"
    CATALOG_ATTRIBUTE = "catalog_attribute"
    CATALOG_PRICE = "catalog_price"
    CATALOG_STOCK = "catalog_stock"
    CATALOG_LINK = "catalog_link"
    CANDIDATE_ASSESSMENT = "candidate_assessment"
    CATALOG_SEARCH_PLAN = "catalog_search_plan"
    SOLUTION_PLAN = "solution_plan"
    CAPABILITY_RESULT = "capability_result"
    COMMERCE_CAPABILITY = "commerce_capability"
    COMMERCE_WORKFLOW = "commerce_workflow"
    COMMERCE_RECEIPT = "commerce_receipt"
    POLICY_REASON = "policy_reason"


class ClaimKind(str, Enum):
    PRODUCT_IDENTITY = "product_identity"
    PRODUCT_ATTRIBUTE = "product_attribute"
    CUSTOMER_CONSTRAINT = "customer_constraint"
    PRICE = "price"
    STOCK = "stock"
    LINK = "link"
    CAPABILITY_FACT = "capability_fact"
    COMMERCE_STATUS = "commerce_status"


class ProductPresentationStatus(str, Enum):
    EXACT = "exact"
    PRELIMINARY = "preliminary"
    ANALOG = "analog"
    ALTERNATIVE = "alternative"
    UNVERIFIED = "unverified"


class ProductRecommendationRole(str, Enum):
    PRIMARY = "primary"
    ALTERNATIVE = "alternative"


class RecommendationCriterion(str, Enum):
    ONLY_EXACT_ELIGIBLE = "only_exact_eligible"
    LOWEST_CONFIRMED_PRICE = "lowest_confirmed_price"
    STABLE_SKU_TIEBREAK = "stable_sku_tiebreak"


class LimitationStatus(str, Enum):
    UNKNOWN = "unknown"
    REFUSED = "refused"
    DEFERRED = "deferred"
    CATALOGUE_MISSING = "catalogue_missing"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    CAPABILITY_BOUNDARY = "capability_boundary"


class AnswerSectionKind(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    CONFIRMED_FACTS = "confirmed_facts"
    PRODUCTS = "products"
    ANALOG_DIFFERENCES = "analog_differences"
    LIMITATIONS = "limitations"
    QUESTION = "question"
    NEXT_STEP = "next_step"


class NextStepKind(str, Enum):
    PROVIDE_DIRECT_ANSWER = "provide_direct_answer"
    ASK_DECISION_FACT = "ask_decision_fact"
    EXPLAIN_HOW_TO_FIND_FACT = "explain_how_to_find_fact"
    SHOW_PRELIMINARY_OPTIONS = "show_preliminary_options"
    RECOMMEND_ONE = "recommend_one"
    CONTINUE_WITH_CONFIRMED_FACTS = "continue_with_confirmed_facts"
    COMPARE_CANDIDATES = "compare_candidates"
    CALCULATE_CATALOGUE_AMOUNT = "calculate_catalogue_amount"
    PRESENT_ANALOG_DIFFERENCES = "present_analog_differences"
    OFFER_VERIFIABLE_EXTERNAL_STEP = "offer_verifiable_external_step"
    STATE_CAPABILITY_BOUNDARY = "state_capability_boundary"
    EXPLAIN_DECISION_RELEVANCE = "explain_decision_relevance"
    STATE_COMPATIBILITY_BOUNDARY = "state_compatibility_boundary"
    STATE_INFORMATION_SOURCE_BOUNDARY = "state_information_source_boundary"
    STATE_INFORMATION_MEANING_BOUNDARY = "state_information_meaning_boundary"
    STATE_DETERMINATION_METHOD_BOUNDARY = "state_determination_method_boundary"
    STATE_INFORMATION_VALUE_BOUNDARY = "state_information_value_boundary"
    REPORT_CANDIDATE_FACTS = "report_candidate_facts"
    CLOSE_TASK = "close_task"
    WAIT_FOR_CUSTOMER = "wait_for_customer"


class SourceReference(FrozenModel):
    source_ref_id: str
    source_type: SourceType
    source_id: str
    field_name: str | None = None
    task_id: str | None = None
    goal_id: str | None = None
    source_turn: int | None = Field(default=None, ge=0)


class AnswerClaim(FrozenModel):
    claim_id: str
    kind: ClaimKind
    subject_ref: str
    predicate: str
    value: Scalar | None = None
    unit: str | None = None
    knowledge_status: KnowledgeStatus
    source_ref_ids: tuple[str, ...] = ()
    allowed_in_response: bool = False
    task_id: str | None = None
    goal_id: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def confirmed_value_only(self) -> "AnswerClaim":
        if self.knowledge_status == KnowledgeStatus.CONFIRMED and self.value is None:
            raise ValueError("confirmed answer claim requires a value")
        if self.knowledge_status != KnowledgeStatus.CONFIRMED and self.value is not None:
            raise ValueError("unconfirmed answer claim cannot contain a value")
        if self.allowed_in_response and self.knowledge_status != KnowledgeStatus.CONFIRMED:
            raise ValueError("only confirmed claims may be asserted")
        if self.allowed_in_response and not self.source_ref_ids:
            raise ValueError("assertable answer claim requires provenance")
        return self


class AnalogDifference(FrozenModel):
    difference_id: str
    product_plan_id: str
    fact_name: str
    requested_value: Scalar
    candidate_value: Scalar | None = None
    source_ref_ids: tuple[str, ...] = ()
    reason_code: str


class ProductPresentationPlan(FrozenModel):
    product_plan_id: str
    sku: str
    name: str
    product_kind: ProductKind
    role: CatalogProductRole
    task_id: str
    goal_id: str | None = None
    search_plan_id: str
    status: ProductPresentationStatus
    matched_hard_facts: tuple[str, ...] = ()
    missing_hard_facts: tuple[str, ...] = ()
    matched_soft_facts: tuple[str, ...] = ()
    mismatched_soft_facts: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    difference_ids: tuple[str, ...] = ()
    source_ref_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    recommendation_role: ProductRecommendationRole | None = None
    recommendation_rank: int | None = Field(default=None, ge=1, le=3)
    recommendation_criterion: RecommendationCriterion | None = None
    recommendation_reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def recommendation_metadata_is_exact_and_coherent(
        self,
    ) -> "ProductPresentationPlan":
        if self.recommendation_role is None:
            if any(
                (
                    self.recommendation_rank is not None,
                    self.recommendation_criterion is not None,
                    bool(self.recommendation_reason_codes),
                )
            ):
                raise ValueError("recommendation metadata requires a role")
            return self
        if self.status != ProductPresentationStatus.EXACT:
            raise ValueError("only exact products may be recommended")
        if (
            self.recommendation_rank is None
            or self.recommendation_criterion is None
            or not self.recommendation_reason_codes
        ):
            raise ValueError("recommendation metadata must be complete")
        if (
            self.recommendation_role == ProductRecommendationRole.PRIMARY
            and self.recommendation_rank != 1
        ):
            raise ValueError("primary recommendation must have rank one")
        if (
            self.recommendation_role == ProductRecommendationRole.ALTERNATIVE
            and self.recommendation_rank not in {2, 3}
        ):
            raise ValueError("recommendation alternative rank must be two or three")
        return self


class LimitationPlan(FrozenModel):
    limitation_id: str
    status: LimitationStatus
    reason_code: str
    task_id: str | None = None
    goal_id: str | None = None
    fact_name: str | None = None
    source_ref_ids: tuple[str, ...] = ()
    allowed_strategy_kinds: tuple[ResponseStrategyKind, ...] = ()


class QuestionPlan(FrozenModel):
    question_id: str
    task_id: str
    fact_name: str
    decision_impact_code: str
    contract_allows_question: bool = True
    learn_method_code: str | None = None
    expected_unit: str | None = None
    source_ref_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CandidateFactStatus(str, Enum):
    CONFIRMED = "confirmed"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"


class CandidateFactReportItem(FrozenModel):
    item_id: str
    sku: str
    name: str
    fact_name: str
    status: CandidateFactStatus
    value: Scalar | None = None
    unit: str | None = None
    source_ref_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def confirmed_values_are_grounded(self) -> "CandidateFactReportItem":
        if self.status == CandidateFactStatus.CONFIRMED:
            if self.value is None:
                raise ValueError("confirmed candidate fact requires a value")
            if not self.source_ref_ids:
                raise ValueError("confirmed candidate fact requires provenance")
        elif self.value is not None or self.unit is not None:
            raise ValueError("missing/ambiguous candidate fact cannot contain a value")
        return self


class CandidateFactReport(FrozenModel):
    report_id: str
    information_request_id: str
    task_id: str
    goal_id: str | None = None
    fact_name: str
    items: tuple[CandidateFactReportItem, ...] = Field(min_length=1, max_length=12)
    reason_codes: tuple[str, ...] = ()


class NextStepPlan(FrozenModel):
    next_step_id: str
    kind: NextStepKind
    task_id: str | None = None
    fact_name: str | None = None
    learn_method_code: str | None = None
    expected_unit: str | None = None
    capability_ref_id: str | None = None
    information_request_id: str | None = None
    information_purpose: InformationPurpose | None = None
    requested_outputs: tuple[RequestedInformationOutput, ...] = ()
    output_relation: InformationOutputRelation | None = None
    source_kind: InformationSourceKind | None = None
    information_subject_scope: InformationSubjectScope = (
        InformationSubjectScope.CUSTOMER_GOAL
    )
    candidate_fact_report: CandidateFactReport | None = None
    contract_fact_recognized: bool = False
    fact_decision_changing: bool = False
    fact_required_for_exact: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def candidate_report_matches_kind(self) -> "NextStepPlan":
        has_report = self.candidate_fact_report is not None
        if has_report != (self.kind == NextStepKind.REPORT_CANDIDATE_FACTS):
            raise ValueError("candidate fact report must match next-step kind")
        if has_report and self.information_subject_scope != InformationSubjectScope.PRESENTED_CANDIDATES:
            raise ValueError("candidate fact report requires presented-candidates scope")
        return self


class AnswerSection(FrozenModel):
    section_id: str
    kind: AnswerSectionKind
    item_ids: tuple[str, ...] = ()
    required: bool = True


class AnswerPlan(FrozenModel):
    schema_version: Literal["1.0"] = ANSWER_SCHEMA_VERSION
    plan_id: str
    turn_id: str
    turn_number: int = Field(ge=0)
    task_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    primary_action: NextActionKind
    secondary_action: NextActionKind | None = None
    status: AnswerPlanStatus
    sections: tuple[AnswerSection, ...]
    sources: tuple[SourceReference, ...] = ()
    claims: tuple[AnswerClaim, ...] = ()
    products: tuple[ProductPresentationPlan, ...] = ()
    analog_differences: tuple[AnalogDifference, ...] = ()
    limitations: tuple[LimitationPlan, ...] = ()
    question: QuestionPlan | None = None
    next_step: NextStepPlan
    semantic_signature: str
    reason_codes: tuple[str, ...] = ()


class RejectedClaim(FrozenModel):
    subject_ref: str
    predicate: str
    reason_code: str


class AnswerPlanningResult(FrozenModel):
    status: Literal["planned", "skipped", "failed"]
    answer_plan: AnswerPlan | None = None
    accepted_claim_ids: tuple[str, ...] = ()
    rejected_claims: tuple[RejectedClaim, ...] = ()
    missing_source_ids: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    error: str | None = None


class VerifiedCapabilityFact(FrozenModel):
    fact_id: str
    task_id: str | None = None
    name: str
    value: Scalar
    unit: str | None = None
    source: str
    source_revision: str
    confirmed: bool = True


class CatalogAnswerProduct(FrozenModel):
    sku: str
    name: str
    product_kind: ProductKind
    role: CatalogProductRole
    price: float | None = None
    currency: str | None = None
    stock_status: str | None = None
    stock_qty: int | None = None
    url: str | None = None
    image_url: str | None = None
    updated_at: str | None = None
    facts: tuple[CatalogFact, ...] = ()
    fact_issues: tuple[CatalogFactIssue, ...] = ()


class ConstraintAnswerEvidence(FrozenModel):
    fact_id: str
    name: str
    value: Scalar | None = None
    unit: str | None = None
    status: str
    task_id: str | None = None
    goal_id: str | None = None
    source_turn: int = Field(ge=0)


class ProductGoalEvidence(FrozenModel):
    goal_id: str
    canonical_type: str
    category: str
    role: str
    confirmed_turn: int = Field(ge=0)


class CatalogCandidateEvidence(FrozenModel):
    search_plan_id: str
    task_id: str
    goal_id: str | None = None
    sku: str
    product_kind: ProductKind
    role: CatalogProductRole
    status: CandidateStatus
    required_hard_facts: tuple[str, ...] = ()
    matched_hard_facts: tuple[str, ...] = ()
    mismatched_hard_facts: tuple[str, ...] = ()
    missing_hard_facts: tuple[str, ...] = ()
    matched_soft_facts: tuple[str, ...] = ()
    mismatched_soft_facts: tuple[str, ...] = ()
    relaxations: tuple[CatalogRelaxation, ...] = ()


class SolutionPlanEvidence(FrozenModel):
    solution_id: str
    task_ids: tuple[str, ...]
    unresolved_dependencies: tuple[str, ...] = ()


class CommerceWorkflowEvidence(FrozenModel):
    workflow_id: str
    task_ids: tuple[str, ...] = ()
    execution_status: str
    receipt_ref: str | None = None
    updated_turn: int = Field(ge=0)


class AnswerSourceSnapshot(FrozenModel):
    schema_version: Literal["1.0"] = ANSWER_SCHEMA_VERSION
    source_revision: str
    products: tuple[CatalogAnswerProduct, ...] = ()
    capability_facts: tuple[VerifiedCapabilityFact, ...] = ()
    constraints: tuple[ConstraintAnswerEvidence, ...] = ()
    product_goals: tuple[ProductGoalEvidence, ...] = ()
    catalog_candidates: tuple[CatalogCandidateEvidence, ...] = ()
    solution_plans: tuple[SolutionPlanEvidence, ...] = ()
    commerce_workflows: tuple[CommerceWorkflowEvidence, ...] = ()

    def product(self, sku: str) -> CatalogAnswerProduct | None:
        return next((item for item in self.products if item.sku == sku), None)

    def constraint(self, fact_id: str) -> ConstraintAnswerEvidence | None:
        return next((item for item in self.constraints if item.fact_id == fact_id), None)

    def candidate(
        self,
        search_plan_id: str,
        sku: str,
    ) -> CatalogCandidateEvidence | None:
        return next(
            (
                item
                for item in self.catalog_candidates
                if item.search_plan_id == search_plan_id and item.sku == sku
            ),
            None,
        )

    def commerce_workflow(self, workflow_id: str) -> CommerceWorkflowEvidence | None:
        return next(
            (
                item
                for item in self.commerce_workflows
                if item.workflow_id == workflow_id
            ),
            None,
        )


class RenderedSegmentKind(str, Enum):
    FACT = "fact"
    PRODUCT = "product"
    LIMITATION = "limitation"
    QUESTION = "question"
    NEXT_STEP = "next_step"
    TRANSITION = "transition"


class TransitionStyle(str, Enum):
    ALSO = "also"
    IMPORTANT = "important"
    THEREFORE = "therefore"
    NEXT = "next"


class NaturalizationTransition(FrozenModel):
    before_segment_id: str
    style: TransitionStyle


class NaturalizationLayout(FrozenModel):
    """The only free choice delegated to the response LLM.

    The model never returns factual prose. It may insert a bounded set of
    allow-listed neutral transitions before existing deterministic segments.
    """

    schema_version: Literal["1.0"] = ANSWER_SCHEMA_VERSION
    plan_id: str
    transitions: tuple[NaturalizationTransition, ...] = Field(max_length=8)


class NaturalizationProposal(FrozenModel):
    """Untrusted LLM output; plan identity is deliberately not delegated."""

    schema_version: Literal["1.0"] = ANSWER_SCHEMA_VERSION
    transitions: tuple[NaturalizationTransition, ...] = Field(max_length=8)


class RenderedSegment(FrozenModel):
    segment_id: str
    kind: RenderedSegmentKind
    source_ids: tuple[str, ...]
    text: str = Field(min_length=1, max_length=1200)
    critical_literals: tuple[str, ...] = ()


class RenderedAnswer(FrozenModel):
    schema_version: Literal["1.0"] = ANSWER_SCHEMA_VERSION
    plan_id: str
    renderer: Literal["deterministic", "llm"]
    segments: tuple[RenderedSegment, ...]
    text: str = Field(min_length=1, max_length=12_000)


class RenderedAnswerResult(FrozenModel):
    status: Literal["rendered", "fallback", "skipped", "failed"]
    rendered_answer: RenderedAnswer | None = None
    deterministic_fallback: RenderedAnswer | None = None
    llm_requested: bool = False
    llm_output_accepted: bool = False
    model: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    rejection_reason: str | None = None
    reason_codes: tuple[str, ...] = ()


class ValidationViolation(FrozenModel):
    code: str
    segment_id: str | None = None
    detail: str | None = Field(default=None, max_length=500)


class AnswerValidationResult(FrozenModel):
    status: Literal["accepted", "rejected", "fallback_required", "skipped"]
    plan_id: str | None = None
    accepted_segment_ids: tuple[str, ...] = ()
    violations: tuple[ValidationViolation, ...] = ()
    unknown_reference_ids: tuple[str, ...] = ()
    extra_critical_literals: tuple[str, ...] = ()
    missing_required_item_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class TaskProgressStatus(str, Enum):
    PROGRESS = "progress"
    NO_PROGRESS = "no_progress"
    NEUTRAL = "neutral"


class TaskProgressAssessment(FrozenModel):
    task_id: str
    turn_id: str
    turn_number: int = Field(ge=0)
    status: TaskProgressStatus
    changes: tuple[str, ...] = ()
    unresolved_blocker: str | None = None
    previous_strategy: ResponseStrategyKind | None = None
    consecutive_no_progress: int = Field(ge=0)
    attempted_strategies: tuple[ResponseStrategyKind, ...] = ()
    strategy_change_required: bool = False
    catalog_signature: str | None = None
    commerce_signature: str | None = None
    reason_codes: tuple[str, ...] = ()


class StrategyDirective(FrozenModel):
    task_id: str
    strategy: ResponseStrategyKind
    fact_name: str | None = None
    reason_codes: tuple[str, ...] = ()
