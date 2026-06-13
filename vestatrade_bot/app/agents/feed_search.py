from __future__ import annotations

import logging
import re

from app.models import Product, SearchQuery

from .utils import normalize_sku, normalize_text


logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - exercised only without optional dependency
    from difflib import SequenceMatcher

    class _FuzzFallback:
        @staticmethod
        def partial_ratio(a: str, b: str) -> int:
            return int(SequenceMatcher(None, a, b).ratio() * 100)

    fuzz = _FuzzFallback()


SYNONYMS: dict[str, list[str]] = {
    "котел": ["котел", "котёл", "boiler"],
    "насос": ["насос", "помпа"],
    "циркуляционный насос": ["циркуляционный насос", "насос циркуляционный"],
    "кран": ["кран", "шаровый кран", "вентиль"],
    "труба": ["труба", "трубы"],
    "канализация": ["канализация", "канализационная труба"],
    "радиаторная арматура": ["радиаторная арматура", "термоголовка", "клапан"],
}

NAME_QUERY_STOPWORDS = {
    "нужен",
    "нужна",
    "нужно",
    "надо",
    "хочу",
    "купить",
    "покажи",
    "дай",
    "есть",
    "мне",
    "нам",
    "для",
    "или",
    "что",
    "это",
    "по",
    "сколько",
    "стоит",
    "цена",
    "наличие",
    "наличии",
    "пожалуйста",
}

CATEGORY_NEEDLES: dict[str, list[str]] = {
    "pipes": ["труба", "трубы", "ppr", "полипропилен"],
    "sewer": ["канализац", "ostendorf", "htem", "htee", "htr"],
    "pumps": ["насос", "помпа", "pump"],
    "boilers": ["котел", "котёл", "boiler"],
    "valves": ["кран", "шаровый", "вентиль"],
    "radiator_fittings": ["радиатор", "термоголов", "термостатическ", "клапан"],
}


