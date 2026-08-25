from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_project_path(value: str | None, default: str) -> Path:
    raw = Path(value or default)
    if raw.is_absolute():
        return raw
    return PROJECT_ROOT / raw


def _resolve_optional_project_path(value: str | None) -> Path | None:
    if not value or not value.strip():
        return None
    raw = Path(value.strip())
    if raw.is_absolute():
        return raw
    return PROJECT_ROOT / raw


def _split_csv(value: str | None, default: str) -> list[str]:
    raw = value if value is not None else default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_rollout_percent(name: str) -> int:
    """Parse an internal-canary percentage without broadening traffic.

    A malformed or out-of-policy value must disable assignment.  Clamping a
    value such as ``99`` to ``5`` would silently enable production traffic
    after an operator error, which violates the Stage 6 fail-closed contract.
    """

    raw = os.getenv(name, "0").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value <= 5 else 0


class Settings(BaseModel):
    feed_url: str
    feed_file_path: Path | None
    llm_provider: str
    ollama_base_url: str | None
    ollama_model: str
    ollama_model_strong: str
    openrouter_api_key: str | None
    openrouter_model: str
    openrouter_model_strong: str
    daily_budget_usd: float
    products_cache_path: Path
    usage_budget_path: Path
    handoff_log_path: Path
    product_docs_dir: Path
    chat_logs_dir: Path
    allowed_origins: list[str]
    reload_feed_token: str | None
    llm_timeout_seconds: float
    llm_request_timeout_seconds: float
    llm_max_retries: int
    llm_retry_delay_seconds: float
    input_price_per_1m_tokens_usd: float
    output_price_per_1m_tokens_usd: float
    session_store_url: str | None = None
    session_ttl_seconds: int = 86_400
    session_lock_timeout_seconds: float = 30.0
    # Both capabilities are opt-in during rollout.  Shadow interpretation is
    # observable but never consumed by the legacy controller.
    diagnostic_telemetry_enabled: bool = False
    diagnostic_trace_path: Path = PROJECT_ROOT / "app/data/diagnostics/turns.jsonl"
    semantic_shadow_enabled: bool = False
    semantic_shadow_model: str | None = None
    dialogue_state_v2_shadow_enabled: bool = False
    seller_policy_v2_shadow_enabled: bool = False
    product_contracts_v2_shadow_enabled: bool = False
    catalog_planner_v2_shadow_enabled: bool = False
    solution_plan_v2_shadow_enabled: bool = False
    commerce_workflows_v2_shadow_enabled: bool = False
    handoff_workflow_v2_shadow_enabled: bool = False
    commerce_outbox_v2_shadow_enabled: bool = False
    commerce_external_execution_enabled: bool = False
    answer_plan_v2_shadow_enabled: bool = False
    response_renderer_v2_shadow_enabled: bool = False
    response_grounding_v2_shadow_enabled: bool = False
    progress_guard_v2_shadow_enabled: bool = False
    dialogue_v2_routing_enabled: bool = False
    dialogue_v2_shadow_compare_enabled: bool = False
    dialogue_v2_live_delivery_enabled: bool = False
    dialogue_v2_internal_canary_enabled: bool = False
    dialogue_v2_internal_canary_percent: int = 0
    dialogue_v2_migration_registry_path: Path | None = None
    dialogue_v2_legacy_dry_run_compare_enabled: bool = False
    dialogue_v2_force_legacy: bool = False

    @property
    def llm_model(self) -> str:
        if self.llm_provider == "ollama":
            return self.ollama_model
        return self.openrouter_model

    @property
    def llm_model_strong(self) -> str:
        if self.llm_provider == "ollama":
            return self.ollama_model_strong
        return self.openrouter_model_strong

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "ollama":
            return bool(self.ollama_base_url and self.ollama_model)
        if self.llm_provider == "openrouter":
            return bool(self.openrouter_api_key)
        return False


