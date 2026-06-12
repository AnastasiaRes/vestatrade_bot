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


class Settings(BaseModel):
    feed_url: str
    openrouter_api_key: str | None
    openrouter_model: str
    daily_budget_usd: float
    products_cache_path: Path
    usage_budget_path: Path
    handoff_log_path: Path
    product_docs_dir: Path
    chat_logs_dir: Path
    openrouter_timeout_seconds: float
    openrouter_max_retries: int
    input_price_per_1m_tokens_usd: float
    output_price_per_1m_tokens_usd: float


@lru_cache
def get_settings() -> Settings:
    return Settings(
        feed_url=os.getenv(
            "FEED_URL",
            "https://www.vestatrade.ru/index.php?route=extension/feed/unixml/all_products",
        ),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        openrouter_model=os.getenv(
            "OPENROUTER_MODEL",
            "qwen/qwen3-vl-8b-instruct",
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
        openrouter_timeout_seconds=float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "30")),
        openrouter_max_retries=int(os.getenv("OPENROUTER_MAX_RETRIES", "2")),
        input_price_per_1m_tokens_usd=float(
            os.getenv("OPENROUTER_INPUT_PRICE_PER_1M_TOKENS_USD", "0.08")
        ),
        output_price_per_1m_tokens_usd=float(
            os.getenv("OPENROUTER_OUTPUT_PRICE_PER_1M_TOKENS_USD", "0.30")
        ),
    )

