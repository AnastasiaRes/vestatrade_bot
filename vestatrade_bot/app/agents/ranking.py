from __future__ import annotations

import re

from app.models import Product, SearchQuery

from .product_constraints import normalize_thread_pair, product_thread_pair
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
            # Цена — сам запрос («подешевле»). Keep stock as the first safety
            # boundary and use the preferred brand only as a final tie-breaker;
            # otherwise an expensive VALTEC card can precede a cheaper,
            # equally compatible in-stock pump and fail the card guard.
            ranked.sort(
                key=lambda product: (
                    *self._explicit_boiler_power_priority(product, query),
                    not product.is_in_stock,
                    product.price is None,
                    product.price or float("inf"),
                    not self._default_preferred_brand(product, query),
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
                *self._explicit_boiler_power_priority(product, query),
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

    def _explicit_boiler_power_priority(
        self,
        product: Product,
        query: SearchQuery,
    ) -> tuple[int, float]:
        if query.category != "boilers" or query.slots.get("power_kw") is None:
            return (0, 0.0)
        requested_kw = float(query.slots["power_kw"])
        actual_kw = self._extract_power_kw(product)
        if actual_kw is None:
            return (4, float("inf"))
        distance = abs(actual_kw - requested_kw)
        exact = distance <= 0.05
        if exact and product.is_in_stock:
            tier = 0
        elif exact:
            tier = 1
        elif product.is_in_stock:
            tier = 2
        else:
            tier = 3
        return (tier, distance)

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
        expected = normalize_thread_pair(wanted)
        return expected is not None and product_thread_pair(product) == expected

    def _thread_code(self, product: Product) -> str | None:
        """Backward-compatible delegate to the shared catalogue parser."""
        return product_thread_pair(product)

    def _filter_weak_boilers(self, products: list[Product], query: SearchQuery) -> list[Product]:
        # An explicit rating ("котёл 6 кВт") is an identity-like catalogue
        # parameter, not a minimum capacity.  Do not discard nearby lower-power
        # analogues here; only area-based sizing has a safe minimum threshold.
        required_kw = self._required_boiler_kw_for_area(query)
        if not required_kw:
            return products
        adequate = [
            product
            for product in products
            if (self._extract_power_kw(product) or 0) >= required_kw * 0.85
        ]
        return adequate or products

    def _required_boiler_kw_for_area(self, query: SearchQuery) -> float | None:
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
