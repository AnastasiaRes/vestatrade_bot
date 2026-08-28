from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable, Mapping
from numbers import Real
from typing import Any

from app.models import Product, SearchQuery
from app.sku_resolution import (
    SkuResolution,
    SkuResolutionStatus,
    resolve_catalog_sku,
)

from .product_constraints import (
    normalize_inch_size,
    product_inch_sizes,
    product_thread_facts,
    single_inch_size_constraint_matches,
    thread_constraint_matches,
)
from .trade_vocabulary import is_reducer_element
from .product_identity import (
    FILTER_PRIMARY_KINDS,
    VALVE_PRIMARY_KINDS,
    ProductIdentityFacts,
    product_identity_facts,
)
from .utils import (
    fold_model_key,
    normalize_sku,
    normalize_text,
    transliterate_model_key,
)


logger = logging.getLogger(__name__)
DEFAULT_PREFERRED_BRAND = "valtec"

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
    "водонагреватель": [
        "водонагреватель",
        "водонагреватели",
        "накопительный водонагреватель",
        "проточный водонагреватель",
        "бойлер косвенного нагрева",
    ],
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
    "hydraulic_accumulators": [
        "гидроаккумулятор",
        "гидробак",
        "мембранный бак",
        "бак для водоснабжения",
        "расширительный бак",
    ],
    "pipes": ["труба", "трубы", "ppr", "полипропилен"],
    "sewer": ["канализац", "ostendorf", "htem", "htee", "htr"],
    "pumps": ["насос", "помпа", "pump"],
    "boilers": ["котел", "котёл", "boiler"],
    "water_heaters": [
        "водонагревател",
        "бойлер косвенного нагрева",
        "water heater",
    ],
    "filters": [
        "фильтр",
        "картридж",
        "водоочистка",
        "водоподготовка",
        "10sl",
        "20sl",
        "10bb",
        "20bb",
    ],
    "controls": [
        "термостат",
        "терморегулятор",
        "сервопривод",
        "контроллер отопления",
    ],
    "valves": ["кран", "шаровый", "вентиль"],
    "radiator_fittings": ["радиатор", "термоголов", "термостатическ", "клапан"],
    "radiators": ["радиатор", "батаре", "биметалл"],
    "fittings": ["угольник", "муфт", "тройник", "переходник", "фитинг"],
}

