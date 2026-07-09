"""Стресс-прогон диалогов против живого API.

Проверяет, что бот ведёт себя как консультант и не «тупит» на нестандартных
репликах: учитывает отрицание («газа нет»), не зацикливает один и тот же вопрос,
не выдумывает товары/цены (граундинг) и рекомендует, когда данных достаточно.

Запуск (сервер должен быть поднят: uvicorn app.main:app):
    .venv/bin/python scripts/stress_dialogs.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request

API = "http://127.0.0.1:8000"


def chat(session_id: str, message: str) -> dict:
    data = json.dumps({"session_id": session_id, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def feed_skus_and_prices() -> tuple[set[str], set[int]]:
    health = json.loads(urllib.request.urlopen(f"{API}/health", timeout=30).read())
    _ = health
    data = json.load(open("app/data/products_cache.json", encoding="utf-8"))
    skus = {p["sku"].lower().replace(" ", "") for p in data}
    prices = {round(p["price"]) for p in data if p.get("price") is not None}
    return skus, prices


GAS_BOILER_SKUS = {"2201375", "2201376", "2201377", "3301679"}


# (label, [сообщения], [инварианты])
SCENARIOS: list[tuple[str, list[str], list[str]]] = [
    (
        "Отрицание газа",
        ["привет", "мне нужно отопление", "газа нет, 200 квадратов", "а что по электрическим?"],
        ["no_gas_boiler_after_no_gas", "grounded", "no_repeat"],
    ),
    (
        "КАПС-фрустрация про газ",
        ["котёл на 200 м2", "ГАЗА НЕЕЕТ", "ну так что посоветуешь?"],
        ["no_gas_boiler_after_no_gas", "grounded", "no_repeat"],
    ),
    (
        "Помоги выбрать всё",
        ["помоги выбрать всё и составить список", "дом 240 м2, газ и электричество", "что в итоге?"],
        ["grounded", "no_repeat", "recommends"],
    ),
    (
        "Тёплый пол + давай все",
        [
            "я хочу провести теплые полы, сориентируй что нужно",
            "давай все",
            "как скажешь, с того и начнем",
        ],
        ["no_repeat", "grounded"],
    ),
    (
        "Целевой сценарий заказчика",
        [
            "Добрый день",
            "Нужно дом построить",
            "240 квадратов, газ и электричество",
            "Котёл хороший? Есть другие?",
            "А насосы есть?",
            "Что в итоге посоветуете?",
        ],
        ["grounded", "no_repeat", "recommends"],
    ),
    (
        "Смена темы",
        ["нужен котёл 100 м2 электрический", "хватит. теперь канализация", "труба 110"],
        ["grounded", "no_repeat"],
    ),
]


def has_gas_boiler(products: list[dict]) -> bool:
    return any(p.get("sku") in GAS_BOILER_SKUS for p in products)


def grounding_ok(answer: str, products: list[dict], skus: set[str], prices: set[int]) -> bool:
    # Любой «артикул XXX» в тексте должен существовать в фиде.
    for m in re.finditer(r"арт(?:икул)?[\s.:№]*([A-Za-zА-Яа-я0-9][\w./\-]{2,})", answer, re.I):
        token = m.group(1).lower().replace(" ", "").rstrip(".,;")
        if token not in skus and not any(token in s or s in token for s in skus):
            return False
    # Любая «цена ₽» >= 100 должна совпасть с реальной ценой из фида.
    for m in re.finditer(r"(\d[\d  .,]{2,})\s*(?:₽|руб|rub)", answer, re.I):
        digits = re.sub(r"[  .,]", "", m.group(1))
        if digits.isdigit() and int(digits) >= 100:
            if not any(abs(int(digits) - pr) <= 10 for pr in prices):
                return False
    return True


def run() -> int:
    skus, prices = feed_skus_and_prices()
    failures = 0
    for label, messages, invariants in SCENARIOS:
        sid = f"stress-{abs(hash(label)) % 100000}"
        print(f"\n===== {label} =====")
        turns = []
        for msg in messages:
            r = chat(sid, msg)
            ans = (r.get("answer") or "").strip()
            prods = r.get("products") or []
            turns.append((msg, ans, prods))
            consult = "ConsultantAgent" in (r.get("debug", {}).get("agents_used") or [])
            print(f"  U: {msg}")
            print(f"  B: {ans[:240].replace(chr(10), ' ')}")
            print(f"     [{'CONSULT' if consult else 'det'}{' skus=' + str([p['sku'] for p in prods]) if prods else ''}]")

        said_no_gas = False
        problems: list[str] = []
        prev_bot = None
        for msg, ans, prods in turns:
            low = msg.lower().replace("ё", "е")
            if any(k in low for k in ["газа нет", "газа неет", "газа нееет", "газа нееет"]) or (
                "газа" in low and "нет" in low
            ):
                said_no_gas = True
            if "no_gas_boiler_after_no_gas" in invariants and said_no_gas and has_gas_boiler(prods):
                problems.append(f"газовый котёл после «газа нет» (на «{msg}»)")
            if "grounded" in invariants and not grounding_ok(ans, prods, skus, prices):
                problems.append(f"граундинг нарушен на «{msg}»")
            if "no_repeat" in invariants and prev_bot is not None and ans == prev_bot and ans:
                problems.append(f"дословный повтор ответа на «{msg}»")
            prev_bot = ans

        if "recommends" in invariants and not any(prods for _, _, prods in turns):
            problems.append("ни одной товарной рекомендации за диалог")

        if problems:
            failures += 1
            print("  ❌ " + "; ".join(problems))
        else:
            print("  ✅ инварианты соблюдены")

    print(f"\n==== Итог: {len(SCENARIOS) - failures}/{len(SCENARIOS)} сценариев чисто ====")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
