"""Нарезка паспортов и поиск по ним.

Замер показал, что слабое звено — не генерация, а поиск: неверно найденный
кусок будет добросовестно процитирован и пройдёт все проверки достоверности.
Поэтому тесты закрепляют то, что этот замер выявил: нарезка ничего не теряет,
фильтр по документу применяется, индекс не переживает смену модели.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.passport_chunks import chunk_pages
from app.passport_retrieval import (
    KEYWORD_WEIGHT,
    PassportIndex,
    build_index,
    expand_query,
    load_or_build,
)


def _pages() -> list[str]:
    header = "ПАСПОРТ. РУКОВОДСТВО ПО ЭКСПЛУАТАЦИИ ГОСТ Р 2.601-2019"
    return [
        f"{header}\n1.1. Насосы предназначены для систем отопления.\n"
        "1.2. В качестве рабочей среды может использоваться вода и "
        "гликолесодержащие жидкости.",
        f"{header}\n5.3. Рекомендуется устанавливать насос в обратную магистраль.\n"
        "5.5. Насос следует устанавливать так, чтобы вал двигателя находился "
        "в горизонтальном положении.",
        f"{header}\n7.2. Выпуск воздуха следует производить один раз в полгода.",
    ]


def test_repeated_page_header_is_removed() -> None:
    # Колонтитул повторяется на каждой странице и делает все куски похожими:
    # для поиска по смыслу это чистый шум.
    chunks = chunk_pages(_pages(), "test.pdf")

    assert chunks
    assert not any("ГОСТ Р 2.601-2019" in chunk.text for chunk in chunks)


def test_no_content_is_dropped() -> None:
    # Абзац про гликолевые жидкости раньше исчезал из корпуса целиком, потому
    # что кусок отбрасывался по длине.
    chunks = chunk_pages(_pages(), "test.pdf")
    joined = " ".join(chunk.text for chunk in chunks)

    for marker in ("гликолесодержащие", "обратную магистраль", "горизонтальном", "полгода"):
        assert marker in joined, marker


def test_long_block_without_sentences_is_split() -> None:
    # Таблица — один «абзац» без точек. Без принудительной резки она остаётся
    # куском на тысячи знаков, где нужная строка тонет.
    table = "Характеристика " + " ".join(f"{n} значение{n}" for n in range(400))
    chunks = chunk_pages([table], "table.pdf")

    assert chunks
    assert max(len(chunk.text) for chunk in chunks) < 1000


def test_synonyms_expand_the_query() -> None:
    expanded = expand_query("Можно ли залить антифриз?")

    assert "гликолевый" in expanded
    assert expand_query("Что такое DN?") == "Что такое DN?"


class _FakeEmbedder:
    """Вектор из трёх чисел: длина, доля цифр, наличие слова «насос».

    Настоящая модель здесь не нужна — проверяется механика индекса, а не
    качество эмбеддингов.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [
            [
                len(text) / 1000.0,
                sum(ch.isdigit() for ch in text) / (len(text) or 1),
                1.0 if "насос" in text.lower() else 0.0,
            ]
            for text in texts
        ]


def _index(tmp_path: Path, model: str = "fake-1") -> PassportIndex:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    return build_index([docs], _FakeEmbedder(), model)


def test_document_filter_restricts_the_search() -> None:
    chunks = chunk_pages(_pages(), "pump.pdf") + chunk_pages(
        ["3.1. Трубы поставляются отрезками по два метра."], "pipe.pdf"
    )
    index = PassportIndex(chunks, None, None)

    hits = index.search("отрезки труб", documents=["pipe.pdf"])

    assert hits
    assert {hit.chunk.document for hit in hits} == {"pipe.pdf"}


def test_missing_document_falls_back_to_the_whole_corpus() -> None:
    # Пустая выдача хуже широкой: у товара может не быть паспорта.
    chunks = chunk_pages(_pages(), "pump.pdf")
    index = PassportIndex(chunks, None, None)

    hits = index.search("обратная магистраль", documents=["нет-такого.pdf"])

    assert hits


def test_index_is_rebuilt_when_the_embedding_model_changes(tmp_path: Path) -> None:
    # Векторы разных моделей несравнимы, а размерности различаются. Молча
    # использовать чужой индекс — значит превратить поиск в шум.
    chunks = chunk_pages(_pages(), "pump.pdf")
    index = PassportIndex(chunks, [[1.0, 0.0, 0.0]] * len(chunks), "model-a")

    restored = PassportIndex.from_payload(index.to_payload(), "model-a")
    assert restored is not None
    assert restored.has_vectors

    assert PassportIndex.from_payload(index.to_payload(), "model-b") is None


def test_vectors_survive_a_save_and_load_cycle() -> None:
    chunks = chunk_pages(_pages(), "pump.pdf")
    vectors = [[0.1 * i, 0.2, 0.3] for i in range(len(chunks))]
    index = PassportIndex(chunks, vectors, "model-a")

    restored = PassportIndex.from_payload(
        json.loads(json.dumps(index.to_payload())), "model-a"
    )

    assert restored is not None
    assert len(restored.vectors or []) == len(chunks)
    assert restored.vectors[0][1] == pytest_approx(0.2)


def pytest_approx(value: float, tolerance: float = 1e-6):
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return abs(float(other) - value) < tolerance  # type: ignore[arg-type]

    return _Approx()


def test_index_without_embeddings_still_searches(tmp_path: Path) -> None:
    # Провайдер может быть недоступен. Поиск по словам должен работать и тогда.
    chunks = chunk_pages(_pages(), "pump.pdf")
    index = PassportIndex(chunks, None, None)

    hits = index.search("горизонтальное положение вала")

    assert hits
    assert not index.has_vectors


def test_cache_is_reused_and_not_recomputed(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    cache = tmp_path / "index.json"
    embedder = _FakeEmbedder()

    load_or_build(cache, [docs], embedder, "fake-1")
    calls_after_build = embedder.calls
    load_or_build(cache, [docs], embedder, "fake-1")

    assert cache.exists()
    assert embedder.calls == calls_after_build


def test_keyword_weight_keeps_exact_designations_usable() -> None:
    # Вес не нулевой намеренно: на точных обозначениях векторы размывают то,
    # что должно совпасть буквально.
    assert 0 < KEYWORD_WEIGHT < 0.5
