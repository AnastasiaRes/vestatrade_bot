#!/usr/bin/env python3
"""Production-like HTTP evaluator for the Vesta Trading assistant.

The runner never opens product URLs.  The XML/YML feed is parsed locally and is
used as static ground truth, while every assistant response is obtained through
the configured HTTP ``/chat`` endpoint.

Examples::

    BOT_API_BASE_URL=http://127.0.0.1:8000 \
      .venv/bin/python run_bot_evaluation.py --suite smoke --output-dir /tmp/vesta-smoke

    BOT_API_BASE_URL=https://bot-api-vestatrade.ru \
      .venv/bin/python run_bot_evaluation.py --suite all

Environment variables:

``BOT_API_BASE_URL`` (default ``http://127.0.0.1:8000``),
``BOT_API_CHAT_PATH``, ``BOT_API_HEALTH_PATH``, ``BOT_EVAL_TIMEOUT_SECONDS``,
``BOT_EVAL_PAUSE_SECONDS``, ``BOT_CATALOG_XML_PATH``, and
``BOT_EVAL_OUTPUT_DIR``.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent
FORBIDDEN_HTTP_HOSTS = {"vestatrade.ru", "www.vestatrade.ru"}
SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|secret|access[_-]?token|bearer)",
    re.IGNORECASE,
)
SKU_TOKEN_RE = re.compile(r"(?<!\w)([A-ZА-Я0-9][A-ZА-Я0-9._/\-]{2,})(?!\w)", re.I)
URL_RE = re.compile(r"https?://[^\s<>()\]\[\"']+", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return re.sub(r"\s+", " ", text).strip()


def norm(value: Any) -> str:
    text = clean_text(value).casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", text).strip()


def norm_sku(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", clean_text(value).casefold().replace("ё", "е"))


def redact(value: Any) -> Any:
    """Recursively remove credentials before any evaluator artifact is written."""
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            output[str(key)] = "***REDACTED***" if SECRET_KEY_RE.search(str(key)) else redact(item)
        return output
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(
            r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
            r"\1***REDACTED***",
            value,
        )
        value = re.sub(
            r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+",
            r"\1***REDACTED***",
            value,
        )
    return value


def canonical_param_value(key: str, value: str) -> str:
    """Normalise feed wording so equivalent values compare equal.

    The same thread is written as "С внутренней резьбой (ff)" for VALTEC and
    plainly as "Внутренняя" for other brands.  Comparing the raw strings would
    report a mismatch that does not exist in the product itself.
    """

    text = norm(value)
    if "резьб" not in norm(key):
        return text
    internal = "внутренн" in text or bool(re.search(r"\bff\b", text))
    external = "наружн" in text or bool(re.search(r"\bmm\b", text))
    if re.search(r"\b(?:fm|mf)\b", text):
        internal = external = True
    if internal and external:
        return "thread:fm"
    if internal:
        return "thread:ff"
    if external:
        return "thread:mm"
    return text


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass
class CatalogProduct:
    offer_id: str
    sku: str
    name: str
    category: str = ""
    vendor: str = ""
    manufacturer: str = ""
    country_of_origin: str = ""
    price: float | None = None
    price_min: float | None = None
    quantity: int | None = None
    description: str = ""
    params: dict[str, str] = field(default_factory=dict)
    url: str = ""
    raw_fields: dict[str, list[str]] = field(default_factory=dict)

    @property
    def blob(self) -> str:
        return norm(
            " ".join(
                [
                    self.name,
                    self.category,
                    self.vendor,
                    self.manufacturer,
                    self.country_of_origin,
                    self.description,
                    *[f"{key} {value}" for key, value in self.params.items()],
                ]
            )
        )

    def param(self, *needles: str) -> str:
        normalized = [(norm(key), value) for key, value in self.params.items()]
        for needle in needles:
            needle_norm = norm(needle)
            for key_norm, value in normalized:
                if key_norm == needle_norm:
                    return clean_text(value)
            for key_norm, value in normalized:
                if needle_norm in key_norm:
                    return clean_text(value)
        return ""


class Catalog:
    def __init__(self, products: list[CatalogProduct], path: Path) -> None:
        self.path = path
        self.products = products
        self.by_sku: dict[str, CatalogProduct] = {}
        self.sku_conflicts: dict[str, list[CatalogProduct]] = defaultdict(list)
        self.url_set: set[str] = set()
        for product in products:
            key = norm_sku(product.sku)
            if key:
                self.sku_conflicts[key].append(product)
                self.by_sku.setdefault(key, product)
            if product.url:
                self.url_set.add(product.url.rstrip(".,;"))

    @classmethod
    def from_xml(cls, path: Path) -> "Catalog":
        products: list[CatalogProduct] = []
        offer_tags = {"offer", "product", "item"}
        for event, elem in ET.iterparse(path, events=("end",)):
            tag = elem.tag.rsplit("}", 1)[-1].casefold()
            if tag not in offer_tags:
                continue
            fields: dict[str, list[str]] = defaultdict(list)
            params: dict[str, str] = {}
            for child in list(elem):
                child_tag = child.tag.rsplit("}", 1)[-1]
                value = clean_text(child.text)
                if child_tag.casefold() == "param":
                    key = clean_text(child.attrib.get("name")) or "param"
                    if value:
                        params[key] = value
                elif value:
                    fields[child_tag.casefold()].append(value)

            def first(*keys: str) -> str:
                for key in keys:
                    values = fields.get(key.casefold()) or []
                    if values:
                        return values[0]
                return ""

            vendor_code = first("vendorCode", "vendor_code", "sku", "article", "articul")
            param_article = next(
                (value for key, value in params.items() if norm(key) == "артикул"),
                "",
            )
            sku = vendor_code or param_article or clean_text(elem.attrib.get("id"))
            name = first("name", "title", "model") or next(
                (value for key, value in params.items() if norm(key) == "полное наименование"),
                "",
            )
            if sku and name:
                products.append(
                    CatalogProduct(
                        offer_id=clean_text(elem.attrib.get("id")),
                        sku=sku,
                        name=name,
                        category=first("category", "category_path", "categorypath"),
                        vendor=first("vendor", "brand"),
                        manufacturer=first("manufacturer"),
                        country_of_origin=first("country_of_origin", "country"),
                        price=_to_float(first("price")),
                        price_min=_to_float(first("priceMin", "price_min")),
                        quantity=_to_int(first("quantity", "stock_quantity", "stock", "available")),
                        description=first("description", "short_description"),
                        params=params,
                        url=first("url", "link", "product_url"),
                        raw_fields=dict(fields),
                    )
                )
            elem.clear()
        if not products:
            raise RuntimeError(f"No products parsed from {path}")
        return cls(products, path)

    def get(self, sku: Any) -> CatalogProduct | None:
        return self.by_sku.get(norm_sku(sku))

    def exact_skus_in_text(self, text: str) -> list[str]:
        token_keys = {
            norm_sku(token)
            for token in SKU_TOKEN_RE.findall(clean_text(text))
            if any(char.isdigit() for char in token)
        }
        text_casefold = clean_text(text).casefold()
        matches: list[tuple[int, str, str]] = []
        for key, product in self.by_sku.items():
            literal = clean_text(product.sku).casefold()
            if key in token_keys or (len(key) >= 6 and literal and literal in text_casefold):
                matches.append((len(key), key, product.sku))
        matches.sort(reverse=True)
        result: list[str] = []
        accepted_keys: list[str] = []
        for _, key, sku in matches:
            if any(key in accepted for accepted in accepted_keys):
                continue
            if norm_sku(sku) not in {norm_sku(item) for item in result}:
                result.append(sku)
                accepted_keys.append(key)
        return result[:5]

    def find(self, *needles: str, in_stock: bool | None = None) -> CatalogProduct | None:
        normalized = [norm(item) for item in needles if item]
        candidates = [
            product
            for product in self.products
            if all(needle in product.blob for needle in normalized)
            and (
                in_stock is None
                or (product.quantity is not None and (product.quantity > 0) == in_stock)
            )
        ]
        candidates.sort(
            key=lambda product: (
                -(len(product.params)),
                -(product.quantity or 0),
                len(product.name),
            )
        )
        return candidates[0] if candidates else None

    def family_key(self, product: CatalogProduct) -> str:
        text = norm(product.name)
        text = re.sub(r"\b(?:dn\s*)?\d+(?:[.,/]\d+)?\b", " ", text)
        text = re.sub(r"\b(?:вн|нар|вр|нр|ff|fm|mf|mm)\b", " ", text)
        text = re.sub(r"\b(?:прямой|прямая|угловой|угловая)\b", " форма ", text)
        return re.sub(r"\s+", " ", text).strip()

    def similar_families(self, limit: int = 25) -> list[list[CatalogProduct]]:
        groups: dict[str, list[CatalogProduct]] = defaultdict(list)
        for product in self.products:
            key = self.family_key(product)
            if len(key) >= 10:
                groups[key].append(product)
        families = [group for group in groups.values() if len(group) >= 2]
        families.sort(
            key=lambda group: (
                -min(len(group), 20),
                -max(len(item.params) for item in group),
                min(len(item.name) for item in group),
            )
        )
        return families[:limit]

    def analysis(self) -> dict[str, Any]:
        categories = Counter(product.category or "<empty>" for product in self.products)
        brands = Counter(product.vendor or product.manufacturer or "<empty>" for product in self.products)
        countries = Counter(product.country_of_origin or "<empty>" for product in self.products)
        types = Counter(
            product.param("тип товара") or "<empty>" for product in self.products
        )
        param_names = Counter(key for product in self.products for key in product.params)
        families = self.similar_families(25)
        rich = sorted(self.products, key=lambda item: len(item.params), reverse=True)[:15]
        return {
            "xml_path": str(self.path.resolve()),
            "raw_offers": len(self.products),
            "unique_skus": len(self.by_sku),
            "duplicate_or_conflicting_sku_keys": sum(
                1 for group in self.sku_conflicts.values() if len(group) > 1
            ),
            "zero_stock": sum(product.quantity == 0 for product in self.products),
            "positive_stock": sum((product.quantity or 0) > 0 for product in self.products),
            "unknown_stock": sum(product.quantity is None for product in self.products),
            "categories_top": categories.most_common(25),
            "brands_top": brands.most_common(25),
            "countries_top": countries.most_common(15),
            "product_types_top": types.most_common(30),
            "param_names_top": param_names.most_common(35),
            "rich_products": [
                {"sku": item.sku, "name": item.name, "param_count": len(item.params)}
                for item in rich
            ],
            "similar_families": [
                [
                    {"sku": item.sku, "name": item.name}
                    for item in family[:8]
                ]
                for family in families
            ],
        }


def _to_float(value: str) -> float | None:
    match = re.search(r"\d+(?:[.,]\d+)?", value.replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _to_int(value: str) -> int | None:
    match = re.search(r"-?\d+", value.replace(" ", ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


@dataclass
class Scenario:
    scenario_id: str
    title: str
    persona: str
    initial: str
    strategy: str
    max_turns: int = 3
    params: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    repeat_group: str | None = None


def choose_catalog_fixtures(catalog: Catalog) -> dict[str, CatalogProduct]:
    known = {
        "valve_butterfly_half_mm": catalog.get("VT.226.N.04"),
        "valve_butterfly_three_quarter_mm": catalog.get("VT.226.N.05"),
        "radiator_straight": catalog.get("VT.048.N.04"),
    }
    fixtures: dict[str, CatalogProduct] = {}
    for key, product in known.items():
        if product:
            fixtures[key] = product

    fallback_queries = {
        "valve_half_ff": ("кран шаровой", "1/2", "внутренней резьбой"),
        "valve_half_fm": ("кран шаровой", "1/2", "внутренней наружной"),
        "ppr_elbow": ("угольник", "ppr", "20", "1/2"),
        "radiator_angle": ("термостатической головкой", "угловой", "1/2"),
        "sewer_pipe": ("канализационные", "труба", "50"),
        "ppr_rich": ("ppr", "20"),
    }
    for key, query in fallback_queries.items():
        product = catalog.find(*query)
        if product:
            fixtures[key] = product

    if "radiator_straight" not in fixtures:
        product = catalog.find("термостатической головкой", "прямой", "1/2")
        if product:
            fixtures["radiator_straight"] = product

    out_of_stock = next(
        (
            product
            for product in sorted(catalog.products, key=lambda item: len(item.params), reverse=True)
            if product.quantity == 0 and len(norm_sku(product.sku)) >= 5 and product.url
        ),
        None,
    )
    if out_of_stock:
        fixtures["out_of_stock"] = out_of_stock

    rich = next(
        (
            product
            for product in sorted(catalog.products, key=lambda item: len(item.params), reverse=True)
            if product.quantity and product.quantity > 0 and product.url and len(norm_sku(product.sku)) >= 5
        ),
        None,
    )
    if rich:
        fixtures["rich"] = rich

    family = next(
        (
            group
            for group in catalog.similar_families(100)
            if len({norm_sku(product.sku) for product in group}) >= 2
            and any((product.quantity or 0) > 0 for product in group)
        ),
        None,
    )
    if family:
        fixtures["similar_a"] = family[0]
        fixtures["similar_b"] = family[1]

    # Every generated scenario needs a real identity even on an unusual feed.
    default = rich or catalog.products[0]
    required = [
        "valve_butterfly_half_mm",
        "valve_butterfly_three_quarter_mm",
        "valve_half_ff",
        "valve_half_fm",
        "ppr_elbow",
        "radiator_straight",
        "radiator_angle",
        "sewer_pipe",
        "ppr_rich",
        "out_of_stock",
        "rich",
        "similar_a",
        "similar_b",
    ]
    for key in required:
        fixtures.setdefault(key, default)
    return fixtures


def build_scenarios(catalog: Catalog) -> tuple[list[Scenario], list[Scenario], dict[str, Any]]:
    fx = choose_catalog_fixtures(catalog)
    rich = fx["rich"]
    similar_a = fx["similar_a"]
    similar_b = fx["similar_b"]
    out = fx["out_of_stock"]

    smoke = [
        Scenario(
            "S01", "Обычный покупатель: кран 1/2", "Новичок", "Нужен кран на воду полдюйма",
            "novice_valve", 3, {"expect_clarify_first": True}, ["novice", "valves"],
        ),
        Scenario(
            "S02", "Обычный покупатель: уголок на трубу 20", "Новичок",
            "Нужен уголок на пластиковую трубу 20", "generic", 3,
            {"details": ["Труба PPR, нужен угол 90 градусов.", "Резьбовой выход не нужен, обе стороны под сварку 20 мм."], "expect_clarify_first": True},
            ["novice", "fittings"],
        ),
        Scenario(
            "S03", "Регулятор температуры на батарею", "Новичок",
            "Хочу поставить регулятор температуры на батарею", "generic", 3,
            {"details": ["Подключение 1/2, нужен клапан с термоголовкой.", "Нужен прямой вариант."], "expect_clarify_first": True},
            ["novice", "radiator"],
        ),
        Scenario(
            "S04", "Монтажник: полнопроходной ВР-НР", "Монтажник",
            "Нужен шаровой полнопроходной 3/4 ВР-НР для воды", "constraints", 2,
            {"verify": "Этот вариант точно 3/4, ВР-НР и полнопроходной?"},
            ["installer", "similar_sku"],
        ),
        Scenario(
            "S05", "Монтажник: PPR угол 20x1/2 НР", "Монтажник",
            "PPR угол 20×1/2 НР", "constraints", 2,
            {"verify": "Проверьте: сторона 20 под сварку, резьба 1/2 именно наружная?"},
            ["installer", "fittings"],
        ),
        Scenario(
            "S06", "Неполный запрос: клапан", "Неопытный покупатель",
            "Нужен клапан", "incomplete", 3,
            {"details": ["Для радиатора, хочу регулировать температуру.", "Прямой, подключение 1/2."], "expect_clarify_first": True},
            ["clarification"],
        ),
        Scenario(
            "S07", "Разговорная резьба мама-мама", "Покупатель с жаргоном",
            "кран пол дюйма мама мама на воду", "generic", 3,
            {"details": ["Да, внутренняя резьба с обеих сторон.", "Ручка бабочка."], "expect_clarify_first": False},
            ["typo", "thread"],
        ),
        Scenario(
            "S08", "Изменение ВР-ВР на ВР-НР", "Монтажник",
            "Нужен шаровой кран 1/2 ВР-ВР для воды", "correction_thread", 3,
            {}, ["correction", "context"],
        ),
        Scenario(
            "S09", "Поиск по реальному артикулу", "Покупатель с артикулом",
            rich.sku, "exact", 3, {"sku": rich.sku}, ["sku"],
        ),
        Scenario(
            "S10", "Контекст: размер, ручка, возврат", "Покупатель сравнивает",
            "Покажи кран 1/2 для воды", "context", 5,
            {}, ["context", "similar_sku"],
        ),
    ]

    core: list[Scenario] = []
    for idx in range(3):
        core.append(
            Scenario(
                f"C-SIM-{idx+1}", f"Повтор похожих SKU #{idx+1}", "Монтажник",
                f"Сравни {similar_a.sku} и {similar_b.sku}. Не перепутай артикулы.",
                "similar", 3,
                {"sku_a": similar_a.sku, "sku_b": similar_b.sku},
                ["similar_sku", "repeat"], "similar_sku",
            )
        )
    incomplete_prompts = ["Нужен клапан", "Нужен фитинг 20", "Что-нибудь на батарею"]
    incomplete_details = [
        ["Для радиатора отопления.", "Термостатический, прямой, 1/2."],
        ["Система PPR.", "Угол 90 градусов, обе стороны 20 мм."],
        ["Хочу регулировать температуру.", "Клапан с термоголовкой, угловой, 1/2."],
    ]
    for idx, prompt in enumerate(incomplete_prompts):
        core.append(
            Scenario(
                f"C-INC-{idx+1}", f"Повтор неполного запроса #{idx+1}", "Новичок",
                prompt, "incomplete", 3,
                {"details": incomplete_details[idx], "expect_clarify_first": True},
                ["clarification", "repeat"], "incomplete_request",
            )
        )
    for idx in range(3):
        core.append(
            Scenario(
                f"C-ALT-{idx+1}", f"Повтор запроса аналога #{idx+1}", "Покупатель ищет аналог",
                f"Покажи товар по артикулу {rich.sku}", "analog", 3,
                {"sku": rich.sku}, ["alternative", "repeat"], "analog",
            )
        )
    corrections = [
        ("correction_thread", "Нужен шаровой кран 1/2 ВР-ВР для воды"),
        ("correction_size", "Нужен шаровой кран 1/2 для воды, ВР-НР"),
        ("correction_form", "Нужен термостатический клапан прямой 1/2 для радиатора"),
    ]
    for idx, (strategy, prompt) in enumerate(corrections):
        core.append(
            Scenario(
                f"C-COR-{idx+1}", f"Повтор исправления требования #{idx+1}", "Покупатель исправляет себя",
                prompt, strategy, 3, {}, ["correction", "repeat"], "correction",
            )
        )
    for idx in range(3):
        core.append(
            Scenario(
                f"C-CTX-{idx+1}", f"Повтор multi-turn context #{idx+1}", "Покупатель сравнивает",
                "Покажи кран 1/2 для воды", "context", 5, {},
                ["context", "repeat"], "multi_turn_context",
            )
        )

    no_dots = re.sub(r"[^A-Za-zА-Яа-я0-9]", "", rich.sku)
    typo = _make_sku_typo(rich.sku, catalog)
    extras = [
        Scenario("C01", "Точный артикул", "Закупщик", f"Найди артикул {rich.sku}", "exact", 3, {"sku": rich.sku}, ["sku"]),
        Scenario("C02", "Артикул без точек", "Закупщик", f"Найди артикул {no_dots}", "exact", 3, {"sku": rich.sku}, ["sku", "normalization"]),
        Scenario("C03", "Опечатка в артикуле", "Закупщик", f"Найди артикул {typo}", "sku_typo", 3, {"sku": rich.sku}, ["sku", "typo"]),
        Scenario("C04", "Артикул и характеристика", "Монтажник", rich.sku, "exact", 3, {"sku": rich.sku}, ["factuality"]),
        Scenario("C05", "Кран 1/2 против 3/4", "Монтажник", "Нужен кран BASE с полусгоном 1/2 наружная-наружная, бабочка, для воды", "constraints", 2, {"verify": "Не меняйте размер: нужен именно 1/2. Назовите точный артикул."}, ["similar_sku"]),
        Scenario("C06", "PPR 20 против 25", "Монтажник", "Нужен PPR угол 20 мм, обе стороны под сварку, 90 градусов", "constraints", 2, {"verify": "Мне точно 20, не 25. Какой артикул?"}, ["similar_sku"]),
        Scenario("C07", "Прямой против углового", "Монтажник", "Термостатический клапан для радиатора угловой 1/2", "constraints", 2, {"verify": "Проверьте, что он угловой, а не прямой."}, ["similar_sku"]),
        Scenario("C08", "Канализация 50 против 110", "Монтажник", "Нужна внутренняя канализационная труба 50 мм", "generic", 3, {"details": ["Длина 1000 мм.", "Именно внутренняя, серая."], "expect_clarify_first": True}, ["sewer", "similar_sku"]),
        Scenario("C09", "Канализация: длина", "Монтажник", "Внутренняя канализационная труба 50 мм длиной 1500 мм", "constraints", 2, {"verify": "Не 1000 и не 2000 мм — нужна 1500. Есть точное совпадение?"}, ["sewer", "similar_sku"]),
        Scenario("C10", "Дешевле", "Экономный покупатель", "Покажи два шаровых крана 1/2 ВР-ВР для воды", "cheaper", 3, {}, ["comparison", "price"]),
        Scenario("C11", "Та же модель с бабочкой", "Покупатель", "Нужен шаровой кран 1/2 ВР-ВР для воды с рычагом", "butterfly", 3, {}, ["alternative", "handle"]),
        Scenario("C12", "Бренд VALTEC", "Монтажник", "валтек 3/4 бабочка шаровой кран для воды", "constraints", 2, {"verify": "Проверьте бренд VALTEC, размер 3/4 и ручку бабочка."}, ["brand", "typo"]),
        Scenario("C13", "Опечатка термоголовка", "Новичок", "термогаловка на батарею 1/2", "generic", 3, {"details": ["Нужна вместе с клапаном.", "Клапан прямой."], "expect_clarify_first": True}, ["typo", "radiator"]),
        Scenario("C14", "Цена точного SKU", "Закупщик", f"Сколько стоит {rich.sku}?", "exact_dynamic", 2, {"sku": rich.sku}, ["price", "dynamic"]),
        Scenario("C15", "Остаток точного SKU", "Закупщик", f"Сколько в наличии {rich.sku}?", "exact_dynamic", 2, {"sku": rich.sku}, ["stock", "dynamic"]),
        Scenario("C16", "Нулевой остаток", "Закупщик", f"Есть ли в наличии {out.sku}?", "exact_dynamic", 2, {"sku": out.sku}, ["stock", "zero_stock"]),
        Scenario("C17", "Несуществующий артикул", "Закупщик", "Найди товар по артикулу VT.QA.NOT-EXIST.999", "no_match", 2, {}, ["hallucination", "no_match"]),
        Scenario("C18", "Пять жёстких параметров", "Монтажник", "Нужен прямой полнопроходной шаровой кран VALTEC 1/2 наружная-наружная с бабочкой для воды", "constraints", 2, {"verify": "Перечислите, где подтверждены все параметры: 1/2, НР-НР, бабочка, прямой, полнопроходной."}, ["constraints", "similar_sku"]),
        Scenario("C19", "Точного сочетания нет", "Монтажник", "Нужен PPR угол 20×1/2 с наружной резьбой, но угол строго 45 градусов", "no_exact_combo", 2, {}, ["no_match", "constraints"]),
        Scenario("C20", "Непрофессиональная речь", "Новичок", "мне штука на батарею чтоб сама жар убавляла", "generic", 3, {"details": ["Подключение полдюйма.", "Нужен угловой клапан с головкой."], "expect_clarify_first": True}, ["novice", "radiator"]),
        Scenario("C21", "Монтажный жаргон", "Монтажник", "шаровый 3/4 мама-папа полнопроход, бабочка, вода", "constraints", 2, {"verify": "Этот SKU точно ВР-НР, а не ВР-ВР?"}, ["jargon", "constraints"]),
        Scenario("C22", "Спор с неподходящим вариантом", "Требовательный покупатель", "Нужен кран 1/2 ВР-ВР для воды", "argue", 3, {}, ["correction", "challenge"]),
        Scenario("C23", "Возврат к предыдущему SKU", "Покупатель", f"Покажи {similar_a.sku}", "return_previous", 4, {"sku_a": similar_a.sku, "sku_b": similar_b.sku}, ["context", "sku"]),
        Scenario("C24", "Сверка богатой карточки", "Инженер", f"Покажи {rich.sku} и кратко перечисли подтвержденные характеристики", "exact", 3, {"sku": rich.sku}, ["factuality", "rich_product"]),
        Scenario("C25", "XML-generated похожая серия", "Инженер", f"Чем отличаются {similar_a.sku} и {similar_b.sku}?", "similar", 3, {"sku_a": similar_a.sku, "sku_b": similar_b.sku}, ["catalog_generated", "similar_sku"]),
    ]
    core.extend(extras)
    assert len(smoke) == 10, len(smoke)
    assert len(core) == 40, len(core)
    matrix = {
        "fixture_skus": {key: product.sku for key, product in fx.items()},
        "smoke_scenarios": len(smoke),
        "core_scenarios": len(core),
        "repeat_groups": dict(Counter(item.repeat_group for item in core if item.repeat_group)),
    }
    return smoke, core, matrix


IGNORED_DIFF_PARAMS = {
    "артикул",
    "полное наименование",
    "цена",
    "вес",
    "масса",
    "штрихкод",
    "код",
    "гарантия",
    "объем упаковки",
    "единица измерения",
}


def _describe_param(key: str, value: str) -> str:
    return f"{clean_text(key).rstrip(':')}: {clean_text(value)}"


def _one_param_apart(
    family: list[CatalogProduct],
) -> tuple[CatalogProduct, CatalogProduct, str] | None:
    """Return two SKUs of one family separated by exactly one parameter."""

    for index, first in enumerate(family):
        for second in family[index + 1 :]:
            keys = set(first.params) & set(second.params)
            if len(keys) < 3:
                continue
            differing = [
                key
                for key in keys
                if norm(first.params[key]) != norm(second.params[key])
                and norm(key) not in IGNORED_DIFF_PARAMS
                and clean_text(first.params[key])
                and clean_text(second.params[key])
            ]
            if len(differing) == 1 and norm(first.name) != norm(second.name):
                return first, second, differing[0]
    return None


def build_extended_scenarios(catalog: Catalog, limit: int = 60) -> list[Scenario]:
    """Auto-generate a regression suite from the actual catalogue content.

    Two generators are used.  The first walks families of near-identical SKUs
    and asks for one member by its distinguishing parameter, which is the
    highest-risk failure mode (neighbouring size, wrong thread, wrong angle).
    The second checks article lookup and card factuality across categories.
    """

    scenarios: list[Scenario] = []
    seen_pairs: set[tuple[str, str]] = set()
    for family in catalog.similar_families(160):
        pair = _one_param_apart(family)
        if not pair:
            continue
        target, neighbour, diff_key = pair
        key = (norm_sku(target.sku), norm_sku(neighbour.sku))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        context_keys = [
            item
            for item in target.params
            if item != diff_key
            and norm(item) not in IGNORED_DIFF_PARAMS
            and clean_text(target.params[item])
            and len(clean_text(target.params[item])) <= 40
        ][:2]
        described = [diff_key] + context_keys
        kind = target.param("тип товара") or clean_text(target.name).split(",")[0]
        brand = clean_text(target.vendor or target.manufacturer)
        prompt = "Нужен " + ", ".join(
            [part for part in [kind, brand] if part]
            + [_describe_param(item, target.params[item]) for item in described]
        )
        required = {item: target.params[item] for item in described}
        scenarios.append(
            Scenario(
                f"X-SIM-{len(scenarios)+1:02d}",
                f"Каталожная пара: {clean_text(diff_key)} {target.sku} vs {neighbour.sku}",
                "Монтажник",
                prompt,
                "catalog_constraint",
                3,
                {
                    "target_sku": target.sku,
                    "neighbour_sku": neighbour.sku,
                    "diff_key": diff_key,
                    "diff_target": target.params[diff_key],
                    "diff_neighbour": neighbour.params[diff_key],
                    "required_params": required,
                    "remaining": [
                        _describe_param(item, target.params[item])
                        for item in list(target.params)
                        if item not in described
                        and norm(item) not in IGNORED_DIFF_PARAMS
                        and clean_text(target.params[item])
                        and len(clean_text(target.params[item])) <= 40
                    ][:2],
                },
                ["catalog_generated", "similar_sku", "constraints"],
            )
        )
        if len(scenarios) >= max(1, int(limit * 0.6)):
            break

    by_category: dict[str, list[CatalogProduct]] = defaultdict(list)
    for product in catalog.products:
        if len(product.params) >= 4 and (product.quantity or 0) > 0:
            by_category[product.category].append(product)
    ordered = sorted(by_category.items(), key=lambda item: -len(item[1]))
    fact_budget = max(1, limit - len(scenarios))
    picks: list[CatalogProduct] = []
    round_index = 0
    while len(picks) < fact_budget and ordered:
        added = False
        for _, items in ordered:
            if round_index < len(items) and len(picks) < fact_budget:
                picks.append(sorted(items, key=lambda item: -len(item.params))[round_index])
                added = True
        if not added:
            break
        round_index += 1
    for product in picks:
        attribute = choose_attribute(product)
        scenarios.append(
            Scenario(
                f"X-SKU-{len(scenarios)+1:02d}",
                f"Каталожный артикул: {product.sku}",
                "Закупщик",
                f"Найди артикул {product.sku}",
                "catalog_fact",
                3,
                {
                    "sku": product.sku,
                    "attribute_key": attribute[0] if attribute else None,
                    "attribute_value": attribute[1] if attribute else None,
                },
                ["catalog_generated", "sku", "factuality"],
            )
        )
    return scenarios


def _make_sku_typo(sku: str, catalog: Catalog) -> str:
    for replacement in ["Z", "8", "9", "X", "7"]:
        chars = list(sku)
        for index in range(len(chars) - 1, -1, -1):
            if chars[index].isalnum():
                chars[index] = replacement if chars[index].casefold() != replacement.casefold() else "Q"
                break
        candidate = "".join(chars)
        if catalog.get(candidate) is None:
            return candidate
    return sku + "X"


class APIClient:
    def __init__(self, base_url: str, chat_path: str, health_path: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_path = "/" + chat_path.lstrip("/")
        self.health_path = "/" + health_path.lstrip("/")
        self.timeout = timeout
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"Invalid BOT_API_BASE_URL: {base_url!r}")
        if parsed.hostname.casefold() in FORBIDDEN_HTTP_HOSTS:
            raise ValueError(
                "Refusing HTTP requests to vestatrade.ru/www.vestatrade.ru. "
                "Use localhost or bot-api-vestatrade.ru."
            )

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + "/" + path.lstrip("/")
        body = raw_body
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        started = time.perf_counter()
        status: int | None = None
        response_headers: dict[str, str] = {}
        text = ""
        error: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                response_headers = dict(response.headers.items())
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            text = exc.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - transport failures are test data
            error = f"{type(exc).__name__}: {exc}"
        latency = time.perf_counter() - started
        parsed_body: Any = None
        malformed = False
        if text:
            try:
                parsed_body = json.loads(text)
            except json.JSONDecodeError:
                malformed = True
                parsed_body = text[:5000]
        return redact(
            {
                "request": {"method": method, "path": path, "json": payload},
                "status_code": status,
                "response_headers": {
                    key: value
                    for key, value in response_headers.items()
                    if key.casefold() in {"content-type", "content-length", "server", "date"}
                },
                "response_json": parsed_body,
                "malformed_json": malformed,
                "latency_sec": round(latency, 4),
                "error": error,
            }
        )

    def health(self) -> dict[str, Any]:
        return self.request("GET", self.health_path)

    def chat(self, session_id: str, message: str) -> dict[str, Any]:
        return self.request(
            "POST",
            self.chat_path,
            {"session_id": session_id, "message": message},
        )


def response_products(technical: dict[str, Any]) -> list[dict[str, Any]]:
    body = technical.get("response_json")
    if not isinstance(body, dict):
        return []
    products = body.get("products")
    return products if isinstance(products, list) else []


def response_answer(technical: dict[str, Any]) -> str:
    body = technical.get("response_json")
    return clean_text(body.get("answer")) if isinstance(body, dict) else ""


def response_debug(technical: dict[str, Any]) -> dict[str, Any]:
    body = technical.get("response_json")
    debug = body.get("debug") if isinstance(body, dict) else None
    return debug if isinstance(debug, dict) else {}


def looks_like_question(answer: str) -> bool:
    text = norm(answer)
    return "?" in answer or any(
        marker in text
        for marker in [
            "уточните", "подскажите", "напишите", "какая нужна", "какой нужен", "для чего",
            "какое подключение", "какая резьба", "нужен прямой", "нужен угловой",
        ]
    )


def clarification_topics(answer: str) -> set[str]:
    """Identify the engineering subjects that a question actually asks."""

    text = norm(answer)
    patterns = {
        "application": ["для чего", "назначение", "вода", "отопление", "радиатор"],
        "size": ["размер", "диаметр", "dn", "дюйм", "1 2", "3 4"],
        "thread": ["резьб", "вр", "нр", "внутрен", "наруж"],
        "handle": ["ручк", "бабоч", "рычаг"],
        "form": ["прям", "углов"],
        "angle": ["угол", "градус"],
        "system": ["ppr", "ппр", "полипроп", "pex", "пнд", "канализац"],
        "kind": ["что именно", "тип товар", "тип фитинг", "кран или", "клапан или"],
        "length": ["длина", "метр"],
        "radiator_type": ["тип радиатор", "биметалл", "алюмини", "панельн"],
        "action": ["регулировать", "перекрывать", "регулиров", "перекрыт"],
    }
    return {
        topic
        for topic, markers in patterns.items()
        if any(marker in text for marker in markers)
    }


def clarification_is_relevant(
    answer: str,
    constraints: dict[str, Any],
    debug: dict[str, Any],
) -> bool:
    """Reject questions unrelated to the requested product and known facts."""

    asked = clarification_topics(answer)
    if not asked:
        return False

    product_kind = constraints.get("product_kind")
    category = clean_text(debug.get("category"))
    if product_kind == "ball_valve":
        category = "valves"
    elif product_kind == "thermostatic_radiator_valve":
        category = "radiator_fittings"
    elif product_kind == "elbow":
        category = "fittings"
    elif product_kind == "sewer_pipe":
        category = "sewer"

    allowed_by_category = {
        "valves": {"application", "size", "thread", "handle", "form"},
        "fittings": {"system", "kind", "size", "thread", "angle"},
        "radiator_fittings": {"kind", "size", "thread", "form"},
        "sewer": {"kind", "size", "length", "angle", "system"},
        "pipes": {"system", "size", "length", "application"},
        "radiators": {"radiator_type", "size", "application"},
    }
    allowed = allowed_by_category.get(category)
    if not allowed:
        return True

    known: set[str] = set()
    if constraints.get("application"):
        known.add("application")
    if constraints.get("size_inch") or constraints.get("diameter_mm"):
        known.add("size")
    if constraints.get("thread"):
        known.add("thread")
    if constraints.get("handle"):
        known.add("handle")
    if constraints.get("form"):
        known.add("form")
    if constraints.get("system"):
        known.add("system")
    if constraints.get("length_mm"):
        known.add("length")
    if constraints.get("product_kind"):
        known.add("kind")

    return bool(asked & (allowed - known))


def alternative_discloses_mismatch(answer: str, mismatch: str) -> bool:
    """An alternative is safe only when every relaxed hard field is named."""

    text = norm(answer)
    if not any(
        marker in text
        for marker in ["отлич", "вместо", "но ", "не совпад", "ближайш"]
    ):
        return False
    key = mismatch.split("=", 1)[0]
    markers = {
        "size_inch": ["размер", "диаметр", "дюйм"],
        "diameter_mm": ["размер", "диаметр"],
        "length_mm": ["длина"],
        "thread": ["резьб", "вр", "нр", "внутрен", "наруж"],
        "handle": ["ручк", "бабоч", "рычаг"],
        "form": ["форм", "прям", "углов"],
        "full_bore": ["полнопроход", "проход"],
        "system": ["система", "ppr", "ппр", "pex", "пнд"],
        "product_kind": ["тип", "кран", "клапан", "голов", "угольник"],
        "sewer_kind": ["канализац", "внутрен", "наруж"],
    }
    return any(
        marker in text for marker in markers.get(key, [key.replace("_", " ")])
    )


def answer_identifies_cheapest(answer: str, cheapest_skus: list[str]) -> bool:
    text = clean_text(answer).casefold().replace("ё", "е")
    for sku in cheapest_skus:
        escaped = re.escape(clean_text(sku).casefold())
        if re.search(
            rf"(?:дешев\w*|выгод\w*|рекоменд\w*)[^.!?\n]{{0,180}}{escaped}", text
        ):
            return True
        if re.search(
            rf"{escaped}[^.!?\n]{{0,180}}(?:дешев\w*|выгод\w*|рекоменд\w*)", text
        ):
            return True
    return False


def choose_attribute(product: CatalogProduct) -> tuple[str, str] | None:
    priorities = [
        "тип резьбы", "диаметр подключения", "тип ручки", "форма корпуса",
        "пропускная способность", "материал корпуса", "назначение", "тип присоединения",
    ]
    for priority in priorities:
        for key, value in product.params.items():
            if priority in norm(key) and value:
                return key, value
    for key, value in product.params.items():
        if norm(key) not in {"артикул", "штрихкод", "полное наименование"} and value:
            return key, value
    return None


def next_message(
    scenario: Scenario,
    transcript: list[dict[str, Any]],
    catalog: Catalog,
    runtime: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    turn = len(transcript)
    last_technical = transcript[-1]["technical"]
    products = response_products(last_technical)
    answer = response_answer(last_technical)
    asked = looks_like_question(answer) and not products

    def shown_sku(index: int = 0) -> str | None:
        if len(products) > index:
            return clean_text(products[index].get("sku"))
        return None

    if products and not runtime.get("first_product_sku"):
        runtime["first_product_sku"] = shown_sku()

    if scenario.strategy in {"generic", "incomplete"}:
        details = list(scenario.params.get("details") or [])
        index = min(turn - 1, max(0, len(details) - 1))
        if turn <= len(details):
            if products and turn == 1:
                message = "Подождите, вы предложили товар до уточнения важных параметров. " + details[0]
            else:
                message = details[index]
            return message, {}
        return None

    if scenario.strategy == "novice_valve":
        if turn == 1:
            if products:
                return "Этот точно полдюйма и с внутренней резьбой с обеих сторон? Мне нужен ВР-ВР.", {}
            return "Для холодной воды. Резьба внутренняя с обеих сторон.", {}
        if turn == 2:
            return "А есть такой же с ручкой-бабочкой?", {}
        return None

    if scenario.strategy == "constraints":
        if turn == 1:
            return scenario.params.get("verify", "Этот товар точно соблюдает все мои параметры?"), {}
        return None

    if scenario.strategy.startswith("correction_"):
        if turn == 1:
            messages = {
                "correction_thread": "Нет, перепутал: нужен ВР-НР, остальные параметры те же.",
                "correction_size": "Нет, перепутал размер: нужен 3/4, остальные параметры те же.",
                "correction_form": "Нет, перепутал: нужен угловой, а не прямой, остальные параметры те же.",
            }
            return messages[scenario.strategy], {"is_correction": True}
        if turn == 2:
            checks = {
                "correction_thread": "Подтвердите текущую резьбу ВР-НР.",
                "correction_size": "Подтвердите текущий размер 3/4.",
                "correction_form": "Подтвердите текущую угловую форму.",
            }
            return checks[scenario.strategy], {"is_context": True}
        return None

    if scenario.strategy in {"exact", "exact_dynamic"}:
        target = catalog.get(scenario.params.get("sku"))
        if turn == 1:
            if target and scenario.strategy == "exact":
                attribute = choose_attribute(target)
                if attribute:
                    key, value = attribute
                    return (
                        f"Какая у этого товара характеристика «{key}»? Назовите артикул.",
                        {"expected_skus": [target.sku], "expected_answer_value": value, "is_context": True},
                    )
            return "Назовите точный артикул, цену и наличие именно этого товара.", {"expected_skus": [scenario.params.get("sku")], "dynamic_only": True, "is_context": True}
        if turn == 2 and scenario.strategy == "exact":
            return "Сколько он стоит и есть ли в наличии? Не меняйте товар.", {"expected_skus": [scenario.params.get("sku")], "dynamic_only": True, "is_context": True}
        return None

    if scenario.strategy == "sku_typo":
        if turn == 1:
            if products:
                return "Какой точный артикул у найденного товара?", {"is_context": True}
            return f"Исправляю: точный артикул {scenario.params['sku']}", {"expected_skus": [scenario.params["sku"]]}
        if turn == 2:
            return "Какая у него основная характеристика по карточке?", {"expected_skus": [scenario.params["sku"]], "is_context": True}
        return None

    if scenario.strategy == "similar":
        a = scenario.params["sku_a"]
        b = scenario.params["sku_b"]
        if turn == 1:
            return "Чем отличаются эти два именно по статическим характеристикам?", {"expected_any_skus": [a, b], "is_context": True}
        if turn == 2:
            return "Вернемся к первому. Какой у него артикул?", {"expected_skus": [a], "expected_answer_value": a, "is_context": True}
        return None

    if scenario.strategy == "analog":
        if turn == 1:
            return "Есть максимально близкий аналог? Четко назовите, чем он отличается.", {"is_context": True, "expects_alternative": True}
        if turn == 2:
            return "Сравните аналог с исходным товаром, не смешивая артикулы.", {"expected_any_skus": [scenario.params["sku"]], "is_context": True}
        return None

    if scenario.strategy == "context":
        stage = runtime.get("context_stage", "initial")
        if stage == "initial":
            if asked:
                runtime["context_stage"] = "specified"
                return "Для холодной воды, ВР-ВР, ручка рычаг.", {}
            runtime["context_stage"] = "size_changed"
            return "А такой же 3/4?", {"is_context": True}
        if stage == "specified":
            runtime["context_stage"] = "size_changed"
            return "А такой же 3/4?", {"is_context": True}
        if stage == "size_changed":
            runtime["context_stage"] = "handle_changed"
            return "А с бабочкой?", {"is_context": True}
        if stage == "handle_changed":
            runtime["context_stage"] = "returned"
            first = runtime.get("first_product_sku")
            return "Вернемся к первому показанному товару. Какой у него артикул?", {"expected_skus": [first] if first else [], "expected_answer_value": first, "is_context": True}
        return None

    if scenario.strategy == "cheaper":
        if turn == 1:
            return "Чем отличаются эти варианты?", {"is_context": True}
        if turn == 2:
            return "Какой дешевле? Назовите его точный артикул.", {"is_context": True, "dynamic_only": True}
        return None

    if scenario.strategy == "butterfly":
        if turn == 1:
            return "А есть такой же, но с бабочкой? Остальные параметры не менять.", {"is_context": True, "expects_alternative": True}
        if turn == 2:
            return "Сверьте резьбу и размер у варианта с бабочкой.", {"is_context": True}
        return None

    if scenario.strategy in {"no_match", "no_exact_combo"}:
        if turn == 1:
            return "Точного совпадения действительно нет? Не выдавайте близкий вариант за точный.", {"expect_no_exact_match": True, "is_context": True}
        return None

    if scenario.strategy == "argue":
        if turn == 1:
            return "Этот вариант кажется неподходящим: мне нужна внутренняя резьба с обеих сторон. Проверьте еще раз.", {"is_correction": True}
        if turn == 2:
            return "Признайте ошибку, если прошлый SKU был ВР-НР, и дайте корректный ВР-ВР.", {"is_context": True}
        return None

    if scenario.strategy == "catalog_constraint":
        required = scenario.params.get("required_params") or {}
        if turn == 1:
            if not products and asked:
                remaining = scenario.params.get("remaining") or []
                extra = ("; ".join(remaining)) if remaining else "Других ограничений нет."
                return (
                    f"{extra} Параметры из запроса не меняем.",
                    {"required_params": required},
                )
            return (
                "Проверьте по карточке: «{key}» должен быть именно {target}, а не {other}. "
                "Назовите точный артикул подходящего товара.".format(
                    key=clean_text(scenario.params["diff_key"]),
                    target=clean_text(scenario.params["diff_target"]),
                    other=clean_text(scenario.params["diff_neighbour"]),
                ),
                {"required_params": required, "is_context": True},
            )
        if turn == 2:
            return (
                "Подтвердите ещё раз, что предложенный артикул не «{other}»-исполнение.".format(
                    other=clean_text(scenario.params["diff_neighbour"])
                ),
                {"required_params": required, "is_context": True},
            )
        return None

    if scenario.strategy == "catalog_fact":
        sku = scenario.params.get("sku")
        if turn == 1:
            key = scenario.params.get("attribute_key")
            value = scenario.params.get("attribute_value")
            if key and value:
                return (
                    f"Какая у него характеристика «{key}»? Не меняйте товар.",
                    {
                        "expected_skus": [sku],
                        "expected_answer_value": value,
                        "is_context": True,
                    },
                )
            return (
                "Назовите его точный артикул и подтверждённые характеристики.",
                {"expected_skus": [sku], "is_context": True},
            )
        if turn == 2:
            return (
                "Сколько он стоит и есть ли в наличии? Товар не меняем.",
                {"expected_skus": [sku], "dynamic_only": True, "is_context": True},
            )
        return None

    if scenario.strategy == "return_previous":
        if turn == 1:
            return f"Теперь покажи {scenario.params['sku_b']}", {"expected_skus": [scenario.params["sku_b"]]}
        if turn == 2:
            return "Вернемся к первому товару. Какой у него артикул?", {"expected_skus": [scenario.params["sku_a"]], "expected_answer_value": scenario.params["sku_a"], "is_context": True}
        if turn == 3:
            return "А цена у первого какая?", {"expected_skus": [scenario.params["sku_a"]], "is_context": True, "dynamic_only": True}
        return None
    return None


def update_constraints(state: dict[str, Any], message: str) -> None:
    text = norm(message)
    original = clean_text(message).casefold().replace("ё", "е")
    positive_original = original.split("а не", 1)[0]
    # A value the user explicitly rejects ("не «32х3/4" ВР»-исполнение") must
    # never be read back as a requested constraint.
    positive_original = re.sub(r"не\s*«[^»]*»", " ", positive_original)
    positive_original = re.sub(r"\bне\s+[^\s,.]+-исполнени\w*", " ", positive_original)
    fractions = re.findall(r"(?<!\d)(1/2|3/4|1(?:\s+1/4|\s+1/2)?|2)(?!\d)", positive_original)
    if fractions:
        state["size_inch"] = fractions[-1].replace(" ", "")
    ppr_size = re.search(r"(?:ppr|ппр)[^\d]{0,15}(16|20|25|32|40|50|63)\b", original, re.I)
    mm_size = re.search(r"\b(16|20|25|32|40|50|63|75|90|110)\s*мм\b", original, re.I)
    if ppr_size:
        state["diameter_mm"] = ppr_size.group(1)
    elif mm_size:
        state["diameter_mm"] = mm_size.group(1)
    corrected_length = re.search(
        r"\b(?:нужн\w*|надо|требуется|имею\s+в\s+виду)"
        r"[^\d]{0,16}(500|1000|1500|2000|3000)(?:\s*мм)?\b",
        original,
    )
    length = corrected_length or re.search(
        r"(?:длин\w*\s*)?(500|1000|1500|2000|3000)\s*мм",
        original,
    )
    if corrected_length:
        state["length_mm"] = corrected_length.group(1)
    elif length and ("длин" in text or "канализац" in text or "труб" in text):
        state["length_mm"] = length.group(1)

    thread_mentions: list[tuple[int, str]] = []
    for pattern, code in [
        (r"\b(?:вр|вн)\s*[-/]\s*(?:вр|вн)\b", "ff"),
        (r"\b(?:вр|вн)\s*[-/]\s*(?:нр|нар)\b", "fm"),
        (r"\b(?:нр|нар)\s*[-/]\s*(?:нр|нар)\b", "mm"),
    ]:
        thread_mentions.extend((match.start(), code) for match in re.finditer(pattern, positive_original))
    for phrase, code in [
        ("мама мама", "ff"),
        ("внутренняя резьба с обеих", "ff"),
        ("мама папа", "fm"),
        ("папа папа", "mm"),
        ("наружная наружная", "mm"),
    ]:
        position = text.rfind(phrase)
        if position >= 0:
            thread_mentions.append((position, code))
    if thread_mentions:
        state["thread"] = max(thread_mentions, key=lambda item: item[0])[1]
    if "бабоч" in text:
        state["handle"] = "бабочка"
    elif "рычаг" in text:
        state["handle"] = "рычаг"
    # "угол 20" in "PPR угол 20x1/2" is a diameter, not degrees.  Require an
    # explicit unit or a plausible fitting angle before storing a constraint.
    angle = re.search(r"\b(\d{2,3})\s*градус", original) or re.search(
        r"\bугол\w*\s*(?:в\s*)?(30|45|60|90|135)\b", original
    )
    if angle:
        state["angle_deg"] = angle.group(1)
    if "углов" in text:
        state["form"] = "угловой"
    elif "прямой" in text:
        state["form"] = "прямой"
    if "полнопроход" in text:
        state["full_bore"] = True
    if "ppr" in text or "ппр" in text:
        state["system"] = "ppr"
    if "valtec" in text or "валтек" in text:
        state["brand"] = "valtec"
    if "вода" in text or "воды" in text:
        state["application"] = "water"
    if "внутренняя канализа" in text or "серая" in text:
        state["sewer_kind"] = "internal"
    if ("термостат" in text or "температур" in text or "термогалов" in text or "термоголов" in text) and any(
        marker in text for marker in ["радиатор", "батаре", "клапан"]
    ):
        state["product_kind"] = "thermostatic_radiator_valve"
    elif "шаровой" in text or re.search(r"\bкран\b", text):
        state["product_kind"] = "ball_valve"
    elif any(marker in text for marker in ["уголок", "угольник", "ppr угол", "ппр угол"]):
        state["product_kind"] = "elbow"
    elif "канализацион" in text and "труб" in text:
        state["product_kind"] = "sewer_pipe"


def _fraction_matches(text: str, fraction: str) -> bool:
    normalized = clean_text(text).casefold()
    if re.search(rf"(?<!\d){re.escape(fraction)}(?!\d)", normalized):
        return True
    # Слоты хранят смешанный размер компактно («11/2»), а фид пишет его через
    # пробел («1 1/2»).  Без этого правильный 1 1/2" SKU считался нарушением.
    compact = re.fullmatch(r"([1-4])(\d\s*/\s*\d)", fraction)
    if compact:
        spaced = f"{compact.group(1)} {compact.group(2)}"
        return bool(re.search(rf"(?<!\d){re.escape(spaced)}(?!\d)", normalized))
    return False


def product_matches(product: CatalogProduct, constraints: dict[str, Any]) -> tuple[bool, list[str]]:
    mismatches: list[str] = []
    blob = product.blob
    params = {norm(key): norm(value) for key, value in product.params.items()}

    size = constraints.get("size_inch")
    if size:
        size_values = [
            value
            for key, value in product.params.items()
            if "дюйм" in norm(key) and any(marker in norm(key) for marker in ["диаметр", "резьба", "подключ"])
        ]
        source = " ".join(size_values) if size_values else product.name
        if not _fraction_matches(source, str(size)):
            mismatches.append(f"size_inch={size}")

    diameter = constraints.get("diameter_mm")
    if diameter:
        mm_values = [
            value
            for key, value in product.params.items()
            if "диаметр" in norm(key) and "дюйм" not in norm(key)
        ]
        source = " ".join(mm_values) if mm_values else product.name
        if not re.search(rf"(?<!\d){re.escape(str(diameter))}(?!\d)", clean_text(source)):
            mismatches.append(f"diameter_mm={diameter}")

    length = constraints.get("length_mm")
    if length:
        length_values = [value for key, value in product.params.items() if "длин" in norm(key)]
        source = " ".join(length_values) if length_values else product.name
        if not re.search(rf"(?<!\d){re.escape(str(length))}(?!\d)", clean_text(source)):
            mismatches.append(f"length_mm={length}")

    thread = constraints.get("thread")
    if thread:
        source = " ".join(
            value for key, value in product.params.items() if "тип резьбы" in norm(key)
        ) or product.name
        source_norm = norm(source)
        has_internal = "внутрен" in source_norm
        has_external = "наруж" in source_norm
        tokens = set(source_norm.split())
        thread_ok = {
            "ff": "ff" in tokens or (has_internal and not has_external) or any(marker in source_norm for marker in ["внутренняя внутренняя", "вн вн", "вр вр"]),
            "fm": bool(tokens & {"fm", "mf"}) or (has_internal and has_external) or any(marker in source_norm for marker in ["вн нар", "вр нр"]),
            "mm": "mm" in tokens or (has_external and not has_internal) or any(marker in source_norm for marker in ["наружная наружная", "нар нар", "нр нр"]),
        }.get(thread, True)
        if not thread_ok:
            mismatches.append(f"thread={thread}")

    handle = constraints.get("handle")
    if handle:
        source = " ".join(value for key, value in product.params.items() if "ручк" in norm(key)) or product.name
        handle_needle = "бабоч" if handle == "бабочка" else norm(handle)
        if handle_needle not in norm(source):
            mismatches.append(f"handle={handle}")

    form = constraints.get("form")
    if form and constraints.get("product_kind") not in {"elbow", "sewer_pipe"}:
        source = " ".join(value for key, value in product.params.items() if "форма" in norm(key)) or product.name
        form_needle = "углов" if form == "угловой" else "прям"
        if form_needle not in norm(source):
            mismatches.append(f"form={form}")

    angle_deg = constraints.get("angle_deg")
    if angle_deg:
        angle_values = [
            value for key, value in product.params.items() if "угол" in norm(key)
        ]
        source = " ".join(angle_values) if angle_values else product.name
        if not re.search(rf"(?<!\d){re.escape(str(angle_deg))}(?!\d)", clean_text(source)):
            mismatches.append(f"angle_deg={angle_deg}")

    if constraints.get("full_bore") and "полнопроход" not in blob:
        mismatches.append("full_bore=true")
    if constraints.get("system") == "ppr" and not any(marker in blob for marker in ["ppr", "ппр", "полипропилен"]):
        mismatches.append("system=ppr")
    if constraints.get("brand") and constraints["brand"] not in norm(product.vendor or product.manufacturer):
        mismatches.append(f"brand={constraints['brand']}")
    if constraints.get("application") == "water" and "для воды" not in blob and "водоснаб" not in blob:
        # Absence of an explicit medium in XML is unknown, not a contradiction.
        pass
    if constraints.get("sewer_kind") == "internal" and not any(marker in blob for marker in ["внутренн", "ht"]):
        mismatches.append("sewer_kind=internal")
    product_kind = constraints.get("product_kind")
    kind_ok = {
        "thermostatic_radiator_valve": "клапан" in blob and "термостат" in blob,
        "ball_valve": "кран шаровой" in blob or "шаровой кран" in blob,
        "elbow": any(marker in blob for marker in ["угольник", "уголок", "отвод ppr"]),
        "sewer_pipe": "труба" in blob and "канализац" in blob,
    }.get(product_kind, True)
    if not kind_ok:
        mismatches.append(f"product_kind={product_kind}")
    return not mismatches, mismatches


def assess_turn(
    scenario: Scenario,
    message: str,
    expectation: dict[str, Any],
    technical: dict[str, Any],
    catalog: Catalog,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    metrics: dict[str, Any] = {
        "retrieval": 1,
        "factuality": 1,
        "constraints": 1,
        "context": "N/A",
        "clarification": "N/A",
        "hallucination": 1,
    }
    status_code = technical.get("status_code")
    body = technical.get("response_json")
    answer = response_answer(technical)
    products = response_products(technical)
    debug = response_debug(technical)

    def issue(code: str, reason: str, severity: str = "FAIL") -> None:
        issues.append({"code": code, "reason": reason, "severity": severity})

    if technical.get("error"):
        code = "TIMEOUT" if "timeout" in norm(technical["error"]) else "API_ERROR"
        issue(code, technical["error"])
        for key in ["retrieval", "factuality", "constraints"]:
            metrics[key] = 0
        return _finalize_assessment(metrics, issues)
    if status_code != 200:
        issue("API_ERROR", f"Expected HTTP 200, got {status_code}")
        for key in ["retrieval", "factuality", "constraints"]:
            metrics[key] = 0
        return _finalize_assessment(metrics, issues)
    if not isinstance(body, dict) or technical.get("malformed_json"):
        issue("API_ERROR", "Response is not a valid JSON object")
        for key in ["retrieval", "factuality", "constraints"]:
            metrics[key] = 0
        return _finalize_assessment(metrics, issues)
    if not answer:
        issue("API_ERROR", "Empty assistant answer")
        metrics["factuality"] = 0
    required_keys = {"session_id", "answer", "products", "need_handoff", "debug"}
    missing_keys = sorted(required_keys - set(body))
    if missing_keys:
        issue("API_ERROR", f"Missing response keys: {missing_keys}")

    static_mismatch = False
    dynamic_mismatch = False
    returned_skus: list[str] = []
    for card in products:
        sku = clean_text(card.get("sku"))
        returned_skus.append(sku)
        truth = catalog.get(sku)
        if not truth:
            issue("HALLUCINATED_PRODUCT", f"API returned SKU absent from XML: {sku}")
            metrics["hallucination"] = 0
            metrics["factuality"] = 0
            continue
        if norm(card.get("name")) != norm(truth.name):
            issue("WRONG_ATTRIBUTE", f"Name for {sku} differs from XML")
            static_mismatch = True
        api_url = clean_text(card.get("url"))
        if api_url and truth.url and api_url.rstrip(".,;") != truth.url.rstrip(".,;"):
            issue("WRONG_ATTRIBUTE", f"URL for {sku} differs from XML string")
            static_mismatch = True
        api_price = card.get("price")
        if api_price is not None and truth.price is not None:
            try:
                if abs(float(api_price) - float(truth.price)) > 0.009:
                    issue("UNVERIFIED_DYNAMIC_DATA", f"Price for {sku}: API={api_price}, XML={truth.price}", "UNVERIFIED")
                    dynamic_mismatch = True
            except (TypeError, ValueError):
                issue("HALLUCINATED_PRICE", f"Non-numeric API price for {sku}: {api_price!r}")
                metrics["hallucination"] = 0
        api_stock = norm(card.get("stock_status"))
        if truth.quantity is not None:
            xml_in_stock = truth.quantity > 0
            api_in_stock = "налич" in api_stock and "нет" not in api_stock
            if xml_in_stock != api_in_stock:
                issue("UNVERIFIED_DYNAMIC_DATA", f"Stock for {sku}: API={card.get('stock_status')}, XML quantity={truth.quantity}", "UNVERIFIED")
                dynamic_mismatch = True

    if static_mismatch:
        metrics["factuality"] = 0

    known_answer_skus: list[str] = []
    for token in SKU_TOKEN_RE.findall(answer):
        if not any(char.isdigit() for char in token):
            continue
        product = catalog.get(token)
        if product:
            known_answer_skus.append(product.sku)
            continue
        before = answer[max(0, answer.find(token) - 25) : answer.find(token)].casefold()
        token_key = norm_sku(token)
        is_grounded_prefix = any(
            norm_sku(sku).startswith(token_key)
            or norm_sku(sku).endswith(token_key)
            or token_key.startswith(norm_sku(sku))
            for sku in returned_skus
            if len(token_key) >= 5
        )
        token_position = answer.find(token)
        token_context = norm(answer[max(0, token_position - 80) : token_position + len(token) + 80])
        is_negative_echo = (
            token_key in norm_sku(message)
            and any(marker in token_context for marker in ["не найден", "не нашел", "нет в каталоге", "отсутствует"])
        )
        if (
            "артикул" in before
            and len(token_key) >= 5
            and not is_grounded_prefix
            and not is_negative_echo
            and not any(marker in norm(token) for marker in ["ppr", "dn", "en"])
        ):
            issue("HALLUCINATED_PRODUCT", f"Answer names unknown article token: {token}")
            metrics["hallucination"] = 0
            metrics["factuality"] = 0

    for url in URL_RE.findall(answer):
        normalized_url = url.rstrip(".,;")
        if normalized_url not in catalog.url_set:
            issue("HALLUCINATED_PRODUCT", f"Answer contains URL absent from XML: {normalized_url}")
            metrics["hallucination"] = 0
            metrics["factuality"] = 0

    expected_skus = [sku for sku in expectation.get("expected_skus", []) if sku]
    if not expected_skus:
        exact = catalog.exact_skus_in_text(message)
        if scenario.strategy not in {"analog", "no_match", "no_exact_combo"}:
            expected_skus = exact
    expected_any = [sku for sku in expectation.get("expected_any_skus", []) if sku]
    returned_norm = {norm_sku(sku) for sku in returned_skus}
    answer_norm_skus = {norm_sku(sku) for sku in known_answer_skus}
    available_identity = returned_norm | answer_norm_skus

    if expected_skus:
        missing = [sku for sku in expected_skus if norm_sku(sku) not in available_identity]
        if missing:
            issue("WRONG_SKU", f"Expected SKU(s) not returned/named: {missing}; got={returned_skus}")
            metrics["retrieval"] = 0
            if expectation.get("is_context"):
                metrics["context"] = 0
                issue("CONTEXT_LOSS", f"Contextual referent lost: {missing}")
    if expected_any and not any(norm_sku(sku) in available_identity for sku in expected_any):
        issue("WRONG_SKU", f"None of expected related SKUs present: {expected_any}")
        metrics["retrieval"] = 0

    previous_product_cards = list(runtime.get("last_product_cards") or [])
    constraints = runtime.setdefault("constraints", {})
    update_constraints(constraints, message)
    mismatched_cards: list[str] = []
    mismatch_details: dict[str, list[str]] = {}
    for sku in returned_skus:
        product = catalog.get(sku)
        if not product:
            continue
        if (
            expectation.get("is_context")
            and expected_skus
            and norm_sku(sku) in {norm_sku(item) for item in expected_skus}
        ):
            # A deliberate return to an earlier exact identity restores that
            # product's historical constraints.  The evaluator does not keep
            # the application's branch snapshots, so applying the latest
            # 3/4/handle refinement to the explicitly expected old 1/2 SKU is
            # a false MISSED_CONSTRAINT.  Identity is already checked above;
            # any extra/unexpected cards still pass through normal matching.
            continue
        matched, mismatches = product_matches(product, constraints)
        if not matched:
            mismatched_cards.append(f"{sku}: {', '.join(mismatches)}")
            mismatch_details[sku] = mismatches
    if mismatched_cards and not expectation.get("expects_alternative"):
        mismatch_code = (
            "RETRIEVAL_WRONG_PRODUCT"
            if any("product_kind=" in item for item in mismatched_cards)
            else "MISSED_CONSTRAINT"
        )
        # Явно раскрытое отличие («точного нет, ближайшее отличается по углу»)
        # — это честный компромисс продавца, а не молчаливая подмена.
        disclosed_deviation = any(
            marker in norm(answer)
            for marker in [
                "нет точного",
                "точного совпадения нет",
                "точного совпадения по всем параметрам нет",
                "не нашел точн",
                "ближайш",
                "отличается по параметру",
            ]
        )
        issue(
            mismatch_code,
            ("Отклонение раскрыто в ответе: " if disclosed_deviation else "")
            + "Returned card violates active constraints: "
            + "; ".join(mismatched_cards),
            "WARN" if disclosed_deviation else "FAIL",
        )
        metrics["constraints"] = 0
        if not disclosed_deviation:
            metrics["retrieval"] = 0

    required_params = expectation.get("required_params") or {}
    if required_params and products:
        param_violations: list[str] = []
        unverified_params: list[str] = []
        for sku in returned_skus:
            product = catalog.get(sku)
            if not product:
                continue
            for key, value in required_params.items():
                actual = product.param(key)
                if not actual:
                    unverified_params.append(f"{sku}: {key} отсутствует в XML")
                    continue
                wanted = canonical_param_value(key, value)
                observed = canonical_param_value(key, actual)
                if wanted != observed and wanted not in observed:
                    param_violations.append(
                        f"{sku}: {clean_text(key)} XML={clean_text(actual)}, "
                        f"запрошено {clean_text(value)}"
                    )
        if param_violations:
            disclosed = any(
                marker in norm(answer)
                for marker in [
                    "нет точного",
                    "точного совпадения нет",
                    "точного варианта",
                    "не нашел точн",
                    "не нашел точное",
                    "ближайш",
                    "отлича",
                    "вместо",
                ]
            )
            issue(
                "MISSED_CONSTRAINT",
                ("Отклонение от запроса раскрыто в ответе: " if disclosed else "")
                + "Карточка не соответствует запрошенным параметрам каталога: "
                + "; ".join(param_violations),
                "WARN" if disclosed else "FAIL",
            )
            metrics["constraints"] = 0
            if not disclosed:
                metrics["retrieval"] = 0
        elif unverified_params:
            issue(
                "UNVERIFIED_DYNAMIC_DATA",
                "Параметр отсутствует в выгрузке, проверка невозможна: "
                + "; ".join(unverified_params),
                "UNVERIFIED",
            )

    if expectation.get("expects_alternative") and products:
        undisclosed = [
            f"{sku}: {mismatch}"
            for sku, mismatches in mismatch_details.items()
            for mismatch in mismatches
            if not alternative_discloses_mismatch(answer, mismatch)
        ]
        if undisclosed:
            issue(
                "BAD_ALTERNATIVE",
                "Alternative violates hard constraints without an explicit field-level diff: "
                + "; ".join(undisclosed),
            )
            metrics["constraints"] = 0
            metrics["retrieval"] = 0

    expected_value = expectation.get("expected_answer_value")
    if expected_value and norm_sku(expected_value) not in norm_sku(answer) and norm(expected_value) not in norm(answer):
        issue("WRONG_ATTRIBUTE", f"Expected grounded value absent from answer: {expected_value}")
        metrics["factuality"] = 0

    first_turn = len(runtime.get("turn_assessments", [])) == 0
    expect_clarify = bool(scenario.params.get("expect_clarify_first") and first_turn)
    if expect_clarify:
        metrics["clarification"] = 1 if looks_like_question(answer) and not products else 0
        if metrics["clarification"] == 0:
            issue("BAD_CLARIFICATION", "Incomplete first request produced products/no critical clarification")
        elif not clarification_is_relevant(answer, constraints, debug):
            metrics["clarification"] = 0
            issue(
                "BAD_CLARIFICATION",
                "Clarification does not ask a missing critical parameter for the requested product kind",
            )

    if (
        not products
        and looks_like_question(answer)
        and constraints.get("product_kind")
        and not clarification_is_relevant(answer, constraints, debug)
        and not any(item["code"] == "BAD_CLARIFICATION" for item in issues)
    ):
        metrics["clarification"] = 0
        issue(
            "BAD_CLARIFICATION",
            "Selection funnel asks an unrelated or already supplied parameter",
        )
    redundant_questions: list[str] = []
    user_text = norm(message)
    answer_text = norm(answer)
    if ("на воду" in user_text or "для воды" in user_text) and any(marker in answer_text for marker in ["для чего нужен", "вода отопление или радиатор", "для воды или"]):
        redundant_questions.append("application already specified as water")
    if re.search(r"\b(?:1/2|3/4)\b", clean_text(message)) and any(marker in answer_text for marker in ["какой размер", "какой диаметр", "размер подключения"]):
        redundant_questions.append("size already specified")
    if redundant_questions:
        metrics["clarification"] = 0
        issue("BAD_CLARIFICATION", "Redundant clarification: " + ", ".join(redundant_questions))

    if expectation.get("is_context") or expectation.get("is_correction"):
        if metrics["context"] == "N/A":
            metrics["context"] = 1
        if expectation.get("is_correction") and mismatched_cards:
            metrics["context"] = 0
            issue("FAILED_CORRECTION", "Old constraint still affects returned product")

    if expectation.get("expect_no_exact_match") or scenario.strategy in {"no_match", "no_exact_combo"}:
        if products and not any(marker in norm(answer) for marker in ["нет точного", "точного совпадения нет", "ближайш", "отлич"]):
            issue("HALLUCINATED_PRODUCT", "Product presented for an impossible/no-match request without a mismatch disclaimer")
            metrics["hallucination"] = 0
            metrics["constraints"] = 0

    if re.search(
        r"\b(?:какой|который)\s+(?:из\s+них\s+)?дешевле\b|\bсамый\s+дешев",
        norm(message),
    ):
        priced_previous = [
            card
            for card in previous_product_cards
            if card.get("price") is not None and clean_text(card.get("sku"))
        ]
        if priced_previous:
            minimum = min(float(card["price"]) for card in priced_previous)
            cheapest_skus = [
                clean_text(card["sku"])
                for card in priced_previous
                if abs(float(card["price"]) - minimum) < 0.01
            ]
            if not answer_identifies_cheapest(answer, cheapest_skus):
                issue(
                    "WRONG_SKU",
                    "Cheapest comparison does not explicitly identify one of the cheapest SKU(s): "
                    + ", ".join(cheapest_skus),
                )
                metrics["retrieval"] = 0

    if debug and not isinstance(debug.get("agents_used", []), list):
        issue("API_ERROR", "debug.agents_used is malformed")
    if dynamic_mismatch and not any(item["code"] != "UNVERIFIED_DYNAMIC_DATA" for item in issues):
        metrics["factuality"] = 1

    assessment = _finalize_assessment(metrics, issues)
    if products:
        runtime["last_product_cards"] = products
    runtime.setdefault("turn_assessments", []).append(assessment)
    return assessment


def _finalize_assessment(metrics: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    severities = {item["severity"] for item in issues}
    if "FAIL" in severities:
        overall = "FAIL"
    elif "WARN" in severities:
        overall = "WARN"
    elif "UNVERIFIED" in severities:
        overall = "UNVERIFIED"
    else:
        overall = "PASS"
    metrics["overall"] = overall
    return {"status": overall, "metrics": metrics, "issues": issues}


def run_scenario(
    scenario: Scenario,
    client: APIClient,
    catalog: Catalog,
    run_id: str,
    pause: float,
) -> dict[str, Any]:
    session_id = f"qa-{run_id}-{scenario.scenario_id}-{uuid.uuid4().hex[:8]}"
    runtime: dict[str, Any] = {"constraints": {}, "turn_assessments": []}
    transcript: list[dict[str, Any]] = []
    message = scenario.initial
    expectation = initial_expectation(scenario)

    for _ in range(scenario.max_turns):
        technical = client.chat(session_id, message)
        assessment = assess_turn(
            scenario, message, expectation, technical, catalog, runtime
        )
        transcript.append(
            redact(
                {
                    "turn": len(transcript) + 1,
                    "user": message,
                    "bot": response_answer(technical),
                    "products": response_products(technical),
                    "technical": technical,
                    "assessment": assessment,
                }
            )
        )
        if technical.get("error") or technical.get("status_code") != 200:
            break
        next_item = next_message(scenario, transcript, catalog, runtime)
        if not next_item:
            break
        message, expectation = next_item
        if pause:
            time.sleep(pause)

    turn_statuses = [turn["assessment"]["status"] for turn in transcript]
    if "FAIL" in turn_statuses:
        status = "FAIL"
    elif "WARN" in turn_statuses:
        status = "WARN"
    elif "UNVERIFIED" in turn_statuses:
        status = "UNVERIFIED"
    else:
        status = "PASS"
    return redact(
        {
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "persona": scenario.persona,
            "tags": scenario.tags,
            "repeat_group": scenario.repeat_group,
            "session_id": session_id,
            "status": status,
            "turns": transcript,
        }
    )


def initial_expectation(scenario: Scenario) -> dict[str, Any]:
    expectation: dict[str, Any] = {}
    if scenario.params.get("sku") and scenario.strategy not in {"sku_typo", "analog"}:
        expectation["expected_skus"] = [scenario.params["sku"]]
    if scenario.strategy == "sku_typo" and scenario.params.get("sku"):
        expectation["expected_any_skus"] = [scenario.params["sku"]]
    if scenario.strategy == "similar":
        expectation["expected_any_skus"] = [scenario.params["sku_a"], scenario.params["sku_b"]]
    if scenario.strategy == "catalog_constraint":
        expectation["required_params"] = scenario.params.get("required_params") or {}
    return expectation


def rescore_dialogues(
    dialogues: list[dict[str, Any]],
    scenarios: Iterable[Scenario],
    catalog: Catalog,
) -> list[dict[str, Any]]:
    """Reapply deterministic checks to captured HTTP traces without API calls."""
    by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    for dialogue in dialogues:
        scenario = by_id.get(dialogue.get("scenario_id"))
        if scenario is None:
            raise KeyError(f"Scenario absent from current matrix: {dialogue.get('scenario_id')}")
        runtime: dict[str, Any] = {"constraints": {}, "turn_assessments": []}
        expectation = initial_expectation(scenario)
        reconstructed: list[dict[str, Any]] = []
        for turn in dialogue.get("turns") or []:
            technical = turn["technical"]
            turn["assessment"] = assess_turn(
                scenario,
                turn["user"],
                expectation,
                technical,
                catalog,
                runtime,
            )
            reconstructed.append(turn)
            next_item = next_message(scenario, reconstructed, catalog, runtime)
            expectation = next_item[1] if next_item else {}
        statuses = [turn["assessment"]["status"] for turn in reconstructed]
        dialogue["status"] = (
            "FAIL" if "FAIL" in statuses else
            "WARN" if "WARN" in statuses else
            "UNVERIFIED" if "UNVERIFIED" in statuses else
            "PASS"
        )
    return redact(dialogues)


def run_api_probes(client: APIClient, catalog: Catalog, run_id: str) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []

    def record(name: str, technical: dict[str, Any], expected_status: int) -> None:
        status = technical.get("status_code")
        valid = status == expected_status and not technical.get("error")
        probes.append(
            redact(
                {
                    "name": name,
                    "expected_status": expected_status,
                    "status": "PASS" if valid else "FAIL",
                    "issue_code": None if valid else "API_ERROR",
                    "technical": technical,
                }
            )
        )

    health = client.health()
    health_ok = (
        health.get("status_code") == 200
        and isinstance(health.get("response_json"), dict)
        and health["response_json"].get("status") == "ok"
        and (health["response_json"].get("products_loaded") or 0) > 0
    )
    probes.append(
        {
            "name": "health_and_catalog",
            "expected_status": 200,
            "status": "PASS" if health_ok else "FAIL",
            "issue_code": None if health_ok else "API_ERROR",
            "technical": health,
        }
    )
    record("empty_message", client.request("POST", client.chat_path, {"session_id": f"probe-{run_id}", "message": "   "}), 400)
    record("malformed_json", client.request("POST", client.chat_path, raw_body=b"{"), 422)
    record("missing_required_fields", client.request("POST", client.chat_path, {"message": "test"}), 422)
    record("unknown_endpoint", client.request("GET", "/__qa_missing_endpoint__"), 404)

    candidates = [
        product for product in catalog.products if product.url and len(norm_sku(product.sku)) >= 5
    ][:2]
    if len(candidates) == 2:
        session_a = f"qa-{run_id}-isolation-a"
        session_b = f"qa-{run_id}-isolation-b"
        a1 = client.chat(session_a, candidates[0].sku)
        b1 = client.chat(session_b, candidates[1].sku)
        a2 = client.chat(session_a, "Какой артикул у этого товара?")
        b2 = client.chat(session_b, "Какой артикул у этого товара?")
        a_answer = norm_sku(response_answer(a2))
        b_answer = norm_sku(response_answer(b2))
        isolated = (
            norm_sku(candidates[0].sku) in a_answer
            and norm_sku(candidates[1].sku) in b_answer
            and norm_sku(candidates[1].sku) not in a_answer
            and norm_sku(candidates[0].sku) not in b_answer
        )
        probes.append(
            {
                "name": "independent_sessions",
                "expected_status": 200,
                "status": "PASS" if isolated else "FAIL",
                "issue_code": None if isolated else "CONTEXT_LOSS",
                "technical": {"a_setup": a1, "b_setup": b1, "a_followup": a2, "b_followup": b2},
            }
        )

    lost = client.chat(f"qa-{run_id}-fresh-lost-session", "Вернемся к первому товару. Какой у него артикул?")
    lost_answer = response_answer(lost)
    lost_products = response_products(lost)
    graceful = (
        lost.get("status_code") == 200
        and not lost_products
        and (looks_like_question(lost_answer) or any(marker in norm(lost_answer) for marker in ["не вижу", "не было", "уточните", "назовите"]))
    )
    probes.append(
        {
            "name": "unknown_or_lost_session_fails_closed",
            "expected_status": 200,
            "status": "PASS" if graceful else "FAIL",
            "issue_code": None if graceful else "CONTEXT_LOSS",
            "technical": lost,
        }
    )
    return redact(probes)


def summarize(
    dialogues: list[dict[str, Any]],
    probes: list[dict[str, Any]],
) -> dict[str, Any]:
    turns = [turn for dialogue in dialogues for turn in dialogue["turns"]]
    turn_status = Counter(turn["assessment"]["status"] for turn in turns)
    dialogue_status = Counter(dialogue["status"] for dialogue in dialogues)
    issue_counts: Counter[str] = Counter()
    sku_issues: Counter[str] = Counter()
    llm_sources: Counter[str] = Counter()
    llm_used = 0
    hallucination_failures = 0
    context_failures = 0
    technical_errors = 0
    latencies: list[float] = []
    for turn in turns:
        technical = turn["technical"]
        latencies.append(float(technical.get("latency_sec") or 0))
        debug = response_debug(technical)
        llm_sources[str(debug.get("final_answer_source") or "unknown")] += 1
        if debug.get("llm_transport_succeeded") or debug.get("any_llm_used"):
            llm_used += 1
        if turn["assessment"]["metrics"].get("hallucination") == 0:
            hallucination_failures += 1
        if turn["assessment"]["metrics"].get("context") == 0:
            context_failures += 1
        for issue in turn["assessment"]["issues"]:
            issue_counts[issue["code"]] += 1
            if issue["code"] in {"API_ERROR", "TIMEOUT"}:
                technical_errors += 1
        for product in turn.get("products") or []:
            if turn["assessment"]["status"] == "FAIL":
                sku_issues[clean_text(product.get("sku"))] += 1

    repeat_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for dialogue in dialogues:
        if dialogue.get("repeat_group"):
            repeat_groups[dialogue["repeat_group"]].append(
                {"scenario_id": dialogue["scenario_id"], "status": dialogue["status"]}
            )

    pass_turns = turn_status.get("PASS", 0)
    total_turns = len(turns)
    pass_rate = (pass_turns / total_turns * 100) if total_turns else 0.0
    severe_codes = {
        "HALLUCINATED_PRODUCT", "HALLUCINATED_ATTRIBUTE", "WRONG_SKU",
        "RETRIEVAL_WRONG_PRODUCT", "MISSED_CONSTRAINT", "API_ERROR", "TIMEOUT",
    }
    if any(issue_counts[code] for code in severe_codes) or pass_rate < 80:
        readiness = "NOT READY"
    elif dialogue_status.get("FAIL") or dialogue_status.get("WARN") or dialogue_status.get("UNVERIFIED"):
        readiness = "READY WITH ISSUES"
    else:
        readiness = "READY"
    return {
        "dialogues": len(dialogues),
        "user_turns": total_turns,
        "dialogue_status": dict(dialogue_status),
        "turn_status": dict(turn_status),
        "dialogue_pass_rate_percent": round(
            dialogue_status.get("PASS", 0) / len(dialogues) * 100,
            2,
        ) if dialogues else 0.0,
        "pass_rate_percent": round(pass_rate, 2),
        "top_errors": issue_counts.most_common(20),
        "problematic_skus": sku_issues.most_common(20),
        "context_failures": context_failures,
        "hallucination_failures": hallucination_failures,
        "hallucination_rate_percent": round(hallucination_failures / total_turns * 100, 2) if total_turns else 0.0,
        "llm_used_turns": llm_used,
        "final_answer_sources": dict(llm_sources),
        "latency_p50_sec": round(percentile(latencies, 0.5) or 0, 3),
        "latency_p95_sec": round(percentile(latencies, 0.95) or 0, 3),
        "latency_max_sec": round(max(latencies), 3) if latencies else 0.0,
        "technical_errors": technical_errors,
        "api_probe_status": dict(Counter(probe["status"] for probe in probes)),
        "repeated_runs": dict(repeat_groups),
        "readiness": readiness,
    }


def priority_fixes(summary: dict[str, Any]) -> list[str]:
    counts = dict(summary.get("top_errors") or [])
    fixes: list[str] = []
    if counts.get("MISSED_CONSTRAINT") or counts.get("WRONG_SKU"):
        fixes.append("P0: усилить hard-filtering до ранжирования для размера, типа резьбы, формы, ручки и системы соединения; не показывать SKU с несовпавшим обязательным параметром.")
    if counts.get("HALLUCINATED_PRODUCT") or counts.get("HALLUCINATED_ATTRIBUTE"):
        fixes.append("P0: блокировать финальный текст при упоминании SKU/URL/характеристики, которых нет в retrieved product evidence.")
    if counts.get("CONTEXT_LOSS") or counts.get("FAILED_CORRECTION"):
        fixes.append("P1: хранить versioned active constraints и явно заменять исправленный слот; добавить проверку referent SKU перед ответом на «первый/он/такой же».")
    if counts.get("BAD_CLARIFICATION"):
        fixes.append("P1: проверять уже заполненные слоты перед вопросом и не переспрашивать назначение/размер, явно указанные в текущей реплике.")
    if counts.get("API_ERROR") or counts.get("TIMEOUT"):
        fixes.append("P0: устранить API/timeout ошибки и добавить стабильный server-side deadline с валидным JSON fallback.")
    if counts.get("BAD_ALTERNATIVE"):
        fixes.append("P1: у каждого аналога формировать явный diff обязательных характеристик относительно запроса/исходного SKU.")
    if not fixes:
        fixes.append("P2: сохранить текущие инварианты и добавить этот suite в pre-deploy regression.")
    return fixes


def write_outputs(
    output_dir: Path,
    metadata: dict[str, Any],
    catalog_analysis: dict[str, Any],
    matrix: dict[str, Any],
    probes: list[dict[str, Any]],
    dialogues: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = redact(
        {
            "metadata": metadata,
            "catalog_analysis": catalog_analysis,
            "test_matrix": matrix,
            "api_probes": probes,
            "summary": summary,
            "dialogues": dialogues,
        }
    )
    (output_dir / "test_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "test_transcripts.jsonl").open("w", encoding="utf-8") as stream:
        for dialogue in dialogues:
            stream.write(json.dumps(redact(dialogue), ensure_ascii=False) + "\n")

    lines = [
        "# VestaTrade local bot evaluation",
        "",
        f"Дата: `{metadata['started_at']}`",
        f"Suite: `{metadata['suite']}`",
        f"API: `{metadata['base_url']}`",
        f"Модель: `{metadata.get('llm_model') or 'unknown'}` через `{metadata.get('llm_provider') or 'unknown'}`",
        f"Каталог API: **{metadata.get('products_loaded', 'unknown')}** товаров, источник `{metadata.get('products_loaded_from', 'unknown')}`.",
        f"XML ground truth: **{catalog_analysis['raw_offers']}** offer, **{catalog_analysis['unique_skus']}** уникальных SKU.",
        "",
        "## Итог",
        "",
        f"Статус готовности: **{summary['readiness']}**",
        "",
        f"Диалогов: **{summary['dialogues']}**; user turns: **{summary['user_turns']}**.",
        f"Диалоги PASS/WARN/FAIL/UNVERIFIED: **{summary['dialogue_status'].get('PASS', 0)} / {summary['dialogue_status'].get('WARN', 0)} / {summary['dialogue_status'].get('FAIL', 0)} / {summary['dialogue_status'].get('UNVERIFIED', 0)}**.",
        f"Ответы PASS/WARN/FAIL/UNVERIFIED: **{summary['turn_status'].get('PASS', 0)} / {summary['turn_status'].get('WARN', 0)} / {summary['turn_status'].get('FAIL', 0)} / {summary['turn_status'].get('UNVERIFIED', 0)}**.",
        f"Pass rate: **{summary['dialogue_pass_rate_percent']}% по диалогам**, **{summary['pass_rate_percent']}% по ответам**.",
        f"Hallucination rate: **{summary['hallucination_rate_percent']}%** ({summary['hallucination_failures']} ответов).",
        f"Потери контекста: **{summary['context_failures']}**.",
        f"Latency p50/p95/max: **{summary['latency_p50_sec']} / {summary['latency_p95_sec']} / {summary['latency_max_sec']} с**.",
        f"Технические ошибки: **{summary['technical_errors']}**; API probes: `{json.dumps(summary['api_probe_status'], ensure_ascii=False)}`.",
        f"Ходы с успешным LLM transport: **{summary['llm_used_turns']}**; источники финального ответа: `{json.dumps(summary['final_answer_sources'], ensure_ascii=False)}`.",
        "",
        "## Наиболее частые ошибки",
        "",
    ]
    if summary["top_errors"]:
        lines.extend(f"- `{code}`: {count}" for code, count in summary["top_errors"])
    else:
        lines.append("- Ошибок не зафиксировано.")
    lines.extend(["", "## Наиболее проблемные SKU", ""])
    if summary["problematic_skus"]:
        lines.extend(f"- `{sku}`: {count} FAIL-ответов" for sku, count in summary["problematic_skus"])
    else:
        lines.append("- Нет.")

    lines.extend(["", "## Повторяемость", ""])
    for group, rows in summary["repeated_runs"].items():
        statuses = Counter(row["status"] for row in rows)
        lines.append(f"- `{group}`: {json.dumps(dict(statuses), ensure_ascii=False)} — {', '.join(row['scenario_id'] for row in rows)}")

    lines.extend(["", "## Каталог", ""])
    lines.append(f"- Нулевой остаток в XML: **{catalog_analysis['zero_stock']}**; положительный: **{catalog_analysis['positive_stock']}**; неизвестный: **{catalog_analysis['unknown_stock']}**.")
    lines.append(f"- Топ категорий: `{json.dumps(catalog_analysis['categories_top'][:12], ensure_ascii=False)}`.")
    lines.append(f"- Топ брендов: `{json.dumps(catalog_analysis['brands_top'][:12], ensure_ascii=False)}`.")
    lines.append(f"- Топ типов товара: `{json.dumps(catalog_analysis['product_types_top'][:15], ensure_ascii=False)}`.")
    lines.append(f"- Повторные группы: `{json.dumps(matrix['repeat_groups'], ensure_ascii=False)}`.")

    lines.extend(["", "## Приоритетные исправления", ""])
    lines.extend(f"- {item}" for item in priority_fixes(summary))

    lines.extend(["", "## Техническая диагностика", ""])
    for finding in metadata.get("technical_findings") or []:
        lines.append(f"- {finding}")
    if not metadata.get("technical_findings"):
        lines.append("- Дополнительные findings не переданы.")

    lines.extend(["", "## Targeted retests", ""])
    for finding in metadata.get("targeted_retests") or []:
        lines.append(f"- {finding}")
    if not metadata.get("targeted_retests"):
        lines.append("- Отдельные targeted retests не зафиксированы в metadata.")

    failed_dialogues = [dialogue for dialogue in dialogues if dialogue["status"] == "FAIL"]
    lines.extend(["", "## Полные диалоги серьёзных ошибок", ""])
    if not failed_dialogues:
        lines.append("Серьёзных ошибок нет.")
    for dialogue in failed_dialogues:
        lines.extend(
            [
                f"### {dialogue['scenario_id']}: {dialogue['title']}",
                "",
                f"Session: `{dialogue['session_id']}`",
                "",
            ]
        )
        for turn in dialogue["turns"]:
            lines.append(f"USER: {turn['user']}")
            lines.append("")
            lines.append(f"BOT: {turn['bot']}")
            lines.append("")
            lines.append(f"Products: `{json.dumps(turn['products'], ensure_ascii=False)}`")
            lines.append("")
            lines.append(f"Assessment: `{json.dumps(turn['assessment'], ensure_ascii=False)}`")
            lines.append("")

    lines.extend(
        [
            "## Ограничения интерпретации",
            "",
            "- Цена и остаток сверяются с локальным XML, но расхождение получает `UNVERIFIED_DYNAMIC_DATA`, а не автоматический FAIL.",
            "- Публичный `/chat` не раскрывает полный hidden retrieval candidate set. Runner различает retrieval/selection и final answer по карточкам, debug agent trace и статическим ограничениям; невидимые отброшенные candidates независимо проверить нельзя.",
            "- Session state хранится в памяти процесса; свежая/потерянная session проверяется на fail-closed, но автоматический restart сервера runner не выполняет.",
            "- URL товаров сравниваются только как строки и никогда не открываются.",
        ]
    )
    (output_dir / "test_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=["smoke", "core", "all", "extended", "full"],
        default="all",
    )
    parser.add_argument(
        "--extended-limit",
        type=int,
        default=int(os.getenv("BOT_EVAL_EXTENDED_LIMIT", "60")),
        help="Number of catalogue-generated scenarios in the extended suite",
    )
    parser.add_argument("--base-url", default=os.getenv("BOT_API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--chat-path", default=os.getenv("BOT_API_CHAT_PATH", "/chat"))
    parser.add_argument("--health-path", default=os.getenv("BOT_API_HEALTH_PATH", "/health"))
    parser.add_argument("--xml", type=Path, default=Path(os.getenv("BOT_CATALOG_XML_PATH", PROJECT_ROOT / "data" / "products_all.xml")))
    parser.add_argument("--output-dir", type=Path, default=Path(os.getenv("BOT_EVAL_OUTPUT_DIR", PROJECT_ROOT)))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("BOT_EVAL_TIMEOUT_SECONDS", "190")))
    parser.add_argument("--pause", type=float, default=float(os.getenv("BOT_EVAL_PAUSE_SECONDS", "0.05")))
    parser.add_argument("--skip-api-probes", action="store_true")
    parser.add_argument("--dry-run-catalog", action="store_true", help="Parse XML and print the generated test matrix without HTTP calls")
    parser.add_argument("--rescore", type=Path, help="Re-score an existing test_results.json without HTTP/LLM calls")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    xml_path = args.xml if args.xml.is_absolute() else PROJECT_ROOT / args.xml
    print(f"Parsing local catalog: {xml_path}", flush=True)
    catalog = Catalog.from_xml(xml_path)
    catalog_analysis = catalog.analysis()
    smoke, core, matrix = build_scenarios(catalog)
    extended: list[Scenario] = []
    if args.suite in {"extended", "full"}:
        extended = build_extended_scenarios(catalog, max(1, args.extended_limit))
        matrix["extended_scenarios"] = len(extended)
    selected = {
        "smoke": smoke,
        "core": core,
        "all": smoke + core,
        "extended": extended,
        "full": smoke + core + extended,
    }[args.suite]
    if args.dry_run_catalog:
        print(
            json.dumps(
                {
                    "catalog": catalog_analysis,
                    "matrix": matrix,
                    "scenarios": [asdict(item) for item in selected],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.rescore:
        source = args.rescore if args.rescore.is_absolute() else PROJECT_ROOT / args.rescore
        existing = json.loads(source.read_text(encoding="utf-8"))
        dialogues = rescore_dialogues(
            existing.get("dialogues") or [],
            smoke + core + build_extended_scenarios(catalog, max(1, args.extended_limit)),
            catalog,
        )
        probes = existing.get("api_probes") or []
        summary = summarize(dialogues, probes)
        metadata = dict(existing.get("metadata") or {})
        metadata["rescored_at"] = now_iso()
        metadata["evaluator_source"] = str(Path(__file__).resolve())
        write_outputs(
            args.output_dir,
            metadata,
            catalog_analysis,
            matrix,
            probes,
            dialogues,
            summary,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary["readiness"] == "NOT READY" else 0

    client = APIClient(args.base_url, args.chat_path, args.health_path, args.timeout)
    health = client.health()
    health_body = health.get("response_json") if isinstance(health.get("response_json"), dict) else {}
    if health.get("status_code") != 200 or health_body.get("status") != "ok":
        print(json.dumps(redact(health), ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit("Backend health check failed")
    if not health_body.get("llm_configured"):
        raise SystemExit("Backend reports llm_configured=false; refusing LLM evaluation")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    started_at = now_iso()
    probes = [] if args.skip_api_probes else run_api_probes(client, catalog, run_id)
    dialogues: list[dict[str, Any]] = []
    for index, scenario in enumerate(selected, start=1):
        result = run_scenario(scenario, client, catalog, run_id, max(0.0, args.pause))
        dialogues.append(result)
        print(
            f"[{index:02d}/{len(selected):02d}] {scenario.scenario_id} "
            f"{result['status']} turns={len(result['turns'])}",
            flush=True,
        )

    summary = summarize(dialogues, probes)
    metadata = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": now_iso(),
        "suite": args.suite,
        "base_url": args.base_url,
        "chat_path": args.chat_path,
        "health_path": args.health_path,
        "timeout_seconds": args.timeout,
        "llm_provider": health_body.get("llm_provider"),
        "llm_model": health_body.get("llm_model"),
        "llm_request_timeout_seconds": health_body.get("llm_request_timeout_seconds"),
        "llm_attempt_timeout_seconds": health_body.get("llm_attempt_timeout_seconds"),
        "llm_max_retries": health_body.get("llm_max_retries"),
        "generation_parameters": {
            "top_p": "not_set_by_application",
            "temperature": {
                "structured_json_agents": 0.0,
                "consultant": 0.35,
                "consultant_retry": 0.2,
                "response_composer_observed_range": "0.2-0.5",
            },
        },
        "products_loaded": health_body.get("products_loaded"),
        "products_loaded_from": health_body.get("products_loaded_from"),
        "product_docs_loaded": health_body.get("product_docs_loaded"),
    }
    write_outputs(args.output_dir, metadata, catalog_analysis, matrix, probes, dialogues, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"report={args.output_dir / 'test_report.md'}", flush=True)
    return 1 if summary["readiness"] == "NOT READY" else 0


if __name__ == "__main__":
    raise SystemExit(main())
