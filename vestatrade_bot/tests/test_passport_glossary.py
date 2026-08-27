"""Индекс определений из паспортов.

Ответ покупателю здесь — цитата производителя, поэтому проверяется не только
то, что определение нашлось, но и что оно дословно, привязано к верному
термину и названо своим источником.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.agents.response_composer import ResponseComposerAgent
from app.passport_glossary import (
    build_index,
    find_definition,
    reset_default_index,
)


DATA = Path(__file__).parents[1] / "data"


@lru_cache(maxsize=1)
def _index():
    # Разбор двух десятков PDF занимает секунды: собираем один раз на модуль,
    # иначе набор из дюжины тестов растягивается на минуту с лишним.
    return build_index(DATA)


def test_construction_clause_is_quoted_in_full() -> None:
    definition = _index()["мокрый ротор"]

    assert definition.text.startswith("Конструктивное исполнение")
    assert "охлаждаются перекачиваемой жидкостью" in definition.text
    assert definition.source == "VRS-0725.pdf"


def test_marking_legend_becomes_definitions() -> None:
    index = _index()

    assert "в мм (25,32)" in index["номинальный диаметр dn"].text
    assert "(4;6;8)" in index["максимальный напор"].text
    assert index["монтажная длина"].section == "раздел «Обозначение»"


def test_explanation_column_becomes_a_definition() -> None:
    # Kvs определён в колонке «Пояснение», и единица «1 бар» — часть смысла:
    # без неё «расход при перепаде давления» не значит ничего.
    definition = _index()["kvs"]

    assert definition.text == "Расход при перепаде давления 1 бар."


def test_operating_class_keeps_the_standard_number() -> None:
    assert "ГОСТ 32415-2013" in _index()["класс эксплуатации"].text


def test_every_definition_is_quoted_verbatim() -> None:
    # Единственная гарантия против склейки regexp-групп: текст обязан
    # дословно встречаться в своём документе.
    from pypdf import PdfReader

    index = _index()
    cache: dict[str, str] = {}
    for definition in index.values():
        if definition.source not in cache:
            raw = " ".join(
                (page.extract_text() or "")
                for page in PdfReader(str(DATA / definition.source)).pages
            )
            cache[definition.source] = re.sub(r"\s+", " ", raw)
        assert definition.text.rstrip(".") in cache[definition.source], definition.term


def test_index_has_no_truncated_terms() -> None:
    # Обрубок вроде «номинальный д» означает, что колонка прочитана не до
    # конца, и пара «термин — пояснение» ненадёжна.
    for term in _index():
        last = term.split()[-1]
        assert len(last) >= 3 or last in {"dn", "pn"}, term


def test_lookup_prefers_the_longer_term() -> None:
    index = _index()

    found = find_definition(index, "Что означает номинальный диаметр?")

    assert found is not None
    assert found.term == "номинальный диаметр dn"


def test_short_abbreviation_matches_as_a_whole_word() -> None:
    index = _index()

    assert find_definition(index, "Что такое Kvs у клапана?") is not None
    # «kvs» не должно находиться внутри чужого слова
    assert find_definition(index, "расскажите про kvsмонтаж") is None


def test_unknown_term_is_not_invented() -> None:
    index = _index()

    assert find_definition(index, "Что такое квазифланец?") is None
    assert find_definition(index, "Какая завтра погода?") is None


def test_composer_answers_from_the_passport_and_names_the_source() -> None:
    reset_default_index()
    answer = ResponseComposerAgent().compose_term_consult("Что такое мокрый ротор?")

    assert "смазываются и охлаждаются перекачиваемой жидкостью" in answer
    assert "VRS-0725.pdf" in answer


def test_curated_glossary_still_wins_over_the_passport() -> None:
    # Выверенный вручную глоссарий отвечает мгновенно и точнее подобран под
    # вопрос покупателя, поэтому остаётся первым источником.
    reset_default_index()
    answer = ResponseComposerAgent().compose_term_consult("Что такое американка?")

    assert "разъёмное резьбовое соединение" in answer
    assert "формулировка производителя" not in answer


def test_unknown_term_still_gets_the_honest_refusal() -> None:
    reset_default_index()
    answer = ResponseComposerAgent().compose_term_consult("Что такое квазифланец?")

    assert "не подскажу без проверки" in answer
