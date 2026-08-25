"""Transparent FeedSearchAgent wrapper that emits shadow diagnostics."""

from __future__ import annotations

from typing import Any

from app.diagnostic_telemetry import catalogue_manifest, record_search_event
from app.models import Product, SearchQuery

from .feed_search import FeedSearchAgent


class DiagnosticFeedSearchAgent(FeedSearchAgent):
    """Record public retrieval operations without changing their results."""

    _catalog_manifest_cache: dict[str, Any] | None

    def set_products(self, products: list[Product]) -> None:
        super().set_products(products)
        # Hashing 14k full feed rows is intentionally lazy: with both feature
        # flags off this wrapper has the same startup cost as FeedSearchAgent.
        self._catalog_manifest_cache = None

    def get_catalog_manifest(self, source: str) -> dict[str, Any]:
        if self._catalog_manifest_cache is None:
            self._catalog_manifest_cache = catalogue_manifest(
                self.products,
                source,
            )
        return {**self._catalog_manifest_cache, "source": source}

    @staticmethod
    def _skus(products: list[Product]) -> list[str]:
        return [product.sku for product in products]

    def resolve_sku_mentions(self, message: str) -> list[Product]:
        try:
            result = super().resolve_sku_mentions(message)
        except Exception as exc:
            record_search_event(
                operation="resolve_sku_mentions",
                query=message,
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="resolve_sku_mentions",
            query=message,
            result_skus=self._skus(result),
        )
        return result

    def search(self, query: SearchQuery) -> list[Product]:
        try:
            result = super().search(query)
        except Exception as exc:
            record_search_event(
                operation="search",
                query=query,
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="search",
            query=query,
            result_skus=self._skus(result),
        )
        return result

    def search_by_name(
        self,
        message: str,
        query: SearchQuery | None = None,
        limit: int = 3,
    ) -> list[Product]:
        try:
            result = super().search_by_name(message, query=query, limit=limit)
        except Exception as exc:
            record_search_event(
                operation="search_by_name",
                query=query or {"message": message, "limit": limit},
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="search_by_name",
            query=query or {"message": message, "limit": limit},
            result_skus=self._skus(result),
        )
        return result

    def search_alternatives(self, query: SearchQuery) -> list[Product]:
        try:
            result = super().search_alternatives(query)
        except Exception as exc:
            record_search_event(
                operation="search_alternatives",
                query=query,
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="search_alternatives",
            query=query,
            result_skus=self._skus(result),
            relaxations=sorted(self.alternative_relaxed_fields(query)),
        )
        return result

    def search_nearest_variants(
        self,
        query: SearchQuery,
        *,
        max_groups: int = 2,
        per_group: int = 2,
    ) -> list[tuple[str, list[Product]]]:
        try:
            result = super().search_nearest_variants(
                query,
                max_groups=max_groups,
                per_group=per_group,
            )
        except Exception as exc:
            record_search_event(
                operation="search_nearest_variants",
                query=query,
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="search_nearest_variants",
            query=query,
            result_skus=[
                product.sku for _, products in result for product in products
            ],
            relaxations=[label for label, _ in result],
        )
        return result

    def find_named_models(self, **kwargs: Any) -> list[Product]:
        try:
            result = super().find_named_models(**kwargs)
        except Exception as exc:
            record_search_event(
                operation="find_named_models",
                query=kwargs,
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="find_named_models",
            query=kwargs,
            result_skus=self._skus(result),
        )
        return result

    def retrieve_for_consult(
        self,
        categories: list[str],
        slots: dict | None = None,
        per_category: int = 4,
    ) -> list[Product]:
        query = {
            "categories": categories,
            "slots": slots or {},
            "per_category": per_category,
        }
        try:
            result = super().retrieve_for_consult(
                categories,
                slots=slots,
                per_category=per_category,
            )
        except Exception as exc:
            record_search_event(
                operation="retrieve_for_consult",
                query=query,
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="retrieve_for_consult",
            query=query,
            result_skus=self._skus(result),
        )
        return result

    def search_unsupported_family(
        self,
        pattern: str,
        message: str,
        limit: int = 3,
        required_word: str | None = None,
    ) -> list[Product]:
        query = {
            "family_pattern": pattern,
            "message": message,
            "limit": limit,
            "required_word": required_word,
        }
        try:
            result = super().search_unsupported_family(
                pattern,
                message,
                limit=limit,
                required_word=required_word,
            )
        except Exception as exc:
            record_search_event(
                operation="search_unsupported_family",
                query=query,
                result_skus=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        record_search_event(
            operation="search_unsupported_family",
            query=query,
            result_skus=self._skus(result),
        )
        return result
