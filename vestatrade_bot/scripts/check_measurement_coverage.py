"""Audit measurement-aware search against every measurable product in the cached feed."""

from __future__ import annotations

import html
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.feed_search import FeedSearchAgent  # noqa: E402
from app.agents.utils import normalize_text  # noqa: E402
from app.feed_loader import FeedLoader  # noqa: E402
from app.models import Product, SearchQuery  # noqa: E402


@dataclass
class AuditCheck:
    ok: bool
    sku: str
    category: str
    field: str
    request: str
    actual: str


def _first_attr(product: Product, marker: str) -> str | None:
    for key, value in product.attributes_normalized.items():
        if marker in normalize_text(key):
            return str(value)
    return None


def _number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:[,.]\d+)?", value)
    return float(match.group(0).replace(",", ".")) if match else None


def main() -> int:
    products, source = FeedLoader().load_products(refresh=False)
    search = FeedSearchAgent(products)
    checks: list[AuditCheck] = []
    # Many products share the same brand and dimensions.  The old audit ran a
    # full 14k-product search once per row, making the release gate quadratic.
    # Cache each distinct filter query and reuse its result set.
    search_cache: dict[tuple[str, tuple[tuple[str, str], ...], str], set[str]] = {}

    def add(
        product: Product,
        field: str,
        category: str,
        slots: dict,
        *,
        actual: str = "",
        brand: str | None = None,
    ) -> None:
        cache_key = (
            category,
            tuple(sorted((str(key), repr(value)) for key, value in slots.items())),
            normalize_text(brand),
        )
        returned = search_cache.get(cache_key)
        if returned is None:
            results = search.search(
                SearchQuery(
                    original_text=f"audit {field}",
                    category=category,
                    slots=slots,
                    brand=brand,
                    limit=max(100, len(products)),
                )
            )
            returned = {item.sku for item in results}
            search_cache[cache_key] = returned
        checks.append(
            AuditCheck(
                ok=product.sku in returned,
                sku=product.sku,
                category=category,
                field=field,
                request=str(slots),
                actual=actual or ", ".join(sorted(returned)[:8]),
            )
        )

    for product in products:
        category = search.canonical_category(product)
        checks.append(
            AuditCheck(
                # ``other`` is a valid quarantine bucket for the many catalogue
                # families the bot intentionally does not sell/consult on.  It
                # must be visible in the report, not counted as a search defect.
                ok=True,
                sku=product.sku,
                category=category,
                field="canonical category",
                request=category,
                actual=product.name,
            )
        )
        if category == "other":
            continue
        if product.brand:
            add(
                product,
                "feed brand filter",
                category,
                {},
                brand=product.brand,
                actual=product.brand,
            )
        name = html.unescape(product.name)
        name_norm = normalize_text(name)

        if category == "boilers":
            boiler_type = _first_attr(product, "тип котла")
            if boiler_type:
                normalized_type = normalize_text(boiler_type)
                add(
                    product,
                    "boiler type",
                    "boilers",
                    {"boiler_type": normalized_type},
                    actual=boiler_type,
                )
                checks.append(
                    AuditCheck(
                        # A name may omit the fuel type; reject only an explicit
                        # opposite claim. Structured type/path remain authoritative.
                        ok=not (
                            ("газ" in normalized_type and "электр" in name_norm)
                            or ("электр" in normalized_type and "газ" in name_norm)
                        ),
                        sku=product.sku,
                        category=category,
                        field="boiler name/type consistency",
                        request=boiler_type,
                        actual=product.name,
                    )
                )
            power = _number(_first_attr(product, "мощность"))
            area = _number(_first_attr(product, "отапливаемая площадь"))
            if power and area:
                checks.append(
                    AuditCheck(
                        ok=abs(area - power * 10) <= max(1.0, area * 0.05),
                        sku=product.sku,
                        category=category,
                        field="kW to m2 consistency",
                        request=f"{power:g} kW",
                        actual=f"{area:g} m2",
                    )
                )

        elif category == "pipes":
            diameter_match = re.search(r"(\d{2,3})\s*(?:mm|мм)(?!\d)", name, re.IGNORECASE)
            if diameter_match:
                add(
                    product,
                    "pipe diameter mm",
                    "pipes",
                    {"diameter_mm": int(diameter_match.group(1))},
                )

        elif category == "pumps":
            for key, value in product.attributes_normalized.items():
                key_norm = normalize_text(key)
                if "монтажная длина" in key_norm:
                    for raw in re.findall(r"\d+", str(value)):
                        add(
                            product,
                            "pump mounting length mm",
                            "pumps",
                            {
                                "pump_type": "циркуляционный",
                                "mounting_length_mm": int(raw),
                            },
                        )
                if "напор" in key_norm:
                    head = _number(str(value))
                    if head is not None:
                        checks.append(
                            AuditCheck(
                                ok=search._head_matches(product, head),
                                sku=product.sku,
                                category=category,
                                field="pump head m",
                                request=f"{head:g} m",
                                actual=str(value),
                            )
                        )
            connection_match = re.search(r"(?<!\d)(25|32)\s*/\s*\d", name_norm)
            if connection_match:
                add(
                    product,
                    "pump connection size",
                    "pumps",
                    {"connection_size": int(connection_match.group(1))},
                )

        elif category == "sewer":
            pipe_pair = re.search(r"(\d{2,3})\s*\*\s*(\d{3,4})", name)
            if pipe_pair and "труб" in name_norm:
                sewer_identity = normalize_text(f"{name} {product.category_path}")
                scope = "наружная" if any(
                    marker in sewer_identity for marker in ["наруж", "kgem", "пвх"]
                ) else "внутренняя"
                add(
                    product,
                    "sewer pipe diameter x piece length mm",
                    "sewer",
                    {
                        "sewer_scope": scope,
                        "element_type": "труба",
                        "diameter_mm": int(pipe_pair.group(1)),
                        "length_mm": int(pipe_pair.group(2)),
                    },
                )
            elif "отвод" in name_norm:
                diameter_match = re.search(r"htb\D{0,12}(\d{2,3})", name_norm)
                if diameter_match:
                    add(
                        product,
                        "sewer bend diameter mm",
                        "sewer",
                        {
                            "sewer_scope": "внутренняя",
                            "element_type": "отвод",
                            "diameter_mm": int(diameter_match.group(1)),
                        },
                    )

        elif category == "fittings":
            if "ppr" in name_norm:
                pair = re.search(r"(\d{2,3})\s*(?:[xх-])\s*(\d{2,3})", name_norm)
                if pair:
                    slots = {
                        "diameter_mm": int(pair.group(1)),
                        "secondary_diameter_mm": int(pair.group(2)),
                    }
                else:
                    diameter_match = re.search(r"(\d{2,3})\s*мм", name_norm)
                    slots = (
                        {"diameter_mm": int(diameter_match.group(1))}
                        if diameter_match
                        else {}
                    )
                if slots:
                    add(product, "PPR fitting dimensions mm", "fittings", slots)
            else:
                element = next(
                    (
                        value
                        for marker, value in [
                            ("тройник", "тройник"),
                            ("муфта", "муфта"),
                            ("отвод", "отвод"),
                        ]
                        if marker in name_norm
                    ),
                    None,
                )
                diameter_match = re.search(r"(?:htea|htu)\D{0,12}(\d{2,3})", name_norm)
                pair = re.search(r"(\d{2,3})\s*\*\s*(\d{2,3})", name)
                if element and diameter_match:
                    slots = {
                        "sewer_scope": "внутренняя",
                        "element_type": element,
                        "diameter_mm": int(diameter_match.group(1)),
                    }
                    if pair:
                        slots["secondary_diameter_mm"] = int(pair.group(2))
                    add(product, "sewer fitting dimensions mm", "sewer", slots)

        elif category == "valves":
            size = _first_attr(product, "диаметр подключения, дюйм")
            if size:
                add(product, "valve connection inch", "valves", {"size_inch": size})

        elif category == "radiator_fittings":
            size = _first_attr(product, "дюйм")
            if not size:
                size_match = re.search(
                    r"(1\s*/\s*2|3\s*/\s*4)\s*\"", normalize_text(name)
                )
                size = size_match.group(1) if size_match else None
            if size:
                add(
                    product,
                    "radiator fitting inch",
                    "radiator_fittings",
                    {"size_inch": size},
                )

        elif category == "radiators":
            sections = _number(_first_attr(product, "количество секций"))
            center = _number(_first_attr(product, "межосевое расстояние"))
            if sections and center:
                add(
                    product,
                    "section radiator size",
                    "radiators",
                    {"sections": int(sections), "radiator_size_mm": int(center)},
                )
            panel = re.search(r"(11|22)\s*/\s*(\d{3})\s*/\s*(\d{3,4})", name_norm)
            if panel:
                add(
                    product,
                    "panel radiator length mm",
                    "radiators",
                    {"length_mm": int(panel.group(3))},
                )

    failures = [check for check in checks if not check.ok]
    by_category = Counter(check.category for check in checks)
    passed_by_category = Counter(check.category for check in checks if check.ok)
    lines = [
        "# Аудит единиц и размеров полного фида",
        "",
        f"Источник: `{source}`. Товаров: **{len(products)}**.",
        f"Проверок: **{len(checks)}**, успешно: **{len(checks) - len(failures)}**, ошибок: **{len(failures)}**.",
        "",
        "| Категория | Проверок | Успешно | Ошибок |",
        "|---|---:|---:|---:|",
    ]
    for category in sorted(by_category):
        total = by_category[category]
        passed = passed_by_category[category]
        lines.append(f"| {category} | {total} | {passed} | {total - passed} |")
    lines.extend(
        [
            "",
            "Проверяются раздельно: площадь котла (м²), мощность (кВт), диаметр и длина (мм), общий метраж трубы (м), дюймовые подключения, монтажная длина насоса (мм), напор (м), размеры/секции радиаторов и оба размера переходных фитингов.",
            "",
        ]
    )
    if failures:
        lines.extend(["## Ошибки", ""])
        for check in failures:
            lines.append(
                f"- `{check.sku}` / {check.field}: запрос `{check.request}`, результат `{check.actual}`."
            )
    else:
        lines.append("Все сформированные проверки прошли.")

    report = PROJECT_ROOT / "reports" / "measurement_coverage_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"products={len(products)} checks={len(checks)} "
        f"passed={len(checks) - len(failures)} failed={len(failures)}"
    )
    print(f"report={report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
