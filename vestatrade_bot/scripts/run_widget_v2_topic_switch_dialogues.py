#!/usr/bin/env python3
"""Record live V2 Preview topic-switching dialogues through the widget API."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "widget_v2_full_feed_roles_2026-08-30" / "topic_switches"

DIALOGUES = (
    {
        "title": "Монтажник · насос → цена → кран → возврат к насосу",
        "role": "Знает рабочую точку, но ведёт несколько задач объекта одновременно.",
        "turns": (
            "Нужен циркуляционный насос для радиаторной системы: расход 1,5 м³/ч, напор 4 м.",
            "Покажите варианты.",
            "Сколько стоит второй вариант?",
            "Теперь ещё нужны два крана BASE 1/2 ВР/ВР.",
            "Вернёмся к насосу: какая монтажная длина у первого варианта?",
        ),
    },
    {
        "title": "Новичок · котёл → канализация → возврат к котлу",
        "role": "Не знает терминов и переключается между двумя домашними проблемами.",
        "turns": (
            "Нужен котёл для дома 150 квадратов.",
            "Газовый, только отопление.",
            "Стоп, ещё труба нужна от дома до септика, покажите что есть.",
            "Вернёмся к котлу: горячая вода от него не нужна.",
        ),
    },
)


def _post(base_url: str, token: str, session_id: str, turn_id: str, message: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat",
        data=json.dumps(
            {
                "session_id": session_id,
                "client_turn_id": turn_id,
                "message": message,
                "qa_mode": "v2_preview",
            }
        ).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Dialogue-QA-Token": token},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "ok": True,
                "latency_sec": round(time.monotonic() - started, 3),
                "response": json.loads(response.read().decode("utf-8")),
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "latency_sec": round(time.monotonic() - started, 3), "error": exc.read().decode("utf-8", "replace")[:1000]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_sec": round(time.monotonic() - started, 3), "error": f"{type(exc).__name__}: {exc}"}


def _write(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "responses.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# V2 Preview · смена темы и возврат", "", f"Дата: {result['created_at']}", ""]
    for dialogue in result["dialogues"]:
        lines.extend([f"## {dialogue['title']}", "", f"_{dialogue['role']}_", ""])
        for turn in dialogue["turns"]:
            lines.extend([f"**П:** {turn['message']}", ""])
            if turn["result"].get("ok"):
                response = turn["result"].get("response") or {}
                lines.extend([f"**Б:** {str(response.get('answer') or '').strip()}", ""])
                cards = response.get("products") or []
                if cards:
                    lines.extend(["<sub>товары: " + "; ".join(f"{card.get('sku')} {card.get('name')}" for card in cards) + "</sub>", ""])
            else:
                lines.extend([f"**ОШИБКА:** {turn['result'].get('error')}", ""])
            lines.extend([f"<sub>latency: {turn['result'].get('latency_sec')} с</sub>", ""])
    (output_dir / "report.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")
    result: dict[str, Any] = {"created_at": datetime.now(timezone.utc).astimezone().isoformat(), "dialogues": []}
    run_id = uuid.uuid4().hex[:8]
    for index, definition in enumerate(DIALOGUES, start=1):
        dialogue = {"title": definition["title"], "role": definition["role"], "session_id": f"topic-{run_id}-{index}", "turns": []}
        result["dialogues"].append(dialogue)
        for turn_index, message in enumerate(definition["turns"], start=1):
            response = _post(args.base_url, token, dialogue["session_id"], f"{dialogue['session_id']}-t{turn_index}", message, args.timeout)
            dialogue["turns"].append({"turn": turn_index, "message": message, "result": response})
            _write(args.output_dir, result)
            time.sleep(0.35)
    _write(args.output_dir, result)
    print(f"REPORT: {args.output_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
