"""Сохранение переписок с ботом на диск.

Каждая сессия пишется в свой Markdown-файл:
app/data/chat_logs/<ГГГГ-ММ-ДД>/<session_id>.md (каталог настраивается
через CHAT_LOGS_DIR). Файл пополняется по ходу диалога, его можно открывать
и читать в любой момент.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from app.models import ChatResponse


logger = logging.getLogger(__name__)

_SAFE_SESSION_RE = re.compile(r"[^a-zA-Z0-9_-]")


class ChatLogger:
    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = logs_dir

    def _session_file(self, session_id: str, now: datetime) -> Path:
        # session_id приходит от клиента — оставляем только безопасные символы,
        # чтобы им нельзя было управлять путём на диске.
        safe_id = _SAFE_SESSION_RE.sub("_", session_id)[:64] or "session"
        day_dir = self.logs_dir / now.strftime("%Y-%m-%d")
        return day_dir / f"{safe_id}.md"

    def log_turn(self, session_id: str, user_message: str, response: ChatResponse) -> None:
        try:
            now = datetime.now()
            path = self._session_file(session_id, now)
            path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not path.exists()
            with path.open("a", encoding="utf-8") as fh:
                if is_new:
                    fh.write(
                        f"# Диалог {session_id}\n\n"
                        f"Начат: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    )
                stamp = now.strftime("%H:%M:%S")
                fh.write(f"**[{stamp}] Клиент:** {user_message.strip()}\n\n")
                fh.write(f"**[{stamp}] Бот:** {response.answer.strip()}\n\n")
                if response.products:
                    skus = ", ".join(product.sku for product in response.products)
                    fh.write(f"_Показанные товары: {skus}_\n\n")
                if response.need_handoff:
                    fh.write("_Передано менеджеру._\n\n")
                fh.write("---\n\n")
        except OSError as exc:
            logger.warning("Не удалось сохранить переписку %s: %s", session_id, exc)
