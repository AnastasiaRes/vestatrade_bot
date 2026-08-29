"""PII redaction at model, logging and diagnostic boundaries.

The redactor deliberately keeps the non-sensitive shape of a request.  Labels
such as ``recipient`` and a city before a street address remain visible, while
the value that can identify or locate a person is replaced with a typed
placeholder.  Product names, catalogue identifiers and engineering dimensions
must remain untouched: the patterns below therefore require an explicit
identity/address context instead of treating every capitalised word or number
as personal data.
"""

from __future__ import annotations

import re
from enum import Enum


class PIIKind(str, Enum):
    """PII classes handled by the shared text-boundary sanitizer."""

    EMAIL = "email"
    PHONE = "phone"
    PERSON_NAME = "name"
    PHYSICAL_ADDRESS = "address"


_PLACEHOLDER = {
    PIIKind.EMAIL: "[email redacted]",
    PIIKind.PHONE: "[phone redacted]",
    PIIKind.PERSON_NAME: "[name redacted]",
    PIIKind.PHYSICAL_ADDRESS: "[address redacted]",
}


_EMAIL_RE = re.compile(
    r"[\w.+-]+@(?:[\w-]+\.)+(?:[^\W\d_]{2,}|xn--[\w-]{2,})(?![\w-])",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?:\+?\d[\s()./-]*){10,}")
_LABELED_LOCAL_PHONE_RE = re.compile(
    r"(?P<label>\b(?:"
    r"(?:мой|моя|мои)\s+(?:(?:рабоч|личн|контактн)\w*\s+)?"
    r"(?:телефон|номер)\w*|для\s+связи|связаться\s+со\s+мной"
    r")\s*[:№#=-]?\s*)"
    r"(?P<number>\+?\d(?:[\s()./-]*\d){6,})",
    re.IGNORECASE,
)
_NON_CONTACT_NUMBER_CONTEXT_RE = re.compile(
    r"(?:инн|огрн(?:ип)?|кпп|окпо|бик|р\s*/?\s*с|к\s*/?\s*с|"
    r"расчетн\w*\s+счет\w*|расчётн\w*\s+счёт\w*|счет\w*\s+№|"
    r"номер\s+заказа|заказ\w*\s*№?|артикул\w*|арт\.?|sku|ску|"
    r"код\w*\s+(?:товар|позици)\w*)\s*[:№#-]?\s*"
    r"(?:\d[\d\s()./-]*\s*(?:(?:,|и|или)\s*)?)*$",
    re.IGNORECASE,
)

# A person's name is sensitive here only when the customer explicitly
# introduces it or labels it as a recipient/contact.  This avoids redacting
# brands and models merely because they happen to be capitalised.  Run the
# title-cased form first so a following lower-case product request cannot be
# swallowed; the second form supports deliberately lower-case chat input when
# the labelled value forms a complete comma/semicolon-delimited clause.
_TITLE_NAME_WORD = (
    r"(?:(?:[A-ZА-ЯЁ][a-zа-яё]+|[A-ZА-ЯЁ])"
    r"(?:[-'’][A-ZА-ЯЁ]?[a-zа-яё]+)?|"
    r"[A-ZА-ЯЁ](?:\.|[A-ZА-ЯЁ]+))"
)
_NAME_WORD = r"(?:[^\W\d_]{2,}(?:[-'’][^\W\d_]{2,})?)"
_EXPLICIT_NAME_LABEL = (
    r"(?:меня\s+зовут|(?:я\s+)?представл(?:юсь|яюсь)|"
    r"фио(?:\s+получател(?:я|ь))?|имя\s+получател(?:я|ь)|"
    r"получател(?:ь|я)|контактн(?:ое|ый)\s+лицо|"
    r"my\s+name\s+is|recipient(?:['’]s)?(?:\s+name)?(?:\s+is)?|"
    r"contact\s+person(?:\s+is)?|consignee(?:\s+is)?)"
)
_EXPLICIT_TITLE_NAME_RE = re.compile(
    rf"(?P<label>\b(?i:{_EXPLICIT_NAME_LABEL})\s*(?:[:=\-—–]\s*)?)"
    rf"(?P<name>{_TITLE_NAME_WORD}(?:\s+{_TITLE_NAME_WORD}){{0,2}})"
)
_EXPLICIT_CLAUSE_NAME_RE = re.compile(
    rf"(?P<label>\b{_EXPLICIT_NAME_LABEL}\s*(?:[:=\-—–]\s*)?)"
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,2}})"
    r"(?=\s*(?:[,;.!?\n]|$|\b(?:телефон|номер|email|e-mail|адрес|"
    r"улица|ул\.?|phone|address)\b))",
    re.IGNORECASE,
)

