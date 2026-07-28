from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable, Mapping
from numbers import Real
from typing import Any

from app.models import Product, SearchQuery

from .utils import normalize_sku, normalize_text


logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - exercised only without optional dependency
    from difflib import SequenceMatcher

    class _FuzzFallback:
        @staticmethod
        def partial_ratio(a: str, b: str) -> int:
            return int(SequenceMatcher(None, a, b).ratio() * 100)

        @staticmethod
        def ratio(a: str, b: str) -> int:
            return int(SequenceMatcher(None, a, b).ratio() * 100)

    fuzz = _FuzzFallback()


SYNONYMS: dict[str, list[str]] = {
    "котел": ["котел", "котёл", "boiler"],
    "насос": ["насос", "помпа"],
    "циркуляционный насос": ["циркуляционный насос", "насос циркуляционный"],
    "кран": ["кран", "шаровый кран", "вентиль"],
    "труба": ["труба", "трубы"],
    "канализация": ["канализация", "канализационная труба"],
    "радиаторная арматура": ["радиаторная арматура", "термоголовка", "клапан"],
}

NAME_QUERY_STOPWORDS = {
    "нужен",
    "нужна",
    "нужно",
    "надо",
    "хочу",
    "купить",
    "покажи",
    "дай",
    "есть",
    "мне",
    "нам",
    "для",
    "или",
    "что",
    "это",
    "по",
    "сколько",
    "стоит",
    "цена",
    "наличие",
    "наличии",
    "пожалуйста",
}

# Слова категории и назначения: они есть у сотен товаров и сами по себе не
# опознают конкретную позицию. Поиск по названию требует хотя бы одного слова
# вне этого набора, иначе общий запрос вида «кран шаровой 1/2 для воды»
# срабатывал как точный поиск по названию и подменял весь ранжированный поиск.
GENERIC_NAME_TOKENS = {
    "кран",
    "краны",
    "шаровой",
    "шаровый",
    "шаровая",
    "вентиль",
    "клапан",
    "труба",
    "трубы",
    "трубу",
    "насос",
    "котел",
    "котёл",
    "радиатор",
    "вода",
    "воды",
    "водоснабжение",
    "отопление",
    "отопления",
    "канализация",
    "канализации",
    "горячей",
    "холодной",
    "горячая",
    "холодная",
}

CATEGORY_NEEDLES: dict[str, list[str]] = {
    "pipes": ["труба", "трубы", "ppr", "полипропилен"],
    "sewer": ["канализац", "ostendorf", "htem", "htee", "htr"],
    "pumps": ["насос", "помпа", "pump"],
    "boilers": ["котел", "котёл", "boiler"],
    "valves": ["кран", "шаровый", "вентиль"],
    "radiator_fittings": ["радиатор", "термоголов", "термостатическ", "клапан"],
    "radiators": ["радиатор", "батаре", "биметалл"],
    "fittings": ["угольник", "муфт", "тройник", "переходник", "фитинг"],
}

# Канонические категории по названию (приоритет) и пути категории фида.
# Котлы и насосы лежат в акционных разделах, поэтому сперва смотрим имя товара,
# и только потом — путь категории. Фитинги (угольник/муфта/тройник) отделяем от труб.
_NAME_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("boilers", ["котел", "котёл", "boiler"]),
    ("pumps", ["насос", "помпа"]),
    ("radiators", ["радиатор алюмин", "радиатор биметал", "радиатор стальн", "радиатор панельн"]),
    ("fittings", ["угольник", "муфта", "тройник", "отвод ppr", "переходник", "американка ppr"]),
]
_PATH_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("sewer", ["канализац"]),
    ("radiator_fittings", ["арматура для радиатор", "радиаторная арматура"]),
    ("radiators", ["радиаторы отоплен"]),
    ("valves", ["водозапорн", "запорн", "краны", "арматура"]),
    ("fittings", ["фитинг"]),
    ("pipes", ["трубы"]),
    ("pumps", ["насос"]),
    ("boilers", ["котел", "котёл", "котельн"]),
]


_FALSE_VALUES = {
    "",
    "0",
    "false",
    "no",
    "off",
    "нет",
    "отсутствует",
    "не предусмотрено",
    "не поддерживается",
}
_TRUE_VALUES = {
    "1",
    "true",
    "yes",
    "on",
    "да",
    "есть",
    "имеется",
    "предусмотрено",
    "поддерживается",
}
_FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "wifi": ("wifi", "wi-fi", "wi fi", "вайфай", "вай-фай", "вай фай"),
}


