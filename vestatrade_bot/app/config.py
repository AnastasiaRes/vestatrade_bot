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


def _split_csv(value: str | None, default: str) -> list[str]:
    raw = value if value is not None else default
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings(BaseModel):
    feed_url: str
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
    llm_max_retries: int
    input_price_per_1m_tokens_usd: float
    output_price_per_1m_tokens_usd: float

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
            os.getenv("LLM_TIMEOUT_SECONDS", os.getenv("OPENROUTER_TIMEOUT_SECONDS", "30"))
        ),
        llm_max_retries=int(
            os.getenv("LLM_MAX_RETRIES", os.getenv("OPENROUTER_MAX_RETRIES", "2"))
        ),
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
    )
