from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.budget import BudgetManager
from app.config import get_settings


def _manager(tmp_path, *, limit: float = 10.0, input_price: float = 0.0, output_price: float = 0.0):
    settings = get_settings().model_copy(
        update={
            "usage_budget_path": tmp_path / "usage.json",
            "daily_budget_usd": limit,
            "input_price_per_1m_tokens_usd": input_price,
            "output_price_per_1m_tokens_usd": output_price,
        }
    )
    return BudgetManager(settings)


def test_provider_reported_cost_is_authoritative_when_local_prices_are_zero(tmp_path) -> None:
    manager = _manager(tmp_path)

    cost, prompt_tokens, completion_tokens = manager.estimate_cost_usd(
        usage={"prompt_tokens": 1200, "completion_tokens": 300, "cost": 0.004321}
    )

    assert cost == pytest.approx(0.004321)
    assert (prompt_tokens, completion_tokens) == (1200, 300)


def test_zero_provider_cost_is_preserved_for_a_cache_hit(tmp_path) -> None:
    manager = _manager(tmp_path, input_price=99.0, output_price=99.0)

    cost, _, _ = manager.estimate_cost_usd(
        usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 0}
    )

    assert cost == 0.0


def test_configured_token_prices_remain_the_fallback_without_provider_cost(tmp_path) -> None:
    manager = _manager(tmp_path, input_price=1.0, output_price=2.0)

    cost, _, _ = manager.estimate_cost_usd(
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
    )

    assert cost == pytest.approx(2.0)


def test_recorded_openrouter_cost_drives_the_daily_budget_guard(tmp_path) -> None:
    manager = _manager(tmp_path, limit=0.01)

    manager.record_call(
        agent="test",
        model="qwen/test",
        prompt_chars=1,
        completion_chars=1,
        usage={"prompt_tokens": 10, "completion_tokens": 10, "cost": 0.01},
    )

    assert manager.spent_today() == pytest.approx(0.01)
    assert manager.can_call() is False
    saved = json.loads(manager.path.read_text(encoding="utf-8"))
    assert saved["calls"][-1]["cost_usd"] == pytest.approx(0.01)


def test_concurrent_managers_cannot_reserve_the_same_remaining_budget(tmp_path) -> None:
    first = _manager(tmp_path, limit=0.01)
    second = _manager(tmp_path, limit=0.01)

    def reserve(manager: BudgetManager) -> str | None:
        return manager.reserve_call(
            agent="test",
            model="qwen/test",
            prompt_chars=100,
            max_tokens=100,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, [first, second]))

    accepted = [reservation for reservation in reservations if reservation]
    assert len(accepted) == 1
    assert first.can_call() is False
    first.release_reservation(accepted[0])
    assert second.can_call() is True