def _constraint_number(value: Any) -> float | None:
    """Read a numeric query constraint, including ``37 000`` / ``37 тыс``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Real):
        return float(value)
    text = str(value).strip().lower().replace("\xa0", " ")
    match = re.search(r"-?\d[\d ]*(?:[,.]\d+)?", text)
    if not match:
        return None
    compact = match.group(0).replace(" ", "").replace(",", ".")
    try:
        number = float(compact)
    except ValueError:
        return None
    if re.search(r"(?:тыс(?:яч\w*)?|\bk\b|к\b)", text[match.end() :]):
        number *= 1000
    return number


def _constraint_features(value: Any) -> list[str]:
    """Normalize scalar/list/mapping feature constraints to canonical names."""
    raw_features: list[Any] = []
    if value is None:
        return []
    if isinstance(value, Mapping):
        raw_features.extend(
            key
            for key, enabled in value.items()
            if normalize_text(str(enabled)) not in _FALSE_VALUES
        )
    elif isinstance(value, str):
        raw_features.extend(part for part in re.split(r"[,;]", value) if part.strip())
    elif isinstance(value, Iterable):
        raw_features.extend(value)
    else:
        raw_features.append(value)

    normalized: list[str] = []
    for raw in raw_features:
        feature = normalize_text(str(raw))
        if not feature:
            continue
        compact = re.sub(r"[^a-zа-я0-9]", "", feature)
        if compact in {"wifi", "вайфай"}:
            feature = "wifi"
        if feature not in normalized:
            normalized.append(feature)
    return normalized


def _feature_aliases(feature: str) -> tuple[str, ...]:
    return _FEATURE_ALIASES.get(feature, (feature,))


def _text_mentions_feature(text: str, feature: str) -> bool:
    normalized = normalize_text(text)
    if feature == "wifi":
        compact = re.sub(r"[^a-zа-я0-9]", "", normalized)
        return "wifi" in compact or "вайфай" in compact
    return any(alias in normalized for alias in _feature_aliases(feature))


def _text_negates_feature(text: str, feature: str) -> bool:
    normalized = normalize_text(text)
    for alias in _feature_aliases(feature):
        escaped = re.escape(normalize_text(alias))
        if re.search(
            rf"(?:\bбез\b|\bнет\b|\bне\s+(?:имеет|поддерживает|предусмотрен\w*)\b|"
            rf"\bотсутств\w*\b)(?:\s+\w+){{0,3}}\s+{escaped}",
            normalized,
        ):
            return True
        if re.search(
            rf"{escaped}(?:\s+\w+){{0,3}}\s+(?:нет|отсутств\w*|не\s+предусмотрен\w*|"
            rf"не\s+поддерживается)",
            normalized,
        ):
            return True
    return False


def _feature_state(product: Product, feature: str) -> bool | None:
    """Return True/False only when the feed gives evidence for the feature."""
    identity = " ".join(
        [
            product.name,
            product.category_path,
            product.brand or "",
        ]
    )
    explicit_false = False
    for key, value in product.attributes_normalized.items():
        key_text = normalize_text(str(key))
        value_text = normalize_text(str(value))
        key_mentions = _text_mentions_feature(key_text, feature)
        value_mentions = _text_mentions_feature(value_text, feature)

        if key_mentions:
            if value_text in _FALSE_VALUES or any(
                marker in value_text
                for marker in ["отсутств", "не предусмотр", "не поддерж", "без "]
            ):
                explicit_false = True
                continue
            if value_text in _TRUE_VALUES or any(
                marker in value_text
                for marker in ["встроен", "включен", "поддерж", "имеется"]
            ):
                return True
            if value_mentions and not _text_negates_feature(value_text, feature):
                return True
        elif value_mentions:
            if _text_negates_feature(value_text, feature):
                explicit_false = True
            else:
                return True
    if explicit_false:
        return False
    if _text_mentions_feature(identity, feature):
        return not _text_negates_feature(identity, feature)
    for grounded_text in [product.description or "", product.docs_text or ""]:
        if _text_mentions_feature(grounded_text, feature):
            return not _text_negates_feature(grounded_text, feature)
    return None


_BUILTIN_PART_TARGETS: dict[str, str] = {
    "насос": r"(?:циркуляционн\w*\s+)?насос",
    "бак": r"(?:расширительн\w*\s+)?бак",
    "3-ходовой клапан": r"(?:трех|3)[- ]?ходов\w*\s+клапан",
    "манометр": r"манометр",
    "камера": r"камер\w*\s+сгоран",
    "бойлер": r"(?:накопительн\w*\s+)?бойлер",
    "группа безопасности": r"групп\w*\s+безопасн",
}


def _builtin_part_state_from_text(text: str, part: str) -> bool | None:
    """Return a grounded built-in/in-package state without inverting negations.

    Product descriptions commonly mention components that are optional, external,
    or explicitly absent.  A keyword hit is therefore not evidence of inclusion.
    """
    normalized = normalize_text(text)
    canonical = normalize_text(part)
    target = _BUILTIN_PART_TARGETS.get(canonical)
    if not target:
        return None

    negative_patterns = (
        rf"(?:\bбез\b|\bне\s+входит\b|\bне\s+включен\w*\b|"
        rf"\bне\s+встроен\w*\b|\bне\s+предусмотрен\w*\b|"
        rf"\bотсутств\w*\b)(?:\s+\w+){{0,5}}\s+{target}",
        rf"{target}(?:\s+\w+){{0,8}}\s+(?:\bнет\b|\bне\s+входит\b|"
        rf"\bне\s+включен\w*\b|\bне\s+встроен\w*\b|"
        rf"\bне\s+предусмотрен\w*\b|\bотсутств\w*\b)",
        rf"{target}(?:[^.!?]{{0,140}})(?:приобрета\w*|поставля\w*)\s+отдельно",
        rf"(?:приобрета\w*|поставля\w*)\s+отдельно(?:[^.!?]{{0,100}}){target}",
    )
    if any(re.search(pattern, normalized) for pattern in negative_patterns):
        return False

    positive_patterns: dict[str, tuple[str, ...]] = {
        "насос": (
            r"встроен\w*\s+(?:циркуляционн\w*\s+)?насос",
            r"(?:циркуляционн\w*\s+)?насос[^.!?]{0,45}встроен",
        ),
        "бак": (
            r"встроенн\w*[^.!?]{0,100}(?:расширительн\w*\s+)?бак",
            r"(?:расширительн\w*\s+)?бак[^.!?]{0,45}встроен",
        ),
        "3-ходовой клапан": (
            r"встроенн\w*[^.!?]{0,35}(?:трех|3)[- ]?ходов\w*\s+клапан",
        ),
        "манометр": (
            r"встроенн\w*[^.!?]{0,45}манометр",
        ),
        "камера": (r"закрыт\w*\s+камер\w*\s+сгоран",),
        "бойлер": (
            r"встроенн\w*\s+(?:накопительн\w*\s+)?бойлер",
            r"(?:накопительн\w*\s+)?бойлер[^.!?]{0,45}встроен",
        ),
        "группа безопасности": (
            r"встроенн\w*[^.!?]{0,45}групп\w*\s+безопасн",
            r"(?:полный\s+)?комплект\s+гидравлическ\w*\s+безопасн",
        ),
    }
    if any(
        re.search(pattern, normalized)
        for pattern in positive_patterns.get(canonical, ())
    ):
        return True
    return None


def _builtin_part_state(product: Product, part: str) -> bool | None:
    text = " ".join(
        [
            product.name,
            product.description or "",
            product.docs_text or "",
            " ".join(
                f"{key} {value}"
                for key, value in product.attributes_normalized.items()
            ),
        ]
    )
    return _builtin_part_state_from_text(text, part)


def _builtin_part_confirmed(product: Product, part: str) -> bool:
    """Strict evidence for a component being inside the selected product."""
    return _builtin_part_state(product, part) is True


def _product_matches_hard_constraints(product: Product, slots: Mapping[str, Any]) -> bool:
    max_price = _constraint_number(slots.get("max_price"))
    min_price = _constraint_number(slots.get("min_price"))
    if max_price is not None or min_price is not None:
        if product.price is None:
            return False
        if max_price is not None and product.price > max_price:
            return False
        if min_price is not None and product.price < min_price:
            return False

    for feature in _constraint_features(slots.get("required_features")):
        if _feature_state(product, feature) is not True:
            return False
    for feature in _constraint_features(slots.get("excluded_features")):
        # «Без Wi‑Fi» is a hard statement.  Missing feed data is unknown, not
        # evidence that the feature is absent.
        if _feature_state(product, feature) is not False:
            return False
    for part in _constraint_features(slots.get("required_builtin_parts")):
        if not _builtin_part_confirmed(product, part):
            return False
    for part in _constraint_features(slots.get("excluded_builtin_parts")):
        # Absence is a hard constraint too: missing documentation is unknown,
        # not proof that the component is absent.
        if _builtin_part_state(product, part) is not False:
            return False
    return True


def _explicitly_disallows_alternatives(slots: Mapping[str, Any]) -> bool:
    if "allow_alternatives" not in slots:
        return False
    value = slots.get("allow_alternatives")
    if isinstance(value, bool):
        return not value
    return normalize_text(str(value)) in _FALSE_VALUES


def _requested_result_limit(slots: Mapping[str, Any]) -> int | None:
    value = _constraint_number(slots.get("result_limit"))
    if value is None:
        return None
    return max(0, int(value))


class FeedSearchAgent:
    def __init__(self, products: list[Product] | None = None) -> None:
        self.products: list[Product] = []
        self._canonical_category_cache: dict[int, str] = {}
        self._sku_mention_patterns: list[
            tuple[Product, re.Pattern[str], int]
        ] = []
        self.set_products(products or [])

    def set_products(self, products: list[Product]) -> None:
        self.products = products
        self._canonical_category_cache.clear()
        self._sku_mention_patterns = self._build_sku_mention_patterns(products)

    @staticmethod
    def _build_sku_mention_patterns(
        products: list[Product],
    ) -> list[tuple[Product, re.Pattern[str], int]]:
        patterns: list[tuple[Product, re.Pattern[str], int]] = []
        for product in products:
            sku = normalize_text(product.sku)
            compact = re.sub(r"[^a-zа-я0-9]", "", sku)
            if not compact or (compact.isdigit() and len(compact) < 5):
                continue
            tokens = re.findall(r"[a-zа-я]+|\d+|[./+\-]", sku)
            if not tokens:
                continue
            pattern = re.compile(
                r"(?<![a-zа-я0-9])"
                + r"\s*".join(re.escape(token) for token in tokens)
                + r"(?![a-zа-я0-9])"
            )
            patterns.append(
                (product, pattern, len(compact) if compact.isdigit() else 0)
            )
        return patterns

    def resolve_sku_mentions(self, message: str) -> list[Product]:
        """Resolve every catalogue SKU explicitly present in a user turn.

        The intent router deliberately recognises only conservative, generic
        article shapes.  A catalogue-aware pass is both safer and more complete:
        it can recognise vendor articles containing spaces and slashes (for
        example ``PS 25/6G 180``), while still returning only identities that
        actually exist in the current feed.
        """
        text = normalize_text(message)
        raw_text = html.unescape(message).lower().replace("ё", "е")
        explicit_numeric_context = bool(
            any(
                marker in text
                for marker in [
                    "артикул",
                    "арт ",
                    "sku",
                    "код товара",
                    "сравни",
                    "сравнение",
                    "проигнор",
                    "не сравнил",
                    "не сравнила",
                    "второй товар",
                    "второй артикул",
                    "оба товар",
                    "оба артикул",
                ]
            )
            or re.fullmatch(r"\d{5,}", text)
        )
        matches: list[tuple[int, int, int, Product]] = []
        for product, pattern, numeric_length in self._sku_mention_patterns:
            # Five-digit numbers are often budgets or quantities. Resolve them
            # as articles only in explicit article/comparison context. Longer
            # numeric identifiers (such as 2202211) remain usable in natural
            # cross-product questions.
            if numeric_length == 5 and not explicit_numeric_context:
                continue
            match = pattern.search(text)
            if match and numeric_length:
                numeric_token = re.escape(re.sub(r"\D", "", product.sku))
                adjacent_unit = bool(
                    re.search(
                        rf"(?<!\d){numeric_token}\s*"
                        r"(?:₽|руб\w*|тыс\w*|к\b|шт\w*|мм\b|см\b|"
                        r"м\b|м2\b|м²\b|квт\b|вт\b|вольт\w*|бар\b|л\b)",
                        raw_text,
                    )
                )
                if adjacent_unit:
                    continue
            if match:
                matches.append(
                    (
                        match.start(),
                        match.end(),
                        -(match.end() - match.start()),
                        product,
                    )
                )

        # Preserve mention order.  If catalogue aliases overlap at the same
        # position, prefer the longer identity and never emit the same SKU twice.
        matches.sort(key=lambda item: (item[0], item[2]))
        result: list[Product] = []
        seen: set[str] = set()
        accepted_spans: list[tuple[int, int]] = []
        for start, end, _, product in matches:
            if any(
                accepted_start <= start and end <= accepted_end
                for accepted_start, accepted_end in accepted_spans
            ):
                continue
            key = normalize_sku(product.sku)
            if key in seen:
                continue
            seen.add(key)
            accepted_spans.append((start, end))
            result.append(product)
        return result

    def matches_constraints(
        self,
        product: Product,
        category: str,
        slots: Mapping[str, Any],
    ) -> bool:
        """Shared final-card predicate for search and consultant paths."""
        if slots.get("in_stock") and not product.is_in_stock:
            return False
        return _product_matches_hard_constraints(
            product,
            slots,
        ) and self._semantic_slots_match(product, category, dict(slots))

    def search(self, query: SearchQuery) -> list[Product]:
        if not self.products:
            return []

        if query.sku:
            exact = self._search_sku(query.sku)
            exact = [
                product
                for product in exact
                if _product_matches_hard_constraints(product, query.slots)
            ]
            if exact:
                return exact[: self._query_result_limit(query, query.limit)]
            # An exact article is an identity boundary.  Falling through to
            # category scoring on a missing/ambiguous SKU can return an entirely
            # different product, which is unsafe and especially confusing after
            # conflicting feed identities have been quarantined.
            return []

        candidates = self.products
        if query.category != "other":
            candidates = [
                product for product in candidates if self._category_matches(product, query.category)
            ]
            # A requested category is a hard boundary. Falling back to the whole
            # feed when that bucket is empty made a radiator/convector look like
            # radiator fittings and accessories look like pumps or boilers.
            if not candidates:
                return []

        if query.brand:
            brand_norm = normalize_text(query.brand)
            candidates = [
                product
                for product in candidates
                if brand_norm and brand_norm in normalize_text(product.brand)
            ]
            if not candidates:
                return []

        effective_slots = self._effective_query_slots(query)
        candidates = [
            product
            for product in candidates
            if _product_matches_hard_constraints(product, effective_slots)
        ]
        if not candidates:
            return []
        slot_filtered = self._filter_by_slots(candidates, query, effective_slots)
        if slot_filtered:
            candidates = slot_filtered
        elif self._has_strict_slots(query, effective_slots):
            return []

        if query.in_stock_only:
            candidates = [product for product in candidates if product.is_in_stock]
            if not candidates:
                return []

        scored = [(self._score(product, query), product) for product in candidates]
        scored = [item for item in scored if item[0] > 0]
        if query.cheap or query.slots.get("sort_mode") == "price_asc":
            # Price sorting must happen before the top-N cut; otherwise the
            # globally cheapest valid product can be discarded by fuzzy score.
            scored.sort(
                key=lambda item: (
                    not item[1].is_in_stock,
                    item[1].price is None,
                    item[1].price or float("inf"),
                    -item[0],
                )
            )
        else:
            scored.sort(key=lambda item: item[0], reverse=True)
        result_limit = self._query_result_limit(query, query.limit)
        return [product for _, product in scored[:result_limit]]

    def search_by_name(
        self,
        message: str,
        query: SearchQuery | None = None,
        limit: int = 3,
    ) -> list[Product]:
        """Find products whose card text covers almost all significant query tokens.

        High-precision path for messages that look like a concrete product name
        (e.g. pasted from the site). Returns [] for generic requests so the
        normal clarification scenarios stay in charge.
        """
        text = normalize_text(message)
        tokens = [
            token
            for token in text.split()
            if len(token) >= 2 and token not in NAME_QUERY_STOPWORDS
        ]
        if len(tokens) < 4:
            return []
        effective_slots = self._effective_query_slots(query) if query else {}
        effective_limit = (
            self._query_result_limit(query, limit)
            if query is not None
            else limit
        )
        matches: list[tuple[float, int, Product]] = []
        for product in self.products:
            if query and query.category != "other" and not self._category_matches(
                product, query.category
            ):
                continue
            if query and query.brand:
                brand = normalize_text(query.brand)
                if not brand or brand not in normalize_text(product.brand):
                    continue
            if query and query.in_stock_only and not product.is_in_stock:
                continue
            if query and not _product_matches_hard_constraints(product, effective_slots):
                continue
            if query and self._has_strict_slots(query, effective_slots) and not self._slots_match(
                product, effective_slots, query.category
            ):
                continue
            # Сопоставляем только с идентичностью товара (название/категория),
            # без длинного маркетингового описания — иначе общая лексика паспорта
            # даёт ложные совпадения.
            identity = self._identity_text(product)
            identity_tokens = set(identity.split())
            matched_tokens = [
                token
                for token in tokens
                if self._name_token_matches(token, identity, identity_tokens)
            ]
            matched = len(matched_tokens)
            ratio = matched / len(tokens)
            # Требуем хотя бы одно отличительное слово. «кран шаровой 1/2 для
            # воды» состоит только из категории и параметров, и слово «воды»
            # добиралось из чужого category_path («Системы контроля протечки
            # воды») — ratio выходил 1.0, и этот единственный товар подменял
            # весь ранжированный поиск. У настоящего названия товара всегда есть
            # что-то отличительное: бренд, серия, модель, типоразмер.
            distinctive = any(self._is_distinctive_token(token) for token in matched_tokens)
            if ratio >= 0.8 and matched >= 4 and distinctive:
                name_score = int(fuzz.partial_ratio(text, normalize_text(product.name)))
                matches.append((ratio, name_score, product))
        if not matches:
            return []
        matches.sort(key=lambda item: (-item[0], -item[1], not item[2].is_in_stock))
        return [product for _, _, product in matches[:effective_limit]]

    @staticmethod
    def _is_distinctive_token(token: str) -> bool:
        """Does this token identify a particular product rather than a class of them?

        Category words («кран», «труба») and bare measurements («1/2», «50x500»,
        «20мм») are shared by hundreds of positions. A real product name always
        carries something else — a brand, series or model.
        """
        if token in GENERIC_NAME_TOKENS:
            return False
        return not re.fullmatch(r"\d+(?:[/.,x×х]\d+)*(?:мм|м|см|dn|дн)?", token)

    def search_alternatives(self, query: SearchQuery) -> list[Product]:
        if not self.products or query.category == "other":
            return []

        effective_slots = self._effective_query_slots(query)
        if _explicitly_disallows_alternatives(effective_slots):
            return []
        alternative_slots = dict(effective_slots)
        # A different contour count may be shown only through the explicit
        # "nearest alternatives" path (and labelled as such by the composer).
        # Boiler fuel/type remains non-negotiable.
        if query.category == "boilers":
            alternative_slots.pop("contours", None)
        candidates = [
            product
            for product in self.products
            if self._category_matches(product, query.category)
            and _product_matches_hard_constraints(product, effective_slots)
            and self._semantic_slots_match(product, query.category, alternative_slots)
            and self._alternative_hard_slots_match(
                product,
                query,
                alternative_slots,
            )
            and (
                not query.brand
                or normalize_text(query.brand) in normalize_text(product.brand)
            )
            and (not query.in_stock_only or product.is_in_stock)
        ]
        if not candidates:
            return []

        scored = [
            (self._alternative_score(product, query), product)
            for product in candidates
        ]
        scored = [item for item in scored if item[0] >= self._alternative_threshold(query)]
        scored.sort(
            key=lambda item: (
                -item[0],
                not item[1].is_in_stock,
                item[1].price is None,
                item[1].price or float("inf"),
            )
        )
        result_limit = self._query_result_limit(query, min(query.limit, 6))
        return [product for _, product in scored[:result_limit]]

    def _search_sku(self, sku: str) -> list[Product]:
        needle = normalize_sku(sku)
        # An article identifies exactly one catalog row.  Prefix/substring
        # fallback made ``ABC-12345`` silently resolve to ``ABC-12345-X`` and
        # is unsafe for both explicit SKU requests and compact feed articles.
        return [
            product
            for product in self.products
            if needle and normalize_sku(product.sku) == needle
        ]

    def _product_text(self, product: Product) -> str:
        attr_text = " ".join(f"{key} {value}" for key, value in product.attributes_normalized.items())
        return normalize_text(
            " ".join(
                [
                    product.sku,
                    product.name,
                    product.category_path,
                    product.brand or "",
                    product.description or "",
                    attr_text,
                ]
            )
        )

    def _identity_text(self, product: Product) -> str:
        """Short product identity (no marketing description) for name matching.

        Feed descriptions are long passport-like prose; matching a pasted product
        name against them caused false positives (a sewer pipe whose passport
        mentions «диаметром»/«фитинги»/«160» matched a "труба 16 мм" query).
        """
        return normalize_text(
            " ".join(
                [
                    product.sku,
                    product.name,
                    product.category_path,
                    product.brand or "",
                ]
            )
        )

    def _name_token_matches(self, token: str, identity: str, identity_tokens: set[str]) -> bool:
        if token in identity:
            return True
        if len(token) < 4:
            return False
        for identity_token in identity_tokens:
            if len(identity_token) < 4:
                continue
            if abs(len(identity_token) - len(token)) > 2:
                continue
            if fuzz.ratio(token, identity_token) >= 84:
                return True
        return False

    def _category_text(self, product: Product) -> str:
        """Identity for category matching — name + type, WITHOUT marketing description.

        A boiler whose description mentions a built-in «насос» must not be classified
        as a pump, so the long description is excluded here.
        """
        type_attr = ""
        for key, value in product.attributes_normalized.items():
            if "тип товара" in normalize_text(key):
                type_attr = value
                break
        return normalize_text(
            " ".join([product.name, product.category_path, product.brand or "", type_attr])
        )

    def _attribute_text(self, product: Product, key_markers: list[str]) -> str:
        """Return values of explicitly relevant structured feed attributes."""
        normalized_markers = [normalize_text(marker) for marker in key_markers]
        values = [
            str(value)
            for key, value in product.attributes_normalized.items()
            if any(marker in normalize_text(key) for marker in normalized_markers)
        ]
        return normalize_text(" ".join(values))

    def _structured_text(self, product: Product) -> str:
        """Product identity and feed attributes, deliberately without description."""
        attributes = " ".join(
            f"{key} {value}" for key, value in product.attributes_normalized.items()
        )
        return normalize_text(
            " ".join([product.name, product.category_path, product.brand or "", attributes])
        )

    def _is_actual_boiler(self, product: Product) -> bool:
        name = normalize_text(product.name)
        type_text = self._attribute_text(product, ["тип товара"])
        return bool(
            re.search(r"\b(?:электро)?котел\b", name)
            or re.search(r"\bкотел\b", type_text)
        )

    def _is_actual_pump(self, product: Product) -> bool:
        name = normalize_text(product.name)
        type_values = [
            normalize_text(str(value))
            for key, value in product.attributes_normalized.items()
            if "тип товара" in normalize_text(key)
        ]
        if any(
            value == "насос"
            or "насосная станц" in value
            or "насосная установ" in value
            for value in type_values
        ):
            return True

        accessory_markers = [
            "без насоса",
            "для насоса",
            "насосная группа",
            "насосно-смесительн",
            "блок управления насос",
            "блок насосной автоматик",
            "реле защиты насос",
        ]
        if any(marker in name for marker in accessory_markers):
            return False
        if name.startswith(
            (
                "трос ",
                "кабель ",
                "адаптер ",
                "оголовок ",
                "шланг ",
                "соединение ",
                "фильтр ",
                "клапан ",
            )
        ):
            return False
        return bool(
            re.match(r"^(?:[a-zа-я0-9./+\-]+\s+){0,4}(?:насос|помпа)\b", name)
            or "насосная станц" in name
            or "насосная установ" in name
        )

    def _is_actual_radiator(self, product: Product) -> bool:
        name = normalize_text(product.name)
        type_text = self._attribute_text(product, ["тип товара"])
        path = normalize_text(product.category_path)
        return bool(
            re.match(r"^(?:[a-zа-я0-9./+\-]+\s+){0,2}радиатор\b", name)
            or "радиатор отопления" in type_text
            or (name.startswith("конвектор ") and "радиатор" in path)
        )

    def _is_radiator_fitting(self, product: Product) -> bool:
        if self._is_actual_radiator(product):
            return False
        path = normalize_text(product.category_path)
        text = self._structured_text(product)
        if "арматура для радиатор" in path or "радиаторная арматура" in path:
            return True
        if "радиатор" not in text and "для рад" not in text:
            return False
        return any(
            marker in text
            for marker in [
                "клапан",
                "кран",
                "вентиль",
                "термоголов",
                "термостат",
                "узел подкл",
                "трубка для подкл",
            ]
        )

    def _is_actual_valve(self, product: Product) -> bool:
        name = normalize_text(product.name)
        type_text = self._attribute_text(product, ["тип товара"])
        return bool(
            re.search(r"\b(?:кран|клапан|вентиль)\b", name)
            or re.search(r"\b(?:кран|клапан|вентиль)\b", type_text)
        )

    def _is_actual_pipe(self, product: Product) -> bool:
        name = normalize_text(product.name)
        type_text = self._attribute_text(product, ["тип товара"])
        if any(marker in name for marker in ["кожух", "защитная гофра"]):
            return False
        if any(marker in type_text for marker in ["дымоход", "кожух", "изоляц"]):
            return False
        return bool(re.search(r"\bтруба\b", name) or re.search(r"\bтруба\b", type_text))

    def _category_matches(self, product: Product, category: str) -> bool:
        return self.canonical_category(product) == category

    def canonical_category(self, product: Product) -> str:
        cache_key = id(product)
        cached = self._canonical_category_cache.get(cache_key)
        if cached is not None:
            return cached
        category = self._compute_canonical_category(product)
        self._canonical_category_cache[cache_key] = category
        return category

    def _compute_canonical_category(self, product: Product) -> str:
        """Single canonical bucket for a product.

        The feed's broad section paths contain both equipment and accessories.
        Product kind therefore has to be confirmed by its name/type instead of
        treating everything below ``Котельное/Насосное оборудование`` as a
        boiler/pump. The same rule keeps protective sleeves and radiator
        connection tubes out of the transport-pipe bucket.
        """
        name = normalize_text(product.name)
        path = normalize_text(product.category_path)

        if self._is_actual_boiler(product):
            return "boilers"
        if self._is_actual_pump(product):
            return "pumps"
        if "канализац" in path or re.search(
            r"\b(?:htu|htea|htb|htem|kgem|kgb)\b",
            name,
        ):
            return "sewer"
        if self._is_actual_radiator(product):
            return "radiators"
        if self._is_radiator_fitting(product):
            return "radiator_fittings"
        if self._is_actual_valve(product):
            return "valves"
        if any(needle in name for needle in ["угольник", "муфта", "тройник", "переходник"]):
            return "fittings"
        if "фитинг" in path:
            return "fittings"
        if self._is_actual_pipe(product):
            return "pipes"
        return "other"

    def retrieve_for_consult(
        self,
        categories: list[str],
        slots: dict | None = None,
        per_category: int = 4,
    ) -> list[Product]:
        """Return real, category-correct products for the consultant to cite.

        Groups the feed by canonical category, keeps only the requested ones, and
        sorts each group sensibly (in stock first; boilers by power; otherwise by
        price). Never invents — only returns feed rows.
        """
        slots = slots or {}
        wanted = [self._canon_alias(category) for category in categories]
        wanted = [category for category in wanted if category]
        if not wanted:
            return []

        grouped: dict[str, list[Product]] = {category: [] for category in wanted}
        for product in self.products:
            if product.price is None or not product.url:
                continue
            if slots.get("in_stock") and not product.is_in_stock:
                continue
            canon = self.canonical_category(product)
            if canon in grouped:
                grouped[canon].append(product)

        result: list[Product] = []
        for category in wanted:
            items = grouped.get(category, [])
            items = self._sort_for_consult(items, category, slots)
            result.extend(items[:per_category])
        result_limit = _requested_result_limit(slots)
        return result if result_limit is None else result[:result_limit]

    def _canon_alias(self, category: str) -> str:
        aliases = {
            "radiator_fittings": "radiator_fittings",
            "radiators": "radiators",
            "boilers": "boilers",
            "pumps": "pumps",
            "pipes": "pipes",
            "fittings": "fittings",
            "valves": "valves",
            "sewer": "sewer",
        }
        return aliases.get(category, category)

    def _sort_for_consult(
        self,
        products: list[Product],
        category: str,
        slots: dict,
    ) -> list[Product]:
        products = [
            product
            for product in products
            if _product_matches_hard_constraints(product, slots)
            and self._semantic_slots_match(product, category, slots)
        ]
        if category == "boilers":
            required_kw = None
            if slots.get("power_kw"):
                required_kw = float(slots["power_kw"])
            elif slots.get("area_m2"):
                required_kw = float(slots["area_m2"]) / 10.0
            if required_kw:
                def closeness(product: Product) -> tuple:
                    power = self._extract_power_kw(product) or 0.0
                    enough = power >= required_kw * 0.9
                    return (not product.is_in_stock, not enough, abs(power - required_kw))

                return sorted(products, key=closeness)
            return sorted(products, key=lambda p: (not p.is_in_stock, p.price or float("inf")))
        if category == "pumps":
            pump_type = normalize_text(str(slots.get("pump_type") or ""))
            pump_use = normalize_text(str(slots.get("pump_use") or slots.get("project_note") or ""))
            if any(marker in pump_use for marker in ["отоплен", "тепл", "тёпл"]):
                products = [
                    product
                    for product in products
                    if self._pump_type_matches(product, "циркуляционный")
                ]
            elif "водоснаб" in pump_use:
                water_supply = [
                    product
                    for product in products
                    if not any(
                        stop in self._structured_text(product)
                        for stop in ["дренаж", "циркуляц", "отопл"]
                    )
                ]
                if water_supply:
                    products = water_supply
            elif "полив" in pump_use:
                irrigation = [
                    product
                    for product in products
                    if not any(
                        stop in self._structured_text(product)
                        for stop in ["циркуляц", "отопл"]
                    )
                ]
                if irrigation:
                    products = irrigation
                if not pump_type:
                    def irrigation_priority(product: Product) -> tuple:
                        text = self._structured_text(product)
                        if "дренаж" in text:
                            kind_priority = 0
                        elif any(marker in text for marker in ["скваж", "насосная станц", "поверхност"]):
                            kind_priority = 1
                        else:
                            kind_priority = 2
                        return (kind_priority, not product.is_in_stock, product.price or float("inf"))

                    return sorted(products, key=irrigation_priority)
            return sorted(products, key=lambda p: (not p.is_in_stock, p.price or float("inf")))
        if category == "sewer":
            element_type = normalize_text(str(slots.get("element_type") or ""))
            if element_type:
                products = [
                    product
                    for product in products
                    if element_type in self._product_text(product)
                ]
            return sorted(products, key=lambda p: (not p.is_in_stock, p.price or float("inf")))
        if category == "pipes":
            project_note = normalize_text(str(slots.get("project_note") or ""))
            if "тепл" in project_note and "пол" in project_note:
                # PPR is suitable for distribution mains in a heating system, but
                # it is not a flexible loop pipe for a water underfloor circuit.
                # Never surface ordinary PPR as though it closes that requirement.
                loop_pipe_markers = [
                    "pex",
                    "pe-rt",
                    "pert",
                    "сшит",
                    "металлопласт",
                    "для теплого пола",
                    "теплый пол",
                ]
                products = [
                    product
                    for product in products
                    if any(marker in self._product_text(product) for marker in loop_pipe_markers)
                ]
            return sorted(products, key=lambda p: (not p.is_in_stock, p.price or float("inf")))
        return sorted(products, key=lambda p: (not p.is_in_stock, p.price or float("inf")))

    def _extract_power_kw(self, product: Product) -> float | None:
        text = normalize_text(
            " ".join([product.name, " ".join(product.attributes_normalized.values())])
        )
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*квт", text)
        if match:
            return float(match.group(1).replace(",", "."))
        # Some feed rows (notably ZOTA Solo) store the unit in the attribute
        # name and only a bare number in its value: ``мощность, кВт: 6``.
        for key, value in product.attributes_normalized.items():
            key_text = normalize_text(str(key))
            if "мощ" not in key_text or "квт" not in key_text:
                continue
            number = re.search(r"\d+(?:[.,]\d+)?", str(value))
            if number:
                return float(number.group(0).replace(",", "."))
        return None

    def _boiler_type_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        trusted = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(
                        product,
                        ["тип котла", "вид котла", "тип товара"],
                    ),
                ]
            )
        )
        if "газ" in expected:
            return "газ" in trusted
        if "электр" in expected:
            return "электр" in trusted
        if "тверд" in expected:
            return "тверд" in trusted
        return bool(expected and expected in trusted)

    def _contours_match(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        trusted = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(product, ["контур", "полное наименование"]),
                ]
            )
        )
        if "двух" in expected or expected in {"2", "two_contour"}:
            return bool(
                "двухконтур" in trusted
                or re.search(r"\b2\s*(?:конт|-конт)", trusted)
            )
        if "одно" in expected or expected in {"1", "one_contour"}:
            return bool(
                "одноконтур" in trusted
                or re.search(r"\b1\s*(?:конт|-конт)", trusted)
            )
        return bool(expected and expected in trusted)

    def _pump_type_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        trusted = self._structured_text(product)
        aliases: list[str]
        if "скваж" in expected:
            aliases = ["скваж"]
        elif "цирк" in expected:
            aliases = ["циркуляц", "цирк.", "цирк "]
        elif "дренаж" in expected:
            aliases = ["дренаж"]
        elif "поверхн" in expected:
            aliases = ["поверхн"]
        elif "повыс" in expected:
            aliases = ["повыс"]
        elif "станц" in expected:
            aliases = ["насосная станц", "станция водоснабж"]
        else:
            aliases = [expected]
        return any(alias and alias in trusted for alias in aliases)

    def _pipe_semantic_evidence(self, product: Product) -> str:
        """Prefer name/structured purpose facts; use description only as fallback.

        Descriptions are useful for sparse PPR cards, but some feed rows contain a
        different SKU's marketing copy. Once the name or a dedicated attribute
        states a purpose/temperature, that primary evidence wins.
        """
        explicit = self._attribute_text(
            product,
            ["назначен", "применен", "рабочая среда"],
        )
        primary = normalize_text(" ".join([product.name, explicit]))
        purpose_markers = [
            "отопл",
            "теплый пол",
            "водоснаб",
            "горяч",
            "холод",
            "хол/",
            "хвс",
            "гвс",
            "канализ",
        ]
        if explicit or any(marker in primary for marker in purpose_markers):
            return primary
        return normalize_text(" ".join([primary, product.description or ""]))

    def _pipe_material_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        explicit = self._attribute_text(product, ["материал"])
        primary = normalize_text(
            " ".join([product.sku, product.name, product.category_path, explicit])
        )
        if any(marker in expected for marker in ["ppr", "ппр", "полипроп"]):
            ppr_markers = [
                "ppr",
                "pprc",
                "pp-r",
                "pp fiber",
                "pp-fiber",
                "pp alux",
                "pp-alux",
                "ппр",
                "полипроп",
            ]
            if any(marker in primary for marker in ppr_markers) or normalize_text(
                product.sku
            ).startswith("vtp.700"):
                return True
            other_materials = [
                "pex",
                "pe-x",
                "pe-rt",
                "pert",
                "полиэтилен",
                "металлопласт",
                "нерж",
                "сталь",
                "медн",
                "пнд",
            ]
            if explicit or any(marker in primary for marker in other_materials):
                return False
            description = normalize_text(product.description or "")
            return any(marker in description for marker in ppr_markers)
        return bool(expected and expected in primary)

    def _pipe_purpose_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        if "канализ" in expected:
            return self.canonical_category(product) == "sewer"
        evidence = self._pipe_semantic_evidence(product)
        if "отопл" in expected or "тепл" in expected:
            return any(
                marker in evidence
                for marker in ["отопл", "теплый пол", "теплого пола", "теплоносител"]
            )
        if "вод" in expected:
            return any(
                marker in evidence
                for marker in ["водоснаб", "питьев", "для воды", "хвс", "гвс"]
            )
        return bool(expected and expected in evidence)

    def _maximum_temperature(self, product: Product) -> float | None:
        values = []
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if "температур" in key_norm and any(
                marker in key_norm for marker in ["макс", "рабоч", "примен"]
            ):
                matches = re.findall(r"-?\d+(?:[.,]\d+)?", normalize_text(str(value)))
                values.extend(float(match.replace(",", ".")) for match in matches)
        return max(values) if values else None

    def _water_temperature_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = self._pipe_semantic_evidence(product)
        maximum_temperature = self._maximum_temperature(product)
        if "горяч" in expected:
            has_hot_evidence = any(marker in evidence for marker in ["горяч", "гвс"])
            has_cold_evidence = any(
                marker in evidence for marker in ["холод", "хол/водосн", "хвс"]
            )
            if has_cold_evidence and not has_hot_evidence:
                return False
            name = normalize_text(product.name)
            hot_water_ppr = self._pipe_material_matches(product, "ppr") and bool(
                re.search(r"\bpn\s*(?:20|25)\b", name)
            )
            return bool(
                has_hot_evidence
                or hot_water_ppr
                or (maximum_temperature is not None and maximum_temperature >= 60)
            )
        if "холод" in expected:
            return any(marker in evidence for marker in ["холод", "хвс"])
        return bool(expected and expected in evidence)

    def _effective_query_slots(self, query: SearchQuery | None) -> dict:
        if query is None:
            return {}
        slots = dict(query.slots)
        if query.category == "pipes" and not slots.get("pipe_material"):
            declared = slots.get("material") or slots.get("fitting_system")
            if declared:
                slots["pipe_material"] = declared
            else:
                text = normalize_text(query.original_text)
                if any(marker in text for marker in ["ppr", "ппр", "полипроп"]):
                    slots["pipe_material"] = "ppr"
        return slots

    def _application_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        explicit = self._attribute_text(
            product,
            ["назначен", "область применен", "рабочая среда"],
        )
        evidence = explicit or normalize_text(
            " ".join([product.name, product.category_path, product.description or ""])
        )
        if "радиатор" in expected:
            return self.canonical_category(product) == "radiator_fittings" or any(
                marker in evidence for marker in ["радиатор", "для рад"]
            )
        if "отопл" in expected:
            return any(marker in evidence for marker in ["отопл", "теплоносител"])
        if "вод" in expected:
            return bool(
                any(
                    marker in evidence
                    for marker in ["для воды", "водоснаб", "питьев", "хвс", "гвс"]
                )
                or re.search(r"\bвод(?:а|ы)\b", evidence)
            )
        return bool(expected and expected in evidence)

    def _connection_form_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(
                        product,
                        ["форма корпуса", "тип конструкции", "подключен", "исполнение"],
                    ),
                ]
            )
        )
        if "угл" in expected:
            return "угл" in evidence
        if "прям" in expected:
            return "прям" in evidence
        if "осев" in expected:
            return "осев" in evidence
        return bool(expected and expected in evidence)

    def _union_matches(self, product: Product) -> bool:
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(
                        product,
                        ["тип конструкции", "тип присоединения", "наличие американки"],
                    ),
                ]
            )
        )
        return any(marker in evidence for marker in ["полусгон", "американк", "накидн"])

    def _pipe_color_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(product, ["цвет", "окраска"]),
                ]
            )
        )
        color_families = {
            "бел": ["бел", "white"],
            "сер": ["сер", "gray", "grey"],
            "черн": ["черн", "black"],
            "син": ["син", "blue"],
            "красн": ["красн", "red"],
            "рыж": ["рыж", "оранж", "orange"],
            "зелен": ["зелен", "green"],
        }
        for prefix, aliases in color_families.items():
            if prefix in expected:
                return any(alias in evidence for alias in aliases)
        return bool(expected and expected in evidence)

    def _sewer_element_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(product, ["тип товара", "полное наименование"]),
                ]
            )
        )
        aliases = {
            "труба": ["труба"],
            "отвод": ["отвод"],
            "тройник": ["тройник"],
            "муфта": ["муфта"],
        }
        markers = next(
            (values for key, values in aliases.items() if key in expected),
            [expected],
        )
        return any(marker and marker in evidence for marker in markers)

    def _coupling_type_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(
                        product,
                        ["тип товара", "тип муфты", "полное наименование"],
                    ),
                ]
            )
        )
        if "муфт" not in evidence:
            return False
        if "соедин" in expected:
            # A reducer or repair/slip coupling is not a plain socket coupling,
            # even when one of its dimensions equals the requested DN.
            excluded = [
                "переход",
                "редукц",
                "ремонт",
                "надвиж",
                "компенсац",
            ]
            return not any(marker in evidence for marker in excluded)
        if "переход" in expected:
            return any(marker in evidence for marker in ["переход", "редукц"])
        if "ремонт" in expected or "надвиж" in expected:
            return any(marker in evidence for marker in ["ремонт", "надвиж"])
        return bool(expected and expected in evidence)

    def _valve_kind_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(product, ["тип товара", "полное наименование"]),
                ]
            )
        )
        if "шаров" in expected:
            return (
                "кран" in evidence
                and "шаров" in evidence
                and "дренаж" not in evidence
                and "обратн" not in evidence
            )
        if "дренаж" in expected:
            return "кран" in evidence and "дренаж" in evidence
        if "обратн" in expected:
            return "клапан" in evidence and "обратн" in evidence
        if "вентил" in expected:
            return "вентил" in evidence
        if "клапан" in expected:
            return "клапан" in evidence
        if "кран" in expected:
            return (
                "кран" in evidence
                and "дренаж" not in evidence
                and "обратн" not in evidence
            )
        return bool(expected and expected in evidence)

    def _product_angles(self, product: Product) -> list[float]:
        values: list[float] = []
        for key, value in product.attributes_normalized.items():
            if "угол" not in normalize_text(key):
                continue
            values.extend(
                float(raw.replace(",", "."))
                for raw in re.findall(r"\d+(?:[,.]\d+)?", str(value))
            )

        raw_name = html.unescape(product.name).lower().replace("ё", "е")
        values.extend(
            float(raw.replace(",", "."))
            for raw in re.findall(
                r"(?<!\d)(\d{1,3}(?:[,.]\d+)?)\s*(?:°|град(?:ус\w*)?)",
                raw_name,
            )
        )
        # Sewer feeds also encode an angle as ``110*87`` / ``110x87`` and
        # occasionally use a trailing asterisk instead of a degree sign.
        for match in re.finditer(
            r"(?<!\d)(\d{2,3})\s*[xх×*]\s*(\d{1,2}(?:[,.]\d+)?)(?!\d)",
            raw_name,
        ):
            angle = float(match.group(2).replace(",", "."))
            if 5 <= angle <= 90:
                values.append(angle)
        values.extend(
            float(raw.replace(",", "."))
            for raw in re.findall(
                r"(?<!\d)(\d{1,2}(?:[,.]\d+)?)\s*\*(?!\s*\d)",
                raw_name,
            )
        )
        return values

    def _angle_matches(self, product: Product, requested: object) -> bool:
        expected = float(requested)
        actual_values = self._product_angles(product)
        if not actual_values:
            return False
        if abs(expected - 90.0) < 0.01:
            # HT sewer systems normally market the right-angle bend as 87° or
            # 87.5°.  Treat only that narrow standards-equivalent family as 90°.
            return any(87.0 <= actual <= 90.0 for actual in actual_values)
        return any(abs(actual - expected) <= 0.6 for actual in actual_values)

    def _alternative_hard_slots_match(
        self,
        product: Product,
        query: SearchQuery,
        slots: dict,
    ) -> bool:
        """Keep safety/compatibility dimensions strict on the alternatives path."""
        category = query.category
        if category == "sewer":
            element_type = normalize_text(str(slots.get("element_type") or ""))
            is_pipe = "труб" in element_type
            diameter = slots.get("diameter_mm")
            if diameter and not is_pipe and not self._dimension_matches(
                product,
                int(diameter),
                ["диаметр", "размер"],
            ):
                return False
            secondary = slots.get("secondary_diameter_mm")
            if secondary and not self._fitting_dimension_matches(product, int(secondary)):
                return False
        generic_analog_request = (
            "аналог" in normalize_text(query.original_text)
            and not query.cheap
        )
        if category == "pumps" and not generic_analog_request:
            mounting = slots.get("mounting_length_mm")
            if mounting and not self._dimension_matches(
                product,
                int(mounting),
                ["монтажная длина", "длина"],
            ):
                return False
            head = slots.get("head_m")
            if head and not self._head_matches(product, float(head)):
                return False
            connection = slots.get("connection_size")
            if connection and not self._connection_matches(product, int(connection)):
                return False
        if category == "boilers":
            voltage = slots.get("voltage_v")
            if voltage and not self._dimension_matches(
                product,
                int(voltage),
                ["напряжение", "питание"],
            ):
                return False
        if category in {"valves", "radiator_fittings"}:
            size_inch = slots.get("size_inch")
            if size_inch and not self._inch_size_matches(product, str(size_inch)):
                return False
            diameter = slots.get("diameter_mm")
            if diameter and not self._dimension_matches(
                product,
                int(diameter),
                ["диаметр", "размер"],
            ):
                return False
            if slots.get("union") and not self._union_matches(product):
                return False
        return True

    def _thermostatic_head_matches(self, product: Product, requested: object) -> bool:
        if isinstance(requested, str):
            wants_thermostatic = normalize_text(requested) not in {
                "",
                "0",
                "false",
                "no",
                "нет",
                "ложь",
            }
        else:
            wants_thermostatic = bool(requested)
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(product, ["тип товара", "полное наименование"]),
                ]
            )
        )
        has_thermostatic = any(
            marker in evidence for marker in ["термостат", "термоголов", "терморег"]
        )
        if wants_thermostatic:
            return has_thermostatic
        return not has_thermostatic and any(
            marker in evidence
            for marker in ["ручн", "запор", "шаров", "настроечн", "вентиль"]
        )

    def _semantic_slots_match(self, product: Product, category: str, slots: dict) -> bool:
        """Enforce categorical/usage slots as non-negotiable constraints."""
        if category == "boilers":
            boiler_types = slots.get("boiler_types") or []
            if isinstance(boiler_types, str):
                boiler_types = [boiler_types]
            if boiler_types and not any(
                self._boiler_type_matches(product, value) for value in boiler_types if value
            ):
                return False
            if slots.get("boiler_type") and not self._boiler_type_matches(
                product, slots["boiler_type"]
            ):
                return False
            if slots.get("contours") and not self._contours_match(product, slots["contours"]):
                return False
        if category == "pumps" and slots.get("pump_type") and not self._pump_type_matches(
            product, slots["pump_type"]
        ):
            return False
        if category in {"pipes", "sewer"}:
            if slots.get("pipe_material") and not self._pipe_material_matches(
                product, slots["pipe_material"]
            ):
                return False
            if slots.get("pipe_purpose") and not self._pipe_purpose_matches(
                product, slots["pipe_purpose"]
            ):
                return False
            if slots.get("water_temperature") and not self._water_temperature_matches(
                product, slots["water_temperature"]
            ):
                return False
        if category == "pipes" and slots.get("pipe_color") and not self._pipe_color_matches(
            product,
            slots["pipe_color"],
        ):
            return False
        if category == "sewer":
            if slots.get("element_type") and not self._sewer_element_matches(
                product,
                slots["element_type"],
            ):
                return False
            if slots.get("coupling_type") and not self._coupling_type_matches(
                product,
                slots["coupling_type"],
            ):
                return False
            if slots.get("angle_deg") and not self._angle_matches(
                product,
                slots["angle_deg"],
            ):
                return False
        if category in {"valves", "radiator_fittings"}:
            if slots.get("application") and not self._application_matches(
                product, slots["application"]
            ):
                return False
        if category == "valves" and slots.get("valve_kind") and not self._valve_kind_matches(
            product,
            slots["valve_kind"],
        ):
            return False
        if category == "radiator_fittings":
            if slots.get("connection_form") and not self._connection_form_matches(
                product, slots["connection_form"]
            ):
                return False
            if "thermostatic_head" in slots and not self._thermostatic_head_matches(
                product, slots["thermostatic_head"]
            ):
                return False
            if slots.get("union") and not self._union_matches(product):
                return False
        return True

    def _filter_by_slots(
        self,
        products: list[Product],
        query: SearchQuery,
        slots: dict | None = None,
    ) -> list[Product]:
        effective_slots = slots if slots is not None else self._effective_query_slots(query)
        result = []
        for product in products:
            if self._slots_match(product, effective_slots, query.category):
                result.append(product)
        return result

    def _slots_match(self, product: Product, slots: dict, category: str = "other") -> bool:
        text = self._product_text(product)
        checks: list[bool] = [
            _product_matches_hard_constraints(product, slots),
            self._semantic_slots_match(product, category, slots),
        ]
        diameter = slots.get("diameter_mm")
        if diameter:
            checks.append(self._dimension_matches(product, int(diameter), ["диаметр", "размер"]))

        size_inch = slots.get("size_inch")
        if size_inch:
            checks.append(self._inch_size_matches(product, str(size_inch)))

        length = slots.get("length_mm")
        if length:
            checks.append(self._dimension_matches(product, int(length), ["длина"]))

        secondary_diameter = slots.get("secondary_diameter_mm")
        if secondary_diameter:
            checks.append(self._fitting_dimension_matches(product, int(secondary_diameter)))

        radiator_size = slots.get("radiator_size_mm")
        if radiator_size:
            checks.append(
                self._dimension_matches(
                    product,
                    int(radiator_size),
                    ["межосев", "высот", "размер"],
                )
            )

        sections = slots.get("sections")
        if sections:
            checks.append(self._dimension_matches(product, int(sections), ["секц"]))

        pump_type = slots.get("pump_type")
        if pump_type:
            checks.append(self._pump_type_matches(product, pump_type))

        mounting_length = slots.get("mounting_length_mm")
        if mounting_length:
            checks.append(self._dimension_matches(product, int(mounting_length), ["монтажная длина", "длина"]))

        head = slots.get("head_m")
        if head:
            checks.append(self._head_matches(product, float(head)))

        voltage = slots.get("voltage_v")
        if voltage and category == "boilers":
            checks.append(
                self._dimension_matches(
                    product,
                    int(voltage),
                    ["напряжение", "питание"],
                )
            )

        connection_size = slots.get("connection_size")
        if connection_size:
            checks.append(self._connection_matches(product, int(connection_size)))

        element_type = slots.get("element_type")
        if element_type:
            if category == "sewer":
                checks.append(self._sewer_element_matches(product, element_type))
            else:
                checks.append(normalize_text(str(element_type)) in text)

        sewer_scope = slots.get("sewer_scope")
        if sewer_scope:
            if sewer_scope == "внутренняя":
                checks.append("внутрен" in text or "htem" in text)
            elif sewer_scope == "наружная":
                checks.append("наруж" in text or "kg" in text)

        body_form = slots.get("body_form")
        if body_form:
            checks.append(normalize_text(str(body_form)) in text)

        if slots.get("union"):
            # «американка» = разъёмное соединение с полусгоном/накидной гайкой.
            checks.append(self._union_matches(product))

        if not checks:
            return True
        return all(checks)

    def _has_strict_slots(self, query: SearchQuery, slots: dict | None = None) -> bool:
        effective_slots = slots if slots is not None else query.slots
        if (
            _constraint_number(effective_slots.get("max_price")) is not None
            or _constraint_number(effective_slots.get("min_price")) is not None
            or _constraint_features(effective_slots.get("required_features"))
            or _constraint_features(effective_slots.get("excluded_features"))
            or _constraint_features(effective_slots.get("required_builtin_parts"))
            or _constraint_features(effective_slots.get("excluded_builtin_parts"))
        ):
            return True
        strict_by_category = {
            "pipes": {
                "diameter_mm",
                "element_type",
                "length_mm",
                "pipe_purpose",
                "water_temperature",
                "pipe_material",
                "pipe_color",
            },
            "sewer": {
                "sewer_scope",
                "element_type",
                "coupling_type",
                "angle_deg",
                "diameter_mm",
                "secondary_diameter_mm",
                "length_mm",
                "pipe_purpose",
                "water_temperature",
            },
            "pumps": {"pump_type", "mounting_length_mm", "head_m", "connection_size", "old_model"},
            "valves": {
                "application",
                "diameter_mm",
                "body_form",
                "union",
                "size_inch",
                "valve_kind",
            },
            "radiator_fittings": {
                "application",
                "connection_form",
                "diameter_mm",
                "size_inch",
                "union",
                "thermostatic_head",
            },
            "radiators": {"radiator_size_mm", "length_mm", "sections", "size_inch"},
            "fittings": {"diameter_mm", "secondary_diameter_mm", "size_inch", "element_type"},
            "boilers": {"boiler_type", "contours", "voltage_v"},
        }
        strict_keys = strict_by_category.get(query.category, set())
        return bool(strict_keys.intersection(effective_slots))

    def _query_result_limit(self, query: SearchQuery, default: int) -> int:
        requested = _requested_result_limit(query.slots)
        if requested is None:
            return default
        return min(default, requested)

    def _alternative_threshold(self, query: SearchQuery) -> int:
        if query.category == "sewer":
            return 55
        if query.category in {"pumps", "valves", "radiator_fittings"}:
            return 45
        return 35

    def _alternative_score(self, product: Product, query: SearchQuery) -> int:
        slots = self._effective_query_slots(query)
        text = self._product_text(product)
        score = 15

        pipe_material = slots.get("pipe_material")
        if pipe_material:
            if self._pipe_material_matches(product, pipe_material):
                score += 25
            else:
                return 0

        pipe_purpose = slots.get("pipe_purpose")
        if pipe_purpose:
            if self._pipe_purpose_matches(product, pipe_purpose):
                score += 20
            else:
                return 0

        water_temperature = slots.get("water_temperature")
        if water_temperature:
            if self._water_temperature_matches(product, water_temperature):
                score += 20
            else:
                return 0

        pipe_color = slots.get("pipe_color")
        if pipe_color:
            if self._pipe_color_matches(product, pipe_color):
                score += 15
            else:
                return 0

        element_type = slots.get("element_type")
        if element_type:
            if normalize_text(str(element_type)) in text:
                score += 35
            elif query.category == "sewer":
                return 0

        sewer_scope = slots.get("sewer_scope")
        if sewer_scope:
            if sewer_scope == "внутренняя" and ("внутрен" in text or "htem" in text):
                score += 30
            elif sewer_scope == "наружная" and ("наруж" in text or "kg" in text):
                score += 30
            elif query.category == "sewer":
                score -= 20

        diameter = slots.get("diameter_mm")
        if diameter:
            score += 25 if self._dimension_matches(product, int(diameter), ["диаметр", "размер"]) else -12

        length = slots.get("length_mm")
        if length:
            score += 20 if self._dimension_matches(product, int(length), ["длина"]) else -8

        secondary_diameter = slots.get("secondary_diameter_mm")
        if secondary_diameter:
            score += 20 if self._fitting_dimension_matches(product, int(secondary_diameter)) else -12

        angle = slots.get("angle_deg")
        if angle:
            if self._angle_matches(product, angle):
                score += 25
            else:
                return 0

        coupling_type = slots.get("coupling_type")
        if coupling_type:
            if self._coupling_type_matches(product, coupling_type):
                score += 25
            else:
                return 0

        radiator_size = slots.get("radiator_size_mm")
        if radiator_size:
            score += 25 if self._dimension_matches(
                product,
                int(radiator_size),
                ["межосев", "высот", "размер"],
            ) else -12

        sections = slots.get("sections")
        if sections:
            score += 20 if self._dimension_matches(product, int(sections), ["секц"]) else -10

        pump_type = slots.get("pump_type")
        if pump_type:
            score += 35 if self._pump_type_matches(product, pump_type) else -20

        connection_size = slots.get("connection_size")
        if connection_size:
            score += 20 if self._connection_matches(product, int(connection_size)) else -12

        head = slots.get("head_m")
        if head:
            score += 25 if self._head_matches(product, float(head)) else -18

        mounting_length = slots.get("mounting_length_mm")
        if mounting_length:
            score += 20 if self._dimension_matches(
                product,
                int(mounting_length),
                ["монтажная длина", "длина"],
            ) else -18

        boiler_type = slots.get("boiler_type")
        if boiler_type:
            # Чужой тип котла (газовый вместо электрического) — не альтернатива.
            if self._boiler_type_matches(product, boiler_type):
                score += 30
            else:
                return 0

        contours = slots.get("contours")
        if contours:
            if self._contours_match(product, contours):
                score += 20
            else:
                score -= 10

        body_form = slots.get("body_form")
        if body_form:
            score += 20 if normalize_text(str(body_form)) in text else -8

        size_inch = slots.get("size_inch")
        if size_inch:
            if self._inch_size_matches(product, str(size_inch)):
                score += 20
            elif query.category in {"valves", "radiator_fittings"}:
                return 0

        valve_kind = slots.get("valve_kind")
        if valve_kind:
            if self._valve_kind_matches(product, valve_kind):
                score += 25
            else:
                return 0

        if slots.get("union"):
            score += 20 if ("полусгон" in text or "американк" in text or "накидн" in text) else -15

        voltage = slots.get("voltage_v")
        if voltage and query.category == "boilers":
            if self._dimension_matches(product, int(voltage), ["напряжение", "питание"]):
                score += 25
            else:
                return 0

        application = slots.get("application")
        if application:
            if query.category in {"valves", "radiator_fittings"} and not self._application_matches(
                product, application
            ):
                return 0
            score += 15

        if product.is_in_stock:
            score += 8
        return score

    def _number_matches(self, text: str, number: int) -> bool:
        return bool(re.search(rf"(^|[^0-9]){number}([^0-9]|$)", text))

    def _inch_size_matches(self, product: Product, size_inch: str) -> bool:
        normalized = re.sub(r"\s+", "", normalize_text(size_inch))
        candidates: set[str] = set()
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if "дюйм" not in key_norm and "резьб" not in key_norm:
                continue
            value_norm = re.sub(r"\s+", "", normalize_text(str(value)))
            match = re.fullmatch(
                r"([1-4]\d?/(?:2|4|8)|[1-4])",
                value_norm,
            )
            if match:
                candidates.add(match.group(1))

        # Keep the literal quote: ``normalize_text`` intentionally removes it,
        # which made names such as ``1/2&quot;`` unsearchable when the feed did not
        # also provide a dedicated inch attribute.
        name = html.unescape(product.name).lower().replace("ё", "е")
        name = re.sub(r"\s+", " ", name)
        for match in re.finditer(
            r"(?<![\d/])([1-4]\s+[1-3]\s*/\s*[248]|[1-4]\s*/\s*[248]|[1-4])\s*\"",
            name,
        ):
            candidates.add(re.sub(r"\s+", "", match.group(1)))
        return normalized in candidates

    def _dimension_matches(self, product: Product, number: int, keys: list[str]) -> bool:
        key_texts = [normalize_text(key) for key in keys]
        values = []
        for attr_key, attr_value in product.attributes_normalized.items():
            normalized_key = normalize_text(attr_key)
            if any(key_text in normalized_key for key_text in key_texts):
                values.append(normalize_text(attr_value))
        if values:
            return any(self._number_matches(value, number) for value in values)
        # В реальном фиде размеры часто только в названии вида «50*1500», а «*»
        # выбрасывается нормализацией — приводим её к «х», чтобы 50х1500 распознавалось.
        fallback = normalize_text(product.name.replace("*", "х"))
        if any(key in {"диаметр", "размер"} for key in key_texts):
            fitting_diameter = re.search(
                r"(?:htb|htu|htea)\D{0,12}(\d{2,3})(?:\D|$)", fallback
            )
            if fitting_diameter and int(fitting_diameter.group(1)) == number:
                return True
            return self._diameter_matches_name(fallback, number)
        if "длина" in key_texts:
            return self._length_matches_name(fallback, number)
        return self._number_matches(fallback, number)

    def _diameter_matches_name(self, text: str, number: int) -> bool:
        compact = normalize_text(text)
        pattern = rf"(?<!pn\s)(?<!pn)(^|[^0-9]){number}\s*(?:мм|mm)\b"
        if re.search(pattern, compact):
            return True
        return bool(
            re.search(rf"(^|[^0-9]){number}\s*[xх×]\s*\d+", compact)
            or re.search(rf"(^|[^0-9]){number}\s+quot\s+\d+", compact)
        )

    def _length_matches_name(self, text: str, number: int) -> bool:
        compact = normalize_text(text)
        return bool(
            re.search(rf"[xх×]\s*{number}([^0-9]|$)", compact)
            or re.search(rf"\d+\s*/\s*\d+\s*/\s*{number}([^0-9]|$)", compact)
            or re.search(rf"(^|[^0-9]){number}\s*(?:мм|mm)([^0-9]|$)", compact)
        )

    def _fitting_dimension_matches(self, product: Product, number: int) -> bool:
        text = normalize_text(
            " ".join([product.name, *product.attributes_normalized.values()])
        )
        return self._number_matches(text, number)

    def _head_matches(self, product: Product, head_m: float) -> bool:
        values = []
        for attr_key, attr_value in product.attributes_normalized.items():
            if "напор" in normalize_text(attr_key):
                values.append(normalize_text(attr_value))
        if values:
            return any(
                any(
                    abs(float(raw.replace(",", ".")) - head_m) < 0.01
                    for raw in re.findall(r"\d+(?:[,.]\d+)?", value)
                )
                for value in values
            )
        head = int(head_m) if head_m.is_integer() else head_m
        return bool(
            re.search(rf"(^|[^0-9]){re.escape(str(head))}([^0-9]|$)", normalize_text(product.name))
        )

    def _connection_matches(self, product: Product, connection_size: int) -> bool:
        values = []
        key_markers = ["присоедин", "подключ", "диаметр", "dn", "размер"]
        for attr_key, attr_value in product.attributes_normalized.items():
            normalized_key = normalize_text(attr_key)
            if any(marker in normalized_key for marker in key_markers):
                values.append(normalize_text(attr_value))
        if values and any(self._number_matches(value, connection_size) for value in values):
            return True

        text = normalize_text(product.name)
        return bool(re.search(rf"(^|[^0-9]){connection_size}\s*[-/]", text))

    def _expanded_query_text(self, query: SearchQuery) -> str:
        text = normalize_text(query.original_text)
        additions: list[str] = []
        for canonical, variants in SYNONYMS.items():
            if any(normalize_text(variant) in text for variant in variants):
                additions.extend(variants)
                additions.append(canonical)
        for value in query.slots.values():
            if isinstance(value, str):
                additions.append(value)
        return normalize_text(" ".join([text, *additions]))

    def _score(self, product: Product, query: SearchQuery) -> int:
        text = self._product_text(product)
        query_text = self._expanded_query_text(query)
        if not query_text:
            return 1
        score = fuzz.partial_ratio(query_text, text)
        if query.category != "other" and self._category_matches(product, query.category):
            score += 25
        if query.brand and normalize_text(query.brand) in normalize_text(product.brand):
            score += 20
        if product.is_in_stock:
            score += 5
        return int(score)
