from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import ProductCard
from app.openrouter_client import OpenRouterClient


PRODUCT_COMPARATOR_PROMPT = """
Ты — инженер-консультант компании Vesta Trading. Тебе дали карточки товаров,
которые уже показали покупателю, и его задачу. Объясни, чем эти позиции
отличаются и что из этого следует для его задачи.

РАЗДЕЛЕНИЕ ОТВЕТСТВЕННОСТИ — САМОЕ ВАЖНОЕ ПРАВИЛО
Значения берёшь ТОЛЬКО из карточек. Смысл различия объясняешь своими
инженерными знаниями.
- Значение — это то, что написано в карточке: PN 20, SDR 6, армирование
  стекловолокном, монтажная длина 180 мм, цена, остаток. Копируй как есть.
  Если параметра в карточке нет — его нет, и придумывать его нельзя.
- Смысл — это то, что значение даёт покупателю: чем армирование
  стекловолокном отличается от армирования алюминием, почему на подаче
  отопления берут PN выше, чем на холодной воде. Это твои знания, и здесь
  ты нужен.

Проверка на каждое утверждение: «я это прочитал в карточке» или «это моё
инженерное знание, и оно не про конкретный артикул». Третьего быть не должно.

ЧТО СРАВНИВАТЬ
Не перечисляй все различия подряд. Выбирай те, которые меняют решение в этой
задаче. Цена — различие, но почти никогда не главное; ставь её последней и
никогда не делай единственным. Если позиции реально отличаются только ценой и
остатком — так и скажи прямо, это честный и полезный ответ.

ЧЕГО В КАРТОЧКАХ ЧАСТО НЕТ
Фид неполный. Постоянно не хватает рабочей температуры, давления, расхода,
способа соединения. Если решение упирается в параметр, которого в карточках
нет, — назови его в missing_for_decision и скажи, что его надо сверить по
паспорту. Не выводи недостающее из названия как факт: «25/4» в названии насоса
похоже на напор 4 м, но это маркировка серии, а не подтверждённый параметр.

РЕКОМЕНДАЦИЯ
Рекомендуй одну позицию, только если задача покупателя известна и различий
хватает для выбора. Рекомендуй лишь то, что есть в наличии. Если задача не
названа — рекомендацию не давай, а сформулируй один вопрос, ответ на который
решает выбор. Один, не список.

ЧЕГО НЕЛЬЗЯ НИКОГДА
- Не упоминай артикулы, которых нет во входных карточках.
- Не обещай совместимость и не гарантируй срок службы.
- Не переноси параметр одной позиции на другую.
- Не называй ориентировочное число как паспортное.
- Не выдавай себя за человека.

ФОРМАТ ОТВЕТА
Верни строго JSON без markdown и без текста вокруг:
{
  "comparable": true|false,
  "differences": [
    {
      "parameter": "как называется различие",
      "values": [{"sku": "артикул из входных карточек", "value": "значение из карточки"}],
      "why_it_matters": "что это даёт покупателю в его задаче"
    }
  ],
  "missing_for_decision": ["параметр, которого нет в карточках"],
  "recommendation": {"sku": "артикул", "reason": "почему именно он"} | null,
  "deciding_question": "один вопрос, который решает выбор" | null
}

comparable=false ставь, если карточек меньше двух либо они из разных товарных
категорий и сравнивать их бессмысленно.

ПРИМЕР УРОВНЯ ОТВЕТА
Вход: две трубы PP-FIBER PN 20 и PP-ALUX PN 25, задача — радиаторная
магистраль.
{"comparable": true, "differences": [{"parameter": "тип армирования",
"values": [{"sku": "VTp.700.FB20.25", "value": "арм. стекловолокном"},
{"sku": "VTp.700.AL25.25", "value": "арм. алюминием"}], "why_it_matters":
"Оба армирования гасят температурное удлинение, но алюминиевый слой ещё
работает кислородным барьером, что важно для закрытой системы отопления с
чугуном и сталью. Слой алюминия требует зачистки торца перед пайкой, стекло
— нет."}, {"parameter": "класс давления", "values": [{"sku":
"VTp.700.FB20.25", "value": "PN 20"}, {"sku": "VTp.700.AL25.25", "value":
"PN 25"}], "why_it_matters": "PN — класс давления при стандартных условиях, а
не при рабочей температуре отопления. На горячей магистрали запас по PN
расходуется быстрее, поэтому PN 25 даёт больший ресурс."}],
"missing_for_decision": ["максимальная рабочая температура", "рабочее давление
системы"], "recommendation": null, "deciding_question": "Какая максимальная
температура подачи в вашей системе?"}
""".strip()


