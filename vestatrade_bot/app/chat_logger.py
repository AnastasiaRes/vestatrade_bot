"""Сохранение переписок с ботом на диск.

Каждая сессия пишется в свой Markdown-файл:
app/data/chat_logs/<ГГГГ-ММ-ДД>/<safe-prefix>--<session-hash>.md (каталог настраивается
через CHAT_LOGS_DIR). Файл пополняется по ходу диалога, его можно открывать
и читать в любой момент.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterator

try:  # POSIX (Linux/macOS)
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    _fcntl = None

try:  # Windows
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    _msvcrt = None

from app.models import ChatResponse


logger = logging.getLogger(__name__)

_SAFE_SESSION_RE = re.compile(r"[^a-zA-Z0-9_-]")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")
_FILE_LOCKS: dict[Path, Lock] = {}
_FILE_LOCKS_GUARD = Lock()
_WINDOWS_LOCK_TIMEOUT_SECONDS = 5.0
_WINDOWS_LOCK_POLL_SECONDS = 0.01


def _redact_pii(text: str) -> str:
    text = _EMAIL_RE.sub("[email скрыт]", text)
    return _PHONE_RE.sub("[телефон скрыт]", text)


def _file_lock(path: Path) -> Lock:
    # Shared by all ChatLogger instances in this process.  A per-instance lock
    # would still allow two app components to interleave writes to one session.
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(path, Lock())


@contextmanager
def _interprocess_file_lock(path: Path) -> Iterator[None]:
    """Lock one transcript across workers without importing POSIX-only modules.

    A sidecar is used instead of the Markdown file so Windows can always lock a
    one-byte region, including before the transcript itself has been created.
    If neither native backend exists, the process-local lock still protects the
    normal single-worker deployment.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+b") as lock_file:
        if _fcntl is not None:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
            return

        if _msvcrt is not None:
            lock_file.seek(0, 2)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            deadline = time.monotonic() + _WINDOWS_LOCK_TIMEOUT_SECONDS
            while True:
                lock_file.seek(0)
                try:
                    _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(_WINDOWS_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                lock_file.seek(0)
                _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_UNLCK, 1)
            return

        yield


class ChatLogger:
    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = logs_dir

    def _session_file(self, session_id: str, now: datetime) -> Path:
        # The readable prefix is only a hint; the digest preserves the full ID.
        # Thus IDs that sanitize/truncate to the same text (``a/b`` vs ``a?b``
        # or long common prefixes) never share a transcript.
        safe_prefix = _SAFE_SESSION_RE.sub("_", session_id)[:40] or "session"
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        day_dir = self.logs_dir / now.strftime("%Y-%m-%d")
        return day_dir / f"{safe_prefix}--{digest}.md"

    def log_turn(self, session_id: str, user_message: str, response: ChatResponse) -> None:
        try:
            now = datetime.now()
            path = self._session_file(session_id, now)
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = now.strftime("%H:%M:%S")
            safe_user_message = _redact_pii(user_message.strip())
            safe_answer = _redact_pii(response.answer.strip())
            turn = (
                f"**[{stamp}] Клиент:** {safe_user_message}\n\n"
                f"**[{stamp}] Бот:** {safe_answer}\n\n"
            )
            if response.products:
                skus = ", ".join(product.sku for product in response.products)
                turn += f"_Показанные товары: {skus}_\n\n"
            handoff_agent_used = "HandoffAgent" in (response.debug.get("agents_used") or [])
            if (
                response.need_handoff
                and handoff_agent_used
                and response.handoff_status == "locally_recorded"
                and response.handoff_ticket_id
            ):
                turn += (
                    f"_Локальный черновик обращения сохранён: "
                    f"{response.handoff_ticket_id}._\n\n"
                )
            elif response.need_handoff:
                turn += "_Рекомендуется или ожидается передача менеджеру._\n\n"
            turn += "---\n\n"

            # Header detection and the whole turn append are one critical
            # section, preventing duplicate headers and interleaved Markdown.
            # The native sidecar lock extends the guarantee to multiple worker
            # processes on both POSIX and Windows.
            with _file_lock(path):
                with _interprocess_file_lock(path):
                    with path.open("a+", encoding="utf-8") as fh:
                        fh.seek(0, 2)
                        if fh.tell() == 0:
                            fh.write(
                                f"# Диалог {session_id}\n\n"
                                f"Начат: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                            )
                        fh.write(turn)
                        fh.flush()
        except OSError as exc:
            logger.warning("Не удалось сохранить переписку %s: %s", session_id, exc)
