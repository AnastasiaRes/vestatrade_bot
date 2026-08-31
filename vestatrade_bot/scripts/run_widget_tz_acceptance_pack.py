#!/usr/bin/env python3
"""Record the fixed V2 Preview acceptance pack through the real /chat route.

This is evaluation-only tooling.  It deliberately makes no production changes;
each dialogue has its own session and preserves the full API response in the
report directory so acceptance is reviewed from evidence rather than text
matching alone.
"""

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
DEFAULT_OUTPUT = ROOT / "reports" / "widget_v2_tz_acceptance_2026-08-31" / "acceptance_pack" / "live_dialogues"

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "exact_sku",
        "purpose": "Точный SKU должен сразу разрешаться в карточку.",
        "turns": ("Покажите, пожалуйста, VT.228.N.04: цену, наличие и ссылку.",),
    },
    {
        "id": "named_model_stock",
        "purpose": "Явно названная модель и проверка количества без нового подбора.",
        "turns": (
            "Покажите электрический котёл Arderia E9: цену, наличие и ссылку.",
            "А есть 2 шт?",
            "А есть 3 шт?",
        ),
    },
    {
        "id": "broad_pipe",
        "purpose": "Широкая труба требует назначения, а не случайной категории.",
        "turns": ("Нужна труба.",),
    },
    {
        "id": "sewer",
        "purpose": "Канализация не должна угадывать внутреннее/наружное назначение или длину.",
        "turns": (
            "Нужна канализационная труба 50.",
            "От дома до септика, снаружи.",
            "Длина 3 метра. Покажите варианты.",
        ),
    },
    {
        "id": "cheap_pump",
        "purpose": "Цена меняет порядок только среди технически допустимых насосов.",
        "turns": (
            "Нужен циркуляционный насос подешевле для радиаторного отопления: расход полтора куба в час, напор четыре метра.",
            "Покажите варианты.",
        ),
    },
    {
        "id": "boiler_area",
        "purpose": "Площадь даёт лишь предварительный ориентир, не инженерное подтверждение.",
        "turns": (
            "Нужен электрокотёл для дома 100 квадратов.",
            "Только отопление, без горячей воды.",
            "Покажите варианты.",
        ),
    },
    {
        "id": "valve",
        "purpose": "Кран получает только предметные уточнения до выдачи.",
        "turns": (
            "Нужен кран.",
            "Шаровый, для воды, прямой 1/2, обе резьбы внутренние.",
            "Покажите варианты.",
        ),
    },
    {
        "id": "radiator_valve",
        "purpose": "Радиаторная арматура не угадывает форму, размер и термоузел.",
        "turns": (
            "Нужна радиаторная арматура.",
            "Прямая, 1/2, с термоголовкой.",
            "Покажите варианты.",
        ),
    },
    {
        "id": "boiler_components",
        "purpose": "Комплектность отвечает только из карточки/паспорта или честно ограничивается.",
        "turns": ("В котле Arderia E9 есть встроенный насос и расширительный бак?",),
    },
    {
        "id": "shown_link",
        "purpose": "Ссылка после выдачи относится к уже показанной карточке.",
        "turns": (
            "Нужен циркуляционный насос для радиаторной системы: расход 1,5 м3/ч, напор 4 м.",
            "Покажите варианты.",
            "Дай ссылку на второй вариант.",
        ),
    },
    {
        "id": "small_talk_then_product",
        "purpose": "Неформальная реплика не ломает последующий товарный запрос.",
        "turns": ("Здравствуйте!", "Нужен насос для радиаторного отопления: расход 1,5 м3/ч, напор 4 м."),
    },
    {
        "id": "engineering_boundary",
        "purpose": "Сложный инженерный расчёт не превращается в выдуманную другую категорию.",
        "turns": ("Рассчитайте гидравлическое сопротивление двухтрубной системы для дома 250 м².",),
    },
)


def _post(base_url: str, token: str, session_id: str, turn_id: str, message: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat",
        data=json.dumps(
            {"session_id": session_id, "client_turn_id": turn_id, "message": message, "qa_mode": "v2_preview"}
        ).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Dialogue-QA-Token": token},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "ok": True,
                "http_status": response.status,
                "latency_sec": round(time.monotonic() - started, 3),
                "response": json.loads(response.read().decode("utf-8")),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "http_status": exc.code,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": exc.read().decode("utf-8", "replace")[:1200],
        }
    except Exception as exc:  # pragma: no cover - evaluation records transport failures
        return {"ok": False, "latency_sec": round(time.monotonic() - started, 3), "error": f"{type(exc).__name__}: {exc}"}


def _write(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "responses.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# V2 Preview · фиксированный acceptance pack", "", f"Дата: {result['created_at']}", ""]
    for scenario in result["scenarios"]:
        lines.extend((f"## {scenario['id']}", "", scenario["purpose"], ""))
        for turn in scenario["turns"]:
            lines.extend((f"**П:** {turn['message']}", ""))
            outcome = turn["result"]
            if outcome.get("ok"):
                response = outcome.get("response") or {}
                lines.extend((f"**Б:** {str(response.get('answer') or '').strip()}", ""))
                cards = response.get("products") or []
                if cards:
                    lines.extend(("<sub>товары: " + "; ".join(f"{card.get('sku')} {card.get('name')} — {card.get('url')}" for card in cards) + "</sub>", ""))
            else:
                lines.extend((f"**ОШИБКА:** {outcome.get('error')}", ""))
            lines.extend((f"<sub>latency: {outcome.get('latency_sec')} с</sub>", ""))
    (output_dir / "report.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--pause", type=float, default=0.35)
    args = parser.parse_args()
    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")
    run_id = uuid.uuid4().hex[:10]
    result: dict[str, Any] = {"created_at": datetime.now(timezone.utc).astimezone().isoformat(), "scenarios": []}
    for number, definition in enumerate(SCENARIOS, start=1):
        session_id = f"tz-acceptance-{run_id}-{number:02d}"
        scenario = {"id": definition["id"], "purpose": definition["purpose"], "session_id": session_id, "turns": []}
        result["scenarios"].append(scenario)
        for turn_number, message in enumerate(definition["turns"], start=1):
            scenario["turns"].append(
                {
                    "turn": turn_number,
                    "message": message,
                    "result": _post(args.base_url, token, session_id, f"{session_id}-t{turn_number}", message, args.timeout),
                }
            )
            _write(args.output_dir, result)
            time.sleep(args.pause)
    _write(args.output_dir, result)
    print(f"REPORT: {args.output_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
