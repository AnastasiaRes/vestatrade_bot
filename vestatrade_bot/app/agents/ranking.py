from __future__ import annotations

import re

from app.models import Product, SearchQuery

from .utils import normalize_sku, normalize_text


class RankingAgent:
    def rank(self, products: list[Product], query: SearchQuery) -> list[Product]:
        ranked = list(products)
        if not ranked:
            return []

        if query.category == "boilers":
            ranked = self._filter_weak_boilers(ranked, query)

        if query.sku:
            needle = normalize_sku(query.sku)
            ranked.sort(key=lambda product: normalize_sku(product.sku) != needle)

        if query.cheap:
            ranked.sort(key=lambda product: (product.price is None, product.price or float("inf")))
            return ranked

        if query.in_stock_only:
            ranked.sort(key=lambda product: not product.is_in_stock)

        if query.brand:
            brand = normalize_text(query.brand)
            ranked.sort(key=lambda product: brand not in normalize_text(product.brand))

        ranked.sort(key=lambda product: (not product.is_in_stock, product.price is None, product.price or 0))
        return ranked

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
        text = normalize_text(
            " ".join(
                [
                    product.name,
                    product.description or "",
                    " ".join(product.attributes_normalized.values()),
                ]
            )
        )
        match = re.search(r"(\d+(?:[,.]\d+)?)\s*квт", text)
        if not match:
            return None
        return float(match.group(1).replace(",", "."))

