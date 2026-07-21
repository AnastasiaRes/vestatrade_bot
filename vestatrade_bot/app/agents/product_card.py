from __future__ import annotations

import html
import logging

from app.models import Product, ProductCard, SearchQuery

from .utils import normalize_sku, normalize_text


logger = logging.getLogger(__name__)


RELEVANT_ATTRS: dict[str, list[str]] = {
    "pipes": ["назначение", "материал", "диаметр (мм)", "длина", "армирование"],
    "sewer": ["тип товара", "диаметр (мм)", "длина", "материал"],
    "pumps": ["тип товара", "напор", "монтажная длина", "присоединение", "мощность"],
    "boilers": [
        "мощность",
        "диапазон мощности отопления по паспорту",
        "тип котла",
        "количество контуров",
        "площадь",
    ],
    "valves": ["назначение", "диаметр", "тип присоединения", "тип конструкции"],
    "radiator_fittings": ["тип товара", "диаметр", "подключение", "комплектация"],
    "radiators": ["тип", "межосевое расстояние", "количество секций", "теплоотдача", "площадь обогрева", "диаметр подключения"],
    "fittings": ["тип товара", "диаметр", "присоединительная резьба", "угол", "тип присоединения"],
}


class ProductCardAgent:
    def build_cards(self, products: list[Product], query: SearchQuery, limit: int = 3) -> list[ProductCard]:
        cards: list[ProductCard] = []
        for product in products:
            if len(cards) >= limit:
                break
            card = self.build_card(product, query)
            if card:
                cards.append(card)
        return cards

    def build_card(self, product: Product, query: SearchQuery) -> ProductCard | None:
        if not product.url:
            logger.error("Product %s has no URL and cannot be shown", product.sku)
            return None
        if product.price is None:
            logger.error("Product %s has no price and cannot be shown", product.sku)
            return None
        return ProductCard(
            sku=product.sku,
            name=html.unescape(product.name),
            brand=product.brand,
            price=product.price,
            currency=product.currency,
            stock_status=product.stock_status,
            stock_qty=product.stock_qty,
            url=product.url,
            image_url=product.image_url,
            characteristics=self._pick_characteristics(product, query),
        )

    def _pick_characteristics(self, product: Product, query: SearchQuery) -> dict[str, str]:
        attrs = product.attributes_normalized
        if not attrs:
            return {}

        preferred = RELEVANT_ATTRS.get(query.category, [])
        max_attributes = 4 if query.category == "boilers" else 3
        picked: dict[str, str] = {}
        for key in preferred:
            for attr_key, value in attrs.items():
                if key in attr_key and value and self._safe_attribute(product, attr_key, value):
                    picked[attr_key] = value
                    break
            if len(picked) >= max_attributes:
                return picked

        for key, value in attrs.items():
            if key in picked or not value or not self._safe_attribute(product, key, value):
                continue
            picked[key] = value
            if len(picked) >= max_attributes:
                break
        return picked

    def _safe_attribute(self, product: Product, key: str, value: str) -> bool:
        # The feed has rows where vendorCode (the card identity) and a copied
        # ``Артикул`` param point at different products.  Never render both as if
        # they were one consistent card.
        if "артикул" in normalize_text(key):
            return normalize_sku(value) == normalize_sku(product.sku)
        return True
