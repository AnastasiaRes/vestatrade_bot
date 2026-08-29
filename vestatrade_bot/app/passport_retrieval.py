"""Поиск по паспортам: гибрид слов и векторов с фильтром по документу.

Замер на четырнадцати вопросах, заданных словами покупателя, дал такую
картину попаданий нужного куска в выдачу:

    слова, топ-3, весь корпус                        3 / 14
    векторы + фильтр по документу, топ-3             9 / 14
    то же, после переделки нарезки                  10 / 14
    векторы + фильтр + топ-5 + синонимы             13 / 14

Отсюда три решения, заложенные в модуль.

*Фильтр по документу* — сильнейший сигнал, и он бесплатный: паспорт уже
привязан к товару, и при разговоре о насосе искать надо в его паспорте, а не
среди 1379 кусков всего каталога.

*Гибрид* — методы падают по-разному. Векторы вытягивают перефразирование
(«ставить вертикально» против «вал в горизонтальном положении»), слова —
точные обозначения (``PN 25``, ``М30х1,5``, ``DN``), где векторы размывают
именно то, что должно совпасть буквально.

*Словарь синонимов* — разрыв словаря векторы не перекрывают: «антифриз»
против «гликолевых растворов» стоял на девятнадцатом месте из сорока четырёх.
Это отраслевое знание, которого у модели нет.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
from array import array
from dataclasses import dataclass
from pathlib import Path

from app.passport_chunks import Chunk, chunk_pages

logger = logging.getLogger(__name__)

INDEX_VERSION = 1
# Топ-8, а не топ-5: на проверочном наборе два нужных куска стояли на седьмом
# месте. Три лишних куска — это около полутора тысяч знаков контекста для
# модели, что на фоне вызова LLM ничего не стоит.
TOP_K = 8

# Вес словесной части в гибриде. На проверочном наборе из четырнадцати
# перефразированных вопросов векторы сильнее: 13 попаданий против 10, и выше
# 0,2 словесная часть начинает мешать. Но набор не содержит вопросов с точными
# обозначениями («PN 25», «М30х1,5», артикул), а это ровно тот случай, где
# векторы размывают то, что должно совпасть буквально. Поэтому вес не нулевой:
# он держит эту способность, оставаясь нейтральным на перефразировании.
KEYWORD_WEIGHT = 0.2

# Слова покупателя против слов паспорта. Список короткий намеренно: это
# отраслевые синонимы, а не попытка описать язык.
SYNONYMS: dict[str, str] = {
    "антифриз": "гликолевый раствор незамерзающая жидкость",
    "незамерзайка": "гликолевый раствор",
    "обратка": "обратная магистраль",
    "подача": "подающая магистраль",
    "теплоноситель": "рабочая среда",
    "шумит": "шум звукоизоляция",
    "шумный": "шум звукоизоляция",
    "заклинило": "заклинивание накипь вал",
    "заклинил": "заклинивание накипь вал",
    "перегрелся": "перегрев тепловая защита обмотка",
    "перегреется": "перегрев тепловая защита обмотка",
    "потечет": "рабочая температура класс эксплуатации",
    "потечёт": "рабочая температура класс эксплуатации",
    "выдержит": "рабочая температура рабочее давление класс эксплуатации",
    "гарантия": "срок службы гарантийные обязательства",
    "промыть": "промывка очистка",
    "вертикально": "положение вала монтаж",
    "горизонтально": "положение вала монтаж",
}

_WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}")
# Точные обозначения, где буквальное совпадение важнее смысла.
_EXACT_RE = re.compile(
    r"\b(?:pn|dn|sdr|kvs|ip|м\s?\d{2}[хx]\d(?:[.,]\d)?)\s*\d*|\b\d{2,3}[/-]\d{1,3}\b",
    re.IGNORECASE,
)
# Доля цифр, при которой кусок перестаёт быть текстом и становится строкой
# таблицы. Такой кусок не может ответить на вопрос словами, но исправно
# набирает вес и вытесняет осмысленные: на вопрос о стандарте труб HT первым
# выдавался обрывок «90 2,2 105 58 110 2,7…».
_TABLE_DIGIT_SHARE = 0.22
_TABLE_PENALTY = 0.45


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def expand_query(question: str) -> str:
    """Дописать к запросу отраслевые синонимы, если они в нём узнаются."""

    lowered = question.lower()
    extra = [value for key, value in SYNONYMS.items() if key in lowered]
    return f"{question} {' '.join(extra)}".strip() if extra else question


def _document_idf(chunks: list[Chunk], indexes: list[int]) -> dict[str, float]:
    """Вес слова по его редкости внутри выборки.

    Внутри паспорта одного изделия его название не различает ничего: слово
    «клапан» стоит в каждом куске. Различают вопрос редкие слова —
    «сервопривод», «утилизация», «запах». Без этой поправки они тонули среди
    общих: нужный кусок про сервопривод стоял шестнадцатым из сорока семи.
    """

    if not indexes:
        return {}
    document_frequency: dict[str, int] = {}
    for index in indexes:
        for token in _tokens(chunks[index].text):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    total = len(indexes)
    return {
        token: math.log(1.0 + total / count)
        for token, count in document_frequency.items()
    }

# Приведение слова к основе перед сравнением.
#
# Без этого совпадений просто нет: в вопросе «чем управляют термостатическим
# клапаном с сервоприводом» и в отвечающем пункте «регулирование… с помощью
# сервопривода… по команде устройства управления» пересечение слов пустое —
# «клапаном» против «клапана», «сервоприводом» против «сервопривода». Словарь
# синонимов такие случаи не закрывает: их не десяток, а всё словоизменение.
#
# Способ выбран грубый намеренно. Список окончаний я написал первым и он давал
# неверные основы: «клапана» превращалось в «клапа», потому что окончание «на»
# съедало часть корня. Полноценный стеммер потребовал бы зависимости; усечение
# до общей части предсказуемо и ошибается в одну сторону — иногда сближает
# разные слова, но не разводит одинаковые. При весе словесной части 0,2 это
# приемлемо.
# Длина основы одна для всех слов: разные пороги для длинных и коротких дают
# артефакт на границе — «насос» и «насосный» расходились по разным основам.
_STEM_LENGTH = 4


def _stem(token: str) -> str:
    if token.isdigit() or len(token) <= _STEM_LENGTH:
        return token
    return token[:_STEM_LENGTH]


def _tokens(text: str) -> set[str]:
    return {_stem(token) for token in _WORD_RE.findall(text.lower())}


def _looks_like_table(text: str) -> bool:
    """Кусок состоит в основном из чисел и не содержит предложений."""

    if not text:
        return False
    digits = sum(character.isdigit() for character in text)
    if digits / len(text) < _TABLE_DIGIT_SHARE:
        return False
    # Одно-два предложения среди чисел — это подпись к таблице, она полезна.
    return text.count(".") + text.count(";") < len(text) / 120


def _query_wants_numbers(question: str) -> bool:
    """Спрашивают ли значение: тогда строка таблицы и есть ответ."""

    return bool(re.search(r"\d", question) or _EXACT_RE.search(question))


def _keyword_score(
    question: str,
    chunk_text: str,
    idf: dict[str, float] | None = None,
) -> float:
    question_tokens = _tokens(question)
    if not question_tokens:
        return 0.0
    chunk_tokens = _tokens(chunk_text)
    shared = question_tokens & chunk_tokens
    if idf:
        weight = sum(idf.get(token, 1.0) for token in shared)
        normaliser = math.sqrt(sum(idf.get(token, 1.0) for token in question_tokens)) or 1.0
        overlap = weight / normaliser
    else:
        overlap = len(shared) / math.sqrt(len(question_tokens))
    # Точное обозначение весит больше обычного слова: покупатель, назвавший
    # «PN 25», спрашивает именно про него.
    exact = {match.group(0).lower().replace(" ", "") for match in _EXACT_RE.finditer(question)}
    if exact:
        found = sum(
            1 for token in exact if token in chunk_text.lower().replace(" ", "")
        )
        overlap += 1.5 * found
    return overlap


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _encode(vectors: list[list[float]]) -> str:
    flat = array("f", (value for vector in vectors for value in vector))
    return base64.b64encode(flat.tobytes()).decode("ascii")


def _decode(blob: str, dimension: int) -> list[list[float]]:
    flat = array("f")
    flat.frombytes(base64.b64decode(blob))
    return [
        list(flat[start : start + dimension])
        for start in range(0, len(flat), dimension)
    ]


class PassportIndex:
    """Куски паспортов вместе с их векторами.

    Индекс хранит имя модели эмбеддингов. Векторы разных моделей несравнимы, а
    размерности различаются (1536 у text-embedding-3-small, 1024 у bge-m3),
    поэтому при смене провайдера индекс пересобирается, а не используется
    молча.
    """

    def __init__(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]] | None,
        model: str | None,
        source_digest: str | None = None,
    ) -> None:
        self.chunks = chunks
        self.vectors = vectors
        self.model = model
        self.source_digest = source_digest

    @property
    def has_vectors(self) -> bool:
        return bool(self.vectors) and len(self.vectors or []) == len(self.chunks)

    def search(
        self,
        question: str,
        *,
        documents: list[str] | None = None,
        query_vector: list[float] | None = None,
        limit: int = TOP_K,
    ) -> list[Hit]:
        """Найти куски, отвечающие на вопрос.

        ``documents`` сужает поиск до паспортов конкретных товаров — это
        сильнейший из доступных сигналов, и без него выдача заметно хуже.
        """

        expanded = expand_query(question)
        candidates = [
            index
            for index, chunk in enumerate(self.chunks)
            if not documents or chunk.document in documents
        ]
        # A requested product scope is a hard evidence boundary.  Falling back
        # to the whole corpus when a scoped document is absent can return a
        # perfectly verified quote from another product.
        if not candidates and documents:
            return []
        if not candidates:
            candidates = list(range(len(self.chunks)))

        idf = _document_idf(self.chunks, candidates)
        keyword = {
            index: _keyword_score(expanded, self.chunks[index].text, idf)
            for index in candidates
        }
        best_keyword = max(keyword.values(), default=0.0) or 1.0

        vector_score: dict[int, float] = {}
        if query_vector is not None and self.has_vectors:
            unit = _normalize(query_vector)
            for index in candidates:
                row = self.vectors[index]  # type: ignore[index]
                vector_score[index] = sum(a * b for a, b in zip(row, unit))

        # Штраф табличным обрывкам снимается, когда покупатель спрашивает
        # именно значение: «какая толщина стенки у 32-й трубы» отвечается
        # строкой таблицы, а «по какому стандарту» — нет.
        penalise_tables = not _query_wants_numbers(expanded)

        scored: list[Hit] = []
        for index in candidates:
            # Обе шкалы приводятся к 0..1, иначе одна подавляет другую просто
            # из-за разного масштаба.
            weight = KEYWORD_WEIGHT if vector_score else 1.0
            score = weight * (keyword[index] / best_keyword)
            if vector_score:
                score += (1.0 - weight) * max(0.0, vector_score[index])
            if penalise_tables and _looks_like_table(self.chunks[index].text):
                score *= _TABLE_PENALTY
            scored.append(Hit(chunk=self.chunks[index], score=score))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:limit]

    def to_payload(self) -> dict:
        return {
            "version": INDEX_VERSION,
            "model": self.model,
            "source_digest": self.source_digest,
            "dimension": len(self.vectors[0]) if self.has_vectors else 0,
            "chunks": [
                {
                    "document": chunk.document,
                    "text": chunk.text,
                    "section": chunk.section,
                    "ordinal": chunk.ordinal,
                }
                for chunk in self.chunks
            ],
            "vectors": _encode(self.vectors) if self.has_vectors else "",
        }

    @classmethod
    def from_payload(
        cls,
        payload: dict,
        expected_model: str,
        expected_source_digest: str | None = None,
    ) -> "PassportIndex | None":
        if payload.get("version") != INDEX_VERSION:
            return None
        if payload.get("model") != expected_model:
            logger.info(
                "Индекс паспортов собран моделью %s, сейчас настроена %s — пересобираю",
                payload.get("model"),
                expected_model,
            )
            return None
        if (
            expected_source_digest is not None
            and payload.get("source_digest") != expected_source_digest
        ):
            logger.info("Набор паспортов изменился — пересобираю индекс")
            return None
        chunks = [
            Chunk(
                document=item["document"],
                text=item["text"],
                section=item["section"],
                ordinal=item["ordinal"],
            )
            for item in payload.get("chunks", [])
        ]
        dimension = int(payload.get("dimension") or 0)
        blob = payload.get("vectors") or ""
        vectors = _decode(blob, dimension) if blob and dimension else None
        if vectors is not None and len(vectors) != len(chunks):
            return None
        return cls(
            chunks,
            vectors,
            payload.get("model"),
            source_digest=payload.get("source_digest"),
        )


def _source_digest(docs_dirs: list[Path]) -> str:
    """Fingerprint the local PDF corpus without reading every file body.

    The cache is local to this checkout, so a stable manifest of resolved path,
    size and nanosecond mtime is sufficient and cheap to recompute per request.
    """

    entries: list[str] = []
    for directory in docs_dirs:
        root = Path(directory)
        if not root.exists():
            continue
        mapping: dict[str, dict] = {}
        map_path = root / "product_docs_map.json"
        if map_path.exists():
            try:
                value = json.loads(map_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    mapping = value
            except (OSError, json.JSONDecodeError):
                mapping = {}
        for path in sorted(root.iterdir()):
            if path.suffix.lower() != ".pdf" and path.name != "product_docs_map.json":
                continue
            if (
                path.suffix.lower() == ".pdf"
                and (mapping.get(path.name) or {}).get("enabled") is False
            ):
                continue
            stat = path.stat()
            entries.append(
                f"{path.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}"
            )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def read_chunks(docs_dirs: list[Path]) -> list[Chunk]:
    from pypdf import PdfReader

    chunks: list[Chunk] = []
    for directory in docs_dirs:
        root = Path(directory)
        if not root.exists():
            continue
        mapping: dict[str, dict] = {}
        map_path = root / "product_docs_map.json"
        if map_path.exists():
            try:
                value = json.loads(map_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    mapping = value
            except (OSError, json.JSONDecodeError):
                mapping = {}
        for path in sorted(root.iterdir()):
            if path.suffix.lower() != ".pdf":
                continue
            if (mapping.get(path.name) or {}).get("enabled") is False:
                continue
            try:
                text_mode = str(
                    (mapping.get(path.name) or {}).get("pdf_text_mode") or "plain"
                )
                reader = PdfReader(str(path))
                if text_mode == "layout":
                    pages = [
                        (page.extract_text(extraction_mode="layout") or "")
                        for page in reader.pages
                    ]
                else:
                    pages = [(page.extract_text() or "") for page in reader.pages]
            except Exception as exc:  # pragma: no cover - защита от битого PDF
                logger.warning("Не удалось прочитать %s: %s", path.name, exc)
                continue
            chunks.extend(chunk_pages(pages, path.name))
    return chunks


def build_index(
    docs_dirs: list[Path],
    embed,
    model: str,
    source_digest: str | None = None,
) -> PassportIndex:
    """Собрать индекс. Без эмбеддингов остаётся поиск по словам."""

    chunks = read_chunks(docs_dirs)
    vectors = embed([chunk.text for chunk in chunks]) if chunks else None
    if vectors is not None and len(vectors) == len(chunks):
        vectors = [_normalize(vector) for vector in vectors]
    else:
        if chunks and vectors is not None:
            logger.warning("Векторов %s против %s кусков — индекс без векторов",
                           len(vectors), len(chunks))
        vectors = None
    return PassportIndex(
        chunks,
        vectors,
        model if vectors else None,
        source_digest=source_digest,
    )


def load_or_build(
    cache_path: Path,
    docs_dirs: list[Path],
    embed,
    model: str,
) -> PassportIndex:
    source_digest = _source_digest(docs_dirs)
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            index = PassportIndex.from_payload(
                payload,
                model,
                expected_source_digest=source_digest,
            )
            if index is not None:
                return index
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("Индекс паспортов повреждён (%s) — пересобираю", exc)
    index = build_index(
        docs_dirs,
        embed,
        model,
        source_digest=source_digest,
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(index.to_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - кэш не критичен
        logger.warning("Не удалось сохранить индекс паспортов: %s", exc)
    return index
