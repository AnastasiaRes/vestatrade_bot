#!/usr/bin/env python3
"""Собрать каталог демо-витрины (`/widget-demo`) из фида-витрины на 100 позиций.

Раньше страница демо содержала шесть карточек, вписанных в HTML руками, и
счётчики разделов в сайдбаре («Все 99», «Отопление 21»), не связанные ни с
одним источником данных.  Тестировать бота на такой витрине неудобно: в чат
можно спросить про позицию, которой на странице нет, а раздел на странице
может не совпасть с тем, что бот считает категорией товара.

Скрипт собирает витрину из того же фида, на котором гоняются живые прогоны
(``data/feed_showcase_100_2026-06-14.xml``), и теми же средствами, что и бот:

* ``FeedLoader.parse_xml`` — одинаковая нормализация артикулов, цен и наличия;
* ``FeedSearchAgent.canonical_category`` — одинаковое распределение по
  разделам.  Рубрики фида для этого не годятся: котлы и насосы лежат в
  «ПРОКАЧИВАЕМ СКИДКИ», и сайдбар показывал бы промо-раздел вместо котлов.

Счётчики разделов считаются по факту, поэтому разойтись с содержимым сетки
они не могут.

Запуск:

    python3 scripts/build_widget_demo_catalog.py
    python3 scripts/build_widget_demo_catalog.py --check   # проверить дрейф
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.feed_search import FeedSearchAgent
from app.feed_loader import FeedLoader
from app.models import Product

DEFAULT_FEED = PROJECT_ROOT / "data" / "feed_showcase_100_2026-06-14.xml"
DEFAULT_OUT = PROJECT_ROOT / "app" / "static" / "widget-demo-catalog.json"

# Заголовки разделов витрины.  Ключи — канонические категории бота
# (``FeedSearchAgent.canonical_category``), включая те, которых нет в витрине
# на 100 позиций: фид можно заменить, и раздел не должен остаться безымянным.
CATEGORY_TITLES: dict[str, str] = {
    "valves": "Водозапорная арматура",
    "sewer": "Канализация",
    "radiator_fittings": "Арматура для радиаторов",
    "pipes": "Трубы",
    "fittings": "Фитинги",
    "pumps": "Насосы",
    "boilers": "Котлы",
    "radiators": "Радиаторы",
    "water_heaters": "Водонагреватели",
    "hydraulic_accumulators": "Гидроаккумуляторы",
    "filters": "Фильтры и водоподготовка",
    "controls": "Автоматика",
    "meters": "Приборы учёта",
    "installation_systems": "Инсталляции",
    "sanitary_ware": "Сантехника",
    "other": "Прочее",
}

# Характеристики, которые коротко описывают позицию в карточке.  Порядок —
# приоритет: в карточку попадают первые две подошедшие.
TAG_KEYS: tuple[tuple[str, str], ...] = (
    ("мощность, квт", "{value} кВт"),
    ("отапливаемая площадь, м²", "до {value} м²"),
    ("диаметр подключения, дюйм", '{value}"'),
    ("диаметр условного прохода", "{value}"),
    ("мощность, вт", "{value} Вт"),
    ("материал корпуса", "{value}"),
    ("материал", "{value}"),
    ("тип присоединения", "{value}"),
    ("тип резьбы", "{value}"),
    ("тип ручки", "{value}"),
    ("тип товара", "{value}"),
)
MAX_TAG_LENGTH = 24


def build_tags(product: Product, category: str) -> list[str]:
    """Две короткие характеристики для карточки, без дублей по значению.

    У части позиций витрины (термоголовки, клапаны для радиаторов) в фиде нет
    ни одного параметра кроме артикула и штрихкода.  Чтобы карточка не
    оставалась вовсе без пояснения, для них подставляется название раздела.
    """
    tags: list[str] = []
    seen: set[str] = set()
    for key, template in TAG_KEYS:
        raw = (product.attributes_normalized.get(key) or "").strip()
        if not raw or len(raw) > MAX_TAG_LENGTH:
            continue
        tag = template.format(value=raw)
        folded = tag.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        tags.append(tag)
        if len(tags) == 2:
            break
    if not tags:
        tags.append(CATEGORY_TITLES.get(category, CATEGORY_TITLES["other"]))
    return tags


def build_catalog(feed_path: Path) -> dict:
    products = FeedLoader().parse_xml(feed_path.read_bytes())
    if not products:
        raise SystemExit(f"фид {feed_path} не дал ни одной позиции")

    search = FeedSearchAgent()
    items: list[dict] = []
    counts: Counter[str] = Counter()
    for product in products:
        category = search.canonical_category(product)
        counts[category] += 1
        items.append(
            {
                "sku": product.sku,
                "name": product.name,
                "brand": product.brand or "",
                "category": category,
                "section": product.category_path,
                "price": product.price,
                "url": product.url or "",
                "image": product.image_url or "",
                "in_stock": product.is_in_stock,
                "qty": product.stock_qty,
                "tags": build_tags(product, category),
            }
        )

    categories = [
        {
            "id": category,
            "title": CATEGORY_TITLES.get(category, CATEGORY_TITLES["other"]),
            "count": count,
        }
        # Крупные разделы выше — сайдбар читается как меню реального магазина.
        for category, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    try:
        source = str(feed_path.relative_to(PROJECT_ROOT))
    except ValueError:  # фид вне репозитория — пишем как есть
        source = str(feed_path)

    return {
        "source": source,
        "feed_date": products[0].updated_at,
        "total": len(items),
        "categories": categories,
        "products": items,
    }


def render(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED, help="XML витрины")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="куда писать JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="не писать файл, а сверить существующий с фидом",
    )
    args = parser.parse_args()

    catalog = build_catalog(args.feed.expanduser())
    payload = render(catalog)

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != payload:
            print(f"каталог {args.out} разошёлся с фидом {args.feed}", file=sys.stderr)
            print("пересоберите: python3 scripts/build_widget_demo_catalog.py", file=sys.stderr)
            return 1
        print(f"каталог актуален: {catalog['total']} позиций")
        return 0

    args.out.write_text(payload, encoding="utf-8")
    print(f"{args.out}: {catalog['total']} позиций из {args.feed}")
    print("разделы:")
    for category in catalog["categories"]:
        print(f"  {category['count']:3d}  {category['title']} ({category['id']})")
    in_stock = sum(1 for item in catalog["products"] if item["in_stock"])
    print(f"в наличии: {in_stock} из {catalog['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