class ComparedValue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=200)


class ProductDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    parameter: str = Field(min_length=1, max_length=120)
    values: list[ComparedValue] = Field(min_length=2, max_length=6)
    why_it_matters: str = Field(min_length=1, max_length=700)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=400)


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    comparable: bool
    differences: list[ProductDifference] = Field(default_factory=list, max_length=6)
    missing_for_decision: list[str] = Field(default_factory=list, max_length=6)
    recommendation: Recommendation | None = None
    deciding_question: str | None = Field(default=None, max_length=300)


def _tokens(text: str) -> list[str]:
    """Разбить строку на сопоставимые токены.

    Регистр и пунктуация значения не несут: модель законно пишет «PN 20» там,
    где в карточке «PN20».
    """

    lowered = text.lower().replace("ё", "е")
    return [token for token in re.split(r"[^a-zа-я0-9]+", lowered) if token]


def _token_is_grounded(token: str, card_tokens: set[str]) -> bool:
    """Проверить словесный токен с поправкой на сокращения в карточке.

    В карточке написано «арм. стекл.», и модель, разворачивая это в
    «армированная стекловолокном», не выдумывает, а расшифровывает. Поэтому
    засчитываем совпадение, когда один токен является началом другого и общая
    часть достаточно длинная, чтобы не склеить разные слова.
    """

    if token in card_tokens:
        return True
    if len(token) < 3:
        # Короткие хвосты («мм», «м») ничего не подтверждают и не опровергают:
        # требовать их наличия значило бы отклонять корректные формулировки.
        return True
    return any(
        (token.startswith(other) or other.startswith(token))
        and min(len(token), len(other)) >= 4
        for other in card_tokens
        if not other.isdigit()
    )


def _value_is_grounded(value: str, card_tokens: set[str], card_blob: str) -> bool:
    """Проверить, что значение действительно взято из карточки.

    Числа проверяются иначе, чем слова, и это не придирка. Токены «pn» и «25»
    по отдельности есть в карточке «PP-FIBER арм. стекл., PN 20, 25 MM»: «pn»
    от класса давления, «25» от диаметра. Поэлементная проверка пропустила бы
    «PN 25» для трубы PN 20 — то есть показала бы покупателю чужой класс
    давления. Поэтому значение с цифрой требуем целиком и подряд, а слова
    по-прежнему сверяем с поправкой на сокращения.
    """

    tokens = _tokens(value)
    if not tokens:
        return False
    if any(character.isdigit() for character in value):
        # Цифра проверяется по слитому виду карточки, а не по отдельным
        # токенам: «PN 20» и «PN20» — одно и то же значение, а «PN 25» на
        # трубе «PN 20, 25 MM» — уже другое.
        return "".join(tokens) in card_blob
    return all(_token_is_grounded(token, card_tokens) for token in tokens)


