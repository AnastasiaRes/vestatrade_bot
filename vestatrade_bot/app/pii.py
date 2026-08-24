"""PII redaction at the external-model transport boundary."""

from __future__ import annotations

import re


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
    r"номер\s+заказа|заказ\w*\s*№?|артикул\w*|sku|ску|"
    r"код\w*\s+(?:товар|позици)\w*)\s*[:№#-]?\s*"
    r"(?:\d[\d\s()./-]*\s*(?:(?:,|и|или)\s*)?)*$",
    re.IGNORECASE,
)


def redact_pii_for_model(text: str) -> str:
    """Remove email/phone PII while preserving order IDs and numeric SKUs."""

    source = str(text or "")
    redacted = _EMAIL_RE.sub("[email redacted]", source)
    redacted = _LABELED_LOCAL_PHONE_RE.sub(
        lambda match: f"{match.group('label')}[phone redacted]",
        redacted,
    )

    def _redact_phone(match: re.Match[str]) -> str:
        prefix = redacted[max(0, match.start() - 48) : match.start()]
        if _NON_CONTACT_NUMBER_CONTEXT_RE.search(prefix.rstrip()):
            return match.group(0)
        trailing = match.group(0)[len(match.group(0).rstrip(" ().-")) :]
        return "[phone redacted]" + trailing

    return _PHONE_RE.sub(_redact_phone, redacted)
