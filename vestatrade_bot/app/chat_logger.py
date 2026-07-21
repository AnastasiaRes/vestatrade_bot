"""Сохранение переписок с ботом на диск.

Каждая сессия пишется в свой Markdown-файл:
app/data/chat_logs/<ГГГГ-ММ-ДД>/<safe-prefix>--<session-hash>.md (каталог настраивается
через CHAT_LOGS_DIR). Файл пополняется по ходу диалога, его можно открывать
и читать в любой момент.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from threading import Lock

from app.models import ChatResponse


logger = logging.getLogger(__name__)

_SAFE_SESSION_RE = re.compile(r"[^a-zA-Z0-9_-]")
_FILE_LOCKS: dict[Path, Lock] = {}
_FILE_LOCKS_GUARD = Lock()


def _file_lock(path: Path) -> Lock:
    # Shared by all ChatLogger instances in this process.  A per-instance lock
    # would still allow two app components to interleave writes to one session.
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(path, Lock())


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
            turn = (
                f"**[{stamp}] Клиент:** {user_message.strip()}\n\n"
                f"**[{stamp}] Бот:** {response.answer.strip()}\n\n"
            )
            if response.products:
                skus = ", ".join(product.sku for product in response.products)
                turn += f"_Показанные товары: {skus}_\n\n"
            if response.need_handoff:
                turn += "_Передано менеджеру._\n\n"
            turn += "---\n\n"

            # Header detection and the whole turn append are one critical
            # section, preventing duplicate headers and interleaved Markdown.
            # ``flock`` extends the guarantee to multiple worker processes.
            with _file_lock(path):
                with path.open("a+", encoding="utf-8") as fh:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    try:
                        fh.seek(0, 2)
                        if fh.tell() == 0:
                            fh.write(
                                f"# Диалог {session_id}\n\n"
                                f"Начат: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                            )
                        fh.write(turn)
                        fh.flush()
                    finally:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            logger.warning("Не удалось сохранить переписку %s: %s", session_id, exc)
