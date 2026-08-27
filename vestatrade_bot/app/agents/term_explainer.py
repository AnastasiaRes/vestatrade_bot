from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.openrouter_client import OpenRouterClient


TERM_EXPLAINER_PROMPT = """
Ты — инженер-консультант компании Vesta Trading. Твоя единственная задача в этом
вызове — объяснить покупателю сантехнический или отопительный термин. Товары ты
здесь не подбираешь и не рекламируешь.

КОМУ ОБЪЯСНЯЕШЬ
Пишет обычный покупатель: хозяин дома, прораб, монтажник. Он спросил про термин,
потому что встретил его в карточке товара, в проекте или в разговоре с мастером.
Ему нужно понять, что это и как это влияет на его выбор, а не прослушать лекцию.

КАК ОБЪЯСНЯТЬ
- Сначала суть одним-двумя предложениями, обычными словами.
- Потом зачем это знать при выборе: на что этот параметр влияет практически.
- Если термин часто путают с другим — назови эту путаницу прямо.
- Никаких вводных вроде «хороший вопрос» и «давайте разберёмся». Сразу по делу.
- Пиши так, как объясняет мастер на объекте: спокойно, коротко, без пафоса и
  без маркетинга. Никаких «оптимальное решение» и «широкий ассортимент».

ЧИСЛА
Числа в объяснении допустимы только как общеотраслевые ориентиры, и тогда их
нужно назвать ориентиром явно. Не выдавай ориентир за требование стандарта.
Не называй конкретную цифру для конкретного изделия: допустимые температура,
давление и ресурс проверяются по паспорту этого изделия, а не по классу.

ЧЕГО НЕЛЬЗЯ НИКОГДА
- Не называй артикулы, цены, остатки и наличие. Каталога у тебя сейчас нет.
- Не рекомендуй конкретный товар и не обещай совместимость.
- Не выдумывай расшифровку, если не уверен. Лучше вернуть is_domain_term=false
  и confidence=low, чем дать красивую неправду: покупатель купит по ней железо.
- Не выдавай себя за человека.

МНОГОЗНАЧНЫЕ ТЕРМИНЫ
Часть слов в отрасли означает разное в разных контекстах: «контур» у котла и у
тёплого пола, «американка» как соединение и как жаргон для целой группы
фитингов, «подача» как расход и как подающая линия. Если у термина в отрасли
несколько рабочих значений — перечисли их в ambiguous_meanings и объясни то,
которое подходит к вопросу; если из вопроса не видно какое — объясни оба
коротко.

ФОРМАТ ОТВЕТА
Верни строго JSON без markdown и без текста вокруг:
{
  "is_domain_term": true|false,
  "term": "термин в именительном падеже",
  "definition": "1-2 предложения: что это",
  "why_it_matters": "1-2 предложения: на что влияет при выборе",
  "pitfall": "частая ошибка или путаница" | null,
  "ambiguous_meanings": ["значение 1", "значение 2"],
  "confidence": "high"|"medium"|"low"
}

is_domain_term=false ставь, когда спрашивают не про инженерные системы вообще
(погода, доставка, политика) либо когда слово тебе незнакомо как термин отрасли.

ПРИМЕРЫ УРОВНЯ ОТВЕТА

Вопрос: «Что такое DN?»
{"is_domain_term": true, "term": "DN", "definition": "DN (условный проход) —
округлённое обозначение внутреннего размера трубы или арматуры в миллиметрах.
Это не точный замер, а типоразмер, по которому детали подбирают друг к другу.",
"why_it_matters": "По DN сходятся кран, фитинг и труба: DN25 к DN25. Наружный
диаметр и резьба при одном DN у разных материалов отличаются, поэтому для
полипропилена и металла размеры сверяют отдельно.", "pitfall": "DN путают с
дюймами резьбы: DN25 обычно соответствует резьбе 1 дюйм, но это соответствие
таблицей, а не пересчётом.", "ambiguous_meanings": [], "confidence": "high"}

Вопрос: «Что такое контур?»
{"is_domain_term": true, "term": "контур", "definition": "Контур — замкнутая
петля, по которой ходит теплоноситель или вода.", "why_it_matters": "От числа
контуров зависит, что именно подбирать: у котла контур определяет, греет ли он
только отопление или ещё и воду на кран; у тёплого пола контур — это отдельная
петля трубы на своём участке пола.", "pitfall": null, "ambiguous_meanings":
["контур котла: одноконтурный греет только отопление, двухконтурный ещё и
горячую воду", "контур тёплого пола: отдельная петля трубы от коллектора"],
"confidence": "high"}
""".strip()


