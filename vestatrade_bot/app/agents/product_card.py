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
        # PN и SDR приходят из разбора названия и есть теперь у большинства
        # труб. Без них в этом списке они попадали только в остаточные слоты,
        # хотя класс давления — то, чем трубы одного диаметра и материала
        # отличаются друг от друга чаще всего.
        "класс давления",
        "максимальная рабочая температура",
        "максимальная температура применения",
        # Давление из паспорта задано по классу эксплуатации, и для отопления
        # это разные числа: у PP-FIBER PN20 радиаторный класс даёт 6 бар, у
        # PP-ALUX PN25 — 10. Общее «максимальное рабочее давление» такую
        # разницу скрывает, поэтому отопительные классы называем отдельно.
        "рабочее давление, радиаторное отопление",
        "рабочее давление, напольное отопление",
        "максимальное рабочее давление",
        "толщина стенки",
        "армирование",
        "sdr",
        "кислородный барьер",
        "длина",
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
    "radiator_fittings": [
        "тип товара",
        "диапазон регулирования температуры",
        "присоединительная резьба",
        "диаметр",
        "подключение",
        "комплектация",
    ],
    "radiators": ["тип", "высота", "межосевое расстояние", "количество секций", "теплоотдача", "площадь обогрева", "диаметр подключения"],
    "fittings": ["тип товара", "диаметр", "присоединительная резьба", "угол", "тип присоединения"],
}


# Требование покупателя должно быть видно в карточке. Статический список
# RELEVANT_ATTRS отражает «обычно важное» для категории и не знает, что именно
# спросили в этом диалоге: на запрос «полнопроходной прямой» карточка
# показывала диаметр/резьбу/ручку, и подтвердить полнопроходность было нечем,
# хотя поле есть в фиде.
CONSTRAINT_ATTR_MARKERS: dict[str, tuple[str, ...]] = {
    "full_bore": ("пропускная способность",),
    "body_form": ("форма корпуса",),
    "form": ("форма корпуса",),
    "handle_type": ("тип ручки", "рукоят"),
    "thread_type": ("тип резьбы",),
    "thread_gender": ("тип резьбы",),
    "size_inch": ("диаметр подключения", "дюйм"),
    "diameter_mm": ("диаметр",),
    "angle_deg": ("угол",),
    "material": ("материал",),
    "radiator_panel_type": ("тип",),
    "radiator_connection": ("тип подключения", "подключение"),
    "radiator_size_mm": ("межосев",),
    "sections": ("секц",),
    "voltage_v": ("напряжение", "питание"),
}


def constrained_characteristic_keys(
    characteristics: dict[str, str],
    slots: dict | None,
) -> list[str]:
    """Ключи карточки, которые отвечают активным условиям запроса."""
    active = slots or {}
    markers: list[str] = []
    for slot_key, slot_markers in CONSTRAINT_ATTR_MARKERS.items():
        value = active.get(slot_key)
        if value is None or value == "" or value is False:
            continue
        markers.extend(slot_markers)
    if not markers:
        return []
    return [
        key
        for key in characteristics
        if any(marker in normalize_text(str(key)) for marker in markers)
    ]


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
    INTERNAL_PROVENANCE_ATTRS = (
        "источник диапазона мощности",
        "источник документа",
        "файл паспорта",
        "страница паспорта",
    )

    def _is_identity_attribute(self, key: str) -> bool:
        key_text = normalize_text(str(key))
        return any(marker in key_text for marker in self.IDENTITY_ATTRS)

    def is_internal_provenance_attribute(self, key: str) -> bool:
        key_text = normalize_text(str(key))
        return any(marker in key_text for marker in self.INTERNAL_PROVENANCE_ATTRS)

    def _pick_characteristics(self, product: Product, query: SearchQuery) -> dict[str, str]:
        attrs = {
            key: value
            for key, value in product.attributes_normalized.items()
            if not self._is_identity_attribute(key)
            and not self.is_internal_provenance_attribute(key)
        }
        if not attrs:
            return {}

        if query.category == "water_heaters":
            return self._pick_water_heater_characteristics(product, attrs)

        preferred = RELEVANT_ATTRS.get(query.category, [])
        # Pumps need four grounded dimensions together: kind, head, mounting
        # length and connection.  Dropping the fourth field made the bot suggest
        # compatible fittings and then lose the very size needed to select them.
        # У труб лимит поднят с четырёх до шести: разбор названия и паспорта
        # добавил им реальные параметры, и при четырёх слотах решающее число —
        # рабочее давление для класса отопления — вытеснялось материалом,
        # диаметром, PN и температурой, хотя именно оно различает PP-FIBER
        # (6 бар) и PP-ALUX (10 бар) на радиаторной магистрали.
        max_attributes = 5 if query.category in {"pumps", "hydraulic_accumulators", "boilers"} else (
            6 if query.category == "pipes" else 3
        )
        picked: dict[str, str] = {}
        # Поля, по которым покупатель поставил условие, попадают в карточку
        # первыми и расширяют лимит: иначе подтвердить требование нечем.
        constrained = self._constrained_attributes(attrs, product, query)
        if constrained:
            picked.update(constrained)
            max_attributes = max(max_attributes, len(picked) + 2)
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

    def _constrained_attributes(
        self,
        attrs: dict[str, str],
        product: Product,
        query: SearchQuery,
    ) -> dict[str, str]:
        """Атрибуты фида, отвечающие активным условиям запроса."""
        slots = query.slots or {}
        markers: list[str] = []
        for slot_key, slot_markers in CONSTRAINT_ATTR_MARKERS.items():
            value = slots.get(slot_key)
            if value is None or value == "" or value is False:
                continue
            markers.extend(slot_markers)
        if not markers:
            return {}
        selected: dict[str, str] = {}
        for marker in markers:
            for attr_key, value in attrs.items():
                if attr_key in selected or not value:
                    continue
                if marker in normalize_text(str(attr_key)) and self._safe_attribute(
                    product, attr_key, value
                ):
                    selected[attr_key] = value
                    break
        return selected

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
