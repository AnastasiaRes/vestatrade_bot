#!/usr/bin/env python3
"""Exercise every current feed product through protected V2 Preview.

This is a catalogue-coverage QA harness, not a replacement for buyer
dialogues.  Each product gets an isolated, direct question for price and
availability through the same /chat contract as the widget.  It checkpoints
the complete customer-visible exchange after each product and checks the
returned card against the feed snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "widget_v2_full_feed_roles_2026-08-30" / "feed100_sweep"
DEFAULT_CACHE = ROOT / "app" / "data" / "products_cache.json"

_HOLDOUT_MESSAGES = (
    "Проверьте, пожалуйста, позицию {sku}: стоимость и остаток.",
    "По артикулу {sku} нужна карточка: сколько стоит и доступна ли сейчас?",
    "Товар {sku}: есть сейчас на складе? Нужна также цена.",
    "Покажите карточку {sku} со стоимостью и наличием.",
)


def _fingerprint(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return digest.translate(str.maketrans("0123456789abcdef", "ghijklmnopqrstuv"))


def _post(
    base_url: str,
    token: str,
    *,
    session_id: str,
    client_turn_id: str,
    message: str,
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat",
        data=json.dumps(
            {
                "session_id": session_id,
                "client_turn_id": client_turn_id,
                "message": message,
                "qa_mode": "v2_preview",
            }
        ).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Dialogue-QA-Token": token,
        },
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
            "error": exc.read().decode("utf-8", "replace")[:1000],
        }
    except Exception as exc:  # noqa: BLE001 - checkpoint every test failure
        return {
            "ok": False,
            "http_status": None,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _load_traces(path: Path) -> dict[str, dict[str, Any]]:
    traces: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return traces
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace = json.loads(line)
        except json.JSONDecodeError:
            continue
        fingerprint = str(trace.get("session_fingerprint") or "")
        if fingerprint:
            traces[fingerprint] = trace
    return traces


def _trace_excerpt(trace: dict[str, Any] | None) -> dict[str, Any]:
    if trace is None:
        return {"found": False}
    cutover = trace.get("cutover_v2") or {}
    decision = cutover.get("decision") or {}
    fact_events = trace.get("passport_events") or []
    return {
        "found": True,
        "owner": decision.get("owner_candidate"),
        "execution_mode": decision.get("execution_mode"),
        "action": ((trace.get("v2_next_action") or {}).get("primary") or {}).get("kind"),
        "semantic_status": (trace.get("turn_understanding") or {}).get("status"),
        "embedding_calls": len(trace.get("embedding_calls") or []),
        "passport_events": len(fact_events),
    }


def _card_matches(product: dict[str, Any], expected: dict[str, Any]) -> bool:
    return (
        str(product.get("sku") or "") == str(expected["sku"])
        and product.get("price") == expected.get("price")
        and str(product.get("currency") or "") == str(expected.get("currency") or "")
        and str(product.get("stock_status") or "") == str(expected.get("stock_status") or "")
        and str(product.get("url") or "") == str(expected.get("url") or "")
    )


def _checkpoint(path: Path, result: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "responses.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _message_for(*, sku: str, index: int, variant: str) -> str:
    if variant == "standard":
        return f"Какая цена и наличие у товара {sku}?"
    return _HOLDOUT_MESSAGES[(index - 1) % len(_HOLDOUT_MESSAGES)].format(sku=sku)


def _render(result: dict[str, Any]) -> str:
    rows = result["rows"]
    latencies = [float(row["result"].get("latency_sec") or 0) for row in rows]
    ok = [row for row in rows if row["result"].get("ok")]
    exact = [row for row in rows if row["checks"].get("exact_card")]
    owners = Counter(
        str((row.get("telemetry") or {}).get("owner") or "unknown") for row in rows
    )
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[str(row["expected"].get("category_path") or "без категории")].append(row)
    p95 = 0.0
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[int(round(0.95 * (len(ordered) - 1)))]
    lines = [
        "# V2 Preview · полный SKU-прогон feed100",
        "",
        f"Дата: {result['created_at']}",
        "Маршрут: `/chat` в `v2_preview` — тот же контракт, что использует виджет.",
        "QA-токен и ключи провайдера в этот отчёт не записываются.",
        "",
        "## Сводка",
        "",
        f"- позиций feed: {len(rows)}",
        f"- HTTP-успех: {len(ok)}/{len(rows)}",
        f"- карточка точного SKU совпала с feed snapshot: {len(exact)}/{len(rows)}",
        f"- владельцы ответов: `{json.dumps(dict(sorted(owners.items())), ensure_ascii=False)}`",
        f"- средняя задержка: {statistics.mean(latencies):.1f} с; P95: {p95:.1f} с" if latencies else "- задержка: нет данных",
        "",
        "## Покрытие категорий",
        "",
        "| Категория фида | Позиций | Точная карточка |",
        "|---|---:|---:|",
    ]
    for category, category_rows in sorted(categories.items()):
        lines.append(
            f"| {category} | {len(category_rows)} | "
            f"{sum(item['checks'].get('exact_card', False) for item in category_rows)} |"
        )
    lines.extend(["", "# Полные однократные диалоги", ""])
    for index, row in enumerate(rows, start=1):
        expected = row["expected"]
        result_item = row["result"]
        response = result_item.get("response") or {}
        lines.extend(
            [
                f"## {index:03d}. {expected['sku']} · {expected['name']}",
                "",
                f"_Категория: {expected.get('category_path') or 'не указана'}_",
                "",
                f"**П:** {row['message']}",
                "",
            ]
        )
        if result_item.get("ok"):
            lines.extend([f"**Б:** {str(response.get('answer') or '').strip()}", ""])
            cards = response.get("products") or []
            if cards:
                lines.extend(
                    [
                        "<sub>карточки: "
                        + "; ".join(
                            f"{card.get('sku')} — {card.get('price')} {card.get('currency')} — {card.get('stock_status')}"
                            for card in cards
                        )
                        + "</sub>",
                        "",
                    ]
                )
        else:
            lines.extend([f"**ОШИБКА:** {result_item.get('error')}", ""])
        diagnostic = {
            "latency_sec": result_item.get("latency_sec"),
            "checks": row["checks"],
            "telemetry": row.get("telemetry") or {},
        }
        lines.extend([f"<sub>{json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)}</sub>", ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--telemetry-path", type=Path)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--message-variant",
        choices=("standard", "holdout"),
        default="standard",
        help="Use alternate natural-language SKU questions without changing checks.",
    )
    args = parser.parse_args()

    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")
    feed = json.loads(args.cache.read_text(encoding="utf-8"))
    if not isinstance(feed, list):
        raise SystemExit("product cache must be a list")
    products = feed[: args.limit] if args.limit else feed
    run_id = uuid.uuid4().hex[:10]
    output = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "base_url": args.base_url.rstrip("/"),
        "feed_cache": str(args.cache),
        "message_variant": args.message_variant,
        "rows": [],
    }
    for index, product in enumerate(products, start=1):
        expected = {
            key: product.get(key)
            for key in ("sku", "name", "category_path", "price", "currency", "stock_status", "url")
        }
        sku = str(expected["sku"] or "")
        session_id = f"feed100-{run_id}-{index:03d}"
        message = _message_for(sku=sku, index=index, variant=args.message_variant)
        result = _post(
            args.base_url,
            token,
            session_id=session_id,
            client_turn_id=f"{session_id}-t01",
            message=message,
            timeout=args.timeout,
        )
        cards = (result.get("response") or {}).get("products") or []
        exact_cards = [card for card in cards if str(card.get("sku") or "") == sku]
        row = {
            "index": index,
            "session_id": session_id,
            "message": message,
            "expected": expected,
            "result": result,
            "checks": {
                "http_ok": bool(result.get("ok")),
                "exact_card": any(_card_matches(card, expected) for card in exact_cards),
                "unexpected_skus": [
                    str(card.get("sku") or "") for card in cards if str(card.get("sku") or "") != sku
                ],
            },
        }
        output["rows"].append(row)
        print(
            f"[{index:03d}/{len(products):03d}] {'OK' if result.get('ok') else 'ERROR'} "
            f"{result.get('latency_sec', 0):5.1f}s {sku} "
            f"exact_card={row['checks']['exact_card']}",
            flush=True,
        )
        _checkpoint(args.output_dir, output)
        if args.pause:
            time.sleep(args.pause)

    if args.telemetry_path:
        traces = _load_traces(args.telemetry_path)
        for row in output["rows"]:
            row["telemetry"] = _trace_excerpt(traces.get(_fingerprint(row["session_id"])))
    _checkpoint(args.output_dir, output)
    (args.output_dir / "report.md").write_text(_render(output), encoding="utf-8")
    print(f"REPORT: {args.output_dir / 'report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
