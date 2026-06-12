from __future__ import annotations

import re

from app.models import GuardrailsResult, Product, ProductCard, SearchQuery

from .utils import normalize_text


CLARIFICATION_TERMS = [
    "отоплен",
    "водоснаб",
    "канализац",
    "материал",
    "диаметр",
    "внутрен",
    "наруж",
    "длина",
    "труба",
    "отвод",
    "тройник",
    "муфта",
    "циркуляц",
    "повыс",
    "дренаж",
    "скваж",
    "колод",
    "центральн",
    "полив",
    "давлен",
    "откач",
    "монтаж",
    "напор",
    "модель",
    "газов",
    "электр",
    "площад",
    "контур",
    "радиатор",
    "термоголов",
    "углов",
    "прям",
    "американ",
    "1/2",
    "3/4",
    "здравств",
    "привет",
]


class GuardrailsAgent:
    def validate_cards(
        self,
        cards: list[ProductCard],
        source_products: list[Product],
        query: SearchQuery,
    ) -> GuardrailsResult:
        issues: list[str] = []
        by_sku = {product.sku: product for product in source_products}

        for card in cards:
            product = by_sku.get(card.sku)
            if not product:
                issues.append(f"card {card.sku} has no source product")
                continue
            if not card.url or card.url != product.url:
                issues.append(f"card {card.sku} has missing or invented URL")
            if card.price != product.price:
                issues.append(f"card {card.sku} has invented price")
            if card.stock_status != product.stock_status:
                issues.append(f"card {card.sku} has invented stock status")
            normalized_attrs = {
                normalize_text(source_key): source_value
                for source_key, source_value in product.attributes_normalized.items()
            }
            for key, value in card.characteristics.items():
                source_value = normalized_attrs.get(normalize_text(key))
                if source_value != value:
                    issues.append(f"card {card.sku} has invented characteristic {key}")

        if query.cheap and not self._prices_sorted(cards):
            issues.append("cheap request was not sorted by ascending price")
        if query.in_stock_only and not self._stock_first(cards):
            issues.append("stock request did not prioritize available products")
        if query.category == "boilers" and query.slots.get("area_m2"):
            issues.extend(self._weak_boiler_issues(cards, source_products, query))

        return GuardrailsResult(
            ok=not issues,
            issues=issues,
            need_handoff=bool(issues),
            safe_message=(
                "Не могу безопасно показать подборку: в данных фида не хватает подтверждённых "
                "ссылок, цен или характеристик. Лучше передать вопрос менеджеру."
                if issues
                else None
            ),
        )

    def validate_complectation_answer(self, product: Product, requested_parts: list[str]) -> GuardrailsResult:
        text = normalize_text(
            " ".join(
                [
                    product.name,
                    product.description or "",
                    product.docs_text or "",
                    " ".join(product.attributes_normalized.values()),
                    " ".join(product.attributes_normalized.keys()),
                ]
            )
        )
        missing = [
            part
            for part in requested_parts
            if not self._part_confirmed(text, normalize_text(part))
        ]
        if missing:
            return GuardrailsResult(
                ok=False,
                issues=[f"no feed confirmation for {part}" for part in missing],
                need_handoff=True,
                safe_message=(
                    "Не вижу подтверждения комплектации в данных фида. Лучше проверить "
                    "карточку/документацию или передать вопрос менеджеру."
                ),
            )
        return GuardrailsResult(ok=True)

    def _part_confirmed(self, text: str, part: str) -> bool:
        if part == "бойлер":
            positive_markers = [
                "встроенный бойлер",
                "встроен бойлер",
                "со встроенным бойлером",
                "встроенным бойлером",
                "накопительный бойлер",
            ]
            return any(marker in text for marker in positive_markers)
        if part == "насос":
            return "насос" in text
        if part == "бак":
            return "бак" in text or "расширительн" in text
        if part in {"обвязка", "группа безопасности"}:
            return part in text
        return part in text

    def validate_response_text(
        self,
        draft: str,
        answer: str,
        mode: str = "generic",
    ) -> GuardrailsResult:
        issues: list[str] = []
        if not answer.strip():
            issues.append("empty final response")

        if mode == "clarification":
            issues.extend(self._missing_clarification_terms(draft, answer))

        if mode in {"products", "link", "complectation"}:
            issues.extend(self._missing_product_facts(draft, answer))
            if mode == "products":
                issues.extend(self._unsupported_product_claims(draft, answer))

        if mode == "small_talk":
            issues.extend(self._missing_small_talk_anchors(draft, answer))

        return GuardrailsResult(
            ok=not issues,
            issues=issues,
            need_handoff=False,
            safe_message=draft if issues else None,
        )

    def _prices_sorted(self, cards: list[ProductCard]) -> bool:
        prices = [card.price for card in cards]
        return prices == sorted(prices)

    def _stock_first(self, cards: list[ProductCard]) -> bool:
        seen_unavailable = False
        for card in cards:
            in_stock = (card.stock_qty or 0) > 0 or (
                "налич" in card.stock_status.lower() and "нет" not in card.stock_status.lower()
            )
            if not in_stock:
                seen_unavailable = True
            if seen_unavailable and in_stock:
                return False
        return True

    def _weak_boiler_issues(
        self,
        cards: list[ProductCard],
        source_products: list[Product],
        query: SearchQuery,
    ) -> list[str]:
        required_kw = float(query.slots["area_m2"]) / 10.0
        by_sku = {product.sku: product for product in source_products}
        issues = []
        for card in cards:
            product = by_sku.get(card.sku)
            power = self._extract_power_kw(product) if product else None
            if power is not None and power < required_kw * 0.75:
                issues.append(
                    f"boiler {card.sku} is too weak for {query.slots['area_m2']} m2 as equal option"
                )
        return issues

    def _extract_power_kw(self, product: Product | None) -> float | None:
        if not product:
            return None
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

    def _missing_small_talk_anchors(self, draft: str, answer: str) -> list[str]:
        """Reject answers that LLM truncated below the safe minimum or that dropped key anchors."""
        issues: list[str] = []
        answer_stripped = answer.strip()
        draft_stripped = draft.strip()
        if len(answer_stripped) < min(40, len(draft_stripped) // 2):
            issues.append("LLM rewrite truncated small talk answer below minimum length")
            return issues
        draft_norm = normalize_text(draft)
        answer_norm = normalize_text(answer)
        category_anchors = ["труб", "насос", "котел", "кран", "канализац", "радиатор"]
        draft_categories = [a for a in category_anchors if a in draft_norm]
        if draft_categories and not any(a in answer_norm for a in draft_categories):
            issues.append("LLM rewrite dropped category mentions from small talk answer")
        return issues

    def _missing_clarification_terms(self, draft: str, answer: str) -> list[str]:
        draft_norm = normalize_text(draft)
        answer_norm = normalize_text(answer)
        missing = [
            f"LLM rewrite dropped clarification term: {term}"
            for term in CLARIFICATION_TERMS
            if term in draft_norm and term not in answer_norm
        ]
        return missing

    def _unsupported_product_claims(self, draft: str, answer: str) -> list[str]:
        issues: list[str] = []
        draft_norm = normalize_text(draft)
        answer_norm = normalize_text(answer)
        broad_dimension_claims = [
            r"вс[её]\s+по\s+\d+\s*мм",
            r"все\s+варианты\s+по\s+\d+\s*мм",
            r"всё\s+по\s+\d+\s*мм",
        ]
        for pattern in broad_dimension_claims:
            match = re.search(pattern, answer_norm)
            if match and match.group(0) not in draft_norm:
                issues.append("LLM rewrite added unsupported broad dimension claim")
                break
        return issues

    def _missing_product_facts(self, draft: str, answer: str) -> list[str]:
        missing: list[str] = []
        draft_norm = normalize_text(draft)
        answer_norm = normalize_text(answer)

        urls = re.findall(r"https?://\S+", draft)
        for url in urls:
            if url.rstrip(".,)") not in answer:
                missing.append(f"LLM rewrite dropped URL: {url}")

        skus = re.findall(r"Артикул:\s*([^\n]+)", draft)
        for sku in skus:
            sku_value = sku.strip()
            if normalize_text(sku_value) not in answer_norm:
                missing.append(f"LLM rewrite dropped SKU: {sku_value}")

        prices = re.findall(r"Цена:\s*([0-9][0-9\s.,]*)\s*([A-ZА-Я]{3})", draft)
        for value, currency in prices:
            compact_value = re.sub(r"\s+", "", value)
            compact_answer = re.sub(r"\s+", "", answer_norm)
            if compact_value not in compact_answer or normalize_text(currency) not in answer_norm:
                missing.append(f"LLM rewrite dropped price: {value} {currency}")

        stock_lines = re.findall(r"Наличие:\s*([^\n]+)", draft)
        for stock in stock_lines:
            stock_value = normalize_text(stock.strip())
            if stock_value and stock_value not in draft_norm:
                continue
            if stock_value and stock_value not in answer_norm:
                missing.append(f"LLM rewrite dropped stock: {stock.strip()}")

        return missing
