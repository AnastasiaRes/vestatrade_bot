#!/usr/bin/env python3
"""Build a read-only interface-evidence matrix for grounded V2 Compatibility.

The audit is intentionally not a second catalogue or passport index.  It uses
the same normalized feed, document attachments and ``InterfaceFactService`` as
V2, but disables LLM/embedding retrieval.  The output tells us which future
compatibility profiles have evidence for a positive verdict and which must
continue to fail closed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.orchestrator import ChatOrchestrator
from app.catalog_v2.contracts import ProductKind
from app.compatibility_v2.service import InterfaceFactService
from app.config import get_settings


DEFAULT_OUTPUT = (
    ROOT / "reports" / "widget_interface_facts_v2_2026-08-30" / "coverage_matrix.md"
)

_SEWER_KINDS = frozenset(
    {
        ProductKind.SEWER_PIPE,
        ProductKind.SEWER_ELBOW,
        ProductKind.TEE,
        ProductKind.COUPLING,
        ProductKind.REDUCING_COUPLING,
    }
)
_THREAD_PREDICATES = ("connection_size", "connection_pattern", "thread_standard")
_SEWER_PREDICATES = ("diameter_mm", "sewer_scope", "sewer_system_family")


def _predicates(product) -> tuple[str, ...]:
    predicates: list[str] = []
    names = {fact.name for fact in product.facts}
    if product.product_kind in {
        ProductKind.THERMOSTATIC_HEAD,
        ProductKind.RADIATOR_VALVE,
    }:
        predicates.append("control_thread")
    if product.product_kind in _SEWER_KINDS:
        predicates.extend(_SEWER_PREDICATES)
    if product.product_kind in {
        ProductKind.BOILER,
        ProductKind.GAS_BOILER,
        ProductKind.ELECTRIC_BOILER,
    }:
        predicates.append("integrated_circulation_pump")
    if names.intersection({"connection_size", "connection_pattern", "thread_standard"}):
        predicates.extend(_THREAD_PREDICATES)
    return tuple(dict.fromkeys(predicates))


def _document_scope(product) -> str:
    documents = tuple(getattr(product, "documents", ()) or ())
    if not documents:
        return "—"
    return ", ".join(
        f"{item.filename} ({item.binding_scope or 'unbound'})" for item in documents
    )


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_report() -> str:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            # V2 snapshots are otherwise intentionally lazy in a public-Legacy
            # process.  The token is process-local and never printed or written.
            "dialogue_v2_qa_controls_enabled": True,
            "dialogue_v2_qa_control_token": "interface-fact-audit",
        }
    )
    bot = ChatOrchestrator(settings=settings)
    bot.reload_products(refresh=False)
    snapshot = bot.answer_source_snapshot_v2
    if snapshot is None:
        raise RuntimeError("V2 answer source snapshot was not created for audit")
    raw_by_sku = {product.sku: product for product in bot.search_agent.products}
    service = InterfaceFactService(snapshot, products=bot.search_agent.products)

    rows: list[str] = []
    summary: Counter[tuple[str, str, str]] = Counter()
    for product in sorted(snapshot.products, key=lambda item: item.sku):
        predicates = _predicates(product)
        if not predicates:
            continue
        raw_product = raw_by_sku.get(product.sku)
        for predicate in predicates:
            resolution = service.observe(product.sku, predicate)
            selected = resolution.selected_fact
            value = (
                f"{selected.value}{(' ' + selected.unit) if selected and selected.unit else ''}"
                if selected is not None
                else "—"
            )
            source = (
                f"{selected.source_kind.value}: {selected.document}"
                if selected is not None
                else "—"
            )
            summary[(product.product_kind.value, predicate, resolution.status.value)] += 1
            rows.append(
                "| "
                + " | ".join(
                    _cell(value)
                    for value in (
                        product.sku,
                        product.product_kind.value,
                        predicate,
                        resolution.status.value,
                        value,
                        source,
                        selected.endpoint.value if selected and selected.endpoint else "—",
                        len(resolution.observations),
                        ", ".join(resolution.reason_codes) or "—",
                        _document_scope(raw_product),
                    )
                )
                + " |"
            )

    summary_rows = [
        "| Product kind | Predicate | Status | SKU count |",
        "| --- | --- | --- | ---: |",
    ]
    summary_rows.extend(
        f"| {kind} | {predicate} | {status} | {count} |"
        for (kind, predicate, status), count in sorted(summary.items())
    )
    attached_documents = sum(len(product.documents) for product in raw_by_sku.values())
    return "\n".join(
        (
            "# V2 Compatibility · interface evidence coverage",
            "",
            f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
            f"Feed products: **{len(snapshot.products)}**  ",
            f"Source revision: `{snapshot.source_revision}`  ",
            f"Attached document bindings: **{attached_documents}**",
            "",
            "This is a read-only coverage checkpoint. It does not call an LLM, build a new index, or change customer-visible state. A `proven` row only says the current Compatibility adapter has an eligible fact; it does not make a multi-port connection compatible.",
            "",
            "## Summary",
            "",
            *summary_rows,
            "",
            "## Per-SKU evidence",
            "",
            "| SKU | Product kind | Predicate | Resolution | Selected value | Selected source | Endpoint surface | Observations | Reason | Attached documents |",
            "| --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- |",
            *rows,
            "",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