# Канонические категории по названию (приоритет) и пути категории фида.
# Котлы и насосы лежат в акционных разделах, поэтому сперва смотрим имя товара,
# и только потом — путь категории. Фитинги (угольник/муфта/тройник) отделяем от труб.
_NAME_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (
        "hydraulic_accumulators",
        ["гидроаккумулятор", "гидробак", "мембранный бак", "бак расш."],
    ),
    (
        "water_heaters",
        ["водонагреватель", "бойлер косвенного нагрева", "water heater"],
    ),
    ("filters", ["картридж", "фильтр для воды", "корпус фильтра", "система обратного осмоса"]),
    ("controls", ["комнатный термостат", "терморегулятор", "сервопривод"]),
    ("boilers", ["котел", "котёл", "boiler"]),
    ("pumps", ["насос", "помпа"]),
    ("radiators", ["радиатор алюмин", "радиатор биметал", "радиатор стальн", "радиатор панельн"]),
    ("fittings", ["угольник", "муфта", "тройник", "отвод ppr", "переходник", "американка ppr"]),
]
_PATH_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("hydraulic_accumulators", ["баки мембранные"]),
    ("filters", ["фильтры", "водоподготовка", "водоочистка"]),
    ("controls", ["автоматика для систем отопления", "терморегуляторы"]),
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
    "измельчитель": (
        "измельчитель",
        "измельчительным",
        "режущий механизм",
        "режущим механизмом",
    ),
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
        # ``не входит в комплект поставки`` is package evidence, not proof
        # that a component is absent from inside the assembled product.  Only
        # explicit construction wording is allowed to establish False here.
        rf"(?:\bбез\b|"
        rf"\bне\s+встроен\w*\b|\bне\s+предусмотрен\w*\b|"
        rf"\bотсутств\w*\b)(?:\s+\w+){{0,5}}\s+{target}",
        rf"{target}(?:\s+\w+){{0,8}}\s+(?:\bнет\b|\bне\s+встроен\w*\b|"
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
    card_text = " ".join(
        [
            product.name,
            product.description or "",
            " ".join(
                f"{key} {value}"
                for key, value in product.attributes_normalized.items()
            ),
        ]
    )
    states = [_builtin_part_state_from_text(card_text, part)]
    if product.documents:
        states.extend(
            _builtin_part_state_from_text(document.text, part)
            for document in product.documents
        )
    elif product.docs_text:
        # Backwards compatibility for old caches without structured sources.
        states.append(_builtin_part_state_from_text(product.docs_text, part))
    grounded = {state for state in states if state is not None}
    if len(grounded) != 1:
        # No evidence or a source conflict: fail closed instead of allowing an
        # arbitrary concatenation order to choose the answer.
        return None
    return grounded.pop()


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
        self._model_key_cache: dict[str, str] = {}
        self._brand_word_key_cache: set[str] | None = None
        self._catalog_word_key_cache: set[str] | None = None
        self._product_identity_cache: dict[int, ProductIdentityFacts] = {}
        self._sku_mention_patterns: list[
            tuple[Product, re.Pattern[str], int]
        ] = []
        self._sku_products_by_prefix: dict[str, list[Product]] = {}
        self.set_products(products or [])

    def set_products(self, products: list[Product]) -> None:
        self.products = products
        self._canonical_category_cache.clear()
        self._product_identity_cache.clear()
        # Ключ модели считается из бренда и названия: после перезагрузки фида
        # у того же артикула они могли измениться.
        self._model_key_cache.clear()
        self._brand_word_key_cache = None
        self._catalog_word_key_cache = None
        self._sku_mention_patterns = self._build_sku_mention_patterns(products)
        self._sku_products_by_prefix = {}
        for product in products:
            groups = re.findall(r"[a-zа-я]+|\d+", normalize_text(product.sku))
            if len(groups) >= 3 and groups[0].isalpha():
                self._sku_products_by_prefix.setdefault(groups[0], []).append(product)

    @staticmethod
    def _build_sku_mention_patterns(
        products: list[Product],
    ) -> list[tuple[Product, re.Pattern[str], int]]:
        spoken_cyrillic = {
            "a": "а", "b": "б", "c": "с", "d": "д", "e": "е",
            "f": "ф", "g": "г", "h": "х", "i": "и", "j": "й",
            "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
            "p": "п", "r": "р", "s": "с", "t": "т", "u": "у",
            "v": "в", "x": "х", "y": "ы", "z": "з",
        }

        def group_pattern(group: str) -> str:
            if not group.isalpha() or not re.fullmatch(r"[a-z]+", group):
                return re.escape(group)
            return "".join(
                (
                    f"[{re.escape(char + spoken_cyrillic[char])}]"
                    if char in spoken_cyrillic
                    else re.escape(char)
                )
                for char in group
            )

        patterns: list[tuple[Product, re.Pattern[str], int]] = []
        for product in products:
            sku = normalize_text(product.sku)
            compact = re.sub(r"[^a-zа-я0-9]", "", sku)
            has_separator = bool(re.search(r"[./+\-\s]", sku))
            if not compact or (
                compact.isdigit()
                and len(compact) < 5
                and not has_separator
            ):
                continue
            # A customer usually reads an article from a label one group at a
            # time: ``VRS 256 13 0`` and ``VT 217 N 04`` are the same grounded
            # identities as ``VRS.256.13.0`` and ``VT.217.N.04``.  Requiring
            # the exact punctuation made those natural readings invisible.
            # Catalogue membership and token boundaries remain the safety
            # boundary; only separators between the SKU's own groups are
            # flexible.
            groups = re.findall(r"[a-zа-я]+|\d+", sku)
            if not groups:
                continue
            pattern = re.compile(
                r"(?<![a-zа-я0-9])"
                + r"[\s./+\-]*".join(group_pattern(group) for group in groups)
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
                    "сопостав",
                    "отлича",
                    "разница",
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
            if 0 < numeric_length <= 5 and not explicit_numeric_context:
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

        # In a comparison people commonly say the vendor prefix once:
        # ``VT 217 N 04 и 218 N 04``.  Resolve the second identity only among
        # catalogue siblings that share the already grounded prefix.  This is
        # still identity matching, not fuzzy retrieval: the whole remaining
        # SKU must be present in the turn with token boundaries.
        if explicit_numeric_context and matches:
            spoken_cyrillic = {
                "a": "а", "b": "б", "c": "с", "d": "д", "e": "е",
                "f": "ф", "g": "г", "h": "х", "i": "и", "j": "й",
                "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
                "p": "п", "r": "р", "s": "с", "t": "т", "u": "у",
                "v": "в", "x": "х", "y": "ы", "z": "з",
            }

            def shorthand_group(group: str) -> str:
                if group.isalpha() and re.fullmatch(r"[a-z]+", group):
                    return "".join(
                        (
                            f"[{re.escape(char + spoken_cyrillic[char])}]"
                            if char in spoken_cyrillic
                            else re.escape(char)
                        )
                        for char in group
                    )
                return re.escape(group)

            prefixes = {
                groups[0]
                for _, _, _, product in matches
                if len(groups := re.findall(
                    r"[a-zа-я]+|\d+", normalize_text(product.sku)
                )) >= 3
                and groups[0].isalpha()
            }
            already_matched = {
                normalize_sku(product.sku) for _, _, _, product in matches
            }
            for prefix in prefixes:
                for product in self._sku_products_by_prefix.get(prefix, []):
                    if normalize_sku(product.sku) in already_matched:
                        continue
                    groups = re.findall(
                        r"[a-zа-я]+|\d+", normalize_text(product.sku)
                    )
                    remainder = re.compile(
                        r"(?<![a-zа-я0-9])"
                        + r"[\s./+\-]*".join(
                            shorthand_group(group) for group in groups[1:]
                        )
                        + r"(?![a-zа-я0-9])"
                    )
                    match = remainder.search(text)
                    if match:
                        matches.append(
                            (
                                match.start(),
                                match.end(),
                                -(match.end() - match.start()),
                                product,
                            )
                        )
                        already_matched.add(normalize_sku(product.sku))

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
        if category != "other" and self.canonical_category(product) != category:
            return False
        return self._slots_match(product, dict(slots), category)

    def search(self, query: SearchQuery) -> list[Product]:
        if not self.products:
            return []

        if query.sku:
            exact_slots = self._effective_query_slots(query)
            exact = self._search_sku(query.sku)
            exact = [
                product
                for product in exact
                if self._slots_match(
                    product,
                    exact_slots,
                    query.category,
                )
                and (
                    query.category == "other"
                    or self._category_matches(product, query.category)
                )
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
                    *self._explicit_boiler_power_priority(item[1], query),
                    not self._default_preferred_brand(item[1], query),
                    not item[1].is_in_stock,
                    item[1].price is None,
                    item[1].price or float("inf"),
                    -item[0],
                )
            )
        else:
            scored.sort(
                key=lambda item: (
                    *self._explicit_boiler_power_priority(item[1], query),
                    not self._default_preferred_brand(item[1], query),
                    -item[0],
                    not item[1].is_in_stock,
                    item[1].price is None,
                    item[1].price or float("inf"),
                )
            )
        result_limit = self._query_result_limit(query, query.limit)
        if query.category == "boilers" and query.slots.get("power_kw") is not None:
            result_limit = self._explicit_boiler_result_limit(scored, query)
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
        # Tokenise punctuation independently.  ``воды,`` must remain the
        # generic word ``воды`` rather than becoming a fake identity token.
        tokens = [
            token
            for token in re.findall(
                r"[a-zа-я0-9]+(?:[./xх×-][a-zа-я0-9]+)*",
                text,
            )
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
        eligible: list[Product] = []
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
            eligible.append(product)

        # A complete catalogue name is an identity boundary.  Fuzzy coverage
        # deliberately tolerates one missing token, but that is unsafe for model
        # families where the only difference is ``30``/``50``/``80``.  Resolve
        # the literal normalized name first and only use fuzzy matching when no
        # full card name is present in the message.
        exact_name_matches = [
            product
            for product in eligible
            if len(normalize_text(product.name).split()) >= 3
            and re.search(
                rf"(?<!\w){re.escape(normalize_text(product.name))}(?!\w)",
                text,
            )
        ]
        if exact_name_matches:
            exact_name_matches.sort(
                key=lambda product: (
                    -len(normalize_text(product.name)),
                    not product.is_in_stock,
                )
            )
            return exact_name_matches[:effective_limit]

        matches: list[tuple[float, int, Product]] = []
        for product in eligible:
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
        if re.fullmatch(
            r"(?:вр|вн|нр|нар)(?:[-/xх×](?:вр|вн|нр|нар))?",
            token,
        ):
            return False
        if any(
            marker in token
            for marker in [
                "полнопроход",
                "стандартнопроход",
                "бабоч",
                "рычаг",
                "углов",
                "прямой",
            ]
        ):
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
                *self._explicit_boiler_power_priority(item[1], query),
                not self._default_preferred_brand(item[1], query),
                -item[0],
                not item[1].is_in_stock,
                item[1].price is None,
                item[1].price or float("inf"),
            )
        )
        result_limit = self._query_result_limit(query, min(query.limit, 6))
        if query.category == "boilers" and query.slots.get("power_kw") is not None:
            result_limit = self._explicit_boiler_result_limit(scored, query)
        return [product for _, product in scored[:result_limit]]

    # Когда точного совпадения нет, честный ответ продавца — «точного нет, но
    # есть вот такое, отличается вот этим». Ослабляем ровно одну группу
    # параметров, чтобы отличие можно было назвать одной фразой.
    NEAREST_RELAXATION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("угол", ("angle_deg",)),
        ("тип ручки", ("handle_type",)),
        ("резьбовой выход", ("thread_gender", "thread_type", "size_inch")),
        ("длина", ("length_mm",)),
        ("бренд", ("brand",)),
        ("присоединительный размер", ("size_inch",)),
        ("диаметр", ("diameter_mm",)),
        ("габариты", ("radiator_height_mm", "length_mm")),
    )

    def search_nearest_variants(
        self,
        query: SearchQuery,
        *,
        max_groups: int = 2,
        per_group: int = 2,
    ) -> list[tuple[str, list[Product]]]:
        """Ближайшие варианты, каждый с одним названным отличием."""
        if not self.products or query.category == "other":
            return []
        results: list[tuple[str, list[Product]]] = []
        seen_skus: set[str] = set()
        for label, fields in self.NEAREST_RELAXATION_GROUPS:
            relaxed_here = [
                field
                for field in fields
                if query.slots.get(field) not in (None, "", [], {})
                or (field == "brand" and query.brand)
            ]
            if not relaxed_here:
                continue
            relaxed_slots = {
                key: value
                for key, value in query.slots.items()
                if key not in fields
            }
            relaxed = SearchQuery(
                original_text=query.original_text,
                category=query.category,
                slots=relaxed_slots,
                sku=None,
                brand=None if "brand" in fields else query.brand,
                cheap=query.cheap,
                in_stock_only=query.in_stock_only,
                limit=query.limit,
            )
            found = [
                product
                for product in self.search(relaxed)
                if normalize_text(product.sku) not in seen_skus
            ][:per_group]
            if not found:
                continue
            seen_skus.update(normalize_text(product.sku) for product in found)
            results.append((label, found))
            if len(results) >= max_groups:
                break
        return results

    @staticmethod
    def alternative_relaxed_fields(query: SearchQuery) -> set[str]:
        """Return the explicit, auditable relaxations for an analogue search.

        Compatibility dimensions stay hard unless this method names the
        field.  Callers must remove only these fields for the final guard and
        describe every relaxation in the user-facing answer.
        """

        text = normalize_text(query.original_text)
        if query.category == "pumps" and "аналог" in text and not query.cheap:
            return {"head_m"}
        if query.category == "boilers":
            return {"contours"}
        return set()

    def _explicit_boiler_result_limit(
        self,
        scored: list[tuple[int, Product]],
        query: SearchQuery,
    ) -> int:
        """Keep every exact rating plus a small, useful analogue tail.

        The normal top-N cut used to hide exact out-of-stock models after a few
        pages.  Returning every exact match makes pagination complete for any
        requested rating, while six nearest non-exact candidates keep the
        analogue tail bounded and relevant.
        """
        requested_limit = _requested_result_limit(query.slots)
        if requested_limit is not None:
            return requested_limit
        exact_count = sum(
            1
            for _, product in scored
            if self._explicit_boiler_power_priority(product, query)[0] in {0, 1}
        )
        return min(len(scored), exact_count + 6)

    def _default_preferred_brand(
        self,
        product: Product,
        query: SearchQuery,
    ) -> bool:
        # An explicit lowest-price request is stronger than the store's normal
        # VALTEC priority.  Without this exception a 21k VALTEC vessel could be
        # labelled "самый дешёвый" ahead of a valid 2k Gekon vessel.
        if query.brand or query.sku or query.cheap or query.slots.get("sort_mode") == "price_asc":
            return False
        return normalize_text(product.brand) == DEFAULT_PREFERRED_BRAND

    def _explicit_boiler_power_priority(
        self,
        product: Product,
        query: SearchQuery,
    ) -> tuple[int, float]:
        """Keep an explicitly requested boiler rating ahead of all analogues.

        ``power_kw`` names a concrete catalogue characteristic, unlike
        ``area_m2`` which is only a sizing estimate.  Exact in-stock models must
        therefore survive the top-N search cut before brand, price or fuzzy
        relevance can promote a different rating.
        """
        if query.category != "boilers":
            return (0, 0.0)
        return self._boiler_power_priority_for_slots(product, query.slots)

    def _boiler_power_priority_for_slots(
        self,
        product: Product,
        slots: Mapping[str, Any],
    ) -> tuple[int, float]:
        requested_kw = _constraint_number(slots.get("power_kw"))
        if requested_kw is None:
            return (0, 0.0)
        actual_kw = self._extract_power_kw(product)
        if actual_kw is None:
            return (4, float("inf"))
        distance = abs(actual_kw - requested_kw)
        exact = distance <= 0.05
        if exact and product.is_in_stock:
            tier = 0
        elif exact:
            tier = 1
        elif product.is_in_stock:
            tier = 2
        else:
            tier = 3
        return (tier, distance)

    def _search_sku(self, sku: str) -> list[Product]:
        resolution = self.resolve_sku(sku)
        if resolution.status not in {
            SkuResolutionStatus.EXACT,
            SkuResolutionStatus.UNIQUE_PREFIX,
        }:
            return []
        return list(resolution.candidates)

    def resolve_sku(self, sku: str) -> SkuResolution[Product]:
        """Resolve an explicit article without fuzzy or substring matching."""

        return resolve_catalog_sku(sku, self.products)

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

    def _is_actual_water_heater(self, product: Product) -> bool:
        """Separate complete water-heating appliances from their accessories.

        The feed section ``Водонагреватели`` also contains heating elements,
        anodes, control panels, jackets and connection kits.  A section path is
        therefore not evidence that a row is an appliance.  Prefer the explicit
        product type, and use the name only for the sparse indirect-cylinder
        rows that have no structured type.
        """
        name = normalize_text(product.name)
        product_types = {
            normalize_text(str(value))
            for key, value in product.attributes_normalized.items()
            if "тип товара" in normalize_text(key)
        }
        if any(value == "водонагреватель" for value in product_types):
            return True
        if product_types.intersection(
            {
                "тэн",
                "анод",
                "комплектующие",
                "автоматика",
                "запчасть",
                "аксессуар",
            }
        ):
            return False

        accessory_prefixes = (
            "тэн ",
            "анод ",
            "комплект ",
            "панель ",
            "термостат ",
            "датчик ",
            "изоляция ",
            "кожух ",
            "фланец ",
            "прокладка ",
            "клапан ",
        )
        if name.startswith(accessory_prefixes):
            return False
        return bool(
            re.match(
                r"^(?:(?:электрическ|газов|накопительн|проточн|"
                r"комбинированн)\w*\s+){0,3}водонагреватель\b",
                name,
            )
            or re.match(r"^бойлер\s+косвенн\w*\s+нагрев\w*\b", name)
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

    def _is_actual_hydraulic_accumulator(self, product: Product) -> bool:
        """Identify complete membrane vessels, not boiler built-in attributes."""
        type_text = self._attribute_text(product, ["тип товара"])
        name = normalize_text(product.name)
        path = normalize_text(product.category_path)
        if any(
            marker in type_text
            for marker in [
                "гидроаккумулятор",
                "расширительный бак",
                "мембранный бак",
            ]
        ):
            return True
        if "баки мембранные" not in path:
            return False
        return bool(
            re.search(r"\b(?:гидроаккумулятор|гидробак)\w*\b", name)
            or ("мембран" in name and "бак" in name)
            or re.search(r"\bбак\w*\s+расш", name)
        )

    def _is_actual_filter(self, product: Product) -> bool:
        """Complete water filters, housings and cartridges, not valve strainers."""
        name = normalize_text(product.name)
        path = normalize_text(product.category_path)
        type_text = self._attribute_text(product, ["тип товара"])
        identity = self.product_identity(product)
        if identity.primary_kind in FILTER_PRIMARY_KINDS:
            return bool(
                any(marker in path for marker in ["фильтр", "водоподготов", "водоочист"])
                or any(
                    marker in type_text
                    for marker in ["фильтр", "картридж", "корпус фильтра"]
                )
            )
        # A tap/valve/pump merely supplied with a filter (or a filter embedded
        # in a valve) must not let a secondary token replace the title head.
        if identity.primary_kind is not None:
            return False
        if any(marker in type_text for marker in ["кран", "клапан", "редуктор", "насос"]):
            return False
        if any(marker in type_text for marker in ["фильтр", "картридж", "корпус фильтра"]):
            return True
        if not any(marker in path for marker in ["фильтр", "водоподготов", "водоочист"]):
            return False
        return bool(
            re.search(r"\b(?:фильтр|картридж|мембрана|корпус)\w*\b", name)
            or re.search(r"\b(?:10|20)\s*(?:sl|bb)\b", name)
        )

    def _is_actual_control(self, product: Product) -> bool:
        """Heating controls/actuators, excluding complete valves and appliances."""
        name = normalize_text(product.name)
        type_text = self._attribute_text(product, ["тип товара"])
        path = normalize_text(product.category_path)
        if self._is_actual_valve(product):
            return False
        if (
            "радиатор" in path
            and (
                "термоголов" in f"{name} {type_text}"
                or (
                    "термостатическ" in f"{name} {type_text}"
                    and "голов" in f"{name} {type_text}"
                )
            )
        ):
            return False
        return bool(
            any(
                marker in type_text
                for marker in [
                    "сервопривод",
                    "термостат",
                    "терморегулятор",
                    "контроллер",
                    "насосно-смесительный узел",
                    "частотный преобразователь",
                ]
            )
            or re.match(
                r"^(?:комнатн\w*\s+)?(?:термостат|терморегулятор|сервопривод|"
                r"контроллер|насосно-смесительн\w*\s+узел|частотн\w*\s+преобразователь)\b",
                name,
            )
            or bool(re.search(r"\brtl\b", name))
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
        return self.product_identity(product).primary_kind in VALVE_PRIMARY_KINDS

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

    def product_identity(self, product: Product) -> ProductIdentityFacts:
        """Return the shared primary-vs-embedded identity for ``product``."""
        cache_key = id(product)
        cached = self._product_identity_cache.get(cache_key)
        if cached is not None:
            return cached
        identity = product_identity_facts(product)
        self._product_identity_cache[cache_key] = identity
        return identity

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

        if self._is_actual_hydraulic_accumulator(product):
            return "hydraulic_accumulators"
        # Водо- и теплосчётчики: 62 позиции каталога, которым до сих пор не
        # соответствовала ни одна внутренняя категория — они все попадали в
        # «other» и были недоступны для подбора.
        if "счетчик" in path or "счетчик" in name or "водомер" in name:
            return "meters"
        if self._is_actual_filter(product):
            return "filters"
        if self._is_actual_water_heater(product):
            return "water_heaters"
        if self._is_actual_boiler(product):
            return "boilers"
        if self._is_actual_pump(product):
            return "pumps"
        if self._is_actual_control(product):
            return "controls"
        if "канализац" in path or re.search(
            r"\b(?:htu|htea|htb|htem|kgem|kgb)\b",
            name,
        ):
            return "sewer"
        if self._is_actual_radiator(product):
            return "radiators"
        if self._is_radiator_fitting(product):
            return "radiator_fittings"
        primary_category = self.product_identity(product).primary_category
        if primary_category == "valves":
            return "valves"
        if any(needle in name for needle in ["угольник", "муфта", "тройник", "переходник"]):
            return "fittings"
        if "фитинг" in path:
            return "fittings"
        if self._is_actual_pipe(product):
            return "pipes"
        return "other"

    def search_unsupported_family(
        self,
        pattern: str,
        message: str,
        limit: int = 3,
        required_word: str | None = None,
    ) -> list[Product]:
        """Позиции семейства вне зоны подбора — поиск по названию.

        Инженерные проверки здесь не применяются и не обещаются: для этих
        групп у бота нет ни правил совместимости, ни контракта уточнений.
        Ранжирование простое — совпадение слов запроса, затем наличие и цена.
        """
        family_re = re.compile(pattern)
        # Покупатель назвал конкретный предмет («унитаз»), а в семейство входят
        # и смежные позиции. Без этого на запрос про унитаз в ответ попадало
        # крепление для умывальника — формально та же группа, но не то.
        word_re = re.compile(re.escape(required_word)) if required_word else family_re
        candidates = [
            product
            for product in self.products
            if product.price is not None
            and product.url
            and word_re.search(
                normalize_text(f"{product.name} {product.category_path}")
            )
        ]
        if not candidates:
            return []

        tokens = [
            token
            for token in re.findall(
                r"[a-zа-я0-9]+(?:[./xх×-][a-zа-я0-9]+)*",
                normalize_text(message),
            )
            if len(token) >= 2
            and token not in NAME_QUERY_STOPWORDS
            and not family_re.fullmatch(token)
        ]

        def overlap(product: Product) -> int:
            blob = normalize_text(
                " ".join(
                    [
                        product.name,
                        product.brand or "",
                        *[
                            f"{key} {value}"
                            for key, value in product.attributes_normalized.items()
                        ],
                    ]
                )
            )
            return sum(1 for token in tokens if token in blob)

        def is_the_product_itself(product: Product) -> bool:
            """Товар — это сам предмет, а не аксессуар к нему.

            «Унитаз подвесной Iddis» против «Обрамление для унитаза»: слово
            покупателя стоит в начале названия или в «Тип товара» только у
            самого изделия.
            """
            if not required_word:
                return True
            # И в типе товара смотрим только на головное слово: «Сиденье для
            # унитаза» — это сиденье, а не унитаз.
            declared = normalize_text(self._attribute_text(product, ["тип товара"]))
            if declared and required_word in declared.split(" ", 1)[0]:
                return True
            # Именно первое слово названия: «Раковина универсальная» — это
            # раковина, а «Выпуск для раковины» — аксессуар к ней.
            head_word = normalize_text(product.name).split(" ", 1)[0]
            return required_word in head_word

        candidates.sort(
            key=lambda product: (
                not is_the_product_itself(product),
                -overlap(product),
                not product.is_in_stock,
                product.price if product.price is not None else float("inf"),
            )
        )
        return candidates[:limit]

    def _brand_word_keys(self) -> set[str]:
        """Слова, из которых состоят названия брендов каталога."""
        if self._brand_word_key_cache is None:
            words: set[str] = set()
            for product in self.products:
                for word in re.split(r"[\s./-]+", str(product.brand or "")):
                    for key in (
                        fold_model_key(word),
                        transliterate_model_key(word),
                    ):
                        if len(key) >= 2:
                            words.add(key)
            self._brand_word_key_cache = words
        return self._brand_word_key_cache

    def _model_key(self, product: Product) -> str:
        """Ключ модели товара с кэшем по артикулу.

        Кэш намеренно по SKU, а не по ``id`` объекта: временные объекты
        переиспользуют адреса, и кэш по ``id`` начинает возвращать чужие
        значения.
        """
        cached = self._model_key_cache.get(product.sku)
        if cached is None:
            identity_attributes = " ".join(
                str(value)
                for key, value in product.attributes_normalized.items()
                if any(
                    marker in normalize_text(str(key))
                    for marker in ["полное наименование", "модель", "серия"]
                )
            )
            source = f"{product.brand or ''} {product.name} {identity_attributes}"
            # Keep both visual-homoglyph and phonetic transliteration keys.
            # A catalogue may spell a brand as ``РЕХАУ`` while the customer
            # writes ``REHAU``; either spelling must resolve the same explicit
            # model without loosening any dimensional constraints.
            cached = f"{fold_model_key(source)}|{transliterate_model_key(source)}"
            self._model_key_cache[product.sku] = cached
        return cached

    # Модельная фраза в реплике: буквы вплотную к числам («UPС 25-40 180»,
    # «Star RS 25/6», «SB28»). Ищется по исходному тексту, потому что
    # нормализация могла привести кириллическую «С» к латинской «S», и точный
    # ключ модели переставал совпадать с фидом.
    # Маркировка всегда начинается с латинской буквы («UPС», «Star RS», «SB»),
    # поэтому русское слово перед ней («насос UPС 25-40») в фразу не попадает.
    _MODEL_PHRASE_RE = re.compile(
        r"[a-z][a-zа-яё]{1,9}\s*[-/]?\s*[a-zа-яё]{0,4}\s*\d{1,4}"
        r"(?:\s*[-/]\s*\d{1,4}){0,3}",
        re.IGNORECASE,
    )

    # Слово-имя: латиница целиком либо кириллица с заглавной. Числа, единицы и
    # размеры сюда не попадают — они не являются идентичностью товара.
    _NAME_TOKEN_RE = re.compile(r"[A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё.\-]{2,}")

    def _catalog_word_keys(self) -> set[str]:
        """Все слова из названий и брендов каталога, свёрнутые в ключи."""
        if self._catalog_word_key_cache is None:
            words: set[str] = set()
            for product in self.products:
                source = f"{product.name} {product.brand or ''} {product.category_path}"
                for word in re.split(r"[\s./\-,()«»\"]+", source):
                    for key in (
                        fold_model_key(word),
                        transliterate_model_key(word),
                    ):
                        if len(key) >= 3:
                            words.add(key)
            self._catalog_word_key_cache = words
        return self._catalog_word_key_cache

    # Слова, которые выглядят как имя, но маркой не являются. Оговорка «такой
    # позиции не нахожу» задумана для «Сунержа Модус», а срабатывала на
    # топонимах («Северной Осетии»), валютах («RUB»), собственных сокращениях
    # бота («ВР-НР») и на кириллическом написании марки из каталога
    # («Ардерия» при наличии Arderia).
    # Ключи хранятся в транслитерированном виде — в том же, в каком приходит
    # проверяемый токен, иначе кириллическая запись мимо набора проходит.
    _IDENTITY_STOP_KEYS = frozenset(
        {
            # валюты и единицы
            "rub", "usd", "eur", "rub", "kvt", "vt", "bar", "mpa", "atm",
            "mm", "sm", "litr", "kg", "sht",
            # инженерные сокращения из собственного словаря бота
            "gvs", "hvs", "vrnr", "vrvr", "nrnr", "dn", "du",
            "pn", "sdr", "gost", "snip", "din", "iso", "aisi",
            "opentherm", "evrokonus", "amerikanka",
            # служебные слова запроса
            "artikul", "model", "seriia", "brend", "katalog", "zakaz",
            "ooo", "zao", "oao", "pao", "inn", "ogrn", "kpp",
        }
    )
    # Место, а не марка: «в Москве», «из Северной Осетии», «город Самара».
    _PLACE_CONTEXT_RE = re.compile(
        # Не больше одного слова между предлогом и именем: «из Северной
        # Осетии» — место, а «в наличии полотенцесушитель Сунержа» — марка,
        # и жадный шаблон глушил как раз тот случай, ради которого писался.
        r"\b(?:в|во|из|до|по|на|под)\s+(?:[а-яё-]+\s+){0,1}$"
        r"|\b(?:город|г\.|область|обл\.|улица|ул\.|шоссе|проспект|"
        r"деревн\w*|посел\w*|район)\s+(?:[а-яё-]+\s+){0,1}$",
        re.IGNORECASE,
    )

    def _matches_catalog_by_sound(self, token: str) -> bool:
        """Совпадает ли токен с брендом каталога после транслитерации.

        «Ардерия» и «Arderia» — одна марка. Свёртка гомоглифов их не сближает,
        поэтому сравниваем по звучанию и с допуском в один-два символа:
        «ия» на конце даёт ``arderiia`` против ``arderia``.
        """
        key = transliterate_model_key(token)
        if len(key) < 4:
            return False
        if key in self._brand_word_keys() or key in self._catalog_word_keys():
            return True
        for known in self._brand_word_keys():
            if abs(len(known) - len(key)) <= 2 and fuzz.ratio(key, known) >= 88:
                return True
        return False

    def unknown_identity_tokens(self, message: str) -> list[str]:
        """Слова-имена из реплики, которых нет в каталоге ни в каком виде.

        Покупатель, назвавший «Сунержа Модус», заслуживает честного «такой
        позиции не нахожу», а не трёх позиций другого бренда молча. Здесь
        намеренно узкое определение имени: латинское слово или кириллическое
        с заглавной буквы не в начале фразы. Первое слово пропускается, потому
        что предложение и так начинается с заглавной, а падежные формы
        нарицательных («Котла», «Трубы») не должны считаться марками.
        """

        if not self.products or not message:
            return []
        known = self._catalog_word_keys()
        brand_words = self._brand_word_keys()
        text = str(message)
        unknown: list[str] = []
        for match in self._NAME_TOKEN_RE.finditer(text):
            token = match.group(0).strip(".-")
            if len(token) < 3:
                continue
            is_latin = bool(re.fullmatch(r"[A-Za-z.\-]+", token))
            # Заглавная в начале предложения ничего не значит: «Когда будет?»
            # после точки — обычный вопрос, а не марка.
            starts_sentence = not text[: match.start()].strip() or bool(
                re.search(r"[.!?]\s*$", text[: match.start()])
            )
            is_capitalised = token[:1].isupper()
            if not is_latin and not (is_capitalised and not starts_sentence):
                continue
            key = fold_model_key(token)
            if len(key) < 3 or key in known or key in brand_words:
                continue
            if transliterate_model_key(token) in self._IDENTITY_STOP_KEYS:
                continue
            if self._matches_catalog_by_sound(token):
                continue
            if self._PLACE_CONTEXT_RE.search(text[: match.start()]):
                continue
            unknown.append(token)
        return unknown

    def find_named_models(
        self,
        *,
        brand: str | None = None,
        name_tokens: list[str] | None = None,
        old_model: str | None = None,
        message: str | None = None,
        category: str | None = None,
        limit: int = 3,
    ) -> list[Product]:
        """Товары по явно названной модели: «Wilo Star RS 25/6», «Arderia D24».

        Покупатель, назвавший конкретную модель, не должен получать вопрос про
        площадь дома: модель уже определяет товар. Сопоставление идёт по ключу
        модели, поэтому смешанные алфавиты в фиде и разные разделители
        («25/6» против «25-6») ему не мешают.
        """
        if not self.products:
            return []

        token_keys = [
            key
            for key in (fold_model_key(token) for token in (name_tokens or []))
            if len(key) >= 2
        ]
        brand_key = fold_model_key(brand) if brand else ""
        old_model_key = fold_model_key(old_model) if old_model else ""

        attempts: list[list[str]] = []
        if len(old_model_key) >= 4:
            attempts.append([old_model_key])
        if brand_key and token_keys:
            attempts.append([brand_key, *token_keys])
        # Пара токенов без бренда — это тоже маркировка: «KERMI FKO», где
        # серия важна функционально (FKO — боковое подключение, FTV — нижнее
        # со встроенным клапаном), и подмена одной на другую недопустима.
        # Но если все токены — это просто слова бренда («PRO AQUA»), то перед
        # нами обычный запрос по бренду и параметрам, а не по маркировке.
        if (
            not brand_key
            and len(token_keys) >= 2
            and any(key not in self._brand_word_keys() for key in token_keys)
        ):
            attempts.append(list(token_keys))
        # Модельный токен с цифрой («d24», «sb28») однозначен и без бренда.
        digit_tokens = [key for key in token_keys if any(ch.isdigit() for ch in key)]
        if digit_tokens:
            attempts.append(digit_tokens)
        # Фразы из самой реплики: они сохраняют алфавит, которым писал
        # покупатель, и переживают нормализацию бренда. Разбирается только
        # тогда, когда роутер уже увидел бренд или маркировку — иначе
        # «котёл на 100 м2» превращается в модельный ключ и ловит случайное.
        has_model_context = bool(brand_key or old_model_key or token_keys)
        for phrase in (
            self._MODEL_PHRASE_RE.findall(str(message or ""))
            if has_model_context
            else []
        ):
            phrase_key = fold_model_key(phrase)
            if len(phrase_key) >= 6 and any(ch.isdigit() for ch in phrase_key):
                attempts.append([phrase_key])
        if not attempts:
            return []

        for required in attempts:
            matches = [
                product
                for product in self.products
                if all(part in self._model_key(product) for part in required)
            ]
            if not matches:
                continue
            if category:
                in_category = [
                    product
                    for product in matches
                    if self.canonical_category(product) == category
                ]
                # Категорию мог не угадать роутер; тогда показываем найденное.
                matches = in_category or matches
            # Числа из реплики уточняют исполнение внутри серии: «RWH 80
            # Citadel Unic» — это 80 литров, а не любой объём этой серии.
            message_numbers = re.findall(r"\d{2,4}", str(message or ""))
            if message_numbers:
                exact = [
                    product
                    for product in matches
                    if all(number in self._model_key(product) for number in message_numbers)
                ]
                matches = exact or matches
            matches.sort(
                key=lambda product: (
                    not product.is_in_stock,
                    product.price if product.price is not None else float("inf"),
                )
            )
            return matches[:limit]
        return []

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
            "water_heaters": "water_heaters",
            "water_heater": "water_heaters",
            "водонагреватель": "water_heaters",
            "водонагреватели": "water_heaters",
            "hydraulic_accumulators": "hydraulic_accumulators",
            "hydraulic_accumulator": "hydraulic_accumulators",
            "гидроаккумулятор": "hydraulic_accumulators",
            "гидроаккумуляторы": "hydraulic_accumulators",
            "мембранные баки": "hydraulic_accumulators",
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
        requested_brand = normalize_text(str(slots.get("brand") or ""))
        if requested_brand:
            products = [
                product
                for product in products
                if requested_brand in normalize_text(product.brand)
            ]
        products = [
            product
            for product in products
            if _product_matches_hard_constraints(product, slots)
            and self._semantic_slots_match(product, category, slots)
        ]

        def brand_priority(product: Product) -> bool:
            return bool(
                not requested_brand
                and normalize_text(product.brand) != DEFAULT_PREFERRED_BRAND
            )

        if category == "boilers":
            if slots.get("power_kw"):
                return sorted(
                    products,
                    key=lambda product: (
                        *self._boiler_power_priority_for_slots(product, slots),
                        brand_priority(product),
                        product.price or float("inf"),
                    ),
                )
            required_kw = (
                float(slots["area_m2"]) / 10.0
                if slots.get("area_m2")
                else None
            )
            if required_kw:
                def closeness(product: Product) -> tuple:
                    power = self._extract_power_kw(product) or 0.0
                    enough = power >= required_kw * 0.9
                    return (
                        not product.is_in_stock,
                        not enough,
                        brand_priority(product),
                        abs(power - required_kw),
                    )

                return sorted(products, key=closeness)
            return sorted(
                products,
                key=lambda p: (
                    brand_priority(p),
                    not p.is_in_stock,
                    p.price or float("inf"),
                ),
            )
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
                        return (
                            kind_priority,
                            brand_priority(product),
                            not product.is_in_stock,
                            product.price or float("inf"),
                        )

                    return sorted(products, key=irrigation_priority)
            return sorted(
                products,
                key=lambda p: (
                    brand_priority(p),
                    not p.is_in_stock,
                    p.price or float("inf"),
                ),
            )
        if category == "sewer":
            element_type = normalize_text(str(slots.get("element_type") or ""))
            if element_type:
                products = [
                    product
                    for product in products
                    if self._sewer_element_matches(product, element_type)
                ]
            return sorted(
                products,
                key=lambda p: (
                    brand_priority(p),
                    not p.is_in_stock,
                    p.price or float("inf"),
                ),
            )
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
            return sorted(
                products,
                key=lambda p: (
                    brand_priority(p),
                    not p.is_in_stock,
                    p.price or float("inf"),
                ),
            )
        return sorted(
            products,
            key=lambda p: (
                brand_priority(p),
                not p.is_in_stock,
                p.price or float("inf"),
            ),
        )

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

    @staticmethod
    def _canonical_heater_type(value: object) -> str | None:
        normalized = normalize_text(str(value or ""))
        if "косвен" in normalized:
            return "косвенного нагрева"
        if "проточ" in normalized:
            return "проточный"
        if "накоп" in normalized or normalized in {"бойлер", "баковый"}:
            return "накопительный"
        return None

    @staticmethod
    def _canonical_heater_energy(value: object) -> str | None:
        normalized = normalize_text(str(value or ""))
        if "комбинир" in normalized or "комбинирован" in normalized:
            return "комбинированный"
        if (
            "косвен" in normalized
            or "внешн" in normalized and "источник" in normalized
            or "от котл" in normalized
        ):
            return "косвенный"
        if "газ" in normalized:
            return "газовый"
        if "электр" in normalized:
            return "электрический"
        return None

    @staticmethod
    def _canonical_heater_mounting(value: object) -> str | None:
        normalized = normalize_text(str(value or ""))
        if "под мойк" in normalized or "под раковин" in normalized:
            return "под мойкой"
        if (
            "над мойк" in normalized
            or "над раковин" in normalized
            or "на раковин" in normalized
        ):
            return "над мойкой"
        if "настен" in normalized or "на стен" in normalized:
            return "настенный"
        if "наполь" in normalized or "на пол" in normalized:
            return "напольный"
        if "универс" in normalized:
            return "универсальный"
        return None

    @staticmethod
    def _canonical_heater_orientation(value: object) -> str | None:
        normalized = normalize_text(str(value or ""))
        has_vertical = "вертик" in normalized
        has_horizontal = "горизонт" in normalized
        if "универс" in normalized or (has_vertical and has_horizontal):
            return "универсальный"
        if has_vertical:
            return "вертикальный"
        if has_horizontal:
            return "горизонтальный"
        return None

    def _water_heater_attribute_values(
        self,
        product: Product,
        key_markers: tuple[str, ...],
    ) -> list[str]:
        normalized_markers = tuple(normalize_text(marker) for marker in key_markers)
        return [
            str(value)
            for key, value in product.attributes_normalized.items()
            if any(marker in normalize_text(key) for marker in normalized_markers)
        ]

    def _water_heater_type(self, product: Product) -> str | None:
        # «Косвенный» is a user-visible heater class even when a supplier also
        # calls the vessel накопительный in the generic type field.
        indirect_evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    *self._water_heater_attribute_values(
                        product,
                        ("вид нагрева", "способ нагрева"),
                    ),
                ]
            )
        )
        if "косвен" in indirect_evidence:
            return "косвенного нагрева"
        values = self._water_heater_attribute_values(
            product,
            ("тип водонагревателя",),
        )
        for value in values:
            canonical = self._canonical_heater_type(value)
            if canonical:
                return canonical
            # Suppliers often put "Комбинированный" into the appliance-type
            # field even though it describes two heat sources. Such products
            # are tank/storage heaters; energy remains a separate dimension.
            if "комбинир" in normalize_text(value):
                return "накопительный"
        canonical = self._canonical_heater_type(product.name)
        if canonical:
            return canonical
        description = normalize_text(product.description or "")
        if re.search(r"\bнакопительн\w*\s+водонагрев", description):
            return "накопительный"
        if re.search(r"\bпроточн\w*\s+водонагрев", description):
            return "проточный"
        if re.search(r"\bбойлер\w*\s+косвенн\w*\s+нагрев", description):
            return "косвенного нагрева"
        return None

    def _water_heater_energy_source(self, product: Product) -> str | None:
        values = self._water_heater_attribute_values(
            product,
            (
                "вид нагрева",
                "способ нагрева",
                "источник энергии",
                "тип нагрева",
            ),
        )
        for value in values:
            canonical = self._canonical_heater_energy(value)
            if canonical:
                return canonical
        canonical = self._canonical_heater_energy(product.name)
        if canonical:
            return canonical
        description = normalize_text(product.description or "")
        if (
            re.search(r"\bэлектрическ\w*\s+(?:накопительн\w*\s+)?водонагрев", description)
            or "электрический нагрев" in description
        ):
            return "электрический"
        if (
            re.search(r"\bгазов\w*\s+(?:проточн\w*\s+)?водонагрев", description)
            or re.search(r"\bгазов\w*\s+колонк", description)
        ):
            return "газовый"
        if (
            re.search(r"\bбойлер\w*\s+косвенн\w*\s+нагрев", description)
            or re.search(r"\bгре\w*\s+[^.!?]{0,30}\bот\s+котл", description)
        ):
            return "косвенный"
        return None

    def _water_heater_volume_l(self, product: Product) -> float | None:
        volume_values = self._water_heater_attribute_values(
            product,
            (
                "объем бака",
                "объём бака",
                "объем, л",
                "объём, л",
                "номинальный объем",
                "номинальный объём",
                "литраж",
            ),
        )
        for value in volume_values:
            number = _constraint_number(value)
            if number is not None:
                return number

        # A unit is mandatory in the name fallback.  Model suffixes such as
        # ``CW080`` are not proof of an 80-litre vessel.
        name = normalize_text(product.name)
        match = re.search(
            r"(?<![a-zа-я0-9])(\d+(?:[.,]\d+)?)\s*(?:л\b|литр\w*\b|liter\b)",
            name,
        )
        if not match:
            description = normalize_text(product.description or "")
            description_patterns = (
                r"\b(?:полезн\w*\s+)?объем\w*(?:\s+бака)?"
                r"\s*(?::|-|составляет)?\s*(\d+(?:[.,]\d+)?)\s*"
                r"(?:л\b|литр\w*\b)",
                r"\b(?:бойлер|водонагревател)\w*[^.!?]{0,45}"
                r"\bна\s+(\d+(?:[.,]\d+)?)\s*литр\w*\b",
            )
            for pattern in description_patterns:
                description_match = re.search(pattern, description)
                if description_match:
                    return float(description_match.group(1).replace(",", "."))
            return None
        return float(match.group(1).replace(",", "."))

    def _water_heater_mounting(self, product: Product) -> str | None:
        values = self._water_heater_attribute_values(
            product,
            (
                "монтаж",
                "способ крепления",
                "тип размещения",
                "размещение",
            ),
        )
        for value in values:
            canonical = self._canonical_heater_mounting(value)
            if canonical:
                return canonical
        return self._canonical_heater_mounting(
            " ".join([product.name, product.description or ""])
        )

    def _water_heater_orientation(self, product: Product) -> str | None:
        values = self._water_heater_attribute_values(
            product,
            ("установка", "ориентация"),
        )
        for value in values:
            canonical = self._canonical_heater_orientation(value)
            if canonical:
                return canonical
        name = normalize_text(product.name)
        if "вертик" in name or "горизонт" in name:
            canonical = self._canonical_heater_orientation(name)
            if canonical:
                return canonical
        description = normalize_text(product.description or "")
        if (
            re.search(
                r"\bуниверсальн(?:ая|ой)\s+"
                r"(?:установ|ориентац)\w*\b"
                r"|\b(?:установ|ориентац)\w*\s+"
                r"универсальн(?:ая|ой)\b"
                r"|\bуниверсальн(?:ый|ого)\s+(?:способ\s+)?монтаж\w*\b"
                r"|\bмонтаж\w*\s+универсальн(?:ый|ого)\b",
                description,
            )
            or re.search(
                r"(?:установ|монтаж)\w*[^.!?]{0,35}"
                r"(?:вертикальн\w*\s+(?:или|и|/)\s+горизонтальн\w*|"
                r"горизонтальн\w*\s+(?:или|и|/)\s+вертикальн\w*)",
                description,
            )
            or re.search(
                r"как\s+вертикальн\w*\s*,?\s+так\s+и\s+горизонтальн",
                description,
            )
        ):
            return "универсальный"
        if re.search(
            r"(?:установ|монтаж)\w*[^.!?]{0,20}"
            r"(?:строго\s+)?вертикальн",
            description,
        ):
            return "вертикальный"
        if re.search(
            r"(?:установ|монтаж)\w*[^.!?]{0,20}горизонтальн",
            description,
        ):
            return "горизонтальный"
        return None

    def water_heater_reference_slots(self, product: Product) -> dict[str, object]:
        """Return verified compatibility dimensions for a shown water heater.

        A concrete card is an identity boundary for follow-up analogue searches.
        Recover the appliance facts from that exact feed row instead of trying to
        infer them again from a short command such as ``покажи аналоги``.
        Unknown dimensions are deliberately omitted: they must never be invented
        merely to make an alternative pass the filter.
        """
        if self.canonical_category(product) != "water_heaters":
            return {}
        values: dict[str, object | None] = {
            "heater_type": self._water_heater_type(product),
            "energy_source": self._water_heater_energy_source(product),
            "volume_l": self._water_heater_volume_l(product),
            "mounting": self._water_heater_mounting(product),
            "orientation": self._water_heater_orientation(product),
        }
        return {
            key: value
            for key, value in values.items()
            if value is not None
        }

    def pump_reference_slots(self, product: Product) -> dict[str, object]:
        """Return verified compatibility dimensions for one shown pump."""
        if self.canonical_category(product) != "pumps":
            return {}

        pump_type = next(
            (
                candidate
                for candidate in [
                    "циркуляционный",
                    "скважинный",
                    "дренажный",
                    "поверхностный",
                    "повысительный",
                    "насосная станция",
                ]
                if self._pump_type_matches(product, candidate)
            ),
            None,
        )
        head = self._maximum_head_m(product)

        mounting_values: list[float] = []
        connection_values: list[float] = []
        for key, value in product.attributes_normalized.items():
            normalized_key = normalize_text(str(key))
            numbers = [
                float(raw.replace(",", "."))
                for raw in re.findall(r"\d+(?:[,.]\d+)?", str(value))
            ]
            if "монтажная длина" in normalized_key:
                mounting_values.extend(numbers)
            if any(
                marker in normalized_key
                for marker in [
                    "диаметр условного прохода",
                    "номинальный диаметр",
                    "условный диаметр",
                ]
            ):
                connection_values.extend(numbers)

        if not connection_values:
            name_connection = re.search(
                r"(?<!\d)(\d{2,3})\s*[/\-]\s*\d",
                normalize_text(product.name),
            )
            if name_connection:
                connection_values.append(float(name_connection.group(1)))

        values: dict[str, object | None] = {
            "pump_type": pump_type,
            "head_m": head,
            "mounting_length_mm": (
                int(mounting_values[0])
                if len(set(mounting_values)) == 1
                else None
            ),
            "connection_size": (
                int(connection_values[0])
                if len(set(connection_values)) == 1
                else None
            ),
        }
        return {key: value for key, value in values.items() if value is not None}

    def product_reference_slots(self, product: Product) -> dict[str, object]:
        """Recover verified hard facets for analogue follow-ups.

        A command such as ``покажи аналоги`` contains no product dimensions.
        They must come from the exact source row; otherwise analogue search is
        either an ungrounded fuzzy lookup or returns nothing.  Only facts with
        unambiguous catalogue evidence are emitted.
        """

        category = self.canonical_category(product)
        if category == "water_heaters":
            return self.water_heater_reference_slots(product)
        if category == "pumps":
            return self.pump_reference_slots(product)
        if category not in {"valves", "radiator_fittings", "fittings"}:
            return {}

        slots: dict[str, object] = {}
        inch_sizes = self._product_inch_sizes(product)
        if len(inch_sizes) == 1:
            slots["size_inch"] = next(iter(inch_sizes))

        thread_facts = product_thread_facts(product)
        if thread_facts.pair is not None:
            slots["thread_type"] = thread_facts.pair
        elif len(thread_facts.genders) == 1:
            slots["thread_gender"] = next(iter(thread_facts.genders))

        product_kinds = [
            canonical
            for canonical in [
                "ball_valve",
                "thermostatic_head",
                "thermostatic_valve",
                "elbow",
            ]
            if self._product_kind_matches(product, canonical)
        ]
        if len(product_kinds) == 1:
            slots["product_kind"] = product_kinds[0]

        body_forms = [
            canonical
            for canonical in ["straight", "angled"]
            if self._body_form_matches(product, canonical)
        ]
        if len(body_forms) == 1:
            slots["body_form"] = body_forms[0]

        if category == "valves":
            handle_types = [
                canonical
                for canonical in ["butterfly", "lever", "mini", "t-shaped"]
                if self._handle_type_matches(product, canonical)
            ]
            if len(handle_types) == 1:
                slots["handle_type"] = handle_types[0]
            if self._bore_type_matches(product, {"full_bore": True}):
                slots["full_bore"] = True
            elif self._bore_type_matches(product, {"full_bore": False}):
                slots["full_bore"] = False
            if self._union_matches(product):
                slots["union"] = True

        if category == "fittings":
            for system in ["ppr", "pex", "pert", "пнд", "пресс", "обжимной"]:
                if self._fitting_system_matches(product, system):
                    slots["fitting_system"] = system
                    break
            diameter_values: set[int] = set()
            for key, value in product.attributes_normalized.items():
                normalized_key = normalize_text(str(key))
                if "диаметр" not in normalized_key or "дюйм" in normalized_key:
                    continue
                for raw in re.findall(r"\d+(?:[,.]\d+)?", str(value)):
                    number = float(raw.replace(",", "."))
                    if number.is_integer() and 6 <= number <= 250:
                        diameter_values.add(int(number))
            if len(diameter_values) == 1:
                slots["diameter_mm"] = next(iter(diameter_values))
            angles = set(self._product_angles(product))
            if len(angles) == 1:
                slots["angle_deg"] = next(iter(angles))
        return slots

    def _water_heater_type_matches(self, product: Product, requested: object) -> bool:
        expected = self._canonical_heater_type(requested)
        actual = self._water_heater_type(product)
        return expected is not None and actual is not None and actual == expected

    def _water_heater_energy_matches(self, product: Product, requested: object) -> bool:
        expected = self._canonical_heater_energy(requested)
        actual = self._water_heater_energy_source(product)
        if expected is None or actual is None:
            return False
        if actual == expected:
            return True
        if expected == "косвенный" and actual == "комбинированный":
            # A combined indirect cylinder adds an electric element but still
            # supports heating from the boiler coil.  Treat it as compatible
            # only when the card explicitly identifies an indirect-heating
            # appliance; never relax every combined heater this way.
            evidence = normalize_text(
                " ".join(
                    [
                        product.name,
                        product.category_path,
                        *self._water_heater_attribute_values(
                            product,
                            ("полное наименование", "тип товара"),
                        ),
                    ]
                )
            )
            return bool(
                re.search(r"\b(?:бойлер\w*\s+)?косвенн\w*\s+нагрев", evidence)
            )
        return False

    def _water_heater_volume_matches(self, product: Product, requested: object) -> bool:
        expected = _constraint_number(requested)
        actual = self._water_heater_volume_l(product)
        return (
            expected is not None
            and actual is not None
            and abs(actual - expected) < 0.01
        )

    def _tank_application(self, product: Product) -> str | None:
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    product.category_path,
                    self._attribute_text(product, ["тип товара", "назначение"]),
                ]
            )
        )
        if any(
            marker in evidence
            for marker in [
                "гидроаккумулятор",
                "водоснабжен",
                "гвс и хвс",
                "хвс и гвс",
            ]
        ):
            return "водоснабжение"
        if "расширительн" in evidence and any(
            marker in evidence for marker in ["отоплен", "теплоносител"]
        ):
            return "отопление"
        return None

    def _tank_application_matches(self, product: Product, requested: object) -> bool:
        expected_text = normalize_text(str(requested or ""))
        expected = (
            "отопление"
            if any(marker in expected_text for marker in ["отоплен", "теплоносител"])
            else "водоснабжение"
            if any(
                marker in expected_text
                for marker in ["водоснаб", "гидроаккум", "насос", "гвс", "хвс"]
            )
            else None
        )
        return expected is not None and self._tank_application(product) == expected

    def _tank_volume_l(self, product: Product) -> float | None:
        for key, value in product.attributes_normalized.items():
            key_text = normalize_text(key)
            if "объем" not in key_text and "объём" not in key_text and "литраж" not in key_text:
                continue
            number = _constraint_number(value)
            if number is not None:
                return number
        name = normalize_text(product.name)
        match = re.search(
            r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:л\b|литр\w*)",
            name,
        )
        return float(match.group(1).replace(",", ".")) if match else None

    def _tank_volume_matches(self, product: Product, requested: object) -> bool:
        expected = _constraint_number(requested)
        actual = self._tank_volume_l(product)
        return (
            expected is not None
            and actual is not None
            and abs(actual - expected) < 0.01
        )

    def _tank_orientation_matches(self, product: Product, requested: object) -> bool:
        expected = self._canonical_heater_orientation(requested)
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(product, ["ориентация бака", "ориентация"]),
                ]
            )
        )
        actual = self._canonical_heater_orientation(evidence)
        return expected is not None and actual is not None and expected == actual

    def _water_heater_mounting_matches(self, product: Product, requested: object) -> bool:
        expected = self._canonical_heater_mounting(requested)
        actual = self._water_heater_mounting(product)
        return expected is not None and actual is not None and actual == expected

    def _water_heater_orientation_matches(
        self,
        product: Product,
        requested: object,
    ) -> bool:
        expected = self._canonical_heater_orientation(requested)
        actual = self._water_heater_orientation(product)
        if expected is None or actual is None:
            return False
        # A card explicitly marked universal/vertical+horizontal supports either
        # requested orientation; unknown orientation never does.
        return actual == expected or (
            actual == "универсальный"
            and expected in {"вертикальный", "горизонтальный"}
        )

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

    def _combustion_chamber_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested or ""))
        # Use the dedicated feed field. A family description that says models
        # exist with both chamber types is not evidence for this exact SKU.
        trusted = self._attribute_text(
            product,
            ["камера сгорания", "тип камеры сгорания"],
        )
        if not trusted:
            trusted = normalize_text(
                " ".join(
                    [
                        product.name,
                        self._attribute_text(product, ["тип котла", "полное наименование"]),
                    ]
                )
            )
        if "закрыт" in expected or "турб" in expected:
            return "закрыт" in trusted or "принудительн" in trusted or "турб" in trusted
        if "открыт" in expected or "атмосфер" in expected:
            return "открыт" in trusted or "естественн" in trusted or "атмосфер" in trusted
        return False

    def _chimney_size_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested or "")).replace("х", "/").replace("x", "/")
        parts = [int(value) for value in re.findall(r"\d{2,3}", expected)]
        if len(parts) < 2:
            return False
        evidence = self._attribute_text(
            product,
            [
                "диаметр дымохода",
                "диаметр коаксиального дымохода",
                "присоединительный размер дымохода",
            ],
        )
        if not evidence:
            return False
        actual = [int(value) for value in re.findall(r"\d{2,3}", evidence)]
        return parts[0] in actual and parts[1] in actual

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
        primary = normalize_text(
            " ".join([product.name, product.category_path, explicit])
        )
        purpose_markers = [
            "отопл",
            "теплый пол",
            "водоснаб",
            "водосн",
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
        evidence = normalize_text(
            " ".join([primary, product.description or ""])
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
            return any(marker in evidence for marker in ppr_markers)
        if any(
            marker in expected
            for marker in ["металлопласт", "металлополимер", "м/п", "pex-al"]
        ):
            return any(
                marker in evidence
                for marker in [
                    "металлопласт",
                    "металлополимер",
                    "м/п",
                    "pex-al-pex",
                    "pe-x/al/pe",
                    "pe-xa/al/pe",
                ]
            )
        multilayer_metal_polymer = bool(
            any(
                marker in evidence
                for marker in [
                    "pex-al",
                    "pe-x/al",
                    "pe-xa/al",
                    "pe-xb/al",
                    "pe-xc/al",
                    "pe-rt/al",
                    "pert-al",
                    "металлопласт",
                    "металлополимер",
                ]
            )
            or re.search(
                r"\bpe\s*-?\s*x[abc]?\s*/\s*al\b|"
                r"\bpe\s*-?\s*rt\s*/\s*al\b",
                evidence,
            )
        )
        if any(marker in expected for marker in ["pex", "pe-x", "сшит"]):
            if multilayer_metal_polymer:
                return False
            return any(
                marker in evidence
                for marker in ["pex", "pe-x", "pe xa", "pe-xa", "сшит"]
            )
        if any(marker in expected for marker in ["pe-rt", "pert", "пе-рт"]):
            if multilayer_metal_polymer:
                return False
            return any(marker in evidence for marker in ["pe-rt", "pert", "пе-рт"])
        if expected in {"pvc", "пвх"} or "поливинилхлор" in expected:
            return any(marker in evidence for marker in ["pvc", "пвх", "поливинилхлор"])
        if expected in {"pp", "пп"}:
            return bool(
                re.search(r"(?<![a-zа-я])(?:pp|пп)(?![a-zа-я])", evidence)
                or "полипропилен" in evidence
            )
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
                for marker in [
                    "водоснаб",
                    "водосн",
                    "питьев",
                    "для воды",
                    "хвс",
                    "гвс",
                ]
            )
        return bool(expected and expected in evidence)

    def _maximum_temperature(self, product: Product) -> float | None:
        values: list[float] = []
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if "температур" in key_norm and any(
                marker in key_norm for marker in ["макс", "рабоч", "примен"]
            ):
                matches = re.findall(r"-?\d+(?:[.,]\d+)?", normalize_text(str(value)))
                values.extend(float(match.replace(",", ".")) for match in matches)
        if not values:
            description = normalize_text(product.description or "")
            matches = re.findall(
                r"(-?\d{1,3}(?:[.,]\d+)?)\s*[cс]\b",
                description,
            )
            values.extend(
                float(match.replace(",", "."))
                for match in matches
                if -50 <= float(match.replace(",", ".")) <= 200
            )
        return max(values) if values else None

    def _maximum_pressure_bar(self, product: Product) -> float | None:
        values: list[float] = []
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if "давлен" not in key_norm or not any(
                marker in key_norm for marker in ["макс", "рабоч", "номин", "pn", "ру"]
            ):
                continue
            numbers = re.findall(r"\d+(?:[.,]\d+)?", normalize_text(str(value)))
            values.extend(float(number.replace(",", ".")) for number in numbers)
        if values:
            return max(values)
        name = normalize_text(product.name)
        pn_match = re.search(r"\bpn\s*(\d+(?:[.,]\d+)?)\b", name)
        if pn_match:
            return float(pn_match.group(1).replace(",", "."))
        return None

    def _pressure_class_bar(self, product: Product) -> float | None:
        """Read PN/Ru as a nominal class, not a hot working-pressure limit."""
        values: list[float] = []
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if not any(
                marker in key_norm
                for marker in ["номинальное давление", "условное давление", "класс давления", "pn", "ру"]
            ):
                continue
            values.extend(
                float(number.replace(",", "."))
                for number in re.findall(r"\d+(?:[.,]\d+)?", normalize_text(str(value)))
            )
        name = normalize_text(product.name)
        values.extend(
            float(number.replace(",", "."))
            for number in re.findall(r"\b(?:pn|ру)\s*(\d+(?:[.,]\d+)?)\b", name)
        )
        return max(values) if values else None

    def _pipe_operating_points(self, product: Product) -> list[tuple[float, float]]:
        text = normalize_text(product.description or "")
        points: list[tuple[float, float]] = []
        temperature_then_pressure = re.compile(
            r"температур\w*[^.!?]{0,45}?"
            r"(-?\d{1,3}(?:[.,]\d+)?)\s*[cс]\b"
            r"[^.!?]{0,45}?(\d+(?:[.,]\d+)?)\s*(?:бар|bar)\b"
        )
        pressure_then_temperature = re.compile(
            r"давлен\w*[^.!?]{0,45}?(\d+(?:[.,]\d+)?)\s*(?:бар|bar)\b"
            r"[^.!?]{0,45}?температур\w*[^.!?]{0,30}?"
            r"(-?\d{1,3}(?:[.,]\d+)?)\s*[cс]\b"
        )
        for match in temperature_then_pressure.finditer(text):
            points.append(
                (
                    float(match.group(1).replace(",", ".")),
                    float(match.group(2).replace(",", ".")),
                )
            )
        for match in pressure_then_temperature.finditer(text):
            points.append(
                (
                    float(match.group(2).replace(",", ".")),
                    float(match.group(1).replace(",", ".")),
                )
            )
        return points

    def _pipe_ratings_match(
        self,
        product: Product,
        requested_temperature: object | None,
        requested_pressure: object | None,
    ) -> bool:
        return self.pipe_ratings_status(
            product,
            requested_temperature,
            requested_pressure,
        ) is not False

    def pipe_ratings_status(
        self,
        product: Product,
        requested_temperature: object | None,
        requested_pressure: object | None,
    ) -> bool | None:
        """True when confirmed, False when conflicting, None when feed is sparse."""
        temperature = (
            float(requested_temperature)
            if requested_temperature is not None
            else None
        )
        pressure = (
            float(requested_pressure)
            if requested_pressure is not None
            else None
        )
        if temperature is not None and pressure is not None:
            operating_points = self._pipe_operating_points(product)
            if operating_points:
                return any(
                    temperature <= rated_temperature
                    and pressure <= rated_pressure
                    for rated_temperature, rated_pressure in operating_points
                )
        unconfirmed = False
        if temperature is not None:
            maximum_temperature = self._maximum_temperature(product)
            if maximum_temperature is None:
                unconfirmed = True
            elif maximum_temperature < temperature:
                return False
        if pressure is not None:
            maximum_pressure = self._maximum_pressure_bar(product)
            if maximum_pressure is None:
                unconfirmed = True
            elif maximum_pressure < pressure:
                return False
        return None if unconfirmed else True

    def _maximum_flow_m3_h(self, product: Product) -> float | None:
        values: list[float] = []
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if not any(
                marker in key_norm
                for marker in ["производительност", "расход", "подача"]
            ):
                continue
            number_match = re.search(r"\d+(?:[.,]\d+)?", str(value))
            if not number_match:
                continue
            number = float(number_match.group(0).replace(",", "."))
            combined = normalize_text(f"{key} {value}")
            if "л/мин" in combined:
                number = number * 60.0 / 1000.0
            elif "л/ч" in combined:
                number = number / 1000.0
            elif not any(
                marker in combined
                for marker in ["м3/ч", "м³/ч", "куб"]
            ):
                # Feed fields named simply ``производительность, л/мин`` are
                # covered above. An unlabeled number is not safe to convert.
                continue
            values.append(number)
        return max(values) if values else None

    def _maximum_head_m(self, product: Product) -> float | None:
        values: list[float] = []
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if not any(
                marker in key_norm
                for marker in ["напор", "высота подъема", "высота подъёма", "подъем"]
            ):
                continue
            numbers = re.findall(r"\d+(?:[.,]\d+)?", normalize_text(str(value)))
            values.extend(float(number.replace(",", ".")) for number in numbers)
        return max(values) if values else None

    def _pipe_service_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = self._pipe_semantic_evidence(product)
        primary = normalize_text(
            " ".join([product.name, product.category_path, *product.attributes_normalized.values()])
        )
        if "петл" in expected and "тепл" in expected:
            return any(
                marker in evidence
                for marker in ["теплый пол", "теплого пола", "напольн", "underfloor"]
            )
        if "подзем" in expected or "источник" in expected:
            pressure_water_pipe = any(
                marker in primary
                for marker in ["пэ100", "pe100", "пнд", "напорн", "водоподъем"]
            )
            heating_only = any(
                marker in primary
                for marker in ["отопит", "радиатор", "теплый пол", "теплого пола"]
            ) and not any(
                marker in primary for marker in ["водоснаб", "холод", "хвс"]
            )
            return pressure_water_pipe and not heating_only
        if any(marker in expected for marker in ["радиатор", "магистрал", "обвяз"]):
            return self._pipe_purpose_matches(product, "отопление")
        if "рециркуляц" in expected:
            return self._water_temperature_matches(product, "горячая")
        if "внутри" in expected or "разводк" in expected:
            return self._pipe_purpose_matches(product, "водоснабжение")
        return bool(expected and expected in evidence)

    def _water_temperature_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = self._pipe_semantic_evidence(product)
        maximum_temperature = self._maximum_temperature(product)
        if "горяч" in expected:
            has_hot_evidence = any(marker in evidence for marker in ["горяч", "гвс"])
            has_cold_evidence = any(
                marker in evidence for marker in ["холод", "хол/водосн", "хвс"]
            )
            has_water_supply_evidence = any(
                marker in evidence
                for marker in ["водоснаб", "питьев", "для воды"]
            )
            has_heating_evidence = any(
                marker in evidence
                for marker in ["отоплен", "теплоносител"]
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
                # Some VALTEC metal-polymer cards state two grounded scopes
                # separately: drinking water supply and heating. Together they
                # establish hot-water applicability even when the short card
                # omits the literal abbreviation «ГВС».
                or (has_water_supply_evidence and has_heating_evidence)
            )
        if "холод" in expected:
            return any(
                marker in evidence
                for marker in ["холод", "хол/водосн", "хол/", "хвс"]
            )
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

    def _fitting_element_matches(self, product: Product, requested: object) -> bool:
        """Match functional fitting names to wording used by the feed.

        A PPR reducer is commonly named ``муфта переходная`` in catalogues,
        while customers ask for a ``переходник``. Literal substring matching
        rejects that valid identity even though both diameters agree.
        """

        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(
                        product,
                        ["тип товара", "полное наименование", "вид фитинга"],
                    ),
                ]
            )
        )
        if any(marker in expected for marker in ("переход", "редукц")):
            # A transition tee contains the word ``переходной`` too, but adds
            # a branch and cannot replace a two-port reducer/coupling.
            has_transition = any(
                marker in evidence for marker in ("переход", "редукц")
            )
            has_branch = any(
                marker in evidence
                for marker in ("тройник", "крестовин", "четверник")
            )
            return has_transition and not has_branch
        aliases = {
            "угольник": ("угольник", "уголок"),
            "отвод": ("отвод",),
            "тройник": ("тройник",),
            "муфта": ("муфта",),
            "крестовина": ("крестовина",),
            "американка": ("американка",),
        }
        markers = next(
            (values for key, values in aliases.items() if key in expected),
            (expected,),
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
        primary_kind = self.product_identity(product).primary_kind
        if primary_kind not in VALVE_PRIMARY_KINDS:
            return False
        if "шаров" in expected:
            return primary_kind == "ball_valve"
        if "дренаж" in expected:
            return primary_kind == "drain_valve"
        if "обратн" in expected:
            return primary_kind == "check_valve"
        if "вентил" in expected:
            return primary_kind == "valve"
        if "клапан" in expected:
            return primary_kind in {
                "valve",
                "check_valve",
                "thermostatic_valve",
            }
        if "кран" in expected:
            return primary_kind in {"valve", "ball_valve", "drain_valve"}
        return False

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
        if category in {"pipes", "sewer"}:
            # Diameter and factory item length describe a different product,
            # not a merely less relevant analogue.  The primary path checks
            # these in ``_slots_match``; keep them just as strict when the
            # caller asks for nearest alternatives.
            diameter = slots.get("diameter_mm")
            if diameter and not self._dimension_matches(
                product,
                int(diameter),
                ["диаметр", "размер"],
            ):
                return False
            length = slots.get("length_mm")
            if length and not self._dimension_matches(
                product,
                int(length),
                ["длина"],
            ):
                return False
        if category == "sewer":
            element_type = normalize_text(str(slots.get("element_type") or ""))
            is_pipe = "труб" in element_type
            secondary = slots.get("secondary_diameter_mm")
            if secondary and not self._fitting_dimension_matches(product, int(secondary)):
                return False
        relaxed_fields = self.alternative_relaxed_fields(query)
        if category == "pumps":
            mounting = slots.get("mounting_length_mm")
            if mounting and not self._dimension_matches(
                product,
                int(mounting),
                ["монтажная длина", "длина"],
            ):
                return False
            head = slots.get("head_m")
            if (
                head
                and "head_m" not in relaxed_fields
                and not self._head_matches(product, float(head))
            ):
                return False
            connection = slots.get("connection_size")
            if connection and not self._connection_matches(product, int(connection)):
                return False
        if category == "boilers":
            voltage = slots.get("voltage_v")
            if voltage and not self._voltage_matches(product, int(voltage)):
                return False
        if category == "water_heaters":
            # These dimensions define a different appliance, not merely a
            # lower-ranked analogue.  Re-check them explicitly here so future
            # relaxation of generic semantic scoring cannot change them.
            if not self._semantic_slots_match(product, category, slots):
                return False
        if category in {"valves", "radiator_fittings"}:
            size_inch = slots.get("size_inch")
            if size_inch and not self._inch_size_matches(
                product, str(size_inch), slots
            ):
                return False
            diameter = slots.get("diameter_mm")
            if diameter and not self._dimension_matches(
                product,
                int(diameter),
                self._diameter_attribute_keys(category),
            ):
                return False
            if slots.get("union") and not self._union_matches(product):
                return False
        return True

    def _radiator_type_matches(self, product: Product, requested: object) -> bool:
        """Match the construction family without confusing material details.

        A bimetal card can legitimately mention both steel and aluminium in
        its description.  The product identity/type fields are therefore the
        authority; scanning the whole description would make it pass an
        explicit correction to an aluminium radiator.
        """
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    product.category_path,
                    self._attribute_text(
                        product,
                        [
                            "тип товара",
                            "тип радиатора",
                            "вид радиатора",
                            "материал радиатора",
                            "материал",
                        ],
                    ),
                ]
            )
        )
        if "биметалл" in expected:
            return "биметалл" in evidence
        if "алюмин" in expected:
            return "алюмин" in evidence and "биметалл" not in evidence
        if "панельн" in expected:
            return "панельн" in evidence
        if "стальн" in expected:
            return "стальн" in evidence and "биметалл" not in evidence
        return bool(expected and expected in evidence)

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

    def _fitting_system_matches(self, product: Product, requested: object) -> bool:
        """Match the physical fitting system using identity/structured facts only."""
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.sku,
                    product.name,
                    product.category_path,
                    self._attribute_text(
                        product,
                        ["материал", "тип присоединения", "тип товара"],
                    ),
                ]
            )
        )
        if any(marker in expected for marker in ["ppr", "pprc", "ппр", "полипроп"]):
            return any(
                marker in evidence
                for marker in [
                    "ppr",
                    "pprc",
                    "pp-r",
                    "ппр",
                    "полипроп",
                ]
            ) or normalize_text(product.sku).startswith("vtp.")
        if any(marker in expected for marker in ["pex", "pe-x", "пекс"]):
            return any(marker in evidence for marker in ["pex", "pe-x", "пекс"])
        if any(marker in expected for marker in ["pert", "pe-rt"]):
            return any(marker in evidence for marker in ["pert", "pe-rt"])
        if any(marker in expected for marker in ["пнд", "hdpe"]):
            return any(marker in evidence for marker in ["пнд", "hdpe"])
        if "пресс" in expected:
            return "пресс" in evidence
        if any(marker in expected for marker in ["обжим", "компресс"]):
            return any(marker in evidence for marker in ["обжим", "компресс"])
        if "резьб" in expected:
            return "резьб" in evidence
        # An explicit but unsupported system is unknown, therefore unsuitable.
        return False

    def _handle_type_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(
                        product,
                        ["тип ручки", "ручка", "рукоятка"],
                    ),
                ]
            )
        )
        if any(marker in expected for marker in ["butterfly", "бабоч"]):
            return "бабоч" in evidence
        if any(marker in expected for marker in ["lever", "рычаг", "стальная рукоят"]):
            return "рычаг" in evidence or "стальная рукоят" in evidence
        if "мини" in expected or expected == "mini":
            return "мини" in evidence or "mini" in evidence
        if any(marker in expected for marker in ["t-shaped", "t shaped", "т-образ"]):
            return "т-образ" in evidence
        return bool(expected and expected in evidence)

    def _bore_type_matches(self, product: Product, slots: Mapping[str, Any]) -> bool:
        if "full_bore" not in slots and not slots.get("bore_type"):
            return True

        if slots.get("bore_type") is not None:
            expected = normalize_text(str(slots["bore_type"]))
            wants_full = any(
                marker in expected for marker in ["full", "полнопроход"]
            ) and not any(
                marker in expected
                for marker in ["standard", "стандартнопроход", "неполнопроход", "редуцирован"]
            )
            wants_reduced = any(
                marker in expected
                for marker in ["standard", "стандартнопроход", "неполнопроход", "редуцирован"]
            )
            if not wants_full and not wants_reduced:
                return False
        else:
            value = slots.get("full_bore")
            if isinstance(value, str):
                normalized = normalize_text(value)
                if normalized in {"true", "1", "yes", "да"}:
                    wants_full = True
                elif normalized in {"false", "0", "no", "нет"}:
                    wants_full = False
                else:
                    return False
            elif isinstance(value, bool):
                wants_full = value
            else:
                return False
            wants_reduced = not wants_full

        evidence = self._structured_text(product)
        reduced = any(
            marker in evidence
            for marker in ["стандартнопроход", "неполнопроход", "редуцирован"]
        )
        full = "полнопроход" in evidence and not reduced
        return full if wants_full else reduced

    def _body_form_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = normalize_text(
            " ".join(
                [
                    product.name,
                    self._attribute_text(
                        product,
                        ["форма корпуса", "тип конструкции", "исполнение"],
                    ),
                ]
            )
        )
        if any(marker in expected for marker in ["straight", "прям"]):
            return "прям" in evidence and "углов" not in evidence
        if any(marker in expected for marker in ["angled", "angle", "углов"]):
            return "углов" in evidence
        if "осев" in expected:
            return "осев" in evidence
        return bool(expected and expected in evidence)

    # Виды, которые этот матчер умеет отличать по типизированной identity
    # товара. Список закрытый, и это нормально: он покрывает те случаи, где
    # название само по себе обманывает («американка» как кран и как фитинг).
    _PRODUCT_KIND_IDENTITY = {
        "thermostatic head": "thermostatic_head",
        "термоголовка": "thermostatic_head",
        "термостатическая головка": "thermostatic_head",
        "thermostatic valve": "thermostatic_valve",
        "термостатический клапан": "thermostatic_valve",
        "термостатический вентиль": "thermostatic_valve",
        "ball valve": "ball_valve",
        "шаровой кран": "ball_valve",
        "кран шаровой": "ball_valve",
        "elbow": "elbow",
        "угольник": "elbow",
        "уголок": "elbow",
        "отвод": "elbow",
    }

    def _product_kind_is_recognised(self, requested: object) -> bool:
        """Умеет ли матчер судить об этом виде товара.

        Разделение обязательно, потому что ``product_kind`` — жёсткий фильтр.
        Незнакомый вид («насос», «radiator shutoff valve») давал False на
        каждом товаре, и запрос с таким слотом вычищал весь каталог: бот
        отвечал «не нашёл подходящих товаров» там, где подходящие лежали в
        наличии. Незнание вида — это отсутствие мнения, а не отказ.
        """

        return normalize_text(str(requested)) in self._PRODUCT_KIND_IDENTITY

    def _product_kind_matches(self, product: Product, requested: object) -> bool:
        expected = self._PRODUCT_KIND_IDENTITY.get(normalize_text(str(requested)))
        if expected is None:
            return False
        return self.product_identity(product).primary_kind == expected

    def _literal_notation_matches(self, product: Product, requested: object) -> bool:
        expected = re.sub(r"\s+", "", normalize_text(str(requested)))
        evidence = re.sub(r"\s+", "", self._structured_text(product))
        return bool(expected and expected in evidence)

    def _thread_standard_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        evidence = self._structured_text(product)
        if expected not in {"g", "r", "rp", "rc"}:
            return False
        return bool(
            re.search(
                rf"(?<![a-zа-я]){re.escape(expected)}\s*"
                r"(?:2|1(?:\s+1/4|\s+1/2)?|3/4|1/2|3/8|1/4)(?!\d)",
                evidence,
            )
        )

    def _ip_rating_matches(self, product: Product, requested: object) -> bool:
        expected = re.sub(r"\s+", "", normalize_text(str(requested)))
        evidence = re.sub(r"\s+", "", self._structured_text(product))
        return bool(expected and re.search(rf"(?<![a-z0-9]){re.escape(expected)}(?!\d)", evidence))

    def _phase_matches(self, product: Product, requested: object) -> bool:
        phase = int(float(str(requested)))
        evidence = self._structured_text(product)
        if phase == 1:
            return bool(
                re.search(r"\b(?:1\s*(?:ф|фаз)|однофаз)\w*\b", evidence)
                or re.search(r"\b(?:220|230)\s*(?:v|в)\b", evidence)
            )
        if phase == 3:
            return bool(
                re.search(r"\b(?:3\s*(?:ф|фаз)|трехфаз|трёхфаз)\w*\b", evidence)
                or re.search(r"\b(?:380|400)\s*(?:v|в)\b", evidence)
            )
        return False

    def _current_type_matches(self, product: Product, requested: object) -> bool:
        expected = normalize_text(str(requested))
        if expected not in {"ac", "dc"}:
            return False
        explicit = self._attribute_text(
            product,
            ["параметры сети", "тип тока", "питание", "напряжение"],
        )
        if re.search(rf"(?<![a-z]){expected}(?![a-z])", explicit):
            return True
        name = normalize_text(product.name)
        return bool(
            re.search(
                rf"(?:\d+\s*(?:v|в)\s*{expected}\b|"
                rf"\b{expected}\s*\d+\s*(?:v|в))",
                name,
            )
        )

    def _numeric_attribute_or_name_matches(
        self,
        product: Product,
        requested: object,
        key_markers: list[str],
        name_pattern: str,
        *,
        tolerance: float = 0.05,
    ) -> bool:
        expected = float(requested)
        values: list[float] = []
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            if not any(marker in key_norm for marker in key_markers):
                continue
            values.extend(
                float(number.replace(",", "."))
                for number in re.findall(r"-?\d+(?:[,.]\d+)?", normalize_text(str(value)))
            )
        # Parenthesised pipe dimensions such as ``16(2,0)`` mean outside
        # diameter and wall thickness.  ``normalize_text`` removes parentheses,
        # so preserve that separator as ``x`` before applying typed patterns.
        normalized_name = normalize_text(product.name.replace("(", "x"))
        name_match = re.search(name_pattern, normalized_name)
        if name_match:
            values.append(float(name_match.group(1).replace(",", ".")))
        return any(abs(value - expected) <= tolerance for value in values)

    def _filter_slot_matches(self, product: Product, slots: dict) -> bool:
        evidence = self._structured_text(product)
        filter_format = slots.get("filter_format")
        if filter_format and not self._literal_notation_matches(product, filter_format):
            return False
        microns = slots.get("filtration_microns")
        if microns is not None and not self._numeric_attribute_or_name_matches(
            product,
            microns,
            ["мкм", "микрон", "тонкость", "фильтрац"],
            r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:мкм|µm|μm)",
        ):
            return False
        technology = normalize_text(str(slots.get("filter_technology") or ""))
        if technology:
            patterns = {
                "ro": r"\bro\b|обратн\w*\s+осмос",
                "uf": r"\buf\b|ультрафильтрац",
                "gac": r"\bgac\b|гранулирован\w*[^.;,]{0,24}угол",
                "cto": r"\bcto\b|прессован\w*[^.;,]{0,24}угол",
                "cbc": r"\bcbc\b|карбон[- ]?блок|угольн\w*\s+блок",
                "pp": r"\bpp\b|полипропилен\w*|механическ",
                "mechanical": r"механическ|осадочн|полипропилен|намоточн",
                "carbon": r"угол|карбон|\bgac\b|\bcto\b|\bcbc\b",
            }
            pattern = patterns.get(technology, rf"\b{re.escape(technology)}\b")
            if not re.search(pattern, evidence):
                return False
        element = normalize_text(str(slots.get("filter_element_type") or ""))
        if element and element not in evidence:
            return False
        temperature = slots.get("water_temperature")
        if temperature and not self._water_temperature_matches(product, temperature):
            return False
        return True

    def _control_slot_matches(self, product: Product, slots: dict) -> bool:
        evidence = self._structured_text(product)
        kind = normalize_text(str(slots.get("control_kind") or ""))
        if kind:
            aliases = {
                "rtl": ["rtl", "ограничител", "обратн"],
                "сервопривод": ["сервопривод", "привод"],
                "термостат": ["термостат", "терморегулятор"],
                "насосно-смесительный узел": ["насосно-смесительн", "смесительный узел", "нсу"],
                "частотный преобразователь": ["частотн", "преобразователь", "пч"],
            }.get(kind, [kind])
            if not any(alias in evidence for alias in aliases):
                return False
        state = normalize_text(str(slots.get("normal_state") or ""))
        if state:
            if "закрыт" in state and not re.search(
                r"\b(?:нз|nc)\b|(?<!\w)н\.з\.(?!\w)|"
                r"нормальн\w*\s+закрыт|\bнорм\.?\s*закр", evidence
            ):
                return False
            if "открыт" in state and not (
                re.search(
                    r"\bno\b|нормальн\w*\s+открыт|"
                    r"\bнорм\.?\s*откр",
                    evidence,
                )
                or re.search(r"\bно\s+(?:сервопривод|привод|клапан)", evidence)
            ):
                return False
        signal = slots.get("control_signal")
        if signal and not self._literal_notation_matches(product, signal):
            return False
        return True

    def _material_spec_matches(self, product: Product, requested: object) -> bool:
        """Материал из строки спецификации: нужны все названные составляющие.

        «Полипропилен, Латунь» — это комбинированное исполнение, и чистый
        полипропилен ему не равен. Если материал в фиде не указан вовсе,
        отсутствие данных не считается противоречием.
        """
        declared = normalize_text(self._attribute_text(product, ["материал"]))
        if not declared:
            return True
        wanted = [
            part.strip()
            for part in normalize_text(str(requested or "")).split(",")
            if part.strip()
        ]
        return all(part in declared for part in wanted)

    def _combined_metal_matches(self, product: Product) -> bool:
        """Комбинированное исполнение: полимер плюс латунная резьбовая часть."""
        material = normalize_text(self._attribute_text(product, ["материал"]))
        if "латун" in material:
            return True
        name = normalize_text(product.name)
        return "комбинирован" in name or "латун" in name

    def _fitting_end_form_matches(self, product: Product, requested: object) -> bool:
        """Match the physical port topology of a polymer fitting."""

        expected = normalize_text(str(requested or ""))
        if expected not in {"socket socket", "две муфты", "двухраструбный"}:
            return False
        if self._combined_metal_matches(product):
            return False
        evidence = self._structured_text(product)
        # ``вн/нар`` on an all-polymer PPR elbow describes a socket on one
        # side and a pipe-sized spigot on the other. It does not accept a pipe
        # at both ends like an ordinary double-socket elbow.
        if re.search(
            r"\b(?:вн\s*[/.-]\s*нар|внутренн\w*\s*[/.-]\s*наружн\w*)\b",
            evidence,
        ):
            return False
        if re.search(
            r"\b(?:вн\.?\s*р\.?|нар\.?\s*р\.?|резьб\w*)\b",
            evidence,
        ):
            return False
        return True

    def _trade_element_matches(self, product: Product, requested: object) -> bool:
        """Семейство товара, названное монтажным словом, — жёсткое условие.

        Значение приходит из словаря и совпадает с «Тип товара» в выгрузке,
        поэтому сначала проверяется сам атрибут, и лишь затем название: у части
        позиций тип не заполнен, но семейство стоит в наименовании.
        """
        expected = normalize_text(str(requested or ""))
        if not expected:
            return True
        declared = self._attribute_text(product, ["тип товара", "тип"])
        if declared and expected in normalize_text(declared):
            return True
        # У части позиций семейство стоит только в названии: «евроконус»
        # у соединителей и адаптеров коллектора — как раз такой случай.
        return expected in normalize_text(product.name)

    def _semantic_slots_match(self, product: Product, category: str, slots: dict) -> bool:
        """Enforce categorical/usage slots as non-negotiable constraints."""
        if slots.get("trade_element") and not self._trade_element_matches(
            product, slots["trade_element"]
        ):
            return False
        if "combined_metal" in slots:
            requested_combined = slots.get("combined_metal")
            wants_combined = (
                normalize_text(str(requested_combined))
                not in {"", "0", "false", "no", "нет", "ложь", "none"}
            )
            if self._combined_metal_matches(product) != wants_combined:
                return False
        if slots.get("material_spec") and not self._material_spec_matches(
            product, slots["material_spec"]
        ):
            return False
        if (slots.get("thread_type") or slots.get("thread_gender")) and not thread_constraint_matches(
            product,
            thread_type=slots.get("thread_type"),
            thread_gender=slots.get("thread_gender"),
        ):
            return False
        if slots.get("fitting_system") and not self._fitting_system_matches(
            product, slots["fitting_system"]
        ):
            return False
        if slots.get("fitting_end_form") and not self._fitting_end_form_matches(
            product,
            slots["fitting_end_form"],
        ):
            return False
        if slots.get("angle_deg") is not None and not self._angle_matches(
            product, slots["angle_deg"]
        ):
            return False
        if slots.get("handle_type") and not self._handle_type_matches(
            product, slots["handle_type"]
        ):
            return False
        if ("full_bore" in slots or slots.get("bore_type")) and not self._bore_type_matches(
            product, slots
        ):
            return False
        if slots.get("body_form") and not self._body_form_matches(
            product, slots["body_form"]
        ):
            return False
        if (
            slots.get("product_kind")
            and self._product_kind_is_recognised(slots["product_kind"])
            and not self._product_kind_matches(product, slots["product_kind"])
        ):
            return False
        if slots.get("thread_standard") and not self._thread_standard_matches(
            product, slots["thread_standard"]
        ):
            return False
        if slots.get("metric_thread"):
            requested_metric = re.sub(
                r"[\s,х×]",
                lambda match: "." if match.group(0) == "," else "x" if match.group(0) in {"х", "×"} else "",
                normalize_text(str(slots["metric_thread"])),
            )
            evidence_metric = re.sub(
                r"[\s,х×]",
                lambda match: "." if match.group(0) == "," else "x" if match.group(0) in {"х", "×"} else "",
                self._structured_text(product),
            )
            if requested_metric not in evidence_metric:
                return False
        if slots.get("ip_rating") and not self._ip_rating_matches(
            product, slots["ip_rating"]
        ):
            return False
        if slots.get("phase_count") and not self._phase_matches(
            product, slots["phase_count"]
        ):
            return False
        if slots.get("current_type") and not self._current_type_matches(
            product, slots["current_type"]
        ):
            return False
        if category == "filters" and not self._filter_slot_matches(product, slots):
            return False
        if category == "controls" and not self._control_slot_matches(product, slots):
            return False
        if category in {
            "pipes",
            "sewer",
            "fittings",
            "valves",
            "radiator_fittings",
        } and slots.get("pressure_class_bar") is not None:
            pressure_class = self._pressure_class_bar(product)
            if pressure_class is None or pressure_class < float(slots["pressure_class_bar"]):
                return False
        if category in {"pipes", "sewer"} and slots.get("sdr") is not None:
            expected_sdr = float(slots["sdr"])
            evidence = normalize_text(
                " ".join(
                    [
                        product.name,
                        product.category_path,
                        self._attribute_text(product, ["sdr"]),
                    ]
                )
            )
            actual_sdr = [
                float(value.replace(",", "."))
                for value in re.findall(r"\bsdr\s*(\d{1,2}(?:[,.]\d+)?)\b", evidence)
            ]
            if not actual_sdr or not any(abs(value - expected_sdr) < 0.01 for value in actual_sdr):
                return False
        if category in {"pipes", "sewer"} and slots.get("wall_thickness_mm") is not None:
            if not self._numeric_attribute_or_name_matches(
                product,
                slots["wall_thickness_mm"],
                ["толщина стен"],
                r"\d{2,3}\s*[xх×(]\s*(\d{1,2}(?:[,.]\d{1,2})?)",
            ):
                return False
        if category == "pipes":
            evidence = self._structured_text(product)
            if slots.get("oxygen_barrier") and not any(
                marker in evidence for marker in ["evoh", "кислородн", "антидиффузион"]
            ):
                return False
            reinforcement = normalize_text(str(slots.get("reinforcement") or ""))
            if reinforcement == "алюминий" and not any(
                marker in evidence for marker in ["alux", "aluminium", "алюмин", "фольг"]
            ):
                return False
            if reinforcement == "стекловолокно" and not any(
                marker in evidence for marker in ["fiber", "gf", "fb", "стекловолок"]
            ):
                return False
        if category == "fittings":
            evidence = self._structured_text(product)
            fitting_material = normalize_text(str(slots.get("fitting_material") or ""))
            if fitting_material and fitting_material not in evidence:
                return False
        if category in {"pipes", "fittings"}:
            evidence = self._structured_text(product)
            press_profile = normalize_text(str(slots.get("press_profile") or ""))
            if press_profile and not re.search(
                rf"(?<![a-zа-я]){re.escape(press_profile)}\s*[- ]?(?:профиль|проф\.?)(?!\w)",
                evidence,
            ):
                return False
            seal_material = normalize_text(str(slots.get("seal_material") or ""))
            if seal_material and seal_material not in evidence and not (
                seal_material == "ptfe" and any(marker in evidence for marker in ["фум", "фторопласт"])
            ):
                return False
        if category == "hydraulic_accumulators":
            if slots.get("tank_application") and not self._tank_application_matches(
                product,
                slots["tank_application"],
            ):
                return False
            if slots.get("volume_l") is not None and not self._tank_volume_matches(
                product,
                slots["volume_l"],
            ):
                return False
            if slots.get("orientation") and not self._tank_orientation_matches(
                product,
                slots["orientation"],
            ):
                return False
            requested_pressure = slots.get("operating_pressure_bar")
            if requested_pressure is not None:
                maximum_pressure = self._maximum_pressure_bar(product)
                if maximum_pressure is None or maximum_pressure < float(requested_pressure):
                    return False
        if category == "water_heaters":
            if slots.get("heater_type") and not self._water_heater_type_matches(
                product,
                slots["heater_type"],
            ):
                return False
            if slots.get("energy_source") and not self._water_heater_energy_matches(
                product,
                slots["energy_source"],
            ):
                return False
            if slots.get("volume_l") is not None and not self._water_heater_volume_matches(
                product,
                slots["volume_l"],
            ):
                return False
            if slots.get("mounting") and not self._water_heater_mounting_matches(
                product,
                slots["mounting"],
            ):
                return False
            if slots.get("orientation") and not self._water_heater_orientation_matches(
                product,
                slots["orientation"],
            ):
                return False
            heating_element = normalize_text(str(slots.get("heating_element_type") or ""))
            if heating_element:
                evidence = self._structured_text(product)
                if "сух" in heating_element and not re.search(r"\bсух\w*\s+тэн\b", evidence):
                    return False
                if "мокр" in heating_element and not any(
                    marker in evidence for marker in ["мокр", "погружн"]
                ):
                    return False
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
            if slots.get("combustion_chamber") and not self._combustion_chamber_matches(
                product,
                slots["combustion_chamber"],
            ):
                return False
            if slots.get("chimney_size") and not self._chimney_size_matches(
                product,
                slots["chimney_size"],
            ):
                return False
            gas_type = normalize_text(str(slots.get("gas_type") or ""))
            if gas_type:
                evidence = self._structured_text(product)
                if "природ" in gas_type and not (
                    "природн" in evidence
                    or re.search(r"(?<![a-zа-я0-9])ng(?![a-zа-я0-9])", evidence)
                ):
                    return False
                if "сжиж" in gas_type and not any(marker in evidence for marker in ["lpg", "сжиж", "пропан"]):
                    return False
        if category == "pumps" and slots.get("pump_type") and not self._pump_type_matches(
            product, slots["pump_type"]
        ):
            return False
        if category == "pumps":
            required_head = slots.get("required_head_m")
            if required_head is not None:
                maximum_head = self._maximum_head_m(product)
                if maximum_head is None or maximum_head < float(required_head):
                    return False
            if (
                normalize_text(str(slots.get("pump_type") or ""))
                == "повысительный"
                and slots.get("required_pressure_bar") is not None
                and slots.get("inlet_pressure_bar") is not None
            ):
                required_boost_head = max(
                    float(slots["required_pressure_bar"])
                    - float(slots["inlet_pressure_bar"]),
                    0.0,
                ) * 10.2
                maximum_head = self._maximum_head_m(product)
                if maximum_head is None or maximum_head < required_boost_head:
                    return False
            required_flow = slots.get("required_flow_m3_h")
            if required_flow is not None:
                maximum_flow = self._maximum_flow_m3_h(product)
                if maximum_flow is None or maximum_flow < float(required_flow):
                    return False
            if slots.get("maximum_head_m") is not None:
                actual_head = self._maximum_head_m(product)
                if actual_head is None or abs(actual_head - float(slots["maximum_head_m"])) > 0.05:
                    return False
            if slots.get("maximum_flow_m3_h") is not None:
                actual_flow = self._maximum_flow_m3_h(product)
                if actual_flow is None or abs(actual_flow - float(slots["maximum_flow_m3_h"])) > 0.05:
                    return False
            if slots.get("input_power_w") is not None and not self._numeric_attribute_or_name_matches(
                product,
                slots["input_power_w"],
                ["потребляемая мощность", "мощность", "p1"],
                r"\bp\s*1\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*(?:вт|w)\b",
                tolerance=1.0,
            ):
                return False
            if slots.get("shaft_power_w") is not None and not self._numeric_attribute_or_name_matches(
                product,
                slots["shaft_power_w"],
                ["мощность на валу", "p2"],
                r"\bp\s*2\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*(?:вт|w)\b",
                tolerance=1.0,
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
        if category == "pipes":
            if slots.get("pipe_service") and not self._pipe_service_matches(
                product,
                slots["pipe_service"],
            ):
                return False
            requested_temperature = slots.get("operating_temperature_c")
            requested_pressure = slots.get("operating_pressure_bar")
            if not self._pipe_ratings_match(
                product,
                requested_temperature,
                requested_pressure,
            ):
                return False
        if category in {"valves", "radiator_fittings"}:
            requested_temperature = slots.get("operating_temperature_c")
            if requested_temperature is not None:
                maximum_temperature = self._maximum_temperature(product)
                if (
                    maximum_temperature is None
                    or maximum_temperature < float(requested_temperature)
                ):
                    return False
            requested_pressure = slots.get("operating_pressure_bar")
            if requested_pressure is not None:
                maximum_pressure = self._maximum_pressure_bar(product)
                if (
                    maximum_pressure is None
                    or maximum_pressure < float(requested_pressure)
                ):
                    return False
        if category == "radiators":
            requested_pressure = slots.get("operating_pressure_bar")
            if requested_pressure is not None:
                maximum_pressure = self._maximum_pressure_bar(product)
                if (
                    maximum_pressure is None
                    or maximum_pressure < float(requested_pressure)
                ):
                    return False
        if category == "pipes" and slots.get("pipe_color") and not self._pipe_color_matches(
            product,
            slots["pipe_color"],
        ):
            return False
        if category == "sewer":
            sewer_code = normalize_text(str(slots.get("sewer_system_code") or ""))
            if sewer_code and not re.search(
                rf"(?<![a-zа-я0-9]){re.escape(sewer_code)}(?![a-zа-я0-9])",
                self._structured_text(product),
            ):
                return False
            ring_stiffness = slots.get("ring_stiffness_sn")
            if ring_stiffness is not None and not re.search(
                rf"\b(?:sn|сн)\s*-?\s*{int(ring_stiffness)}\b",
                self._structured_text(product),
            ):
                return False
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
        if category in {"valves", "radiator_fittings"}:
            evidence = self._structured_text(product)
            coefficient = slots.get("flow_coefficient")
            coefficient_kind = normalize_text(str(slots.get("flow_coefficient_kind") or ""))
            if coefficient is not None:
                pattern = (
                    rf"(?<![a-zа-я]){re.escape(coefficient_kind)}\s*(?:=|:)?\s*"
                    r"(\d+(?:[,.]\d+)?)"
                )
                values = [
                    float(match.replace(",", "."))
                    for match in re.findall(pattern, evidence)
                ]
                if not values or not any(abs(value - float(coefficient)) <= 0.01 for value in values):
                    return False
            differential = slots.get("differential_pressure_bar")
            if differential is not None and not self._numeric_attribute_or_name_matches(
                product,
                differential,
                ["перепад давления", "дифференциальное давление", "δp", "∆p"],
                r"(?:δ|∆|d)\s*p\s*(?:=|:)?\s*(\d+(?:[,.]\d+)?)\s*(?:бар|bar)",
            ):
                return False
            ways = slots.get("valve_ways")
            if ways is not None and not re.search(rf"(?<!\d){int(ways)}\s*/\s*2(?!\d)", evidence):
                return False
            state = normalize_text(str(slots.get("normal_state") or ""))
            if state:
                if "закрыт" in state and not re.search(
                    r"\b(?:нз|nc)\b|нормальн\w*\s+закрыт|"
                    r"\bнорм\.?\s*закр", evidence
                ):
                    return False
                if "открыт" in state and not re.search(
                    r"\bno\b|нормальн\w*\s+открыт|\bнорм\.?\s*откр|"
                    r"\bно\s+(?:клапан|привод)", evidence
                ):
                    return False
        if category == "radiators":
            radiator_type = slots.get("radiator_type")
            if radiator_type and not self._radiator_type_matches(
                product,
                radiator_type,
            ):
                return False
            panel_type = slots.get("radiator_panel_type")
            if panel_type is not None:
                evidence = self._structured_text(product)
                if not (
                    re.search(rf"\bтип\s*{int(panel_type)}\b", evidence)
                    or re.search(rf"\b(?:c|cv|vc|vk)\s*{int(panel_type)}(?=[-\s]|$)", evidence)
                ):
                    return False
            rating_delta = slots.get("rating_delta_t_c")
            if rating_delta is not None and not re.search(
                rf"(?:δ|∆|d)\s*t\s*(?:=|:)?\s*{int(rating_delta)}\b",
                self._structured_text(product),
            ):
                return False
            heat_output = slots.get("heat_output_w")
            if heat_output is not None and not self._numeric_attribute_or_name_matches(
                product,
                heat_output,
                ["теплоотдач", "мощност"],
                r"(?:теплоотдач\w*|мощност\w*)[^\d]{0,12}(\d+(?:[,.]\d+)?)\s*(?:вт|w)",
                tolerance=1.0,
            ):
                return False
            radiator_connection = normalize_text(str(slots.get("radiator_connection") or ""))
            if radiator_connection:
                evidence = self._structured_text(product)
                if "нижн" in radiator_connection and not any(
                    marker in evidence for marker in ["нижн", "донн", "ventil"]
                ):
                    return False
                if "боков" in radiator_connection and "боков" not in evidence:
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
            checks.append(
                self._dimension_matches(
                    product,
                    int(diameter),
                    self._diameter_attribute_keys(category),
                )
            )

        size_inch = slots.get("size_inch")
        if size_inch:
            checks.append(self._inch_size_matches(product, str(size_inch), slots))

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
                    ["межосев", "размер"],
                )
            )

        radiator_height = slots.get("radiator_height_mm")
        if radiator_height:
            checks.append(
                self._dimension_matches(product, int(radiator_height), ["высот"])
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

        required_head = slots.get("required_head_m")
        if required_head is not None and category == "pumps":
            maximum_head = self._maximum_head_m(product)
            checks.append(
                maximum_head is not None
                and maximum_head >= float(required_head)
            )

        if (
            category == "pumps"
            and normalize_text(str(slots.get("pump_type") or ""))
            == "повысительный"
            and slots.get("required_pressure_bar") is not None
            and slots.get("inlet_pressure_bar") is not None
        ):
            required_boost_head = max(
                float(slots["required_pressure_bar"])
                - float(slots["inlet_pressure_bar"]),
                0.0,
            ) * 10.2
            maximum_head = self._maximum_head_m(product)
            checks.append(
                maximum_head is not None
                and maximum_head >= required_boost_head
            )

        required_flow = slots.get("required_flow_m3_h")
        if required_flow is not None and category == "pumps":
            maximum_flow = self._maximum_flow_m3_h(product)
            checks.append(
                maximum_flow is not None
                and maximum_flow >= float(required_flow)
            )

        voltage = slots.get("voltage_v")
        if voltage and category in {"boilers", "pumps", "water_heaters", "controls"}:
            checks.append(self._voltage_matches(product, int(voltage)))

        connection_size = slots.get("connection_size")
        if connection_size:
            checks.append(self._connection_matches(product, int(connection_size)))

        element_type = slots.get("element_type")
        if element_type:
            if category == "sewer":
                checks.append(self._sewer_element_matches(product, element_type))
            elif category == "fittings":
                checks.append(self._fitting_element_matches(product, element_type))
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
            checks.append(self._body_form_matches(product, body_form))

        if slots.get("union"):
            # «американка» = разъёмное соединение с полусгоном/накидной гайкой.
            checks.append(self._union_matches(product))

        if not checks:
            return True
        return all(checks)

    def _has_strict_slots(self, query: SearchQuery, slots: dict | None = None) -> bool:
        effective_slots = slots if slots is not None else query.slots
        globally_strict = {
            "thread_type",
            "thread_gender",
            "fitting_system",
            "combined_metal",
            "fitting_end_form",
            "angle_deg",
            "handle_type",
            "full_bore",
            "bore_type",
            "body_form",
            "product_kind",
        }
        if globally_strict.intersection(effective_slots):
            return True
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
                "pipe_service",
                "water_temperature",
                "pipe_material",
                "pipe_color",
                "operating_temperature_c",
                "operating_pressure_bar",
                "pressure_class_bar",
                "sdr",
                "wall_thickness_mm",
                "oxygen_barrier",
                "reinforcement",
                "thread_standard",
                "thread_gender",
                "press_profile",
                "seal_material",
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
                "pressure_class_bar",
                "sdr",
                "wall_thickness_mm",
                "sewer_system_code",
                "ring_stiffness_sn",
                "thread_standard",
                "thread_gender",
            },
            "pumps": {
                "pump_type",
                "mounting_length_mm",
                "head_m",
                "required_head_m",
                "required_flow_m3_h",
                "connection_size",
                "old_model",
                "maximum_head_m",
                "maximum_flow_m3_h",
                "input_power_w",
                "shaft_power_w",
                "thread_standard",
                "thread_gender",
                "ip_rating",
                "phase_count",
                "voltage_v",
                "current_type",
            },
            "valves": {
                "application",
                "diameter_mm",
                "body_form",
                "union",
                "size_inch",
                "valve_kind",
                "operating_temperature_c",
                "operating_pressure_bar",
                "pressure_class_bar",
                "thread_standard",
                "thread_gender",
                "metric_thread",
                "flow_coefficient_kind",
                "flow_coefficient",
                "valve_ways",
                "normal_state",
                "differential_pressure_bar",
            },
            "radiator_fittings": {
                "application",
                "connection_form",
                "diameter_mm",
                "size_inch",
                "union",
                "thermostatic_head",
                "operating_temperature_c",
                "operating_pressure_bar",
                "pressure_class_bar",
                "thread_standard",
                "thread_gender",
                "metric_thread",
                "flow_coefficient_kind",
                "flow_coefficient",
                "valve_ways",
                "normal_state",
                "differential_pressure_bar",
            },
            "radiators": {
                "radiator_type",
                "radiator_size_mm",
                "radiator_height_mm",
                "length_mm",
                "sections",
                "size_inch",
                "radiator_panel_type",
                "rating_delta_t_c",
                "heat_output_w",
                "radiator_connection",
            },
            "fittings": {
                "diameter_mm",
                "secondary_diameter_mm",
                "size_inch",
                "element_type",
                "pressure_class_bar",
                "thread_standard",
                "thread_gender",
                "press_profile",
                "fitting_material",
                "seal_material",
            },
            "boilers": {
                "boiler_type",
                "contours",
                "combustion_chamber",
                "chimney_size",
                "voltage_v",
                "current_type",
                "phase_count",
                "ip_rating",
                "gas_type",
            },
            "water_heaters": {
                "heater_type",
                "energy_source",
                "volume_l",
                "mounting",
                "orientation",
                "voltage_v",
                "phase_count",
                "ip_rating",
                "heating_element_type",
                "current_type",
            },
            "hydraulic_accumulators": {
                "tank_application",
                "volume_l",
                "orientation",
                "size_inch",
                "operating_pressure_bar",
                "thread_standard",
                "thread_gender",
            },
            "filters": {
                "filter_format",
                "filtration_microns",
                "filter_technology",
                "filter_element_type",
                "water_temperature",
                "size_inch",
                "thread_standard",
                "thread_gender",
            },
            "controls": {
                "control_kind",
                "normal_state",
                "control_signal",
                "voltage_v",
                "phase_count",
                "ip_rating",
                "current_type",
            },
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
        if query.category in {
            "pumps",
            "valves",
            "radiator_fittings",
            "water_heaters",
            "hydraulic_accumulators",
            "filters",
            "controls",
        }:
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
            if query.category == "fittings" and self._fitting_element_matches(
                product, element_type
            ):
                score += 35
            elif query.category == "sewer" and self._sewer_element_matches(
                product, element_type
            ):
                score += 35
            elif normalize_text(str(element_type)) in text:
                score += 35
            elif query.category in {"sewer", "fittings"}:
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
            score += 25 if self._dimension_matches(
                product,
                int(diameter),
                self._diameter_attribute_keys(query.category),
            ) else -12

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

        if slots.get("fitting_system"):
            if self._fitting_system_matches(product, slots["fitting_system"]):
                score += 25
            else:
                return 0

        if slots.get("thread_type") or slots.get("thread_gender"):
            if thread_constraint_matches(
                product,
                thread_type=slots.get("thread_type"),
                thread_gender=slots.get("thread_gender"),
            ):
                score += 20
            else:
                return 0

        if slots.get("product_kind") and self._product_kind_is_recognised(
            slots["product_kind"]
        ):
            if self._product_kind_matches(product, slots["product_kind"]):
                score += 25
            else:
                return 0

        if slots.get("handle_type"):
            if self._handle_type_matches(product, slots["handle_type"]):
                score += 20
            else:
                return 0

        if "full_bore" in slots or slots.get("bore_type"):
            if self._bore_type_matches(product, slots):
                score += 20
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
                ["межосев", "размер"],
            ) else -12

        radiator_height = slots.get("radiator_height_mm")
        if radiator_height:
            score += 25 if self._dimension_matches(
                product,
                int(radiator_height),
                ["высот"],
            ) else -12

        sections = slots.get("sections")
        if sections:
            score += 20 if self._dimension_matches(product, int(sections), ["секц"]) else -10

        radiator_type = slots.get("radiator_type")
        if radiator_type:
            if self._radiator_type_matches(product, radiator_type):
                score += 30
            else:
                return 0

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

        heater_type = slots.get("heater_type")
        if heater_type:
            if self._water_heater_type_matches(product, heater_type):
                score += 30
            else:
                return 0

        energy_source = slots.get("energy_source")
        if energy_source:
            if self._water_heater_energy_matches(product, energy_source):
                score += 30
            else:
                return 0

        volume_l = slots.get("volume_l")
        if volume_l is not None:
            if self._water_heater_volume_matches(product, volume_l):
                score += 30
            else:
                return 0

        heater_mounting = slots.get("mounting")
        if heater_mounting:
            if self._water_heater_mounting_matches(product, heater_mounting):
                score += 20
            else:
                return 0

        orientation = slots.get("orientation")
        if orientation:
            if self._water_heater_orientation_matches(product, orientation):
                score += 15
            else:
                return 0

        body_form = slots.get("body_form")
        if body_form:
            if self._body_form_matches(product, body_form):
                score += 20
            else:
                return 0

        size_inch = slots.get("size_inch")
        if size_inch:
            if self._inch_size_matches(product, str(size_inch), slots):
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
            if self._voltage_matches(product, int(voltage)):
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

    def _product_inch_sizes(self, product: Product) -> set[str]:
        """Extract explicitly evidenced inch connection sizes."""
        return product_inch_sizes(product)

    def _inch_size_matches(
        self,
        product: Product,
        size_inch: str,
        slots: dict | None = None,
    ) -> bool:
        if is_reducer_element((slots or {}).get("trade_element")):
            expected = normalize_inch_size(size_inch)
            return bool(expected and expected in self._product_inch_sizes(product))
        return single_inch_size_constraint_matches(product, size_inch)

    def _dimension_matches(self, product: Product, number: int, keys: list[str]) -> bool:
        key_texts = [normalize_text(key) for key in keys]
        values = []
        for attr_key, attr_value in product.attributes_normalized.items():
            normalized_key = normalize_text(attr_key)
            if any(key_text in normalized_key for key_text in key_texts):
                values.append(normalize_text(attr_value))
        if values:
            return any(self._number_matches(value, number) for value in values)
        raw_identity = " ".join(
            [
                product.name,
                *[
                    str(value)
                    for key, value in product.attributes_normalized.items()
                    if "полное наименование" in normalize_text(key)
                ],
            ]
        )
        if (
            any(key in {"диаметр", "размер"} for key in key_texts)
            and re.search(
                rf"(?<!\d){number}\s*\(\s*\d+(?:[.,]\d+)?\s*\)",
                raw_identity,
            )
        ):
            # Металлополимерные трубы часто записаны как 16(2,0):
            # наружный диаметр 16 мм, толщина стенки 2,0 мм.
            return True
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
        wants_height = any("высот" in key for key in key_texts)
        wants_length = any("длина" in key for key in key_texts)
        if wants_height or wants_length:
            identity = normalize_text(f"{product.category_path} {product.name}")
            if "радиатор" in identity or "конвектор" in identity:
                height, length = self._radiator_dimensions_from_name(product.name)
                target = height if wants_height else length
                if target is not None:
                    # Габариты в названии — единственное доказательство размера.
                    # Свободный поиск числа по названию здесь недопустим: у
                    # «22 300 x 500» иначе совпадала бы «высота 500».
                    return target == number
        if wants_length:
            return self._length_matches_name(fallback, number)
        return self._number_matches(fallback, number)

    @staticmethod
    def _diameter_attribute_keys(category: str) -> list[str]:
        """Return category-aware evidence labels for a diameter constraint.

        Pipe feeds normally call the field simply ``diameter``.  Industrial
        valve feeds use DN terminology such as ``nominal diameter`` or
        ``nominal bore`` for the same request, so treating only the former as
        evidence incorrectly removes an otherwise exact valve.
        """
        keys = ["диаметр", "размер"]
        if category in {"valves", "radiator_fittings"}:
            keys.extend(
                [
                    "условный проход",
                    "условный диаметр",
                    "номинальный диаметр",
                    "dn",
                    "ду",
                ]
            )
        return keys

    def _voltage_matches(self, product: Product, requested: int) -> bool:
        """Match equivalent nominal mains labels without mixing voltage classes."""
        equivalents = {
            220: {220, 230},
            230: {220, 230},
            380: {380, 400},
            400: {380, 400},
        }.get(int(requested), {int(requested)})
        return any(
            self._dimension_matches(
                product,
                voltage,
                ["напряжение", "питание"],
            )
            for voltage in equivalents
        )

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
            # Серии вида «VC22-500-1000» и «CV 22-500-1000» кодируют длину
            # через дефис, а не через «х» или «/».
            or re.search(rf"\d+\s*-\s*\d+\s*-\s*{number}([^0-9]|$)", compact)
            or re.search(rf"(^|[^0-9]){number}\s*(?:мм|mm)([^0-9]|$)", compact)
        )

    @staticmethod
    def _radiator_dimensions_from_name(name: str) -> tuple[int | None, int | None]:
        """Высота и длина радиатора, зашитые в название.

        В выгрузке у панельных радиаторов нет отдельных <param> с габаритами —
        они есть только в названии, причём в четырёх разных нотациях:
        «VC22-500-900», «AXIS 22 500 x 1000», «Радиатор 11/500/1000» и
        «тип 22 высота 300 длина 900». Без разбора названия подбор по размеру
        сваливался на соседний типоразмер.
        """
        text = normalize_text(str(name or "").replace("*", "х"))
        height = length = None
        explicit_height = re.search(r"высот\w*\D{0,6}(\d{3,4})", text)
        if explicit_height:
            height = int(explicit_height.group(1))
        explicit_length = re.search(r"длин\w*\D{0,6}(\d{3,4})", text)
        if explicit_length:
            length = int(explicit_length.group(1))
        if height is not None and length is not None:
            return height, length
        triple = re.search(
            r"(?<!\d)(\d{2})\s*[-/]\s*(\d{3,4})\s*[-/]\s*(\d{3,4})(?!\d)", text
        )
        if triple:
            return (
                height if height is not None else int(triple.group(2)),
                length if length is not None else int(triple.group(3)),
            )
        pair = re.search(r"(?<!\d)(\d{3,4})\s*[xх×]\s*(\d{3,4})(?!\d)", text)
        if pair:
            return (
                height if height is not None else int(pair.group(1)),
                length if length is not None else int(pair.group(2)),
            )
        return height, length

    def _fitting_dimension_matches(self, product: Product, number: int) -> bool:
        text = normalize_text(
            " ".join([product.name, *product.attributes_normalized.values()])
        )
        return self._number_matches(text, number)

    # Насколько паспортный максимум может превышать номинал из маркировки.
    #
    # Паспорт VRS расшифровывает обозначение прямо: в «VRS.25 4.130» цифра 4 —
    # это «максимальный напор в м.вод.ст. (4; 6; 8)», то есть номинал серии. В
    # каталоге у той же позиции стоит фактический максимум на третьей скорости:
    # 4,2 при номинале 4 и 8,5 при номинале 8. Покупатель называет цифру с
    # шильдика, и сравнение по точному равенству отбрасывало ровно тот насос,
    # который ему нужен. Соседние серии расходятся на 50 % и больше, так что
    # запас в 15 % их не смешивает.
    _HEAD_NOMINAL_TOLERANCE = 1.15

    def _head_matches(self, product: Product, head_m: float) -> bool:
        values = []
        for attr_key, attr_value in product.attributes_normalized.items():
            if "напор" in normalize_text(attr_key):
                values.append(normalize_text(attr_value))
        if values:
            upper = head_m * self._HEAD_NOMINAL_TOLERANCE
            return any(
                any(
                    # Допуск односторонний: паспортный максимум не бывает ниже
                    # номинала, поэтому меньшее значение — это другая серия, а
                    # не та же с округлением.
                    head_m - 0.01 <= float(raw.replace(",", ".")) <= upper + 0.01
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
