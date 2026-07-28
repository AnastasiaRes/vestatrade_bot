from __future__ import annotations

import re

from app.models import GuardrailsResult, Product, ProductCard, SearchQuery

from .feed_search import (
    FeedSearchAgent,
    _builtin_part_confirmed,
    _builtin_part_state,
    _builtin_part_state_from_text,
    _constraint_features,
    _constraint_number,
    _feature_state,
    _requested_result_limit,
)
from .utils import normalize_sku, normalize_text


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
        max_price = _constraint_number(query.slots.get("max_price"))
        min_price = _constraint_number(query.slots.get("min_price"))
        required_features = _constraint_features(query.slots.get("required_features"))
        excluded_features = _constraint_features(query.slots.get("excluded_features"))
        required_builtin_parts = _constraint_features(
            query.slots.get("required_builtin_parts")
        )
        excluded_builtin_parts = _constraint_features(
            query.slots.get("excluded_builtin_parts")
        )
        result_limit = _requested_result_limit(query.slots)
        semantic_matcher = FeedSearchAgent()

        if result_limit is not None and len(cards) > result_limit:
            issues.append(
                f"response has {len(cards)} cards but result_limit is {result_limit}"
            )

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
            if query.in_stock_only and not product.is_in_stock:
                issues.append(
                    f"card {card.sku} is unavailable for an in-stock-only request"
                )
            if max_price is not None and card.price > max_price:
                issues.append(
                    f"card {card.sku} price {card.price:g} exceeds max_price {max_price:g}"
                )
            if min_price is not None and card.price < min_price:
                issues.append(
                    f"card {card.sku} price {card.price:g} is below min_price {min_price:g}"
                )
            for feature in required_features:
                if _feature_state(product, feature) is not True:
                    issues.append(
                        f"card {card.sku} does not confirm required feature {feature}"
                    )
            for feature in excluded_features:
                feature_state = _feature_state(product, feature)
                if feature_state is True:
                    issues.append(
                        f"card {card.sku} contains excluded feature {feature}"
                    )
                elif feature_state is None:
                    issues.append(
                        f"card {card.sku} does not confirm absence of excluded feature {feature}"
                    )
            for part in required_builtin_parts:
                if not _builtin_part_confirmed(product, part):
                    issues.append(
                        f"card {card.sku} does not confirm required built-in part {part}"
                    )
            for part in excluded_builtin_parts:
                part_state = _builtin_part_state(product, part)
                if part_state is True:
                    issues.append(
                        f"card {card.sku} contains excluded built-in part {part}"
                    )
                elif part_state is None:
                    issues.append(
                        f"card {card.sku} does not confirm absence of built-in part {part}"
                    )
            if query.category == "water_heaters":
                if semantic_matcher.canonical_category(product) != "water_heaters":
                    issues.append(
                        f"card {card.sku} is not a complete water-heating appliance"
                    )
                water_heater_checks = (
                    (
                        "heater_type",
                        semantic_matcher._water_heater_type_matches,
                    ),
                    (
                        "energy_source",
                        semantic_matcher._water_heater_energy_matches,
                    ),
                    (
                        "volume_l",
                        semantic_matcher._water_heater_volume_matches,
                    ),
                    (
                        "mounting",
                        semantic_matcher._water_heater_mounting_matches,
                    ),
                    (
                        "orientation",
                        semantic_matcher._water_heater_orientation_matches,
                    ),
                )
                for slot_key, matcher in water_heater_checks:
                    requested = query.slots.get(slot_key)
                    if requested is None or requested == "":
                        continue
                    if not matcher(product, requested):
                        issues.append(
                            f"card {card.sku} does not confirm requested "
                            f"water-heater characteristic {slot_key}={requested}"
                        )

            semantic_matches = semantic_matcher._semantic_slots_match(
                product,
                query.category,
                query.slots,
            )
            if not semantic_matches:
                if query.slots.get("contours"):
                    issues.append(
                        f"card {card.sku} does not match requested contours "
                        f"{query.slots['contours']}"
                    )
                else:
                    issues.append(
                        f"card {card.sku} violates mandatory category characteristics"
                    )
            normalized_attrs = {
                normalize_text(source_key): source_value
                for source_key, source_value in product.attributes_normalized.items()
            }
            for key, value in card.characteristics.items():
                source_value = normalized_attrs.get(normalize_text(key))
                if source_value != value:
                    issues.append(f"card {card.sku} has invented characteristic {key}")

        if query.cheap and not self._cheap_ordered(cards):
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
                "Не могу безопасно показать подборку: в карточках не хватает подтверждённых "
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
                    "Не вижу подтверждения комплектации в карточке товара. Лучше проверить "
                    "карточку/документацию или передать вопрос менеджеру."
                ),
            )
        return GuardrailsResult(ok=True)

    def builtin_part_states(
        self,
        product: Product,
        requested_parts: list[str],
    ) -> dict[str, bool | None]:
        """Return grounded inclusion states for known built-in components.

        ``False`` is reserved for explicit evidence such as «не встроен» or
        «приобретается отдельно».  A mere absence of a component in the card
        remains ``None`` so callers cannot turn missing data into a confident
        negative answer.
        """
        return {
            part: _builtin_part_state(product, part)
            for part in requested_parts
            if normalize_text(part)
            in {
                "насос",
                "бак",
                "3-ходовой клапан",
                "манометр",
                "камера",
                "бойлер",
                "группа безопасности",
            }
        }

    def list_builtin_components(self, product: Product) -> list[str]:
        """Read the card (name + description + docs + attrs) and list components the
        feed actually states are built in / included. No guessing — a component is
        listed only when its keyword is present (and "встроен" appears for the
        ambiguous ones, so "подключение насоса" doesn't count as a built-in pump).
        """
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
        found: list[str] = []
        catalogue = [
            ("циркуляционный насос", "насос"),
            ("расширительный бак", "бак"),
            ("3-ходовой клапан", "3-ходовой клапан"),
            ("манометр", "манометр"),
            ("закрытая камера сгорания", "камера"),
            ("бойлер", "бойлер"),
            ("группа безопасности", "группа безопасности"),
        ]
        for label, part in catalogue:
            if _builtin_part_confirmed(product, part):
                found.append(label)
        return found

    def _part_confirmed(self, text: str, part: str) -> bool:
        canonical = normalize_text(part)
        if canonical in {
            "насос",
            "бак",
            "3-ходовой клапан",
            "манометр",
            "камера",
            "бойлер",
            "группа безопасности",
        }:
            return _builtin_part_state_from_text(text, canonical) is True
        return bool(
            re.search(rf"в комплект.{{0,160}}{re.escape(part)}", text)
            or re.search(rf"комплект поставки.{{0,500}}{re.escape(part)}", text)
        )

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
            issues.extend(self._fabricated_specs(draft, answer))
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

    def _cheap_ordered(self, cards: list[ProductCard]) -> bool:
        def key(card: ProductCard) -> tuple[bool, float]:
            in_stock = (card.stock_qty or 0) > 0 or (
                "налич" in card.stock_status.lower()
                and "нет" not in card.stock_status.lower()
            )
            return (not in_stock, card.price)

        return cards == sorted(cards, key=key)

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
        # Structured catalogue fields are authoritative.  Long marketing
        # descriptions often enumerate every model in a series (for example a
        # 3 kW boiler whose description also mentions 9 kW), so reading the
        # first number from the description can silently approve an
        # underpowered product.
        for key, value in product.attributes_normalized.items():
            key_text = normalize_text(str(key))
            if "мощ" not in key_text or "квт" not in key_text:
                continue
            number = re.search(r"\d+(?:[,.]\d+)?", str(value))
            if number:
                return float(number.group(0).replace(",", "."))
        trusted_text = normalize_text(
            " ".join([product.name, *product.attributes_normalized.values()])
        )
        match = re.search(r"(\d+(?:[,.]\d+)?)\s*квт", trusted_text)
        if match:
            return float(match.group(1).replace(",", "."))
        return None

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
        if "дела хорошо" in draft_norm and "дел" not in answer_norm:
            issues.append("LLM rewrite dropped how-are-you acknowledgement")
        awkward_refusals = [
            "не могу обсуждать личные",
            "не могу обсуждать персональные",
            "не могу отвечать на личные",
            "персональные вопросы",
            "личные вопросы",
        ]
        if any(marker in answer_norm for marker in awkward_refusals):
            issues.append("LLM rewrite used awkward small talk refusal")
        reciprocal_personal = ["как у вас дела", "как ваши дела", "как у тебя дела"]
        if any(marker in answer_norm for marker in reciprocal_personal):
            issues.append("LLM rewrite asked a reciprocal personal small talk question")
        premature_tech_questions = [
            "тип котла",
            "контурность",
            "диаметр и материал",
            "материал трубы",
            "тип насоса",
            "потребляемая мощность",
        ]
        if any(marker in answer_norm for marker in premature_tech_questions):
            issues.append("LLM rewrite started technical selection before the task")
        if any(
            marker in answer_norm
            for marker in [
                "вашего интернет-магазина",
                "ваш интернет-магазин",
                "вашему интернет-магазину",
            ]
        ):
            issues.append("LLM rewrite called Vesta Trading the customer's store")
        return issues

    def _missing_clarification_terms(self, draft: str, answer: str) -> list[str]:
        draft_norm = normalize_text(draft)
        answer_norm = normalize_text(answer)
        missing = [
            f"LLM rewrite dropped clarification term: {term}"
            for term in CLARIFICATION_TERMS
            if term in draft_norm and term not in answer_norm
        ]
        # Numeric alternatives in a clarification are constraints, not style.
        # For example, replacing "220 или 380 В?" with a generic question about
        # the electrical network makes the customer guess which value is needed.
        # Reject that rewrite and return the deterministic draft unchanged.
        for number in sorted(set(re.findall(r"(?<!\d)\d+(?:[.,/]\d+)?(?!\d)", draft_norm))):
            if not re.search(rf"(?<!\d){re.escape(number)}(?!\d)", answer_norm):
                missing.append(f"LLM rewrite dropped clarification number: {number}")
        if "площад" in draft_norm and any(
            marker in answer_norm
            for marker in [
                "без уточнения площади",
                "площадь уточнять не",
                "площадь не нужно уточнять",
                "площадь не важна",
            ]
        ):
            missing.append("LLM rewrite contradicted the area clarification")
        return missing

    def validate_context_answer(self, answer: str, context: str) -> GuardrailsResult:
        """Reject a context-grounded answer that invents prices, stock or SKUs.

        Every price (… руб), stock quantity (… шт.) and article number the model
        states must already appear in the provided card context; otherwise it made
        the fact up and we fall back to a safe reply.
        """
        issues: list[str] = []
        ctx = normalize_text(context)
        ctx_compact = re.sub(r"\s+", "", ctx)
        ctx_numbers = set(re.findall(r"\d+", ctx))
        ans = normalize_text(answer)
        context_skus: set[str] = set()
        for line in context.splitlines():
            sku_match = re.search(
                r"\b(?:артикул|арт|sku)\b\.?\s*[:№#-]?\s*([^;)\n]+)",
                line,
                flags=re.IGNORECASE,
            )
            if not sku_match:
                continue
            raw_sku = sku_match.group(1).strip().rstrip(".,;:!?")
            normalized = normalize_sku(raw_sku)
            if normalized:
                context_skus.add(normalized)
        # A card name or description may legitimately mention another model,
        # for example «адаптер (для VTc.589)».  It is safe to repeat that exact
        # reference in prose, but it must not be presented as the card's own
        # article (the explicitly labelled check below remains stricter).
        context_reference_skus = {
            normalize_sku(token)
            for token in re.findall(
                r"\b[a-zа-я]{2,}[.\-][a-zа-я0-9.\-]*[a-zа-я0-9]\b",
                ctx,
            )
            if re.search(r"\d", token)
        }
        context_reference_skus.update(context_skus)

        for match in re.finditer(r"(\d[\d\s]*)\s*(?:руб|rub|₽)", ans):
            if re.sub(r"\s+", "", match.group(1)) not in ctx_compact:
                issues.append(f"invented price: {match.group(0).strip()}")
        for match in re.finditer(r"(\d+)\s*шт", ans):
            if match.group(1) not in ctx_numbers:
                issues.append(f"invented stock qty: {match.group(0).strip()}")
        # Артикулы вида VT.227.N.04 / VRS.256.18.0.
        for token in re.findall(
            r"\b[a-zа-я]{2,}[.\-][a-zа-я0-9.\-]*[a-zа-я0-9]\b",
            ans,
        ):
            if not re.search(r"\d", token):
                continue
            if normalize_sku(token) not in context_reference_skus:
                issues.append(f"invented sku: {token}")
        # Compact/numeric articles are safest to recognise after an explicit
        # label.  This covers values such as CMSR02CA28 and 2201375 without
        # treating every ordinary alphanumeric model word as an SKU.
        for token in re.findall(
            r"\b(?:артикул|арт|sku)\b\.?\s*[:№#-]?\s*"
            r"([a-zа-я0-9][a-zа-я0-9._/\-]{2,})",
            ans,
        ):
            token = token.rstrip(".,;:!?")
            compact_token = normalize_sku(token)
            if compact_token and compact_token not in context_skus:
                issues.append(f"invented sku: {token}")
        issues.extend(self._invented_context_measurements(ans, ctx, ctx_numbers))
        for term in ["шланг"]:
            if term in ans and term not in ctx:
                issues.append(f"invented context term: {term}")

        # Electric boilers do not burn fuel.  A fluent answer can preserve all
        # catalogue numbers yet still invent a combustion chamber, so protect
        # this semantic invariant separately from numeric grounding.
        if "электрическ" in ctx:
            mentions_chamber = re.search(
                r"(?:камер\w* сгоран|закрыт\w* камер|открыт\w* камер)",
                ans,
            )
            denies_chamber = re.search(
                r"(?:нет|не\s+имеет|не\s+оснащ\w*|без)[^.]{0,35}камер"
                r"|камер[^.]{0,35}(?:нет|отсутств\w*|не\s+предусмотр\w*)",
                ans,
            )
            if mentions_chamber and not denies_chamber:
                issues.append("electric boiler was assigned a combustion chamber")

        return GuardrailsResult(ok=not issues, issues=issues, safe_message=None)

    def _invented_context_measurements(
        self,
        answer_norm: str,
        context_norm: str,
        context_numbers: set[str],
    ) -> list[str]:
        issues: list[str] = []
        measurement_re = re.compile(
            r"(\d+(?:[,.]\d+)?)\s*(квт|вт|мм|см|м|бар|дюйм(?:а|ов)?|л/мин|м3/ч|м2|м²)"
        )
        for match in measurement_re.finditer(answer_norm):
            unit = match.group(2)
            digit_parts = set(re.findall(r"\d+", match.group(1)))
            if unit in context_norm and digit_parts and digit_parts.issubset(context_numbers):
                continue
            issues.append(f"invented measurement: {match.group(0).strip()}")
        return issues

    def _fabricated_specs(self, draft: str, answer: str) -> list[str]:
        """Reject specs the LLM invented while polishing a product/complectation answer.

        Product and complectation drafts already contain every fact. A measurement
        (e.g. "180 мм", "9 квт") or pump-code ("25/6") in the answer is allowed only
        if every number in it also occurs in the draft; otherwise the model made it
        up (often pulling defaults like "25/6 на 180 мм" from its persona) and we
        fall back to the safe draft.
        """
        draft_numbers = set(re.findall(r"\d+", normalize_text(draft)))
        answer_norm = normalize_text(answer)
        spec_patterns = [
            r"(\d+)\s*/\s*(\d+)",                 # 25/6 — насосные коды
            r"(\d+)(?:[.,]\d+)?\s*(?:мм|квт|бар)",  # 180 мм, 9 квт, 3 бар
        ]
        for pattern in spec_patterns:
            for match in re.finditer(pattern, answer_norm):
                numbers = [g for g in match.groups() if g]
                if any(number not in draft_numbers for number in numbers):
                    return [f"LLM rewrite invented spec: {match.group(0).strip()}"]
        return []

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
