"""Ответ на вопрос о товаре цитатой из паспорта.

Роль модели здесь узкая намеренно: она выбирает пункт среди переданных и
вырезает из него дословный фрагмент. Ответ покупателю не сочиняется — он
собирается из цитаты производителя и одной поясняющей фразы.

Так сделано потому, что свободный пересказ уже дважды дал уверенную неправду:
на паре труб модель заявила, что стекловолокно термостойче алюминия, хотя
паспорта говорят обратное. Проверить пересказ нечем, а покупатель понесёт его
в магазин.

Четыре проверки перед показом, все механические:

1. Цитата дословно встречается в указанном пункте.
2. Указан пункт из числа переданных, а не выдуманный.
3. Нет цен, артикулов и обещаний наличия: у паспорта таких данных нет.
4. Каждое число из пояснения есть в цитате — это ловит ровно тот случай, когда
   модель добавляет правдоподобную цифру от себя.

Порядок проверок влияет только на то, какая причина отказа попадёт в
диагностику. Коммерческая идёт раньше числовой намеренно: «стоит 4000 руб»
содержит и цифру, и цену, но назвать это отказом по цене точнее.

Не прошло — возвращается ``None``, и вызывающий код показывает честный отказ.
Отказ дешевле неправды.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.openrouter_client import OpenRouterClient
from app.passport_chunks import Chunk


PASSPORT_ANSWER_PROMPT = """
Ты — инженер-консультант компании Vesta Trading. Покупатель задал вопрос о
товаре, и тебе дали выдержки из паспорта этого товара. Твоя задача — найти
среди них ту, что отвечает на вопрос, и вырезать из неё точную цитату.

ТЫ НЕ ПИШЕШЬ ОТВЕТ. Ответ покупателю соберут из твоей цитаты. От тебя нужны
три вещи: номер подходящей выдержки, дословный фрагмент из неё и одна фраза,
объясняющая цитату простыми словами.

КАК ВЫБИРАТЬ ВЫДЕРЖКУ
Выдержки отсортированы по близости к вопросу: первая — самая вероятная.
Начинай с неё и бери другую только тогда, когда она отвечает точнее.

Подходит та, где ответ содержится прямо. Выдержка «про ту же тему» не годится:
на вопрос о температуре хранения не подходит пункт о температуре рабочей
среды, а на вопрос о положении насоса — пункт о повороте клеммной коробки.

Прежде чем выбрать, сформулируй для себя: какими словами эта выдержка отвечает
на заданный вопрос? Если ответить нечем — это не та выдержка.

Если ни одна не отвечает — верни answerable=false. Это нормальный исход, а не
неудача.

КАК ВЫРЕЗАТЬ ЦИТАТУ
Копируй из выбранной выдержки: те же слова, те же числа, те же единицы.
Ничего не переписывай и не сокращай внутри фразы — цитату сверяют с документом.
Бери законченную мысль: одно-два предложения, а не обрывок.

Разрешено ровно одно исправление: склеить слово, разорванное пробелом при
извлечении из PDF. «пр и исправном» — это «при исправном», «перерыв ов» — это
«перерывов», «темпера туре» — «температуре». В самом паспорте таких разрывов
нет, они появились при чтении файла, и показывать их покупателю значит
показывать чужую ошибку вместо текста производителя.

Больше ничего менять нельзя. Ни одной буквы, ни одной цифры, ни одной
единицы измерения. Если кажется, что в паспорте опечатка в числе — это не
опечатка, это значение, и оно уйдёт покупателю как есть.

КАК ПИСАТЬ ПОЯСНЕНИЕ
Одна фраза обычными словами: что цитата означает для покупателя.
В пояснении не должно быть ни одного числа, которого нет в цитате. Если
хочется назвать цифру — значит, её надо было включить в цитату.
Не повторяй в пояснении номер модели или мощность из вопроса, если их нет в
цитате. Если безопасное пояснение не требуется, оставь framing пустым.
Без вводных «хороший вопрос» и «согласно документации». Сразу по делу.

