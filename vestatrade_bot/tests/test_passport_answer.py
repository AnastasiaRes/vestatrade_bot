"""Проверки, через которые проходит ответ из паспорта.

Ценность не в том, что модель что-то ответила, а в том, что непроверяемое до
покупателя не доходит. Тесты закрепляют границу: что принимается, что
отклоняется и почему.
"""

from __future__ import annotations

from typing import Any

from app.agents.passport_answer import PassportAnswerAgent
from app.passport_chunks import Chunk


CLAUSES = [
    "5.5. Насос следует устанавливать так, чтобы вал двигателя находился в "
    "горизонтальном положении.",
    "7.2. Процедуру выпуска воздуха следует производить один раз в полгода.",
    "4.4. Насосы снабжены устройством защиты от перегрева. При превышении "
    "температуры обмотки статора 150°С отключается электропитание насоса.",
]


def _chunks() -> list[Chunk]:
    return [
        Chunk(document="VRS-0725.pdf", text=text, section=f"пункт {i}", ordinal=i)
        for i, text in enumerate(CLAUSES, start=1)
    ]


class _StubLLM:
    def __init__(self, payload: dict[str, Any] | None, used: bool = True) -> None:
        self.payload = payload
        self.used = used

    def complete_json(self, _agent, _messages, _fallback):
        return (self.payload or {}), self.used


def _reply(**overrides: Any) -> dict[str, Any]:
    payload = {
        "answerable": True,
        "excerpt": 1,
        "quote": "вал двигателя находился в горизонтальном положении",
        "framing": "Вертикально ставить нельзя.",
    }
    payload.update(overrides)
    return payload


def test_verbatim_quote_is_shown_with_its_source() -> None:
    agent = PassportAnswerAgent(_StubLLM(_reply()))

    result = agent.answer("Можно ли ставить насос вертикально?", _chunks())

    assert result is not None
    answer, chunk = result
    assert "горизонтальном положении" in answer
    assert "VRS-0725.pdf" in answer
    assert chunk.section == "пункт 1"


def test_invented_quote_is_rejected() -> None:
    # Главная защита: фрагмента нет ни в одной переданной выдержке.
    agent = PassportAnswerAgent(
        _StubLLM(_reply(quote="насос допускается ставить в любом положении"))
    )

    assert agent.answer("Можно ли ставить вертикально?", _chunks()) is None
    assert agent.last_rejection_reason == "quote_not_verbatim"


def test_wrong_excerpt_number_is_corrected_not_refused() -> None:
    # Модель цитирует верно, но путает номер выдержки. Гарантия в том, что
    # фрагмент дословно есть среди переданного, а ссылку можно восстановить.
    agent = PassportAnswerAgent(_StubLLM(_reply(excerpt=3)))

    result = agent.answer("Можно ли ставить вертикально?", _chunks())

    assert result is not None
    _, chunk = result
    assert chunk.section == "пункт 1"
    assert agent.corrected_excerpts == 1


def test_number_absent_from_the_quote_is_rejected() -> None:
    # Ровно тот случай, ради которого проверка и написана: модель добавляет
    # правдоподобную цифру от себя.
    agent = PassportAnswerAgent(
        _StubLLM(
            _reply(
                excerpt=2,
                quote="Процедуру выпуска воздуха следует производить один раз в полгода",
                framing="Выпускайте воздух раз в 6 месяцев, то есть дважды в год.",
            )
        )
    )

    assert agent.answer("Как часто выпускать воздух?", _chunks()) is None
    assert (agent.last_rejection_reason or "").startswith("number_not_in_quote")


def test_number_present_in_the_quote_passes() -> None:
    agent = PassportAnswerAgent(
        _StubLLM(
            _reply(
                excerpt=3,
                quote="При превышении температуры обмотки статора 150°С отключается "
                "электропитание насоса",
                framing="При 150°С насос отключается сам.",
            )
        )
    )

    assert agent.answer("Что при перегреве?", _chunks()) is not None