class ProductComparatorAgent:
    """Объясняет разницу между показанными позициями.

    Табличное сравнение по общим атрибутам фид вытягивает плохо: у половины
    позиций нужного поля просто нет, а важное различие сидит в названии
    («арм. стекловолокном» против «арм. алюминием»). Поэтому значения
    по-прежнему берутся из карточек и проверяются кодом, а модель отвечает за
    то, что эти значения означают для задачи покупателя.
    """

    def __init__(self, llm_client: OpenRouterClient) -> None:
        self.llm_client = llm_client
        self.last_llm_used = False
        self.last_rejection_reason: str | None = None
        self.last_dropped_differences = 0

    def compare(
        self,
        cards: list[ProductCard],
        *,
        task_summary: str | None = None,
        customer_message: str | None = None,
    ) -> str | None:
        self.last_rejection_reason = None
        self.last_llm_used = False
        self.last_dropped_differences = 0

        if len(cards) < 2:
            self.last_rejection_reason = "less_than_two_cards"
            return None

        payload = {
            "задача_покупателя": task_summary or "не названа",
            "последнее_сообщение": customer_message or "",
            "карточки": [self._card_payload(card) for card in cards],
        }
        data, used = self.llm_client.complete_json(
            "ProductComparatorAgent",
            [
                {"role": "system", "content": PRODUCT_COMPARATOR_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
            {},
        )
        self.last_llm_used = used
        if not used or not data:
            self.last_rejection_reason = "llm_unavailable"
            return None

        result = self._validate(data, cards)
        if result is None:
            return None
        return self._render(result, cards)

    @staticmethod
    def _card_payload(card: ProductCard) -> dict[str, Any]:
        return {
            "sku": card.sku,
            "название": card.name,
            "бренд": card.brand,
            "цена": card.price,
            "валюта": card.currency,
            "наличие": card.stock_status,
            "остаток": card.stock_qty,
            "характеристики": card.characteristics,
        }

    @staticmethod
    def _card_index(card: ProductCard) -> tuple[set[str], str]:
        """Вернуть токены карточки и её склеенный вид для проверки чисел."""

        parts = [card.name, card.brand or "", card.stock_status, str(card.price)]
        for key, value in card.characteristics.items():
            parts.append(f"{key} {value}")
        tokens = _tokens(" ".join(parts))
        return set(tokens), "".join(tokens)

    def _validate(
        self,
        data: dict[str, Any],
        cards: list[ProductCard],
    ) -> ComparisonResult | None:
        try:
            result = ComparisonResult.model_validate(data)
        except ValidationError as exc:
            self.last_rejection_reason = f"schema: {exc.error_count()} ошибок"
            return None

        if not result.comparable:
            self.last_rejection_reason = "model_declined_comparison"
            return None

        known = {card.sku: self._card_index(card) for card in cards}
        in_stock = {
            card.sku
            for card in cards
            if "нет" not in card.stock_status.lower()
        }

        grounded: list[ProductDifference] = []
        for difference in result.differences:
            if any(value.sku not in known for value in difference.values):
                # Артикул не из входного набора — модель придумала позицию.
                # Это не «слабое различие», а выдумка: отклоняем всё сравнение.
                self.last_rejection_reason = "unknown_sku"
                return None
            if all(
                _value_is_grounded(value.value, *known[value.sku])
                for value in difference.values
            ):
                grounded.append(difference)
            else:
                self.last_dropped_differences += 1

        if not grounded:
            self.last_rejection_reason = "no_grounded_differences"
            return None
        result.differences = grounded

        if result.recommendation is not None:
            sku = result.recommendation.sku
            if sku not in known:
                self.last_rejection_reason = "recommended_unknown_sku"
                return None
            if sku not in in_stock:
                # Рекомендовать позицию без остатка нельзя: покупатель пойдёт
                # оформлять то, чего нет. Сравнение при этом остаётся полезным.
                result.recommendation = None
        return result

    @staticmethod
    def _render(result: ComparisonResult, cards: list[ProductCard]) -> str:
        names = {card.sku: card.name for card in cards}
        lines = ["Сравниваю по карточкам товаров:"]
        for difference in result.differences:
            values = "; ".join(
                f"{names.get(value.sku, value.sku)} — {value.value}"
                for value in difference.values
            )
            lines.append(f"• {difference.parameter}: {values}.")
            lines.append(f"  {difference.why_it_matters}")
        if result.missing_for_decision:
            lines.append(
                "В карточках не указано: "
                + ", ".join(result.missing_for_decision)
                + ". Эти параметры сверьте по паспорту изделия."
            )
        if result.recommendation is not None:
            name = names.get(
                result.recommendation.sku,
                result.recommendation.sku,
            )
            lines.append(f"Под вашу задачу — {name}: {result.recommendation.reason}")
        elif result.deciding_question:
            lines.append(result.deciding_question)
        return "\n".join(lines)