ЧЕГО НЕЛЬЗЯ НИКОГДА
- Не добавляй фактов из своих знаний. Даже верных: их нечем проверить.
- Не называй цены, артикулы, наличие и сроки поставки — в паспорте их нет.
- Не обещай совместимость с другим товаром.
- Не объединяй цитаты из разных выдержек.

ФОРМАТ ОТВЕТА
Строго JSON, без markdown и текста вокруг:
{
  "answerable": true|false,
  "excerpt": <номер выдержки, целое число>,
  "why": "чем именно эта выдержка отвечает на вопрос",
  "quote": "дословный фрагмент из этой выдержки",
  "framing": "одна фраза простыми словами"
}
Поле why заполняется первым и объясняет выбор: оно нужно, чтобы проверить
себя, а покупателю не показывается.
При answerable=false поля excerpt, quote, why и framing оставь пустыми
(excerpt: 0, остальные "").

ПРИМЕР
Вопрос: «Можно ли ставить насос вертикально?»
Выдержка 1: «5.5. Насос следует устанавливать так, чтобы вал двигателя
находился в горизонтальном положении.»
Выдержка 4: «5.11. Кожух электродвигателя с клеммной коробкой может быть
переустановлен в любое удобное положение.»
{"answerable": true, "excerpt": 1, "why": "прямо задаёт требуемое положение
вала при установке", "quote": "Насос следует устанавливать так, чтобы вал
двигателя находился в горизонтальном положении.", "framing": "Вертикально
ставить нельзя: паспорт требует горизонтального вала."}
Выдержка 4 тоже про положение, но про поворот кожуха, а не про установку
насоса — она на вопрос не отвечает.
""".strip()


class PassportAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answerable: bool
    excerpt: int = Field(default=0, ge=0)
    # Обоснование выбора. Покупателю не показывается: оно нужно, чтобы модель
    # проверила себя, — без него она берёт правдоподобную выдержку вместо
    # отвечающей. На вопросе о вертикальной установке так был выбран пункт о
    # клеммной коробке, хотя пункт о горизонтальном вале стоял первым.
    why: str = Field(default="", max_length=300)
    quote: str = Field(default="", max_length=600)
    framing: str = Field(default="", max_length=400)


class VerifiedPassportEvidence(BaseModel):
    """Structured result kept behind the existing passport-answer API.

    Legacy callers still receive the same rendered string from ``answer``.
    V2 can additionally consume this immutable, source-preserving result
    instead of parsing prose back into a fact.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    quote: str
    framing: str = ""
    document: str
    section: str
    ordinal: int = Field(ge=0)


# Паспорт не содержит коммерческих данных, поэтому их появление означает, что
# модель добавила от себя.
_COMMERCE_RE = re.compile(
    r"\bруб|\brub\b|₽|в наличии|на складе|\bарт\.|\bцена\b|\bскидк|"
    r"\bдостав|\bсрок поставки",
    re.IGNORECASE,
)
_SKU_RE = re.compile(r"\b[A-Za-z]{2,4}[.\-][A-Za-z0-9.\-]{3,}\b")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _normalise(text: str) -> str:
    """Свести к виду, в котором цитату можно искать в выдержке.

    Извлечение PDF расставляет пробелы непредсказуемо — «перерыв ов», «пр и», —
    и требовать посимвольного совпадения значило бы отклонять верные цитаты.
    Поэтому пробелы при сверке не учитываются, а всё остальное учитывается.
    """

    normalized = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    # PDF extractors and LLMs can render the same decimal separator according
    # to different locales.  Treat only punctuation between digits as format;
    # changed digits still fail the verbatim evidence check.
    normalized = re.sub(r"(?<=\d)[,.](?=\d)", ".", normalized)
    return re.sub(r"\s+", "", normalized)


class PassportAnswerAgent:
    """Собирает ответ из цитаты паспорта под контролем четырёх проверок."""

    def __init__(self, llm_client: OpenRouterClient) -> None:
        self.llm_client = llm_client
        self.last_rejection_reason: str | None = None
        self.last_framing_drop_reason: str | None = None
        self.last_llm_used = False
        self.last_verified_evidence: VerifiedPassportEvidence | None = None
        # Сколько раз пришлось поправить номер выдержки: если растёт, значит
        # список кандидатов стоит подавать иначе.
        self.corrected_excerpts = 0

    def answer(
        self,
        question: str,
        chunks: list[Chunk],
        context: str | None = None,
    ) -> tuple[str, Chunk] | None:
        """Вернуть текст ответа и пункт-источник либо ``None``."""

        self.last_rejection_reason = None
        self.last_framing_drop_reason = None
        self.last_llm_used = False
        self.last_verified_evidence = None
        if not chunks:
            self.last_rejection_reason = "no_candidates"
            return None

        table_answer = self._deterministic_arderia_power_answer(
            question,
            chunks,
            context=context,
        )
        if table_answer is not None:
            return table_answer

        listing = "\n\n".join(
            f"Выдержка {number} (паспорт {chunk.document}, {chunk.section}):\n{chunk.text}"
            for number, chunk in enumerate(chunks, start=1)
        )
        data, used = self.llm_client.complete_json(
            "PassportAnswerAgent",
            [
                {"role": "system", "content": PASSPORT_ANSWER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        # Диалог нужен для местоимений: на «можно ли его ставить
                        # вертикально» без контекста непонятно, о чём речь.
                        (f"О чём разговор: {context}\n\n" if context else "")
                        + f"Вопрос покупателя: {question}\n\n{listing}"
                    ),
                },
            ],
            {},
        )
        self.last_llm_used = used
        if not used or not data:
            self.last_rejection_reason = "llm_unavailable"
            return None

        verified = self._verify(data, chunks, question)
        if verified is None:
            return None
        answer, chunk = verified
        return answer, chunk

    def _verify(
        self,
        data: dict[str, Any],
        chunks: list[Chunk],
        question: str = "",
    ) -> tuple[str, Chunk] | None:
        try:
            parsed = PassportAnswer.model_validate(data)
        except ValidationError as exc:
            self.last_rejection_reason = f"schema: {exc.error_count()} ошибок"
            return None

        if not parsed.answerable:
            self.last_rejection_reason = "model_found_no_answer"
            return None

        # Проверка 1: цитата дословна и взята из переданного материала.
        #
        # Номер выдержки модель иногда путает — цитирует первую, а указывает
        # вторую. Отклонять из-за этого верную цитату незачем: гарантия в том,
        # что фрагмент дословно есть среди переданного, а ссылку можно
        # восстановить поиском. Выдумка так всё равно не пройдёт — её нет ни в
        # одной выдержке.
        if not parsed.quote:
            self.last_rejection_reason = "empty_quote"
            return None
        needle = _normalise(parsed.quote)
        matching = [
            index
            for index, candidate in enumerate(chunks)
            if needle in _normalise(candidate.text)
        ]
        if not matching:
            self.last_rejection_reason = "quote_not_verbatim"
            return None

        # Проверка 2: источником называется выдержка, где цитата и правда есть.
        stated = parsed.excerpt - 1
        chunk = chunks[stated] if stated in matching else chunks[matching[0]]
        if stated not in matching:
            self.corrected_excerpts += 1

        # Проверка 3: коммерческих обещаний в паспорте нет.
        #
        # Обозначение изделия — не коммерческое обещание. Оно стоит и в
        # вопросе покупателя, и в цитате: «кран VT.226 не подходит для
        # соединения с накидной гайкой» — законный ответ. Отклоняется только
        # обозначение, взявшееся ниоткуда, и слова про цену и наличие, которых
        # в паспорте нет вовсе.
        if _COMMERCE_RE.search(parsed.framing):
            self.last_rejection_reason = "commerce_claim"
            return None
        known = f"{parsed.quote} {question}".lower()
        framing_without_designations = parsed.framing
        for designation in _SKU_RE.findall(parsed.framing):
            if designation.lower() not in known:
                self.last_rejection_reason = f"unknown_designation: {designation}"
                return None
            # Цифры внутри обозначения — часть имени изделия, а не факт о нём:
            # «VRS.254.18.0» иначе провалит числовую проверку, хотя ничего не
            # утверждает.
            framing_without_designations = framing_without_designations.replace(
                designation, " "
            )

        # Проверка 4: числа пояснения содержатся в цитате.
        quote_numbers = {
            number.replace(",", ".") for number in _NUMBER_RE.findall(parsed.quote)
        }
        for number in _NUMBER_RE.findall(framing_without_designations):
            if number.replace(",", ".") not in quote_numbers:
                # The quote itself is already proven verbatim.  Do not throw
                # away useful evidence because the optional explanation added
                # a number; drop the entire explanation so the unproved value
                # can never reach the customer.
                self.last_framing_drop_reason = f"number_not_in_quote: {number}"
                parsed = parsed.model_copy(update={"framing": ""})
                break

        self.last_verified_evidence = VerifiedPassportEvidence(
            quote=parsed.quote.strip().strip('«»"'),
            framing=parsed.framing,
            document=chunk.document,
            section=chunk.section,
            ordinal=chunk.ordinal,
        )
        return self._render(parsed, chunk), chunk

    def _deterministic_arderia_power_answer(
        self,
        question: str,
        chunks: list[Chunk],
        *,
        context: str | None,
    ) -> tuple[str, Chunk] | None:
        """Read one model column from the verified Arderia E-series table.

        The PDF extractor flattens the table into one row.  Asking an LLM to
        reconstruct the column mapping made it quote the heading and stop just
        before the values.  Here the mapping is mechanical: model and minimum
        power arrays are read from the same source chunk and matched by index.
        """

        request = _normalise(f"{question} {context or ''}")
        if "миним" not in request or "мощн" not in request:
            return None
        model_match = re.search(r"(?:arderia)?e(4|6|9|12|16|20|24)\b", request)
        if not model_match:
            return None
        requested_model = model_match.group(1)

        for chunk in chunks:
            if chunk.document != "Руководство_электрические_котлы_ARDERIA_2023.pdf":
                continue
            text = " ".join(chunk.text.split())
            models_match = re.search(
                r"Модель\s+((?:E\d+\s+){2,}E\d+)",
                text,
                re.IGNORECASE,
            )
            minimum_match = re.search(
                r"мин\.{0,2}\s*((?:\d+(?:[.,]\d+)?\s+){2,}\d+(?:[.,]\d+)?)",
                text,
                re.IGNORECASE,
            )
            if not models_match or not minimum_match:
                continue
            models = re.findall(r"E(\d+)", models_match.group(1), re.IGNORECASE)
            values = re.findall(r"\d+(?:[.,]\d+)?", minimum_match.group(1))
            if requested_model not in models:
                continue
            column = models.index(requested_model)
            if column >= len(values):
                continue
            value = values[column]
            quote_start = models_match.start()
            quote_end = minimum_match.end()
            quote = text[quote_start:quote_end]
            framing = f"Минимальная мощность модели E{requested_model} — {value} кВт."
            parsed = PassportAnswer(
                answerable=True,
                excerpt=1,
                why="модель и строка минимальной мощности сопоставлены по одной колонке таблицы",
                quote=quote,
                framing=framing,
            )
            self.last_verified_evidence = VerifiedPassportEvidence(
                quote=quote,
                framing=framing,
                document=chunk.document,
                section=chunk.section,
                ordinal=chunk.ordinal,
            )
            return self._render(parsed, chunk), chunk
        return None

    @staticmethod
    def _render(parsed: PassportAnswer, chunk: Chunk) -> str:
        quote = parsed.quote.strip().strip('«»"')
        parts = [f"По паспорту: «{quote}»"]
        if parsed.framing:
            parts.append(parsed.framing.strip())
        source = chunk.section.rstrip(".")
        parts.append(f"Источник: {chunk.document}, {source}.")
        return " ".join(parts)
