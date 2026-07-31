from __future__ import annotations

import html
import logging
import re

from app.models import Product, ProductCard, SearchQuery

from .utils import normalize_sku, normalize_text


logger = logging.getLogger(__name__)


RELEVANT_ATTRS: dict[str, list[str]] = {
    "pipes": [
        "назначение",
        "материал",
        "диаметр (мм)",
        "максимальная рабочая температура",
        "максимальная температура применения",
        "максимальное рабочее давление",
        "длина",
        "армирование",
    ],
    "sewer": ["тип товара", "диаметр (мм)", "длина", "материал"],
    "pumps": [
        "тип товара",
        "тип насоса",
        "напор",
        "производительность",
        "монтажная длина",
        "присоедин",
        "мощность",
    ],
    "boilers": [
        "мощность",
        "диапазон мощности отопления по паспорту",
        "тип котла",
        "количество контуров",
        "камера сгорания",
        "диаметр дымохода",
        "площадь",
    ],
    "water_heaters": [
        "объем бака",
        "тип водонагревателя",
        "вид нагрева",
        "монтаж",
        "мощность",
    ],
    "hydraulic_accumulators": [
        "тип товара",
        "объем бака",
        "объём бака",
        "ориентация бака",
        "присоединительный размер",
        "максимальное давление",
    ],
    "filters": [
        "тип товара",
        "типоразмер",
        "тонкость фильтрации",
        "мкм",
        "назначение",
        "производительность",
        "присоедин",
    ],
    "controls": [
        "тип товара",
        "напряжение",
        "параметры сети",
        "класс защиты",
        "тип управления",
        "монтаж",
    ],
    # «тип резьбы» и «тип ручки» стоят выше «типа присоединения»: у кранов
    # одного диаметра присоединение почти всегда «Резьбовой», и сравнение
    # «чем отличаются» показывало только цену, хотя товары различались именно
    # резьбой (ВР/ВР против ВР/НР) и рукояткой.
    "valves": [
        "диаметр",
        "тип резьбы",
        "тип ручки",
        "назначение",
        "тип присоединения",
        "тип конструкции",
    ],
    "radiator_fittings": ["тип товара", "диаметр", "подключение", "комплектация"],
    "radiators": ["тип", "высота", "межосевое расстояние", "количество секций", "теплоотдача", "площадь обогрева", "диаметр подключения"],
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

    # Идентификаторы, а не характеристики: «полное наименование» дублирует имя
    # карточки, «артикул»/«штрихкод» — её же артикул. Для 31% фида (категория
    # «other», где нет карты RELEVANT_ATTRS) именно они занимали все три слота,
    # и сравнение «чем отличаются» сопоставляло названия вместо параметров.
    IDENTITY_ATTRS = ("полное наименование", "штрихкод", "артикул")

    def _is_identity_attribute(self, key: str) -> bool:
        key_text = normalize_text(str(key))
        return any(marker in key_text for marker in self.IDENTITY_ATTRS)

    def _pick_characteristics(self, product: Product, query: SearchQuery) -> dict[str, str]:
        attrs = {
            key: value
            for key, value in product.attributes_normalized.items()
            if not self._is_identity_attribute(key)
        }
        if not attrs:
            return {}

        if query.category == "water_heaters":
            return self._pick_water_heater_characteristics(product, attrs)

        preferred = RELEVANT_ATTRS.get(query.category, [])
        # Pumps need four grounded dimensions together: kind, head, mounting
        # length and connection.  Dropping the fourth field made the bot suggest
        # compatible fittings and then lose the very size needed to select them.
        max_attributes = 5 if query.category in {"pumps", "hydraulic_accumulators", "boilers"} else (
            4 if query.category == "pipes" else 3
        )
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

    def _pick_water_heater_characteristics(
        self,
        product: Product,
        attrs: dict[str, str],
    ) -> dict[str, str]:
        """Show the five dimensions needed to verify a heater selection.

        ``Вид нагрева`` and ``Способ нагрева`` are alternative feed names for
        one concept, not two separate characteristics.  Grouping aliases keeps
        both from crowding out mounting or power on otherwise complete cards.
        """
        preferred_groups = (
            (
                "объем бака",
                "объём бака",
                "объем, л",
                "объём, л",
                "литраж",
            ),
            ("тип водонагревателя",),
            (
                "вид нагрева",
                "способ нагрева",
                "источник энергии",
                "тип нагрева",
            ),
            (
                "монтаж",
                "способ крепления",
                "тип размещения",
                "размещение",
            ),
            ("мощность",),
        )
        picked: dict[str, str] = {}
        for aliases in preferred_groups:
            matched = False
            for alias in aliases:
                for attr_key, value in attrs.items():
                    if (
                        alias in normalize_text(attr_key)
                        and value
                        and self._safe_water_heater_attribute(
                            product,
                            attr_key,
                            value,
                        )
                    ):
                        picked[attr_key] = value
                        matched = True
                        break
                if matched:
                    break

        for key, value in attrs.items():
            if (
                len(picked) >= 5
                or key in picked
                or not value
                or not self._safe_water_heater_attribute(product, key, value)
            ):
                continue
            picked[key] = value
        return picked

    def _safe_water_heater_attribute(
        self,
        product: Product,
        key: str,
        value: str,
    ) -> bool:
        if not self._safe_attribute(product, key, value):
            return False

        # Several gas-column rows currently contain values such as
        # ``мощность, кВт: 0.02`` while their exact descriptions identify
        # 20 kW appliances.  A grounded card must not repeat a physically
        # implausible primary heating power merely because it is present in the
        # feed.  Suppression is safer than multiplying by 1000: normalization
        # would require a single, independently confirmed value from a passport
        # or exact description.
        key_text = normalize_text(key)
        if "мощност" not in key_text or "квт" not in key_text:
            return True
        if any(
            marker in key_text
            for marker in [
                "теплообмен",
                "режим",
                "диапазон",
                "потреб",
            ]
        ):
            return True
        number_match = re.search(r"\d+(?:[.,]\d+)?", str(value))
        if not number_match:
            return True
        power_kw = float(number_match.group(0).replace(",", "."))
        return not (0 < power_kw < 0.1)

    def _safe_attribute(self, product: Product, key: str, value: str) -> bool:
        # The feed has rows where vendorCode (the card identity) and a copied
        # ``Артикул`` param point at different products.  Never render both as if
        # they were one consistent card.
        if "артикул" in normalize_text(key):
            return normalize_sku(value) == normalize_sku(product.sku)
        return True
