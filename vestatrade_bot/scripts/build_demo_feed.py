#!/usr/bin/env python3
"""Собрать демонстрационный фид: витрина магазина плюс добор недостающих групп.

Витрина из 100 позиций (``index.php``-выгрузка) — строгое подмножество полного
фида, и в ней отсутствуют десять товарных групп, которые задействованы в
тест-наборе живых диалогов: тёплый пол, коллекторы, расширительные баки,
водоподготовка, смесители, балансировочная и регулирующая арматура,
автоматика, теплоноситель, водонагреватели, приборы учёта, PEX и
металлопласт.  На такой витрине примерно десять сценариев непроходимы
физически, а ещё два десятка задеты частично: бот честно отвечает «нет в
каталоге», и это неотличимо от провала подбора.

Скрипт берёт витрину как основу и добирает недостающие группы из полного
фида, оставаясь в пределах нескольких сотен позиций — чтобы прогоны были
быстрыми, а покрытие сценариев полным.

Категории источника намеренно НЕ переписываются.  В реальных данных котлы
лежат в «ПРОКАЧИВАЕМ СКИДКИ», а теплоноситель — в «Радиаторы отопления»;
нормализация таких рубрик — задача кода поиска, а не подготовки данных.
Спрятать проблему в фикстуре означало бы отлаживать бота на данных, которых
в проде не существует.

Запуск:

    python3 scripts/build_demo_feed.py \
        --showcase "~/Downloads/index.php (1).xml" \
        --full data/products_all.xml \
        --out data/products_demo.xml
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections import Counter
from pathlib import Path

OFFER_RE = re.compile(r"<offer\b.*?</offer>", re.S)
ID_RE = re.compile(r'<offer[^>]*\bid="([^"]*)"')

# Сколько позиций добрать в каждую группу.  Квоты подобраны так, чтобы в
# каждой группе бот мог предложить осмысленный выбор из нескольких вариантов,
# а не единственную позицию: сценарии требуют «2–3 конкретные модели».
QUOTAS: tuple[tuple[str, str, int], ...] = (
    # (метка группы, категория в полном фиде, сколько добрать)
    ("тёплый пол", "Системы теплых полов", 22),
    ("коллекторы", "Коллекторы и аксессуары", 16),
    ("расширительные баки", "Баки мембранные", 12),
    ("водоподготовка", "Водоподготовка", 12),
    ("фильтры", "Фильтры", 12),
    ("смесители", "Смесители", 20),
    ("регулирующая арматура", "Регулирующая арматура", 16),
    ("автоматика", "Автоматика для систем отопления", 12),
    ("водонагреватели", "Водонагреватели", 16),
    ("приборы учёта", "Измерительные приборы", 8),
    ("водосчётчики", "Водосчетчики", 6),
    ("полотенцесушители", "Полотенцесушители", 6),
    ("радиаторы (добор)", "Радиаторы отопления", 14),
    ("котлы (добор)", "Котельное оборудование", 12),
    ("насосы (добор)", "Насосное оборудование", 10),
)

# Группы, которые в полном фиде не выделены отдельной категорией и ищутся по
# названию.  Второй элемент — регулярное выражение по имени товара.
NAME_QUOTAS: tuple[tuple[str, str, int], ...] = (
    ("теплоноситель", r"теплоносител|антифриз|гликол", 6),
    ("PEX и металлопласт (труба)", r"\bPEX\b|сшит|металлопласт", 12),
    # Балансировочная арматура рассыпана по «Регулирующей арматуре» и
    # «Коллекторам»; квоты по категориям вытягивают её случайно, а сценарий
    # B17 требует именно выбора из нескольких балансировочных клапанов.
    ("балансировочная арматура", r"балансиров", 8),
)


def field(offer: str, tag: str) -> str:
    """Достать текст тега из сырого XML оффера."""
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", offer, re.S)
    if not match:
        return ""
    return html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", match.group(1)).strip())


def has_real_sku(offer: str) -> bool:
    """Отсечь позиции без внятного артикула.

    В выгрузке есть записи с ``<vendorCode>?</vendorCode>`` — расходники вроде
    прокладок и соли.  Такой артикул ломает и адресацию товара в диалоге, и
    детектор выдуманных SKU в скорере: шесть разных товаров под одним «?».
    Для фикстуры это чистый шум, сценарии их не задействуют.
    """
    sku = field(offer, "vendorCode")
    return bool(sku) and sku not in {"?", "-", "0"}


def family_key(name: str) -> str:
    """Ключ «семейства» товара: имя без размеров, длин и чисел.

    Нужен, чтобы добор не превратился в двадцать типоразмеров одной и той же
    позиции.  Бот должен показывать разные решения, а не разные диаметры
    одного решения.
    """
    key = name.lower()
    key = re.sub(r"\d+[.,]?\d*", " ", key)
    key = re.sub(r"[^\wа-яё]+", " ", key)
    return " ".join(key.split()[:4])


def pick(
    offers: list[str],
    quota: int,
    taken_ids: set[str],
) -> list[str]:
    """Отобрать до ``quota`` разнообразных позиций, отдавая приоритет наличию.

    Порядок предпочтения: есть остаток и цена → есть цена → всё остальное.
    Внутри каждого разряда одно семейство даёт не больше одной позиции, пока
    квота не исчерпана; только потом добираются повторы семейств.
    """
    ranked: list[tuple[int, str]] = []
    for offer in offers:
        offer_id = (ID_RE.search(offer) or [None, ""])[1]
        if offer_id in taken_ids:
            continue
        price = field(offer, "price")
        if not price or not has_real_sku(offer):
            continue
        try:
            in_stock = int(field(offer, "quantity") or 0) > 0
        except ValueError:
            in_stock = False
        ranked.append((0 if in_stock else 1, offer))

    ranked.sort(key=lambda pair: pair[0])

    chosen: list[str] = []
    seen_families: set[str] = set()
    leftovers: list[str] = []
    for _, offer in ranked:
        if len(chosen) >= quota:
            break
        key = family_key(field(offer, "name"))
        if key in seen_families:
            leftovers.append(offer)
            continue
        seen_families.add(key)
        chosen.append(offer)
    for offer in leftovers:
        if len(chosen) >= quota:
            break
        chosen.append(offer)
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--showcase", required=True, help="XML витрины магазина")
    parser.add_argument("--full", default="data/products_all.xml", help="полный фид")
    parser.add_argument("--out", default="data/products_demo.xml", help="куда писать")
    args = parser.parse_args()

    showcase_path = Path(args.showcase).expanduser()
    full_path = Path(args.full).expanduser()

    showcase_raw = showcase_path.read_text(encoding="utf-8", errors="replace")
    full_raw = full_path.read_text(encoding="utf-8", errors="replace")

    base = [o for o in OFFER_RE.findall(showcase_raw) if has_real_sku(o)]
    full = OFFER_RE.findall(full_raw)
    print(f"витрина: {len(base)} позиций | полный фид: {len(full)} позиций")

    taken_ids = {(ID_RE.search(o) or [None, ""])[1] for o in base}
    result = list(base)

    by_category: dict[str, list[str]] = {}
    for offer in full:
        by_category.setdefault(field(offer, "category"), []).append(offer)

    print("\nдобор по категориям:")
    for label, category, quota in QUOTAS:
        pool = by_category.get(category, [])
        if not pool:
            print(f"  ПРОПУСК {label:28s} категории «{category}» нет в фиде")
            continue
        picked = pick(pool, quota, taken_ids)
        for offer in picked:
            taken_ids.add((ID_RE.search(offer) or [None, ""])[1])
        result.extend(picked)
        print(f"  +{len(picked):3d}  {label:28s} из «{category}» ({len(pool)} доступно)")

    print("\nдобор по названию:")
    for label, pattern, quota in NAME_QUOTAS:
        rx = re.compile(pattern, re.I)
        pool = [o for o in full if rx.search(field(o, "name"))]
        picked = pick(pool, quota, taken_ids)
        for offer in picked:
            taken_ids.add((ID_RE.search(offer) or [None, ""])[1])
        result.extend(picked)
        print(f"  +{len(picked):3d}  {label:28s} (найдено {len(pool)})")

    date = (re.search(r'<yml_catalog date="([^"]*)"', showcase_raw) or [None, ""])[1]
    body = "\n".join(result)
    out_text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<yml_catalog date="{date}">\n<shop>\n<offers>\n'
        f"{body}\n"
        "</offers>\n</shop>\n</yml_catalog>\n"
    )
    out_path = Path(args.out)
    out_path.write_text(out_text, encoding="utf-8")

    cats = Counter(field(o, "category") for o in result)
    print(f"\nитого: {len(result)} позиций в {len(cats)} категориях → {out_path}")
    for name, count in cats.most_common():
        print(f"  {count:4d}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