def test_price_claim_is_rejected() -> None:
    agent = PassportAnswerAgent(
        _StubLLM(_reply(framing="Такой насос стоит около 4000 руб."))
    )

    assert agent.answer("Можно ли ставить вертикально?", _chunks()) is None
    assert agent.last_rejection_reason == "commerce_claim"


def test_designation_from_nowhere_is_rejected() -> None:
    agent = PassportAnswerAgent(
        _StubLLM(_reply(framing="Вместо него подойдёт VRS.999.18.0."))
    )

    assert agent.answer("Можно ли ставить вертикально?", _chunks()) is None
    assert (agent.last_rejection_reason or "").startswith("unknown_designation")


def test_designation_from_the_question_is_allowed() -> None:
    # Обозначение изделия — предмет разговора, а не коммерческое обещание:
    # оно стоит и в вопросе покупателя, и в цитате.
    agent = PassportAnswerAgent(
        _StubLLM(_reply(framing="Насос VRS.254.18.0 ставят валом горизонтально."))
    )

    assert agent.answer("Как ставить VRS.254.18.0?", _chunks()) is not None


def test_model_may_report_that_nothing_answers() -> None:
    # Отказ — нормальный исход, а не сбой.
    agent = PassportAnswerAgent(
        _StubLLM({"answerable": False, "excerpt": 0, "quote": "", "framing": ""})
    )

    assert agent.answer("Какая завтра погода?", _chunks()) is None
    assert agent.last_rejection_reason == "model_found_no_answer"


def test_broken_schema_is_rejected() -> None:
    agent = PassportAnswerAgent(_StubLLM({"answerable": True, "лишнее": 1}))

    assert agent.answer("Вопрос", _chunks()) is None
    assert (agent.last_rejection_reason or "").startswith("schema")


def test_unavailable_model_is_not_an_answer() -> None:
    agent = PassportAnswerAgent(_StubLLM(None, used=False))

    assert agent.answer("Вопрос", _chunks()) is None
    assert agent.last_rejection_reason == "llm_unavailable"


def test_empty_candidate_list_is_refused_without_calling_the_model() -> None:
    agent = PassportAnswerAgent(_StubLLM(_reply()))

    assert agent.answer("Вопрос", []) is None
    assert agent.last_rejection_reason == "no_candidates"
    assert agent.last_llm_used is False


def test_changed_digit_is_rejected_even_with_spacing_allowed() -> None:
    # Граница держится кодом, а не промптом: сверка игнорирует пробелы и
    # только их. Изменённая цифра — это подмена значения, и она не пройдёт.
    agent = PassportAnswerAgent(
        _StubLLM(
            _reply(
                excerpt=3,
                quote="При превышении температуры обмотки статора 160°С отключается "
                "электропитание насоса",
                framing="При 160°С насос отключается сам.",
            )
        )
    )

    assert agent.answer("Что при перегреве?", _chunks()) is None
    assert agent.last_rejection_reason == "quote_not_verbatim"


def test_changed_letter_is_rejected() -> None:
    agent = PassportAnswerAgent(
        _StubLLM(_reply(quote="вал двигателя находился в вертикальном положении"))
    )

    assert agent.answer("Как ставить?", _chunks()) is None
    assert agent.last_rejection_reason == "quote_not_verbatim"


def test_pdf_spacing_does_not_break_the_verbatim_check() -> None:
    # Извлечение PDF расставляет пробелы непредсказуемо: «перерыв ов», «пр и».
    # Требовать посимвольного совпадения значило бы отклонять верные цитаты.
    chunks = [
        Chunk(
            document="VRS-0725.pdf",
            text="7.5. Во время длительных перерыв ов в эксплуатации насос "
            "рекомендуется включать.",
            section="пункт 7.5",
            ordinal=1,
        )
    ]
    agent = PassportAnswerAgent(
        _StubLLM(
            _reply(
                quote="Во время длительных перерывов в эксплуатации насос "
                "рекомендуется включать",
                framing="Простой требует периодических включений.",
            )
        )
    )

    assert agent.answer("Что делать при простое?", chunks) is not None