# ``площадь`` is intentionally absent here.  In an engineering dialogue it is
# normally a quantity (``Площадь всё-таки 100 м²``), and the former broad
# address expression consequently routed ordinary revisions away from V2.
# Named squares still have a dedicated, stricter expression below.
_RU_STREET_MARKER = (
    r"(?:ул(?:ица)?\.?|проспект|переулок|проезд|набережная|шоссе|"
    r"бульвар|аллея)"
)
_ADDRESS_NUMBER = r"(?:№\s*)?\d[0-9A-Za-zА-Яа-яЁё/\-]{0,15}"
_STANDALONE_UNIT_NUMBER = r"(?:№\s*)?\d{1,5}[A-Za-zА-Яа-яЁё]?"
_RU_STREET_WORD = (
    r"(?:(?:[^\W\d_]+|\d+-[^\W\d_]+)"
    r"(?:[-'’][^\W\d_]+)*\.?)"
)
_RU_STREET_NAME = (
    rf"(?:(?:\d+\s+)?{_RU_STREET_WORD}"
    rf"(?:\s+{_RU_STREET_WORD}){{0,7}})"
)
_RU_ADDRESS_UNIT = (
    rf"(?:корп(?:ус)?\.?|стр(?:оение)?\.?|кв(?:артира)?\.?|"
    rf"оф(?:ис)?\.?|подъезд|пом(?:ещение)?\.?)\s*{_ADDRESS_NUMBER}"
)

# The Russian pattern requires both an explicit street marker and a house
# number.  Thus ``улица 40 лет Победы`` alone, pipe lengths and pump mounting
# sizes are not mistaken for an address.
_RU_STREET_ADDRESS_RE = re.compile(
    rf"(?P<address>\b{_RU_STREET_MARKER}\s+{_RU_STREET_NAME}"
    rf"\s*,?\s*(?:д(?:ом)?\.?\s*)?{_ADDRESS_NUMBER}"
    rf"(?:\s*,?\s*{_RU_ADDRESS_UNIT})*)",
    re.IGNORECASE,
)

# An abbreviated square is an unambiguous address marker.  The fully written
# form is accepted only with an explicit house designator: this preserves
# address redaction for ``площадь Ленина, дом 10`` without treating a product
# requirement such as ``Площадь 100 квадратов`` as an address.
_RU_SQUARE_ADDRESS_RE = re.compile(
    rf"(?P<address>\b(?:"
    rf"пл\.?\s+{_RU_STREET_NAME}\s*,?\s*(?:д(?:ом)?\.?\s*)?{_ADDRESS_NUMBER}"
    rf"|площадь\s+{_RU_STREET_NAME}\s*,?\s*(?:д(?:ом)?\.?\s*){_ADDRESS_NUMBER}"
    rf")(?:\s*,?\s*{_RU_ADDRESS_UNIT})*)",
    re.IGNORECASE,
)

# English street addresses conventionally put the house number first, which
# is a strong enough signal when followed by a street-type suffix.
_EN_STREET_ADDRESS_RE = re.compile(
    r"(?P<address>\b\d[0-9A-Za-z/\-]{0,10}\s+"
    r"(?:[A-Za-z][A-Za-z.'’\-]*\s+){1,7}"
    r"(?:street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|"
    r"drive|dr|court|ct|way|parkway|pkwy)\.?\b"
    r"(?:\s*,?\s*(?:apartment|apt|flat)\.?\s*(?:#|no\.?)?\s*"
    r"\d[0-9A-Za-z/\-]{0,10})*)",
    re.IGNORECASE,
)

# Apartment/office designators are independently identifying even if a user
# omits the street.  Do not include bare English ``unit`` or Russian ``корпус``
# here: both are common product/engineering words without address context.
_STANDALONE_ADDRESS_UNIT_RE = re.compile(
    rf"(?P<address>\b(?:кв(?:артира)?\.?|офис|пом(?:ещение)?\.?)\s*"
    rf"{_STANDALONE_UNIT_NUMBER}\b|\b(?:apartment|apt)\.?\s*"
    rf"(?:#|no\.?)?\s*\d{{1,5}}[A-Za-z]?\b)",
)

# Some checkout forms omit ``улица``/``street`` and submit a labelled value
# such as ``Адрес доставки: Москва, Тверская 12``.  The explicit address label
# makes this safe to recognise without guessing from an arbitrary product
# phrase.  A comma-separated non-numeric locality is retained; the locating
# portion through its final house/apartment number is removed.
_LABELED_ADDRESS_RE = re.compile(
    rf"(?P<label>\b(?:адрес(?:\s+(?:доставки|получател(?:я|ь)))?|"
    rf"address|delivery\s+address|shipping\s+address)"
    rf"\s*(?:[:=\-—–]\s*))"
    rf"(?:(?P<locality>[^\d,;.!?\n]{{2,60}}),\s*)?"
    rf"(?P<address>[^\n;.!?]{{1,120}}?{_ADDRESS_NUMBER}"
    rf"(?:\s*,?\s*(?:{_RU_ADDRESS_UNIT}|"
    rf"(?:apartment|apt|flat)\.?\s*(?:#|no\.?)?\s*"
    rf"\d[0-9A-Za-z/\-]{{0,10}}))*)",
    re.IGNORECASE,
)