class FeedSearchAgent:
    def __init__(self, products: list[Product] | None = None) -> None:
        self.products = products or []

    def set_products(self, products: list[Product]) -> None:
        self.products = products

    def search(self, query: SearchQuery) -> list[Product]:
        if not self.products:
            return []

        if query.sku:
            exact = self._search_sku(query.sku)
            if exact:
                return exact[: query.limit]

        candidates = self.products
        if query.category != "other":
            category_filtered = [
                product for product in candidates if self._category_matches(product, query.category)
            ]
            if category_filtered:
                candidates = category_filtered

        if query.brand:
            brand_norm = normalize_text(query.brand)
            brand_filtered = [
                product
                for product in candidates
                if brand_norm and brand_norm in normalize_text(product.brand)
            ]
            if brand_filtered:
                candidates = brand_filtered

        slot_filtered = self._filter_by_slots(candidates, query)
        if slot_filtered:
            candidates = slot_filtered
        elif self._has_strict_slots(query):
            return []

        if query.in_stock_only:
            in_stock = [product for product in candidates if product.is_in_stock]
            if in_stock:
                candidates = in_stock

        scored = [(self._score(product, query), product) for product in candidates]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [product for _, product in scored[: query.limit]]

    def search_by_name(
        self,
        message: str,
        query: SearchQuery | None = None,
        limit: int = 3,
    ) -> list[Product]:
        """Find products whose card text covers almost all significant query tokens.

        High-precision path for messages that look like a concrete product name
        (e.g. pasted from the site). Returns [] for generic requests so the
        normal clarification scenarios stay in charge.
        """
        text = normalize_text(message)
        tokens = [
            token
            for token in text.split()
            if len(token) >= 2 and token not in NAME_QUERY_STOPWORDS
        ]
        if len(tokens) < 4:
            return []
        matches: list[tuple[float, int, Product]] = []
        for product in self.products:
            # Сопоставляем только с идентичностью товара (название/категория),
            # без длинного маркетингового описания — иначе общая лексика паспорта
            # даёт ложные совпадения.
            identity = self._identity_text(product)
            matched = sum(1 for token in tokens if token in identity)
            ratio = matched / len(tokens)
            if ratio >= 0.8 and matched >= 4:
                name_score = int(fuzz.partial_ratio(text, normalize_text(product.name)))
                matches.append((ratio, name_score, product))
        if not matches:
            return []
        matches.sort(key=lambda item: (-item[0], -item[1], not item[2].is_in_stock))
        return [product for _, _, product in matches[:limit]]

    def search_alternatives(self, query: SearchQuery) -> list[Product]:
        if not self.products or query.category == "other":
            return []

        candidates = [
            product for product in self.products if self._category_matches(product, query.category)
        ]
        if not candidates:
            return []

        scored = [
            (self._alternative_score(product, query), product)
            for product in candidates
        ]
        scored = [item for item in scored if item[0] >= self._alternative_threshold(query)]
        scored.sort(
            key=lambda item: (
                -item[0],
                not item[1].is_in_stock,
                item[1].price is None,
                item[1].price or float("inf"),
            )
        )
        return [product for _, product in scored[: min(query.limit, 6)]]

    def _search_sku(self, sku: str) -> list[Product]:
        needle = normalize_sku(sku)
        exact = [product for product in self.products if normalize_sku(product.sku) == needle]
        if exact:
            return exact
        partial = [
            product
            for product in self.products
            if needle and needle in normalize_sku(product.sku)
        ]
        return partial

    def _product_text(self, product: Product) -> str:
        attr_text = " ".join(f"{key} {value}" for key, value in product.attributes_normalized.items())
        return normalize_text(
            " ".join(
                [
                    product.sku,
                    product.name,
                    product.category_path,
                    product.brand or "",
                    product.description or "",
                    attr_text,
                ]
            )
        )

    def _identity_text(self, product: Product) -> str:
        """Short product identity (no marketing description) for name matching.

        Feed descriptions are long passport-like prose; matching a pasted product
        name against them caused false positives (a sewer pipe whose passport
        mentions «диаметром»/«фитинги»/«160» matched a "труба 16 мм" query).
        """
        return normalize_text(
            " ".join(
                [
                    product.sku,
                    product.name,
                    product.category_path,
                    product.brand or "",
                ]
            )
        )

    def _category_matches(self, product: Product, category: str) -> bool:
        text = self._product_text(product)
        needles = CATEGORY_NEEDLES.get(category, [])
        if category == "pipes" and "канализац" in text:
            return False
        return any(normalize_text(needle) in text for needle in needles)

    def _filter_by_slots(self, products: list[Product], query: SearchQuery) -> list[Product]:
        result = []
        for product in products:
            if self._slots_match(product, query.slots):
                result.append(product)
        return result

    def _slots_match(self, product: Product, slots: dict) -> bool:
        text = self._product_text(product)
        checks: list[bool] = []
        diameter = slots.get("diameter_mm")
        if diameter:
            checks.append(self._dimension_matches(product, int(diameter), ["диаметр", "размер"]))

        size_inch = slots.get("size_inch")
        if size_inch:
            checks.append(self._inch_size_matches(product, str(size_inch)))

        length = slots.get("length_mm")
        if length:
            checks.append(self._dimension_matches(product, int(length), ["длина"]))

        pump_type = slots.get("pump_type")
        if pump_type:
            checks.append(normalize_text(str(pump_type)) in text)

        mounting_length = slots.get("mounting_length_mm")
        if mounting_length:
            checks.append(self._dimension_matches(product, int(mounting_length), ["монтажная длина", "длина"]))

        head = slots.get("head_m")
        if head:
            checks.append(self._head_matches(product, float(head)))

        connection_size = slots.get("connection_size")
        if connection_size:
            checks.append(self._connection_matches(product, int(connection_size)))

        boiler_type = slots.get("boiler_type")
        if boiler_type:
            checks.append(normalize_text(str(boiler_type)) in text)

        contours = slots.get("contours")
        if contours:
            checks.append(normalize_text(str(contours)) in text)

        element_type = slots.get("element_type")
        if element_type:
            checks.append(normalize_text(str(element_type)) in text)

        sewer_scope = slots.get("sewer_scope")
        if sewer_scope:
            if sewer_scope == "внутренняя":
                checks.append("внутрен" in text or "htem" in text)
            elif sewer_scope == "наружная":
                checks.append("наруж" in text or "kg" in text)

        body_form = slots.get("body_form")
        if body_form:
            checks.append(normalize_text(str(body_form)) in text)

        application = slots.get("application")
        if application and slots.get("category_strict_application"):
            checks.append(normalize_text(str(application)) in text)

        if not checks:
            return True
        return all(checks)

    def _has_strict_slots(self, query: SearchQuery) -> bool:
        strict_by_category = {
            "pipes": {"diameter_mm", "element_type", "length_mm"},
            "sewer": {"sewer_scope", "element_type", "diameter_mm", "length_mm"},
            "pumps": {"pump_type", "mounting_length_mm", "head_m", "connection_size", "old_model"},
            "valves": {"application", "diameter_mm", "body_form", "union", "size_inch"},
            "radiator_fittings": {"application", "connection_form", "diameter_mm", "thermostatic_head"},
        }
        strict_keys = strict_by_category.get(query.category, set())
        return bool(strict_keys.intersection(query.slots))

    def _alternative_threshold(self, query: SearchQuery) -> int:
        if query.category == "sewer":
            return 55
        if query.category in {"pumps", "valves", "radiator_fittings"}:
            return 45
        return 35

    def _alternative_score(self, product: Product, query: SearchQuery) -> int:
        slots = query.slots
        text = self._product_text(product)
        score = 15

        element_type = slots.get("element_type")
        if element_type:
            if normalize_text(str(element_type)) in text:
                score += 35
            elif query.category == "sewer":
                return 0

        sewer_scope = slots.get("sewer_scope")
        if sewer_scope:
            if sewer_scope == "внутренняя" and ("внутрен" in text or "htem" in text):
                score += 30
            elif sewer_scope == "наружная" and ("наруж" in text or "kg" in text):
                score += 30
            elif query.category == "sewer":
                score -= 20

        diameter = slots.get("diameter_mm")
        if diameter:
            score += 25 if self._dimension_matches(product, int(diameter), ["диаметр", "размер"]) else -12

        length = slots.get("length_mm")
        if length:
            score += 20 if self._dimension_matches(product, int(length), ["длина"]) else -8

        pump_type = slots.get("pump_type")
        if pump_type:
            score += 35 if normalize_text(str(pump_type)) in text else -20

        connection_size = slots.get("connection_size")
        if connection_size:
            score += 20 if self._connection_matches(product, int(connection_size)) else -12

        head = slots.get("head_m")
        if head:
            score += 25 if self._head_matches(product, float(head)) else -18

        boiler_type = slots.get("boiler_type")
        if boiler_type:
            score += 30 if normalize_text(str(boiler_type)) in text else -15

        body_form = slots.get("body_form")
        if body_form:
            score += 20 if normalize_text(str(body_form)) in text else -8

        application = slots.get("application")
        if application:
            score += 15 if normalize_text(str(application)) in text else -5

        if product.is_in_stock:
            score += 8
        return score

    def _number_matches(self, text: str, number: int) -> bool:
        return bool(re.search(rf"(^|[^0-9]){number}([^0-9]|$)", text))

    def _inch_size_matches(self, product: Product, size_inch: str) -> bool:
        normalized = size_inch.replace(" ", "")
        text = self._product_text(product)
        if normalized in text:
            return True
        attr_blob = " ".join(product.attributes_normalized.values())
        return normalized in normalize_text(attr_blob)

    def _dimension_matches(self, product: Product, number: int, keys: list[str]) -> bool:
        key_texts = [normalize_text(key) for key in keys]
        values = []
        for attr_key, attr_value in product.attributes_normalized.items():
            normalized_key = normalize_text(attr_key)
            if any(key_text in normalized_key for key_text in key_texts):
                values.append(normalize_text(attr_value))
        if values:
            return any(self._number_matches(value, number) for value in values)
        # В реальном фиде размеры часто только в названии вида «50*1500», а «*»
        # выбрасывается нормализацией — приводим её к «х», чтобы 50х1500 распознавалось.
        fallback = normalize_text(product.name.replace("*", "х"))
        if any(key in {"диаметр", "размер"} for key in key_texts):
            return self._diameter_matches_name(fallback, number)
        if "длина" in key_texts:
            return self._length_matches_name(fallback, number)
        return self._number_matches(fallback, number)

    def _diameter_matches_name(self, text: str, number: int) -> bool:
        compact = normalize_text(text)
        pattern = rf"(?<!pn\s)(?<!pn)(^|[^0-9]){number}\s*(?:мм|mm)\b"
        if re.search(pattern, compact):
            return True
        return bool(re.search(rf"(^|[^0-9]){number}\s*[xх×]\s*\d+", compact))

    def _length_matches_name(self, text: str, number: int) -> bool:
        compact = normalize_text(text)
        return bool(
            re.search(rf"[xх×]\s*{number}([^0-9]|$)", compact)
            or re.search(rf"(^|[^0-9]){number}\s*(?:мм|mm)([^0-9]|$)", compact)
        )

    def _head_matches(self, product: Product, head_m: float) -> bool:
        head = int(head_m) if head_m.is_integer() else head_m
        values = []
        for attr_key, attr_value in product.attributes_normalized.items():
            if "напор" in normalize_text(attr_key):
                values.append(normalize_text(attr_value))
        if values:
            return any(
                bool(re.search(rf"(^|[^0-9]){re.escape(str(head))}([^0-9]|$)", value))
                for value in values
            )
        return bool(
            re.search(rf"(^|[^0-9]){re.escape(str(head))}([^0-9]|$)", normalize_text(product.name))
        )

    def _connection_matches(self, product: Product, connection_size: int) -> bool:
        values = []
        key_markers = ["присоедин", "подключ", "диаметр", "dn", "размер"]
        for attr_key, attr_value in product.attributes_normalized.items():
            normalized_key = normalize_text(attr_key)
            if any(marker in normalized_key for marker in key_markers):
                values.append(normalize_text(attr_value))
        if values and any(self._number_matches(value, connection_size) for value in values):
            return True

        text = normalize_text(product.name)
        return bool(re.search(rf"(^|[^0-9]){connection_size}\s*[-/]", text))

    def _expanded_query_text(self, query: SearchQuery) -> str:
        text = normalize_text(query.original_text)
        additions: list[str] = []
        for canonical, variants in SYNONYMS.items():
            if any(normalize_text(variant) in text for variant in variants):
                additions.extend(variants)
                additions.append(canonical)
        for value in query.slots.values():
            if isinstance(value, str):
                additions.append(value)
        return normalize_text(" ".join([text, *additions]))

    def _score(self, product: Product, query: SearchQuery) -> int:
        text = self._product_text(product)
        query_text = self._expanded_query_text(query)
        if not query_text:
            return 1
        score = fuzz.partial_ratio(query_text, text)
        if query.category != "other" and self._category_matches(product, query.category):
            score += 25
        if query.brand and normalize_text(query.brand) in normalize_text(product.brand):
            score += 20
        if product.is_in_stock:
            score += 5
        return int(score)
