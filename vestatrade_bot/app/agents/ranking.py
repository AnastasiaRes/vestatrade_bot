from __future__ import annotations

import re

from app.models import Product, SearchQuery

from .utils import normalize_sku, normalize_text


DEFAULT_PREFERRED_BRAND = "valtec"


class RankingAgent:
    def rank(self, products: list[Product], query: SearchQuery) -> list[Product]:
        ranked = list(products)
        if not ranked:
            return []

        if query.category == "boilers":
            ranked = self._filter_weak_boilers(ranked, query)

        if query.cheap:
            # Цена — сам запрос («подешевле»), но товар в наличии всё равно выше.
            ranked.sort(
                key=lambda product: (
                    not self._default_preferred_brand(product, query),
                    not product.is_in_stock,
                    product.price is None,
                    product.price or float("inf"),
                )
            )
            return ranked

        # ОДИН составной ключ. Раньше это были последовательные .sort() по
        # отдельным признакам, и финальная сортировка по цене затирала все
        # предыдущие: точное совпадение по бренду/резьбе оказывалось третьим
        # после более дешёвых, но не подходящих товаров.
        needle = normalize_sku(query.sku) if query.sku else None
        ranked.sort(
            key=lambda product: (
                needle is not None and normalize_sku(product.sku) != needle,
                not self._default_preferred_brand(product, query),
                -self._relevance_score(product, query),
                not product.is_in_stock,
                product.price is None,
                product.price or 0,
            )
        )
        return ranked

    def _default_preferred_brand(
        self,
        product: Product,
        query: SearchQuery,
    ) -> bool:
        if query.brand or query.sku:
            return False
        return normalize_text(product.brand) == DEFAULT_PREFERRED_BRAND

    def _relevance_score(self, product: Product, query: SearchQuery) -> int:
        """How many of the constraints the customer actually stated are met.

        Only explicit constraints count, so an unconstrained search keeps its
        previous stock-then-price order.
        """
        score = 0
        if query.brand and normalize_text(query.brand) in normalize_text(product.brand):
            score += 2
        thread = query.slots.get("thread_type")
        if thread and self._thread_matches(product, str(thread)):
            score += 2
        for token in query.slots.get("name_tokens") or []:
            if normalize_text(str(token)) in normalize_text(product.name):
                score += 1
        return score

    def _thread_matches(self, product: Product, wanted: str) -> bool:
        return self._thread_code(product) == wanted

    def _thread_code(self, product: Product) -> str | None:
        """Canonical thread pairing of a product: ff / fm / mm.

        The feed states it as «тип резьбы» for some products and only inside the
        name for others («вн.-вн.», «ВН/НР»), so both are read.
        """
        attr_text = " ".join(
            normalize_text(str(value))
            for key, value in product.attributes_normalized.items()
            if "резьб" in normalize_text(str(key))
        )
        name = normalize_text(product.name)
        for text in (attr_text, name):
            if not text:
                continue
            if "(ff)" in text or re.search(r"внутренн\w*\s+внутренн", text):
                return "ff"
            if "(fm)" in text or re.search(r"внутренн\w*\s+наружн|наружн\w*\s+внутренн", text):
                return "fm"
            if "(mm)" in text or re.search(r"наружн\w*\s+наружн", text):
                return "mm"
            if re.search(r"\bвн\.?\s*[-/]\s*вн\b|\bвр\s*[-/]\s*вр\b", text):
                return "ff"
            if re.search(r"\bвн\.?\s*[-/]\s*нар\b|\bвн\s*[-/]\s*нр\b|\bвр\s*[-/]\s*нр\b", text):
                return "fm"
            if re.search(r"\bнар\.?\s*[-/]\s*нар\b|\bнр\s*[-/]\s*нр\b", text):
                return "mm"
            if text.strip() == "внутренняя":
                return "ff"
            if text.strip() == "наружная":
                return "mm"
        return None

    def _filter_weak_boilers(self, products: list[Product], query: SearchQuery) -> list[Product]:
        required_kw = self._required_boiler_kw(query)
        if not required_kw:
            return products
        adequate = [
            product
            for product in products
            if (self._extract_power_kw(product) or 0) >= required_kw * 0.85
        ]
        return adequate or products

    def _required_boiler_kw(self, query: SearchQuery) -> float | None:
        if query.slots.get("power_kw"):
            return float(query.slots["power_kw"])
        if query.slots.get("area_m2"):
            return float(query.slots["area_m2"]) / 10.0
        return None

    def _extract_power_kw(self, product: Product) -> float | None:
        for key, value in product.attributes_normalized.items():
            key_text = normalize_text(str(key))
            if "мощ" not in key_text or "квт" not in key_text:
                continue
            number = re.search(r"\d+(?:[,.]\d+)?", str(value))
            if number:
                return float(number.group(0).replace(",", "."))
        # Do not inspect the free-form description here: series descriptions
        # commonly list powers of sibling models and are not SKU-specific.
        text = normalize_text(
            " ".join([product.name, *product.attributes_normalized.values()])
        )
        match = re.search(r"(\d+(?:[,.]\d+)?)\s*квт", text)
        if not match:
            return None
        return float(match.group(1).replace(",", "."))