def _redact_explicit_name(match: re.Match[str]) -> str:
    """Keep the source label and remove only the introduced person's name."""

    return f"{match.group('label')}{_PLACEHOLDER[PIIKind.PERSON_NAME]}"


def _redact_address(match: re.Match[str]) -> str:
    return _PLACEHOLDER[PIIKind.PHYSICAL_ADDRESS]


def _redact_labeled_address(match: re.Match[str]) -> str:
    locality = str(match.group("locality") or "")
    preserved_locality = f"{locality}, " if locality else ""
    return (
        f"{match.group('label')}{preserved_locality}"
        f"{_PLACEHOLDER[PIIKind.PHYSICAL_ADDRESS]}"
    )


def _looks_like_contact_phone(candidate: str) -> bool:
    """Separate ordinary phones from catalogue ranges and dimensions."""

    core = str(candidate or "").rstrip(" ().-")
    digits = re.sub(r"\D", "", core)
    if not 10 <= len(digits) <= 15:
        return False
    # An explicit international prefix is strong evidence, including the
    # slash-separated notation accepted by the legacy handoff flow.
    if core.lstrip().startswith("+"):
        return True
    # Slashes overwhelmingly denote compound sizes in this catalogue.  A
    # customer-owned slash phone is still caught by the labelled-phone rule.
    if "/" in core:
        return False

    groups = re.findall(r"\d+", core)
    if len(groups) == 1:
        return (
            (len(digits) == 10 and digits.startswith("9"))
            or (len(digits) == 11 and digits.startswith(("7", "8")))
        )
    # Two long groups are normally a model/range such as ``70031-70035``.
    if len(groups) == 2 and min(map(len, groups)) >= 4:
        return False
    # Common international/local formatting: 415-555-2671,
    # 8 (800) 555-35-35, 020 7946 0958.  Engineering dimensions generally
    # contain a slash or much longer groups and therefore do not pass.
    return len(groups) >= 3 and all(1 <= len(group) <= 4 for group in groups)


def redact_pii_for_model(text: str) -> str:
    """Remove supported PII while preserving useful catalogue/delivery context.

    In particular, a city or region before/after the matched street portion is
    left intact, so a delivery request can still be scoped without exposing a
    house or apartment.  The function is deterministic and idempotent and is
    safe to apply recursively to telemetry strings.
    """

    source = str(text or "")
    redacted = _EMAIL_RE.sub(_PLACEHOLDER[PIIKind.EMAIL], source)
    redacted = _LABELED_LOCAL_PHONE_RE.sub(
        lambda match: f"{match.group('label')}{_PLACEHOLDER[PIIKind.PHONE]}",
        redacted,
    )

    def _redact_phone(match: re.Match[str]) -> str:
        if not _looks_like_contact_phone(match.group(0)):
            return match.group(0)
        prefix = redacted[max(0, match.start() - 48) : match.start()]
        if _NON_CONTACT_NUMBER_CONTEXT_RE.search(prefix.rstrip()):
            return match.group(0)
        # Catalogue model families commonly put a compact uppercase code
        # immediately before dot-separated dimensions (``SCN 110.240.1000``).
        # That context is incompatible with an unlabeled contact number.
        if re.search(r"\b[A-ZА-ЯЁ][A-ZА-ЯЁ0-9_-]{1,15}\s+$", prefix):
            return match.group(0)
        # Numeric tails of catalogue identifiers such as
        # ``VTp.704.0.040025`` look phone-shaped after the leading letters.
        # The alphanumeric SKU prefix is stronger evidence than digit count.
        if re.search(r"[a-zа-я]{2,}[._/-]\s*$", prefix, re.IGNORECASE):
            return match.group(0)
        trailing = match.group(0)[len(match.group(0).rstrip(" ().-")) :]
        return _PLACEHOLDER[PIIKind.PHONE] + trailing

    redacted = _PHONE_RE.sub(_redact_phone, redacted)
    redacted = _EXPLICIT_TITLE_NAME_RE.sub(_redact_explicit_name, redacted)
    redacted = _EXPLICIT_CLAUSE_NAME_RE.sub(_redact_explicit_name, redacted)
    redacted = _RU_STREET_ADDRESS_RE.sub(_redact_address, redacted)
    redacted = _RU_SQUARE_ADDRESS_RE.sub(_redact_address, redacted)
    redacted = _EN_STREET_ADDRESS_RE.sub(_redact_address, redacted)
    redacted = _LABELED_ADDRESS_RE.sub(_redact_labeled_address, redacted)
    return _STANDALONE_ADDRESS_UNIT_RE.sub(_redact_address, redacted)
