"""Сплошная проверка бота по всем карточкам фида.

Для каждого товара из кэша фида проверяем два базовых сценария менеджера:
1. Запрос по артикулу — бот обязан вернуть ровно этот товар.
2. Запрос по названию товара — товар обязан попасть в топ-3 выдачи.

Запуск:  .venv/bin/python scripts/check_feed_coverage.py
Отчёт пишется в reports/feed_coverage_report.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["LLM_PROVIDER"] = "disabled"  # детерминированный режим, без LLM
# os.environ["OPENROUTER_API_KEY"] = ""  # старый OpenRouter-режим, оставлен для отката

from app.agents.orchestrator import ChatOrchestrator  # noqa: E402
from app.agents.utils import normalize_sku  # noqa: E402
from app.feed_loader import FeedLoader  # noqa: E402


def main() -> int:
    loader = FeedLoader()
    products, source = loader.load_products(refresh=False)
    print(f"Загружено товаров: {len(products)} (источник: {source})")

    sku_failures: list[str] = []
    name_failures: list[str] = []

    for index, product in enumerate(products):
        orchestrator = ChatOrchestrator(products=products)

        response = orchestrator.handle_chat(f"sku-{index}", product.sku)
        returned = [normalize_sku(item.sku) for item in response.products]
        if normalize_sku(product.sku) not in returned[:1]:
            sku_failures.append(
                f"- `{product.sku}` ({product.name[:60]}): вернул {returned or 'ничего'} | ответ: {response.answer[:90]}"
            )

        response = orchestrator.handle_chat(f"name-{index}", product.name)
        returned = [normalize_sku(item.sku) for item in response.products]
        if normalize_sku(product.sku) not in returned[:3]:
            name_failures.append(
                f"- `{product.sku}` «{product.name[:70]}»: вернул {returned or 'ничего'} | ответ: {response.answer[:90]}"
            )

    total = len(products)
    sku_ok = total - len(sku_failures)
    name_ok = total - len(name_failures)

    lines = [
        "# Отчёт сплошной проверки по карточкам фида",
        "",
        f"Товаров проверено: {total}",
        "",
        f"## Поиск по артикулу: {sku_ok}/{total}",
        "",
        *(sku_failures or ["Все артикулы находятся первой позицией."]),
        "",
        f"## Поиск по названию (топ-3): {name_ok}/{total}",
        "",
        *(name_failures or ["Все товары находятся по своему названию."]),
        "",
    ]
    report_path = PROJECT_ROOT / "reports" / "feed_coverage_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Артикул: {sku_ok}/{total}, название: {name_ok}/{total}")
    print(f"Отчёт: {report_path}")
    return 0 if not sku_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