class TermExplanation(BaseModel):
    """Типизированный ответ агента-объяснителя.

    ``extra="forbid"`` намеренно: лишнее поле означает, что модель начала
    сочинять структуру, и такой ответ безопаснее отклонить целиком.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    is_domain_term: bool
    term: str = Field(min_length=1, max_length=120)
    # Текст необязателен намеренно: на вопрос не по теме модель обязана
    # вернуть is_domain_term=false, и требовать от неё при этом определение
    # значило бы заставлять сочинять. Обязательность проверяется ниже, но
    # только для терминов, которые агент признал отраслевыми.
    definition: str | None = Field(default=None, max_length=600)
    why_it_matters: str | None = Field(default=None, max_length=600)
    pitfall: str | None = Field(default=None, max_length=400)
    ambiguous_meanings: list[str] = Field(default_factory=list, max_length=4)
    confidence: str = Field(pattern=r"^(high|medium|low)$")

    @model_validator(mode="after")
    def domain_terms_are_explained(self) -> "TermExplanation":
        if self.is_domain_term and not (self.definition and self.why_it_matters):
            raise ValueError("отраслевой термин требует definition и why_it_matters")
        return self


# Объяснение термина не имеет права выглядеть как оффер. Эти следы означают,
# что модель ушла в подбор товара, где у неё нет ни каталога, ни остатков.
#
# Границы слова здесь обязательны, а не косметика: подстрока «руб» сидит внутри
# слова «труба», и без \b фильтр отклонял любое объяснение про трубы.
_COMMERCE_MARKERS = (
    r"\bруб",
    r"\brub\b",
    r"₽",
    r"в наличии",
    r"на складе",
    r"\bарт\.",
    r"\bартикул",
    r"\bзаказ",
    r"\bкупи",
    r"\bцена\b",
    r"\bцены\b",
    r"\bскидк",
)

_COMMERCE_PATTERN = re.compile("|".join(_COMMERCE_MARKERS))

# Артикулы вида VT.214.N.04, VRS.254.18.0, VTp.700.FB20.25, PA35010P.
_SKU_PATTERN = re.compile(r"\b[A-Za-z]{2,4}[.\-][A-Za-z0-9.\-]{3,}\b")


class TermExplainerAgent:
    """Объясняет отраслевой термин, когда его нет в проверенном глоссарии.

    Глоссарий в ``response_composer`` остаётся приоритетным источником: он
    выверен вручную и отвечает мгновенно. Этот агент нужен для длинного хвоста
    терминов, на котором глоссарий сейчас отвечает отказом. Ответ модели
    принимается только после проверок ниже; всё непрошедшее возвращает None,
    и вызывающий код показывает прежний честный отказ.
    """

    def __init__(self, llm_client: OpenRouterClient) -> None:
        self.llm_client = llm_client
        self.last_llm_used = False
        self.last_rejection_reason: str | None = None

    def explain(self, message: str, *, category_hint: str | None = None) -> str | None:
        self.last_rejection_reason = None
        self.last_llm_used = False

        user_content = f"Вопрос покупателя: {message}"
        if category_hint:
            user_content += f"\nТекущая категория диалога: {category_hint}"

        data, used = self.llm_client.complete_json(
            "TermExplainerAgent",
            [
                {"role": "system", "content": TERM_EXPLAINER_PROMPT},
                {"role": "user", "content": user_content},
            ],
            {},
        )
        self.last_llm_used = used
        if not used or not data:
            self.last_rejection_reason = "llm_unavailable"
            return None

        explanation = self._validate(data)
        if explanation is None:
            return None
        return self._render(explanation)

    def _validate(self, data: dict[str, Any]) -> TermExplanation | None:
        try:
            explanation = TermExplanation.model_validate(data)
        except ValidationError as exc:
            self.last_rejection_reason = f"schema: {exc.error_count()} ошибок"
            return None

        if not explanation.is_domain_term:
            self.last_rejection_reason = "not_a_domain_term"
            return None
        if explanation.confidence == "low":
            # Низкая уверенность на определении термина — это ровно тот случай,
            # ради которого в коде стоит честный отказ. Не подменяем его.
            self.last_rejection_reason = "low_confidence"
            return None

        blob = " ".join(
            part
            for part in [
                explanation.definition or "",
                explanation.why_it_matters or "",
                explanation.pitfall or "",
                *explanation.ambiguous_meanings,
            ]
        ).lower()

        commerce = _COMMERCE_PATTERN.search(blob)
        if commerce:
            self.last_rejection_reason = f"commerce_claim: {commerce.group(0)}"
            return None
        if _SKU_PATTERN.search(blob):
            self.last_rejection_reason = "sku_mentioned"
            return None
        return explanation

    @staticmethod
    def _render(explanation: TermExplanation) -> str:
        parts = [explanation.definition or "", explanation.why_it_matters or ""]
        if explanation.ambiguous_meanings:
            parts.append(
                "В отрасли это слово значит разное: "
                + "; ".join(explanation.ambiguous_meanings)
                + "."
            )
        if explanation.pitfall:
            parts.append(explanation.pitfall)
        parts.append(
            "Если нужно, подберу подходящие позиции из ассортимента — опишите задачу."
        )
        return " ".join(part.strip() for part in parts if part and part.strip())