@lru_cache
def get_settings() -> Settings:
    llm_provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    openrouter_model = os.getenv(
        "OPENROUTER_MODEL",
        "qwen/qwen3-vl-8b-instruct",
    )
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
    return Settings(
        feed_url=os.getenv(
            "FEED_URL",
            "https://www.vestatrade.ru/index.php?route=extension/feed/unixml/all_products",
        ),
        feed_file_path=_resolve_optional_project_path(os.getenv("FEED_FILE_PATH")),
        llm_provider=llm_provider,
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=ollama_model,
        ollama_model_strong=os.getenv("OLLAMA_MODEL_STRONG", ollama_model),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        openrouter_model=openrouter_model,
        # Сильная модель для подбора/консультанта. По умолчанию = дешёвой,
        # чтобы без настройки ничего не ломалось; в .env можно указать мощнее.
        openrouter_model_strong=os.getenv(
            "OPENROUTER_MODEL_STRONG",
            openrouter_model,
        ),
        daily_budget_usd=float(os.getenv("DAILY_BUDGET_USD", "10")),
        products_cache_path=_resolve_project_path(
            os.getenv("PRODUCTS_CACHE_PATH"),
            "app/data/products_cache.json",
        ),
        usage_budget_path=_resolve_project_path(
            os.getenv("USAGE_BUDGET_PATH"),
            "app/data/usage_budget.json",
        ),
        handoff_log_path=_resolve_project_path(
            os.getenv("HANDOFF_LOG_PATH"),
            "app/data/handoff_requests.jsonl",
        ),
        product_docs_dir=_resolve_project_path(
            os.getenv("PRODUCT_DOCS_DIR"),
            "app/data/product_docs",
        ),
        chat_logs_dir=_resolve_project_path(
            os.getenv("CHAT_LOGS_DIR"),
            "app/data/chat_logs",
        ),
        allowed_origins=_split_csv(os.getenv("ALLOWED_ORIGINS"), "*"),
        reload_feed_token=os.getenv("RELOAD_FEED_TOKEN"),
        llm_timeout_seconds=float(
            os.getenv("LLM_TIMEOUT_SECONDS", os.getenv("OPENROUTER_TIMEOUT_SECONDS", "60"))
        ),
        # One user turn may invoke several LLM agents.  They must share one
        # deadline so retries or downstream agents cannot make the browser wait
        # indefinitely.  Ollama receives the whole remaining budget for its
        # current generation; after the deadline the deterministic pipeline
        # continues.
        llm_request_timeout_seconds=float(
            os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "180")
        ),
        llm_max_retries=int(
            os.getenv("LLM_MAX_RETRIES", os.getenv("OPENROUTER_MAX_RETRIES", "2"))
        ),
        llm_retry_delay_seconds=float(os.getenv("LLM_RETRY_DELAY_SECONDS", "1")),
        input_price_per_1m_tokens_usd=float(
            os.getenv(
                "LLM_INPUT_PRICE_PER_1M_TOKENS_USD",
                os.getenv("OPENROUTER_INPUT_PRICE_PER_1M_TOKENS_USD", "0"),
            )
        ),
        output_price_per_1m_tokens_usd=float(
            os.getenv(
                "LLM_OUTPUT_PRICE_PER_1M_TOKENS_USD",
                os.getenv("OPENROUTER_OUTPUT_PRICE_PER_1M_TOKENS_USD", "0"),
            )
        ),
        session_store_url=(
            os.getenv("SESSION_STORE_URL") or os.getenv("REDIS_URL") or None
        ),
        session_ttl_seconds=max(
            60,
            int(os.getenv("SESSION_TTL_SECONDS", "86400")),
        ),
        session_lock_timeout_seconds=max(
            1.0,
            float(os.getenv("SESSION_LOCK_TIMEOUT_SECONDS", "30")),
        ),
        diagnostic_telemetry_enabled=_env_bool(
            "DIAGNOSTIC_TELEMETRY_ENABLED",
            False,
        ),
        diagnostic_trace_path=_resolve_project_path(
            os.getenv("DIAGNOSTIC_TRACE_PATH"),
            "app/data/diagnostics/turns.jsonl",
        ),
        semantic_shadow_enabled=_env_bool("SEMANTIC_SHADOW_ENABLED", False),
        semantic_shadow_model=(
            os.getenv("SEMANTIC_SHADOW_MODEL") or None
        ),
        dialogue_state_v2_shadow_enabled=_env_bool(
            "DIALOGUE_STATE_V2_SHADOW_ENABLED",
            False,
        ),
        seller_policy_v2_shadow_enabled=_env_bool(
            "SELLER_POLICY_V2_SHADOW_ENABLED",
            False,
        ),
        product_contracts_v2_shadow_enabled=_env_bool(
            "PRODUCT_CONTRACTS_V2_SHADOW_ENABLED",
            False,
        ),
        catalog_planner_v2_shadow_enabled=_env_bool(
            "CATALOG_PLANNER_V2_SHADOW_ENABLED",
            False,
        ),
        solution_plan_v2_shadow_enabled=_env_bool(
            "SOLUTION_PLAN_V2_SHADOW_ENABLED",
            False,
        ),
        commerce_workflows_v2_shadow_enabled=_env_bool(
            "COMMERCE_WORKFLOWS_V2_SHADOW_ENABLED",
            False,
        ),
        handoff_workflow_v2_shadow_enabled=_env_bool(
            "HANDOFF_WORKFLOW_V2_SHADOW_ENABLED",
            False,
        ),
        commerce_outbox_v2_shadow_enabled=_env_bool(
            "COMMERCE_OUTBOX_V2_SHADOW_ENABLED",
            False,
        ),
        commerce_external_execution_enabled=_env_bool(
            "COMMERCE_EXTERNAL_EXECUTION_ENABLED",
            False,
        ),
        answer_plan_v2_shadow_enabled=_env_bool(
            "ANSWER_PLAN_V2_SHADOW_ENABLED",
            False,
        ),
        response_renderer_v2_shadow_enabled=_env_bool(
            "RESPONSE_RENDERER_V2_SHADOW_ENABLED",
            False,
        ),
        response_grounding_v2_shadow_enabled=_env_bool(
            "RESPONSE_GROUNDING_V2_SHADOW_ENABLED",
            False,
        ),
        progress_guard_v2_shadow_enabled=_env_bool(
            "PROGRESS_GUARD_V2_SHADOW_ENABLED",
            False,
        ),
        dialogue_v2_routing_enabled=_env_bool(
            "DIALOGUE_V2_ROUTING_ENABLED",
            False,
        ),
        dialogue_v2_shadow_compare_enabled=_env_bool(
            "DIALOGUE_V2_SHADOW_COMPARE_ENABLED",
            False,
        ),
        dialogue_v2_live_delivery_enabled=_env_bool(
            "DIALOGUE_V2_LIVE_DELIVERY_ENABLED",
            False,
        ),
        dialogue_v2_internal_canary_enabled=_env_bool(
            "DIALOGUE_V2_INTERNAL_CANARY_ENABLED",
            False,
        ),
        dialogue_v2_internal_canary_percent=_bounded_rollout_percent(
            "DIALOGUE_V2_INTERNAL_CANARY_PERCENT"
        ),
        dialogue_v2_migration_registry_path=_resolve_optional_project_path(
            os.getenv("DIALOGUE_V2_MIGRATION_REGISTRY_PATH")
        ),
        dialogue_v2_legacy_dry_run_compare_enabled=_env_bool(
            "DIALOGUE_V2_LEGACY_DRY_RUN_COMPARE_ENABLED",
            False,
        ),
        dialogue_v2_force_legacy=_env_bool(
            "DIALOGUE_V2_FORCE_LEGACY",
            False,
        ),
    )
