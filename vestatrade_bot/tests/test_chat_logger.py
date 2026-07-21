from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app import chat_logger as chat_logger_module
from app.chat_logger import ChatLogger
from app.models import ChatResponse


def _response() -> ChatResponse:
    return ChatResponse(session_id="logger-test", answer="Ответ")


def test_session_log_names_do_not_collide_after_sanitizing_or_truncating(tmp_path) -> None:
    logger = ChatLogger(tmp_path)
    now = datetime(2026, 7, 20, 12, 0, 0)

    slash = logger._session_file("a/b", now)
    question = logger._session_file("a?b", now)
    long_a = logger._session_file("x" * 200 + "a", now)
    long_b = logger._session_file("x" * 200 + "b", now)

    assert slash != question
    assert long_a != long_b
    for path in [slash, question, long_a, long_b]:
        assert re.fullmatch(r"[A-Za-z0-9_-]+--[0-9a-f]{64}\.md", path.name)
        assert len(path.name.encode("utf-8")) < 255


def test_concurrent_turns_append_once_without_interleaving_or_duplicate_header(tmp_path) -> None:
    logger = ChatLogger(tmp_path)
    total = 40

    def write(index: int) -> None:
        logger.log_turn("same/session", f"turn-{index:02d}", _response())

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(write, range(total)))

    files = list(tmp_path.rglob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert content.count("# Диалог same/session") == 1
    assert content.count("Клиент:**") == total
    assert content.count("Бот:** Ответ") == total
    assert content.count("---") == total
    for index in range(total):
        assert content.count(f"turn-{index:02d}") == 1


def test_windows_lock_backend_does_not_require_fcntl(monkeypatch, tmp_path) -> None:
    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        def locking(self, _fd: int, mode: int, size: int) -> None:
            self.calls.append((mode, size))

    backend = FakeMsvcrt()
    monkeypatch.setattr(chat_logger_module, "_fcntl", None)
    monkeypatch.setattr(chat_logger_module, "_msvcrt", backend)

    transcript = tmp_path / "windows-session.md"
    with chat_logger_module._interprocess_file_lock(transcript):
        assert transcript.with_suffix(".md.lock").read_bytes() == b"\0"

    assert backend.calls == [(backend.LK_NBLCK, 1), (backend.LK_UNLCK, 1)]
