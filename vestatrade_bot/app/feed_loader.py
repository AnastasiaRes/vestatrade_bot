from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.models import Product, model_to_dict
from app.agents.utils import normalize_sku, normalize_text


logger = logging.getLogger(__name__)

OFFER_TAGS = {"offer", "product", "item"}
INVALID_SKU_KEYS = {"", "na", "none", "нет", "безартикула"}


def _is_usable_sku(value: Any) -> bool:
    return normalize_sku(str(value or "")) not in INVALID_SKU_KEYS


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = html.unescape(value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    value = value.replace(",", ".")
    value = re.sub(r"[^0-9.]", "", value)
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    value = re.sub(r"[^0-9-]", "", value)
    try:
        return int(value)
    except ValueError:
        return None


def _first(mapping: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        values = mapping.get(key.lower())
        if values:
            return values[0]
    return None


def _all(mapping: dict[str, list[str]], *keys: str) -> list[str]:
    result: list[str] = []
    for key in keys:
        result.extend(mapping.get(key.lower()) or [])
    return result


class FeedLoader:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def fetch_feed(self) -> bytes:
        logger.info("Downloading Vesta Trade feed from %s", self.settings.feed_url)
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(self.settings.feed_url)
            response.raise_for_status()
            return response.content

    def parse_xml(self, xml_bytes: bytes) -> list[Product]:
        products: list[Product] = []
        catalog_date: str | None = None
        source = BytesIO(xml_bytes)

        for event, elem in ET.iterparse(source, events=("start", "end")):
            tag = _strip_namespace(elem.tag)
            if event == "start" and tag == "yml_catalog":
                catalog_date = elem.attrib.get("date")
            if event == "end" and tag in OFFER_TAGS:
                product = self._parse_offer(elem, catalog_date)
                if product:
                    products.append(product)
                elem.clear()

        if not products:
            logger.warning("No offer/product/item nodes were parsed from XML feed")
        return self._sanitize_products(products)

    def _sanitize_products(self, products: list[Product]) -> list[Product]:
        """Remove identities that cannot be grounded safely.

        The production feed currently contains placeholder articles (``?``) and
        the same article assigned to unrelated products.  Returning an arbitrary
        row for such an exact-SKU lookup is worse than an honest no-match, so
        conflicting identities are excluded.  True duplicate rows with the same
        normalized name are collapsed, preferring an in-stock/complete card.
        """
        by_sku: dict[str, Product] = {}
        conflicted: set[str] = set()
        placeholder_count = 0
        duplicate_count = 0
        conflict_count = 0
        recovered_placeholder_count = 0
        internal_article_count = 0
        foreign_description_count = 0

        for product in products:
            (
                product,
                article_removed,
                description_removed,
                placeholder_recovered,
            ) = self._sanitize_product_identity(product)
            recovered_placeholder_count += int(placeholder_recovered)
            internal_article_count += int(article_removed)
            foreign_description_count += int(description_removed)
            sku_key = normalize_sku(product.sku)
            if sku_key in INVALID_SKU_KEYS:
                placeholder_count += 1
                continue
            if sku_key in conflicted:
                continue
            previous = by_sku.get(sku_key)
            if previous is None:
                by_sku[sku_key] = product
                continue

            if normalize_text(previous.name) != normalize_text(product.name):
                by_sku.pop(sku_key, None)
                conflicted.add(sku_key)
                conflict_count += 1
                continue

            duplicate_count += 1
            by_sku[sku_key] = self._prefer_duplicate(previous, product)

        if (
            placeholder_count
            or duplicate_count
            or conflict_count
            or recovered_placeholder_count
            or internal_article_count
            or foreign_description_count
        ):
            logger.warning(
                "Feed identity sanitization: placeholders=%s duplicates_collapsed=%s "
                "conflicting_skus_excluded=%s placeholders_recovered=%s internal_articles_removed=%s "
                "foreign_descriptions_removed=%s",
                placeholder_count,
                duplicate_count,
                conflict_count,
                recovered_placeholder_count,
                internal_article_count,
                foreign_description_count,
            )
        return list(by_sku.values())

    def _sanitize_product_identity(
        self,
        product: Product,
    ) -> tuple[Product, bool, bool, bool]:
        """Keep top-level SKU/name/structured attributes as the identity authority."""
        updates: dict[str, Any] = {}
        attrs = dict(product.attributes_normalized or {})
        placeholder_recovered = False
        if not _is_usable_sku(product.sku):
            param_article = next(
                (
                    str(value)
                    for key, value in attrs.items()
                    if normalize_text(key) == "артикул" and _is_usable_sku(value)
                ),
                None,
            )
            if param_article:
                updates["sku"] = param_article
                placeholder_recovered = True

        authoritative_sku = str(updates.get("sku", product.sku))
        article_removed = False
        for key in list(attrs):
            if normalize_text(key) != "артикул":
                continue
            if normalize_sku(attrs[key]) != normalize_sku(authoritative_sku):
                attrs.pop(key, None)
                article_removed = True
        if article_removed:
            updates["attributes_normalized"] = attrs

        description_removed = False
        description = product.description or ""
        mentioned_articles = re.findall(
            r"артикул[\s:№#-]*([a-zа-я0-9][a-zа-я0-9._/\-]{2,})",
            normalize_text(description),
            flags=re.IGNORECASE,
        )
        has_foreign_article = any(
            normalize_sku(article) != normalize_sku(product.sku)
            for article in mentioned_articles
        )
        description_mentions_own_sku = bool(
            normalize_sku(authoritative_sku)
            and normalize_sku(authoritative_sku) in normalize_sku(description)
        )
        if has_foreign_article and not description_mentions_own_sku:
            # A concrete foreign article is strong evidence that the marketing
            # description was copied from another product.  Do not let it
            # override the card's name and structured characteristics later.
            updates["description"] = None
            description_removed = True

        if updates:
            data = model_to_dict(product)
            data.update(updates)
            product = Product(**data)
        return product, article_removed, description_removed, placeholder_recovered

    def _prefer_duplicate(self, left: Product, right: Product) -> Product:
        def quality(product: Product) -> tuple[int, int, int]:
            return (
                int(product.is_in_stock),
                int(bool(product.url) and product.price is not None),
                len(product.attributes_normalized or {}),
            )

        return right if quality(right) > quality(left) else left

    def _parse_offer(self, elem: ET.Element, catalog_date: str | None) -> Product | None:
        fields: dict[str, list[str]] = {}
        params: dict[str, str] = {}
        raw: dict[str, Any] = {"id": elem.attrib.get("id"), "attrs": dict(elem.attrib)}

        for child in list(elem):
            tag = _strip_namespace(child.tag)
            text = _clean_text(child.text)
            if tag == "param":
                param_name = _clean_text(child.attrib.get("name")) or "param"
                if text:
                    params[param_name.lower()] = text
                continue
            if text:
                fields.setdefault(tag, []).append(text)

        raw["fields"] = fields
        raw["params"] = params

        field_sku = _first(fields, "vendorCode", "vendor_code", "sku", "article", "articul")
        sku = (
            field_sku
            if _is_usable_sku(field_sku)
            else params.get("артикул")
            if _is_usable_sku(params.get("артикул"))
            else field_sku
            if field_sku is not None
            else elem.attrib.get("id")
        )
        name = _first(fields, "name", "title", "model") or params.get("полное наименование")
        if not sku or not name:
            logger.warning("Skipping product with missing sku/name: %s", raw)
            return None

        quantity = _to_int(_first(fields, "quantity", "stock_quantity", "stock", "available"))
        explicit_stock = _first(fields, "stock_status", "availability", "available")
        if quantity is not None:
            stock_status = "в наличии" if quantity > 0 else "нет в наличии"
        elif explicit_stock:
            stock_status = explicit_stock
        else:
            stock_status = "unknown"

        pictures = _all(fields, "picture", "image", "image_url")

        return Product(
            sku=sku,
            name=name,
            category_path=_first(fields, "category", "category_path", "categorypath") or "",
            brand=_first(fields, "vendor", "brand", "manufacturer"),
            url=_first(fields, "url", "link", "product_url"),
            image_url=pictures[0] if pictures else None,
            price=_to_float(_first(fields, "price")),
            currency=_first(fields, "currencyId", "currency", "currency_id") or "RUB",
            stock_status=stock_status,
            stock_qty=quantity,
            attributes_normalized=params,
            description=_first(fields, "description", "short_description"),
            updated_at=catalog_date or "",
            raw=raw,
        )

    def save_cache(self, products: list[Product], path: Path | None = None) -> None:
        products = self._sanitize_products(products)
        target = path or self.settings.products_cache_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps([model_to_dict(product) for product in products], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved %s products to cache %s", len(products), target)

    def load_cache(self, path: Path | None = None) -> list[Product]:
        target = path or self.settings.products_cache_path
        if not target.exists():
            raise FileNotFoundError(f"Products cache not found: {target}")
        data = json.loads(target.read_text(encoding="utf-8"))
        return self._sanitize_products([Product(**item) for item in data])

    def load_products(self, refresh: bool = False) -> tuple[list[Product], str]:
        if refresh:
            try:
                products = self.parse_xml(self.fetch_feed())
                self.save_cache(products)
                return products, "feed"
            except Exception as exc:
                logger.exception("Could not refresh feed, trying cache: %s", exc)

        try:
            return self.load_cache(), "cache"
        except Exception as cache_exc:
            if not refresh:
                try:
                    products = self.parse_xml(self.fetch_feed())
                    self.save_cache(products)
                    return products, "feed"
                except Exception as feed_exc:
                    logger.exception("Could not load feed or cache: %s / %s", cache_exc, feed_exc)
                    raise RuntimeError("Feed is unavailable and no products cache exists") from feed_exc
            raise RuntimeError("Feed is unavailable and no products cache exists") from cache_exc
