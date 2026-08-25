"""Read-only, machine-readable contract coverage audit construction."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from app.models import Product

from .contracts import CatalogProductSnapshot, CoverageEntry, FeedCoverageAudit
from .registry import ProductContractRegistry, normalize_identity


def build_feed_coverage_audit(
    products: Iterable[Product],
    snapshot: Iterable[CatalogProductSnapshot],
    *,
    source_path: str,
    source_sha256: str,
    raw_offer_count: int,
    registry: ProductContractRegistry | None = None,
) -> FeedCoverageAudit:
    product_list = tuple(products)
    snapshot_list = tuple(snapshot)
    if len(product_list) != len(snapshot_list):
        raise ValueError("products and snapshot must preserve one-to-one ordering")
    selected_registry = registry or ProductContractRegistry()
    grouped: dict[object, list[tuple[Product, CatalogProductSnapshot]]] = defaultdict(list)
    unsupported_reasons: dict[str, int] = defaultdict(int)
    for product, normalized in zip(product_list, snapshot_list, strict=True):
        grouped[normalized.product_kind].append((product, normalized))
        if normalized.unsupported_reason:
            unsupported_reasons[normalized.unsupported_reason] += 1

    entries: list[CoverageEntry] = []
    for kind, rows in sorted(grouped.items(), key=lambda item: item[0].value):
        count = len(rows)
        contract = selected_registry.for_kind(kind)
        fact_names = tuple(
            definition.name for definition in (contract.fact_definitions if contract else ())
        )
        presence: dict[str, float] = {}
        structured: dict[str, float] = {}
        source_text_facts: set[str] = set()
        for name in fact_names:
            present = 0
            structured_present = 0
            for _, normalized in rows:
                fact = next((item for item in normalized.facts if item.name == name), None)
                if fact is not None:
                    present += 1
                    structured_present += int(fact.provenance.source == "attribute")
                    if fact.provenance.source in {"name", "description"}:
                        source_text_facts.add(name)
            presence[name] = round(present / count, 4)
            structured[name] = round(structured_present / count, 4)
        type_values = {
            str(product.attributes_normalized.get("тип товара") or "")
            for product, _ in rows
        }
        ambiguities: list[str] = []
        if "" in type_values:
            ambiguities.append("structured_product_type_missing_for_some")
        categories = tuple(sorted({product.category_path for product, _ in rows}))
        if len(categories) > 1:
            ambiguities.append("product_kind_spans_multiple_feed_categories")
        entries.append(
            CoverageEntry(
                product_kind=kind,
                role=rows[0][1].role,
                count=count,
                contract_id=contract.contract_id if contract else None,
                categories=categories,
                catalog_type_values=tuple(sorted(value for value in type_values if value)),
                fact_presence_coverage=presence,
                missing_fact_fraction={
                    name: round(1.0 - value, 4) for name, value in presence.items()
                },
                structured_attribute_coverage=structured,
                name_or_description_facts=tuple(sorted(source_text_facts)),
                ambiguity_codes=tuple(ambiguities),
            )
        )
    return FeedCoverageAudit(
        source_path=source_path,
        source_sha256=source_sha256,
        raw_offer_count=raw_offer_count,
        sanitized_product_count=len(snapshot_list),
        entries=tuple(entries),
        unsupported_count=sum(
            len(rows) for kind, rows in grouped.items() if kind.value == "unsupported"
        ),
        unsupported_reason_counts=dict(sorted(unsupported_reasons.items())),
        metadata={
            "contract_count": len(selected_registry.contracts),
            "covered_product_kind_count": sum(
                1 for entry in entries if entry.contract_id is not None
            ),
            "source_is_read_only": True,
            "catalog_was_not_modified": True,
        },
    )
