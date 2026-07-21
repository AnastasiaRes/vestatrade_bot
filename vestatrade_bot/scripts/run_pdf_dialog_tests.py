from __future__ import annotations

import json
import os
import re
import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


API_URL = os.getenv("QA_API_URL", "http://127.0.0.1:8000").rstrip("/")
REPORTS_DIR = Path("reports")
RUN_REPORT = Path(
    os.getenv("QA_RUN_REPORT", str(REPORTS_DIR / "vesta_trading_dialog_test_run.md"))
)
ANALYSIS_REPORT = Path(
    os.getenv(
        "QA_ANALYSIS_REPORT",
        str(REPORTS_DIR / "vesta_trading_dialog_test_analysis.md"),
    )
)
USAGE_PATH = Path(os.getenv("USAGE_BUDGET_PATH", "app/data/usage_budget.json"))
PRODUCTS_CACHE_PATH = Path(
    os.getenv("PRODUCTS_CACHE_PATH", "app/data/products_cache.json")
)


def load_catalog() -> dict[str, dict[str, Any]]:
    try:
        rows = json.loads(PRODUCTS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        re.sub(r"[^a-zа-я0-9]", "", str(row.get("sku") or "").casefold()): row
        for row in rows
        if row.get("sku")
    }


CATALOG = load_catalog()


def catalog_provenance() -> dict[str, Any]:
    try:
        payload = PRODUCTS_CACHE_PATH.read_bytes()
        raw_count = len(json.loads(payload.decode("utf-8")))
        digest = hashlib.sha256(payload).hexdigest()
    except Exception:
        raw_count = 0
        digest = "unavailable"
    return {
        "path": str(PRODUCTS_CACHE_PATH.resolve()),
        "raw_count": raw_count,
        "indexed_unique_skus": len(CATALOG),
        "sha256": digest,
    }


def llm_telemetry(results: list[dict[str, Any]]) -> dict[str, Any]:
    debug_rows = [
        turn.get("response", {}).get("debug") or {}
        for item in results
        for turn in item.get("turns", [])
    ]
    requested = sum(bool(row.get("llm_requested")) for row in debug_rows)
    transported = sum(bool(row.get("llm_transport_succeeded")) for row in debug_rows)
    accepted = sum(bool(row.get("llm_output_accepted")) for row in debug_rows)
    if requested and not transported:
        mode = "fallback-only"
    elif transported and not accepted:
        mode = "llm-transport-without-accepted-output"
    elif accepted:
        mode = "live-llm"
    else:
        mode = "deterministic-only"
    return {
        "mode": mode,
        "turns": len(debug_rows),
        "requested": requested,
        "transport_succeeded": transported,
        "output_accepted": accepted,
    }


@dataclass
class Scenario:
    number: int
    title: str
    category: str
    priority: str
    messages: list[str]
    checks: dict[str, Any]


def scenarios() -> list[Scenario]:
    return [
        Scenario(1, "Точный SKU без лишних вопросов", "ссылка", "P0", ["VT.217.N.04", "скинь ссылку"], {"first_sku": "VT.217.N.04", "product_first": True, "url_first": True, "link_later": True}),
        Scenario(2, "Точный SKU насоса сразу в карточку", "насосы", "P0", ["VRS.256.18.0", "есть что подешевле?"], {"first_sku": "VRS.256.18.0", "product_first": True, "url_first": True, "mentions_cheaper_or_analog": True}),
        Scenario(3, "Точный цифровой SKU котла", "котлы", "P0", ["2202210", "а какие там основные характеристики?"], {"first_sku": "2202210", "product_first": True, "url_first": True, "characteristics_later": True, "same_sku_later": True}),
        Scenario(4, "Нормализация SKU с регистром и пробелами", "ссылка", "P0", ["  vrs . 256 . 18 . 0  ", "это точно он?"], {"first_sku": "VRS.256.18.0", "product_first": True, "url_first": True, "same_later": True}),
        Scenario(5, "Простой запрос кран шаровый", "краны", "P1", ["кран шаровый", "для воды, 1/2"], {"clarify_first": ["вод", "размер"], "no_products_first": True, "product_later": True, "url_later": True, "ball_valves_only": True}),
        Scenario(6, "Простой запрос нужен насос", "насосы", "P1", ["нужен насос", "для отопления, 130 мм", "25/6"], {"clarify_first": ["насос"], "no_products_first": True, "no_products_second": True, "clarify_second": ["напор", "присоедин"], "product_later": True, "url_later": True, "pump_constraints": {"connection_size": 25, "head_m": 6, "mounting_length_mm": 130}}),
        Scenario(7, "Простой запрос котёл электрический", "котлы", "P1", ["котёл электрический", "95 метров, 380"], {"clarify_first": ["площад"], "no_products_first": True, "product_later": True, "url_later": True, "avoid_weak_boiler": True, "minimum_boiler_power_kw": 9.5, "required_voltage_v": 380}),
        Scenario(8, "Широкий запрос нужна труба", "трубы", "P0", ["нужна труба", "для отопления, 25 мм"], {"clarify_first": ["отоп", "канал"], "no_products_first": True, "product_or_no_match_later": True}),
        Scenario(9, "Широкий запрос труба для воды", "трубы", "P1", ["труба для воды", "для горячей, 20 мм"], {"clarify_first": ["холод", "горяч", "диаметр"], "no_products_first": True, "product_or_no_match_later": True}),
        Scenario(10, "Широкий запрос не знаю какую трубу", "трубы", "P1", ["надо трубу, не знаю какую", "в квартиру, для воды"], {"clarify_first": ["отоп", "вод", "канал"], "no_products_first": True, "clarify_later": ["холод", "горяч", "диаметр"]}),
        Scenario(11, "Канализационная труба 50 без длины", "канализация", "P0", ["канализационная труба 50", "внутренняя, труба, 500 мм"], {"clarify_first": ["внутрен", "наруж", "длин"], "no_products_first": True, "product_later": True, "url_later": True, "must_not_show_wrong_sewer": True}),
        Scenario(12, "Отвод 110 без типа канализации", "канализация", "P1", ["мне отвод 110", "внутренняя, 90"], {"clarify_first": ["внутрен", "наруж"], "no_products_first": True, "product_or_no_match_later": True, "sewer_bend": {"diameter_mm": 110, "angle_deg": 90}}),
        Scenario(13, "Муфта на канализацию без диаметра", "канализация", "P1", ["муфта на канализацию нужна", "внутренняя, 50, соединительная"], {"clarify_first": ["внутрен", "наруж", "диаметр"], "no_products_first": True, "product_or_no_match_later": True, "connecting_coupling": True}),
        Scenario(14, "Циркуляционный насос подешевле", "насосы", "P0", ["циркуляционный насос, подешевле", "25/6, 130 мм"], {"clarify_first": ["монтаж", "напор"], "no_products_first": True, "product_later": True, "cheap_sorted_later": True, "url_later": True, "pump_constraints": {"connection_size": 25, "head_m": 6, "mounting_length_mm": 130}}),
        Scenario(15, "Насос для отопления с вопросом почему", "насосы", "P0", ["нужен насос для отопления", "да, старый 25/6 130", "а почему ты это предлагаешь?"], {"clarify_first": ["циркуляц", "стар"], "product_later": True, "why_answer": True}),
        Scenario(16, "Насос как Grundfos, но дешевле", "насосы", "P0", ["насос как Grundfos, но дешевле", "старый 25/4, 180 мм"], {"clarify_first": ["модель", "25"], "product_later": True, "cheap_sorted_later": True, "url_later": True, "no_false_compatibility": True}),
        Scenario(17, "Есть насос в наличии", "наличие", "P0", ["есть насос в наличии?", "циркуляционный 25/6 130, только то что реально есть"], {"clarify_first": ["насос", ["парамет", "модел", "артикул", "тип"]], "product_later": True, "in_stock_later": True, "url_later": True}),
        Scenario(18, "Насос 25/6 130 без бренда", "насосы", "P1", ["насос 25/6 130", "да, бренд не важен"], {"clarify_or_product_first": True, "product_later": True, "url_later": True, "pump_constraints": {"connection_size": 25, "head_m": 6, "mounting_length_mm": 130}}),
        Scenario(19, "Замена старого насоса по модели", "насосы", "P1", ["старый насос есть, нужен на замену", "старый 25/6 130, можно дешевле"], {"clarify_first": ["модель", "размер"], "product_later": True, "cheap_sorted_later": True, "url_later": True, "pump_constraints": {"connection_size": 25, "head_m": 6, "mounting_length_mm": 130}}),
        Scenario(20, "Электрический котёл на 100 м²", "котлы", "P0", ["электрический котёл на 100 м²", "380"], {"clarify_or_product_first": True, "product_later_or_first": True, "avoid_weak_boiler": True, "minimum_boiler_power_kw": 10, "required_voltage_v": 380, "url_any": True}),
        Scenario(21, "Котёл подешевле", "котлы", "P0", ["котёл подешевле", "электрический, 90 метров, 380"], {"clarify_first": ["газ", "электр", "площад"], "product_later": True, "cheap_sorted_later": True, "avoid_weak_boiler": True, "minimum_boiler_power_kw": 9, "required_voltage_v": 380}),
        Scenario(22, "Нужен котёл, но я не знаю какой", "котлы", "P1", ["нужен котёл, но я не знаю какой", "70 квадратов, газа нет"], {"clarify_first": ["площад", "газ"], "clarify_later": ["220", "380"]}),
        Scenario(23, "Хватит ли 6 кВт на 100 метров", "котлы", "P0", ["а 6 кВт хватит на 100 метров?", "но сосед говорит хватит"], {"should_warn_6kw": True, "consistent_later": True}),
        Scenario(24, "Спор о 12 кВт или 15 кВт", "котлы", "P1", ["12 кВт или 15 кВт на дом 100 м²?", "обычный дом, без суперутепления"], {"clarify_first": ["утеп"], "tradeoff_later": True, "ordinary_tradeoff": True}),
        Scenario(25, "В котле есть насос и бак", "комплектация", "P0", ["в котле есть насос и бак?", "2202210"], {"clarify_first": ["модель", "артикул"], "no_hallucinated_complectation": True, "answers_components": ["насос", "бак"]}),
        Scenario(26, "Чем его обвязать", "комплектация", "P1", ["чем его обвязать?", "электрический котёл, только радиаторы"], {"clarify_first": ["кот", "систем"], "cautious_later": True}),
        Scenario(27, "Нужна ли группа безопасности", "комплектация", "P1", ["нужна группа безопасности?", "электрический котёл, закрытая система"], {"clarify_first": ["кот", "систем"], "cautious_later": True}),
        Scenario(28, "Ссылка на предложенный товар", "ссылка", "P0", ["покажи шаровый кран 1/2", "для воды", "скинь ссылку на первый"], {"product_later": True, "url_later": True, "link_final": True}),
        Scenario(29, "Повтори ссылку и карточку ещё раз", "ссылка", "P1", ["покажи шаровый кран 1/2", "для воды", "повтори ссылку ещё раз и артикул тоже", "ты точно тот же товар прислал?"], {"product_later": True, "link_later": True, "same_later": True}),
        Scenario(30, "Есть 2 штуки", "наличие", "P0", ["есть 2 штуки?", "2202210"], {"clarify_first": [["какой", "какому", "товар"], ["артикул", "модел"]], "quantity_later": 2, "url_later": True}),
        Scenario(31, "В наличии без точного количества", "наличие", "P0", ["в наличии?", "2202210"], {"clarify_first": ["товар", "артикул"], "product_later": True, "url_later": True}),
        Scenario(32, "Можно забрать сегодня", "наличие", "P1", ["можно забрать сегодня?", "2202210"], {"clarify_first": ["артикул", "товар"], "no_pickup_promise": True, "pickup_requires_confirmation": True}),
        Scenario(33, "Самый дешёвый шаровый кран", "краны", "P0", ["самый дешёвый шаровый кран 1/2", "для воды"], {"clarify_first": ["вод"], "product_later": True, "cheap_sorted_later": True, "url_later": True, "ball_valves_only": True}),
        Scenario(34, "Только в наличии насос 25/6", "насосы", "P0", ["насос 25/6, только в наличии", "130"], {"clarify_first": ["монтаж", "130", "180"], "product_later": True, "in_stock_later": True, "url_later": True, "pump_constraints": {"connection_size": 25, "head_m": 6, "mounting_length_mm": 130}}),
        Scenario(35, "Только VALTEC без аналогов", "краны", "P1", ["нужен кран 1/2, только Valtec", "для воды, без аналогов"], {"clarify_first": [["назнач", "для чего", "какую воду"]], "product_later": True, "brand_only": "VALTEC", "url_later": True, "ball_valves_only": True}),
        Scenario(36, "Смена темы с крана на котёл", "смена темы", "P0", ["нужен кран шаровый", "1/2, для воды", "теперь нужен котёл на 100 метров"], {"product_later": True, "topic_change_final": True, "boiler_final": True}),
        Scenario(37, "Смена темы с насоса на канализацию", "смена темы", "P1", ["нужен насос для отопления", "ладно, не насос. теперь нужна канализационная труба 50"], {"topic_change_final": True, "sewer_final": True, "clarify_final": ["внутрен", "наруж", "длин"]}),
        Scenario(38, "Small talk как дела потом насос", "small talk", "P2", ["как дела?", "нужен насос 25/6 130"], {"small_talk_first": True, "product_later": True, "url_later": True}),
        Scenario(39, "Комплимент потом товар", "small talk", "P2", ["ты красивая", "кран 1/2 для воды"], {"small_talk_first": True, "product_later": True, "url_later": True}),
        Scenario(40, "Штука для батареи", "радиаторная арматура", "P1", ["нужна штука для батареи", "перекрывать"], {"clarify_first": ["радиатор", "перекры", "температур"], "product_or_clarify_later": True}),
        Scenario(41, "Труба белая", "трубы", "P1", ["труба белая", "горячая вода, 20 мм"], {"clarify_first": ["для чего", "вод", "диаметр"], "product_or_no_match_later": True, "required_pipe_color": "бел"}),
        Scenario(42, "Эта фигня под раковину", "другое", "P1", ["нужна эта фигня под раковину", "слив"], {"clarify_first": ["слив", "сифон", "кран"], "product_or_no_match_later": True}),
        Scenario(43, "Надо чтобы вода шла", "другое", "P0", ["надо чтобы вода шла", "слабый напор в доме"], {"symptom_first": True, "clarify_later": ["напор", "источник"]}),
        Scenario(44, "Сложная обвязка с эскалацией только после уточнений", "fallback", "P0", ["подберите обвязку котла, бойлера и теплого пола, я вообще не разбираюсь", "дом 180 метров, котёл не выбран, нужен ещё бойлер", "бойлер 150 л, тёплый пол 60 м², 6 контуров"], {"clarify_first": ["площад", "кот", "бойлер"], "no_handoff_before_final": True, "handoff_later": True, "handoff_requirements": ["180", "150", "60", "6", "бойлер", "тепл", "кот"], "handoff_slots": {"area_m2": 180, "boiler_volume_l": 150, "warm_floor_area_m2": 60, "warm_floor_contours": 6}}),
        Scenario(45, "Неизвестная комплектация и корректная передача менеджеру", "fallback", "P0", ["у этого котла встроенный бойлер есть?", "2202210"], {"clarify_first": ["модель", "артикул"], "no_hallucinated_complectation": True, "answers_builtin_boiler": True}),
        Scenario(46, "Ау после короткого сбоя", "другое", "P1", ["нужен насос", "ау"], {"clarify_first": ["насос"], "repeat_pending_later": True}),
        Scenario(47, "Опечатки и переформулировка в одной сессии", "другое", "P1", ["нсос 256 130", "да, тока подешевле"], {"typo_first": True, "product_later_or_first": True, "cheap_sorted_later": True, "pump_constraints": {"connection_size": 25, "head_m": 6, "mounting_length_mm": 130}}),
        Scenario(48, "Повторный вопрос без противоречий", "другое", "P1", ["6 кВт на 100 метров хватит?", "точно? а то ты раньше 12 советовал"], {"should_warn_6kw": True, "consistent_later": True, "explains_power_later": True}),
    ]


def read_usage() -> float:
    if not USAGE_PATH.exists():
        return 0.0
    try:
        return float(json.loads(USAGE_PATH.read_text(encoding="utf-8")).get("spent_usd", 0.0))
    except Exception:
        return 0.0


def post_json(path: str, payload: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:
        return {"error": str(exc)}


def get_json(path: str, timeout: int = 30) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{API_URL}{path}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def normalize(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def normalize_sku(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(value or "").casefold())


def catalog_entry(item: dict[str, Any]) -> dict[str, Any]:
    return CATALOG.get(normalize_sku(item.get("sku")), {})


def catalog_text(item: dict[str, Any]) -> str:
    entry = catalog_entry(item)
    attrs = entry.get("attributes_normalized") or {}
    return normalize(
        " ".join(
            [
                str(entry.get("name") or item.get("name") or ""),
                str(entry.get("category_path") or ""),
                str(entry.get("description") or ""),
                *[f"{key}: {value}" for key, value in attrs.items()],
            ]
        )
    )


def catalog_attribute_text(item: dict[str, Any], markers: list[str]) -> str:
    attrs = catalog_entry(item).get("attributes_normalized") or {}
    values = [
        str(value)
        for key, value in attrs.items()
        if any(marker in normalize(str(key)) for marker in markers)
    ]
    return normalize(" ".join(values))


def boiler_power_kw(item: dict[str, Any]) -> float | None:
    entry = catalog_entry(item)
    attrs = entry.get("attributes_normalized") or {}
    for key, value in attrs.items():
        key_text = normalize(str(key))
        if "мощ" in key_text and "квт" in key_text:
            match = re.search(r"\d+(?:[,.]\d+)?", str(value))
            if match:
                return float(match.group(0).replace(",", "."))
    text = normalize(f"{entry.get('name', '')} {entry.get('description', '')}")
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*квт", text)
    return float(match.group(1).replace(",", ".")) if match else None


def contains_number(text: str, expected: float, tolerance: float = 0.01) -> bool:
    values = [
        float(raw.replace(",", "."))
        for raw in re.findall(r"\d+(?:[,.]\d+)?", text or "")
    ]
    return any(abs(value - float(expected)) <= tolerance for value in values)


def has_any(text: str, words: list[str]) -> bool:
    normalized = normalize(text)
    return any(word.lower().replace("ё", "е") in normalized for word in words)


def has_all(text: str, words: list[str]) -> bool:
    normalized = normalize(text)
    return all(word.lower().replace("ё", "е") in normalized for word in words)


def has_marker_groups(text: str, groups: list[str | list[str]]) -> bool:
    """Require every semantic group while allowing inflection-friendly alternatives."""
    return all(
        has_any(text, group if isinstance(group, list) else [group])
        for group in groups
    )


def products(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return turn.get("response", {}).get("products") or []


def answer(turn: dict[str, Any]) -> str:
    return turn.get("response", {}).get("answer") or ""


def debug(turn: dict[str, Any]) -> dict[str, Any]:
    return turn.get("response", {}).get("debug") or {}


def prices_are_sorted(items: list[dict[str, Any]]) -> bool:
    prices = [item.get("price") for item in items if item.get("price") is not None]
    return len(prices) < 2 or prices == sorted(prices)


def is_in_stock(item: dict[str, Any]) -> bool:
    status = normalize(item.get("stock_status", ""))
    return "в наличии" in status and "нет в наличии" not in status


def product_matches_category(item: dict[str, Any], category: str) -> bool:
    text = normalize(f"{item.get('name', '')} {item.get('url', '')}")
    if category == "boilers":
        return has_any(text, ["котел", "котёл"]) and not has_any(
            text,
            ["датчик", "дымоход", "удлинитель", "адаптер", "комплект подключения"],
        )
    if category == "pumps":
        return "насос" in text and not has_any(text, ["трос", "кабель", "кожух"])
    if category == "pipes":
        return "труб" in text and not has_any(
            text,
            ["кожух", "трубка подключения", "декоратив"],
        )
    if category == "valves":
        return has_any(text, ["кран", "вентил", "клапан"]) and not has_any(
            text,
            ["чашка", "декоратив"],
        )
    return True


def asserts_weak_power_is_sufficient(text: str) -> bool:
    for sentence in re.split(r"[.!?\n]+", normalize(text)):
        if not any(re.search(rf"\b{power}\s*квт\b", sentence) for power in (6, 9)):
            continue
        positive = has_any(
            sentence,
            ["хватит", "будет достаточно", "мощности достаточно", "есть запас", "с запасом"],
        )
        negative = has_any(
            sentence,
            ["не хват", "недостаточ", "без запаса", "запаса нет", "не рекоменд"],
        )
        if positive and not negative:
            return True
    return False


def contains_url(text: str) -> bool:
    return "http://" in text or "https://" in text


def evaluate(scenario: Scenario, turns: list[dict[str, Any]]) -> tuple[str, list[str]]:
    issues: list[str] = []
    checks = scenario.checks

    if not turns:
        return "FAIL", ["нет turn-ов"]
    for index, turn in enumerate(turns, start=1):
        if turn.get("response", {}).get("error") or turn.get("error"):
            issues.append(f"turn {index}: ошибка API {turn.get('error') or turn.get('response', {}).get('error')}")
        if not answer(turn).strip():
            issues.append(f"turn {index}: пустой ответ")
        for item in products(turn):
            if not item.get("url"):
                issues.append(f"turn {index}: товар {item.get('sku')} без URL")
            if normalize(str(item.get("sku") or "")) in {"", "?", "-"}:
                issues.append(f"turn {index}: карточка с недопустимым SKU {item.get('sku')!r}")
            response_category = debug(turn).get("category")
            if response_category in {"boilers", "pumps", "pipes", "valves"} and not product_matches_category(
                item,
                response_category,
            ):
                issues.append(
                    f"turn {index}: товар {item.get('sku')} не соответствует категории {response_category}"
                )

    first = turns[0]
    last = turns[-1]
    second = turns[1] if len(turns) > 1 else turns[-1]

    if checks.get("product_first") and not products(first):
        issues.append("первый ответ должен был дать карточку, но товаров нет")
    if checks.get("product_later") and not any(products(turn) for turn in turns[1:]):
        issues.append("после уточнений ожидались товары, но карточек нет")
    if checks.get("product_later_or_first") and not any(products(turn) for turn in turns):
        issues.append("ожидались товары на одном из шагов, но карточек нет")
    if checks.get("product_or_no_match_later") and len(turns) > 1:
        later_text = answer(second)
        if not products(second) and not has_any(later_text, ["не вижу", "не наш", "уточн", "передать"]):
            issues.append("после уточнения нет ни товаров, ни честного no-match/уточнения")
    if checks.get("product_or_clarify_later") and len(turns) > 1:
        later_text = answer(second)
        if not products(second) and not (
            has_any(later_text, ["уточ", "подскаж", "какой", "нужно"])
            or "?" in later_text
        ):
            issues.append("после уточнения нет ни товаров, ни продолжения уточнения")

    if checks.get("no_products_first") and products(first):
        issues.append("первый ответ преждевременно показал товары")
    if checks.get("no_products_second") and len(turns) > 1 and products(second):
        issues.append("второй ответ преждевременно показал товары без обязательных параметров")
    if checks.get("clarify_second") and len(turns) > 1:
        if not has_marker_groups(answer(second), checks["clarify_second"]):
            issues.append(
                f"clarify_second: не найдены ожидаемые маркеры {checks['clarify_second']}"
            )
    if checks.get("url_first") and not contains_url(answer(first)):
        issues.append("в первом ответе нет прямой ссылки")
    if checks.get("url_later") and not any(contains_url(answer(turn)) for turn in turns[1:]):
        issues.append("после уточнений нет прямой ссылки")
    if checks.get("url_any") and not any(contains_url(answer(turn)) for turn in turns):
        issues.append("нет прямой ссылки ни на одном шаге")
    if checks.get("link_later") and not any(contains_url(answer(turn)) for turn in turns[1:]):
        issues.append("запрос ссылки не вернул URL")
    if checks.get("link_final") and not contains_url(answer(last)):
        issues.append("финальный запрос ссылки не вернул URL")

    first_sku = checks.get("first_sku")
    if first_sku:
        first_products = products(first)
        if not first_products or first_products[0].get("sku") != first_sku:
            issues.append(f"ожидался exact SKU {first_sku} первым, получено {[p.get('sku') for p in first_products[:3]]}")

    if checks.get("same_later"):
        shown_skus = [p.get("sku") for turn in turns for p in products(turn)]
        same_products = {
            normalize(str(item.get("sku") or "")) for item in products(last)
        }
        confirms_same = has_any(answer(last), ["да", "именно", "тот же", "это он", "верно"])
        if shown_skus and not (
            shown_skus[0] in answer(last)
            or normalize(str(shown_skus[0])) in same_products
            or confirms_same
        ):
            issues.append("повторный ответ не подтвердил тот же SKU")

    if checks.get("same_sku_later"):
        expected_sku = normalize_sku(checks.get("first_sku"))
        later_skus = {
            normalize_sku(item.get("sku"))
            for turn in turns[1:]
            for item in products(turn)
        }
        if expected_sku not in later_skus or expected_sku not in normalize_sku(answer(last)):
            issues.append("follow-up потерял exact SKU или не назвал его в ответе")

    for key in ["clarify_first", "clarify_later", "clarify_final"]:
        if key in checks:
            target = first if key == "clarify_first" else second if key == "clarify_later" else last
            if not has_marker_groups(answer(target), checks[key]):
                issues.append(f"{key}: не найдены ожидаемые маркеры {checks[key]}")

    if checks.get("clarify_or_product_first") and not products(first):
        if not has_any(answer(first), ["уточ", "правильно", "подскаж", "питание", "длина", "бренд"]):
            issues.append("первый ответ должен был уточнить или показать товар")

    if checks.get("mentions_cheaper_or_analog") and not has_any(answer(second), ["дешев", "аналог", "подходящ"]):
        issues.append("follow-up про дешевле/аналоги обработан слабо")

    if checks.get("characteristics_later") and not has_any(answer(second), ["характерист", "мощ", "тип", "данных"]):
        issues.append("запрос характеристик не получил ответа по характеристикам")

    if checks.get("cheap_sorted_later"):
        candidate_turns = [turn for turn in turns[1:] if products(turn)]
        if candidate_turns and not prices_are_sorted(products(candidate_turns[-1])):
            issues.append("товары не отсортированы по цене по возрастанию")
        elif not candidate_turns and not (
            products(first)
            and "дешев" in normalize(answer(second))
            and has_any(answer(second), ["не виж", "не наш", "нет"])
        ):
            issues.append("для cheap-сценария нет товарной выдачи")

    if checks.get("in_stock_later"):
        candidate_turns = [turn for turn in turns[1:] if products(turn)]
        if candidate_turns and not all(is_in_stock(item) for item in products(candidate_turns[-1])):
            issues.append("в выдаче есть товары не в наличии")
        elif not candidate_turns:
            issues.append("нет выдачи для проверки наличия")

    brand_only = checks.get("brand_only")
    if brand_only:
        candidate_turns = [turn for turn in turns[1:] if products(turn)]
        if candidate_turns:
            shown = products(candidate_turns[-1])
            answer_text = answer(candidate_turns[-1])
            confirmed_brand_lines = len(
                re.findall(
                    rf"бренд:\s*{re.escape(normalize(brand_only))}\b",
                    normalize(answer_text),
                )
            )
            names_all_match = all(
                normalize(brand_only) in normalize(item.get("name", "")) for item in shown
            )
            if not names_all_match and confirmed_brand_lines < len(shown):
                issues.append(f"в ответе не подтверждён бренд-фильтр {brand_only}")
        else:
            issues.append("нет выдачи для проверки бренд-фильтра")

    candidate_turns = [turn for turn in turns if products(turn)]
    candidate_turn = candidate_turns[-1] if candidate_turns else None

    pump_constraints = checks.get("pump_constraints")
    if pump_constraints and candidate_turn:
        slots = debug(candidate_turn).get("slots") or {}
        for key, expected in pump_constraints.items():
            actual = slots.get(key)
            try:
                matches = abs(float(actual) - float(expected)) <= 0.01
            except (TypeError, ValueError):
                matches = False
            if not matches:
                issues.append(f"потерян параметр насоса {key}={expected}; debug={actual}")
        for item in products(candidate_turn):
            fact_checks = [
                (
                    "connection_size",
                    catalog_attribute_text(item, ["присоедин", "диаметр подключ"]),
                ),
                ("head_m", catalog_attribute_text(item, ["напор"])),
                ("mounting_length_mm", catalog_attribute_text(item, ["монтажн"])),
            ]
            for key, facts in fact_checks:
                expected = pump_constraints.get(key)
                if expected is None:
                    continue
                # Pump size is conventionally encoded in the model name
                # (25/60, 25/6) even when the structured thread field contains
                # the physical union thread, e.g. 1 1/2 inch.
                evidence = f"{facts} {catalog_text(item)}"
                if not contains_number(evidence, float(expected)):
                    issues.append(
                        f"насос {item.get('sku')} не подтверждает {key}={expected}"
                    )
    elif pump_constraints:
        issues.append("нет карточек для проверки параметров насоса")

    required_voltage = checks.get("required_voltage_v")
    if required_voltage and candidate_turn:
        slots = debug(candidate_turn).get("slots") or {}
        if slots.get("voltage_v") != required_voltage:
            issues.append(
                f"фильтр напряжения потерян: ожидалось {required_voltage}, debug={slots.get('voltage_v')}"
            )
        for item in products(candidate_turn):
            evidence = catalog_attribute_text(item, ["напряж", "питание"])
            if not contains_number(evidence or catalog_text(item), float(required_voltage)):
                issues.append(
                    f"товар {item.get('sku')} не подтверждает питание {required_voltage} В"
                )

    minimum_power = checks.get("minimum_boiler_power_kw")
    if minimum_power and candidate_turn:
        for item in products(candidate_turn):
            power = boiler_power_kw(item)
            if power is None:
                issues.append(f"у котла {item.get('sku')} не удалось подтвердить мощность")
            elif power < float(minimum_power) * 0.9:
                issues.append(
                    f"котёл {item.get('sku')} слабее требования: {power:g} < {minimum_power:g} кВт"
                )
            elif power < float(minimum_power) and not has_any(
                answer(candidate_turn),
                ["погранич", "ниже ориентир", "не считаю", "без теплотехнического", "не рекоменд"],
            ):
                issues.append(
                    f"пограничный котёл {item.get('sku')} показан без явного предупреждения"
                )

    if checks.get("ball_valves_only") and candidate_turn:
        for item in products(candidate_turn):
            item_text = catalog_text(item)
            if "кран шар" not in item_text or has_any(
                item_text,
                ["дренаж", "сливной кран", "клапан обрат"],
            ):
                issues.append(f"выдан не шаровой водяной кран: {item.get('sku')}")

    bend = checks.get("sewer_bend")
    if bend:
        target_slots = debug(second).get("slots") or {}
        if target_slots.get("diameter_mm") != bend["diameter_mm"]:
            issues.append("DN отвода потерян или заменён углом")
        if target_slots.get("angle_deg") != bend["angle_deg"]:
            issues.append("угол отвода не сохранён в slots")
        for item in products(second):
            item_text = catalog_text(item)
            if not contains_number(item_text, bend["diameter_mm"]):
                issues.append(f"отвод {item.get('sku')} не DN{bend['diameter_mm']}")
            angle_ok = any(
                contains_number(item_text, angle)
                for angle in [bend["angle_deg"], 87, 88]
            )
            if not angle_ok:
                issues.append(f"отвод {item.get('sku')} не подтверждает угол 87–90°")

    if checks.get("connecting_coupling"):
        target_slots = debug(second).get("slots") or {}
        if target_slots.get("coupling_type") != "соединительная":
            issues.append("тип соединительной муфты потерян в slots")
        for item in products(second):
            item_text = catalog_text(item)
            if "муфт" not in item_text or not contains_number(item_text, 50):
                issues.append(f"неподходящая муфта {item.get('sku')}")
            if has_any(item_text, ["переход", "ремонт", "надвиж"]):
                issues.append(f"вместо соединительной выдана другая муфта {item.get('sku')}")

    required_color = checks.get("required_pipe_color")
    if required_color and candidate_turn:
        slots = debug(candidate_turn).get("slots") or {}
        if required_color not in normalize(str(slots.get("pipe_color") or "")):
            issues.append("цвет трубы потерян в slots")
        for item in products(candidate_turn):
            if required_color not in catalog_text(item):
                issues.append(f"цвет товара {item.get('sku')} не подтверждён как белый")

    if checks.get("avoid_weak_boiler"):
        text = "\n".join(answer(turn) for turn in turns)
        bad_products = [p for turn in turns for p in products(turn) if "6 кВт" in p.get("name", "")]
        if bad_products and not has_any(text, ["слаб", "не рекоменд", "не как равн", "не буду"]):
            issues.append("6 кВт показан без явной оговорки")

    if checks.get("should_warn_6kw"):
        if not has_any(answer(first), ["не рекоменд", "слаб", "не хват", "спорно", "без запаса"]):
            issues.append("бот не предупредил про 6 кВт на 100 м²")

    if checks.get("consistent_later") and asserts_weak_power_is_sufficient(answer(last)):
        issues.append("второй ответ противоречит осторожной позиции по слабой мощности")

    if checks.get("tradeoff_later") and not has_any(answer(last), ["15", "12", "запас", "утеп", "не равн"]):
        issues.append("нет объяснения trade-off 12/15 кВт")
    if checks.get("ordinary_tradeoff"):
        later = normalize(answer(last))
        bad_recommendation = (
            has_any(later, ["рекомендовал 15", "рекомендую 15", "лучше 15"])
            or ("12" in later and "впритык" in later)
        )
        if bad_recommendation or not has_any(
            later,
            ["начать проверку с 12", "12 квт", "теплопотер", "минимальн"],
        ):
            issues.append("для обычного дома дан необоснованный приоритет 15 кВт")

    if checks.get("no_hallucinated_complectation"):
        combined = "\n".join(answer(turn) for turn in turns)
        if has_any(combined, ["точно есть встроенный бойлер", "бойлер есть"]) and not has_any(combined, ["не вижу", "не подтверж"]):
            issues.append("есть риск выдуманной комплектации")

    requested_components = checks.get("answers_components")
    if requested_components:
        later = answer(last)
        if not has_marker_groups(later, requested_components):
            issues.append("ответ после SKU не разобрал все запрошенные компоненты")
        if not has_any(
            later,
            ["встро", "подтверж", "не виж", "не указан", "нет данных", "карточ"],
        ):
            issues.append("комплектация не подтверждена и не опровергнута по данным карточки")

    if checks.get("cautious_later") and not has_any(answer(last), ["не буду", "зависит", "докум", "подтверж", "менедж"]):
        issues.append("ответ недостаточно осторожен для комплектации/обвязки")

    expected_quantity = checks.get("quantity_later")
    if expected_quantity:
        quantities = [
            int(value)
            for value in re.findall(
                r"наличие:[^\n]{0,80}?\b(\d+)\s*шт",
                normalize(answer(second)),
            )
        ]
        if not quantities or max(quantities) < int(expected_quantity):
            issues.append("ответ по количеству не подтверждает достаточный qty")

    if checks.get("no_pickup_promise"):
        combined = "\n".join(answer(turn) for turn in turns)
        if has_any(combined, ["можно забрать сегодня", "заберете сегодня", "забрать сегодня можно"]) and not has_any(combined, ["не подтверж", "если", "только"]):
            issues.append("самовывоз сегодня обещан без подтверждения")
    if checks.get("pickup_requires_confirmation") and not has_any(
        "\n".join(answer(turn) for turn in turns),
        ["уточнит менеджер", "подтверд", "готовность", "самовывоз"],
    ):
        issues.append("не объяснено, что готовность самовывоза требует подтверждения")

    if checks.get("topic_change_final") and not debug(last).get("topic_changed"):
        issues.append("debug.topic_changed не поднят при смене темы")
    if checks.get("boiler_final") and debug(last).get("category") != "boilers":
        issues.append(f"после смены темы ожидалась категория boilers, получено {debug(last).get('category')}")
    if checks.get("sewer_final") and debug(last).get("category") != "sewer":
        issues.append(f"после смены темы ожидалась категория sewer, получено {debug(last).get('category')}")

    if checks.get("small_talk_first") and not has_any(answer(first), ["спасибо", "на связи", "помогу", "подбер"]):
        issues.append("small talk обработан неестественно или слишком сухо")

    if checks.get("symptom_first"):
        first_slots = debug(first).get("slots") or {}
        recognized = (
            debug(first).get("category") == "pumps"
            and bool(first_slots.get("symptom") or first_slots.get("pump_use"))
            and has_any(answer(first), ["источник", "вод", "напор", "скваж", "колод"])
        )
        if not recognized:
            issues.append("symptom-flow не распознан")

    if checks.get("handoff_later") and not turns[-1].get("response", {}).get("need_handoff"):
        issues.append("сложный fallback не дошёл до корректного handoff/summary")
    if checks.get("no_handoff_before_final") and any(
        turn.get("response", {}).get("need_handoff") for turn in turns[:-1]
    ):
        issues.append("handoff создан до получения обязательных инженерных параметров")
    if checks.get("handoff_requirements") and not has_marker_groups(
        answer(last),
        checks["handoff_requirements"],
    ):
        issues.append("handoff summary потерял обязательные требования проекта")
    if checks.get("handoff_slots"):
        final_slots = debug(last).get("slots") or {}
        for key, expected in checks["handoff_slots"].items():
            actual = final_slots.get(key)
            try:
                matches = abs(float(actual) - float(expected)) <= 0.01
            except (TypeError, ValueError):
                matches = actual == expected
            if not matches:
                issues.append(
                    f"handoff потерял структурированный параметр {key}={expected}; debug={actual}"
                )

    if checks.get("answers_builtin_boiler"):
        if not (
            "бойлер" in normalize(answer(last))
            and has_any(
                answer(last),
                ["не виж", "не подтверж", "нет", "встро", "докум", "карточ"],
            )
        ):
            issues.append("после SKU нет ответа на вопрос о встроенном бойлере")

    if checks.get("must_not_show_wrong_sewer"):
        for item in [product for turn in turns[1:] for product in products(turn)]:
            item_text = normalize(f"{item.get('name', '')} {item.get('url', '')}")
            if "труб" not in item_text or "50" not in item_text or has_any(
                item_text,
                ["наружн", "naruzh", "kgem"],
            ):
                issues.append(f"неподходящая канализационная позиция {item.get('sku')}")

    if checks.get("repeat_pending_later") and not has_any(answer(last), ["на связи", "для чего", "насос", "отоп", "давлен", "дренаж"]):
        issues.append("бот не удержал pending-question после 'ау'")

    if checks.get("typo_first") and not has_any(answer(first), ["25", "130", "насос", "уточ", "правильно"]):
        issues.append("опечатка/сжатая запись не распознана")

    if checks.get("why_answer") and not has_any(answer(last), ["потому", "совпада", "парамет", "данн", "карточ"]):
        issues.append("нет объяснения логики подбора")

    if checks.get("explains_power_later"):
        later = answer(last)
        if not has_all(later, ["6", "100"]) or not has_any(
            later,
            ["10 квт", "не хват", "теплопотер", "ориентир"],
        ):
            issues.append("повторный вопрос о 6/12 кВт не получил объяснения")

    if checks.get("no_false_compatibility"):
        combined = "\n".join(answer(turn) for turn in turns)
        if has_any(combined, ["точно подойдет", "полный аналог", "гарантированно"]) and not has_any(combined, ["не обещ", "свер", "проверь"]):
            issues.append("слишком уверенная совместимость")

    if issues:
        return ("FAIL" if scenario.priority == "P0" else "PARTIAL"), issues
    return "PASS", []


def run() -> None:
    RUN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_REPORT.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")
    usage_before = read_usage()
    health = get_json("/health")
    all_results: list[dict[str, Any]] = []

    for scenario in scenarios():
        session_id = f"pdf-test-{scenario.number}-{int(time.time())}"
        print(f"[{scenario.number:02d}/48] {scenario.title}", flush=True)
        turns: list[dict[str, Any]] = []
        for turn_index, message in enumerate(scenario.messages, start=1):
            started = time.perf_counter()
            response = post_json("/chat", {"session_id": session_id, "message": message})
            elapsed = round(time.perf_counter() - started, 2)
            turns.append({"turn": turn_index, "user": message, "response": response, "elapsed_sec": elapsed})
            time.sleep(0.1)
        verdict, issues = evaluate(scenario, turns)
        all_results.append({"scenario": scenario, "turns": turns, "verdict": verdict, "issues": issues})

    usage_after = read_usage()
    finished_at = datetime.now().isoformat(timespec="seconds")
    write_run_report(all_results, health, started_at, finished_at, usage_before, usage_after)
    write_analysis_report(all_results, health, started_at, finished_at, usage_before, usage_after)
    print(f"Run report: {RUN_REPORT}")
    print(f"Analysis report: {ANALYSIS_REPORT}")
    print(f"Budget delta USD: {usage_after - usage_before:.6f}")


def write_run_report(
    results: list[dict[str, Any]],
    health: dict[str, Any],
    started_at: str,
    finished_at: str,
    usage_before: float,
    usage_after: float,
) -> None:
    telemetry = llm_telemetry(results)
    lines = [
        "# Протокол тестовых диалогов Vesta Trading AI-консультанта",
        "",
        f"Источник сценариев: `Глубокий набор тестовых диалогов для AI-консультанта Vesta Trading.pdf`.",
        f"Локальный API: `{API_URL}`.",
        f"Начало: `{started_at}`.",
        f"Окончание: `{finished_at}`.",
        f"Health перед запуском: `{json.dumps(health, ensure_ascii=False)}`.",
        f"Снимок каталога оценщика: `{json.dumps(catalog_provenance(), ensure_ascii=False)}`.",
        f"LLM telemetry: `{json.dumps(telemetry, ensure_ascii=False)}`.",
        f"LLM spent до запуска: `${usage_before:.6f}`.",
        f"LLM spent после запуска: `${usage_after:.6f}`.",
        f"Расход на прогон: `${usage_after - usage_before:.6f}`.",
        "",
    ]
    for item in results:
        scenario: Scenario = item["scenario"]
        lines.extend(
            [
                f"## {scenario.number}. {scenario.title}",
                "",
                f"Категория: `{scenario.category}`. Приоритет: `{scenario.priority}`. Вердикт: **{item['verdict']}**.",
            ]
        )
        if item["issues"]:
            lines.append("Проблемы:")
            for issue in item["issues"]:
                lines.append(f"- {issue}")
        lines.append("")
        for turn in item["turns"]:
            response = turn["response"]
            lines.append(f"### Ход {turn['turn']}")
            lines.append("")
            lines.append(f"Пользователь: {turn['user']}")
            lines.append("")
            lines.append(f"Время ответа: `{turn['elapsed_sec']}` сек.")
            lines.append("")
            if "error" in response:
                lines.append(f"Ошибка API: `{response['error']}`")
                lines.append("")
                continue
            lines.append("Ответ бота:")
            lines.append("")
            lines.append("```text")
            lines.append(response.get("answer", ""))
            lines.append("```")
            lines.append("")
            products_list = response.get("products") or []
            if products_list:
                lines.append("Товары:")
                for product in products_list:
                    lines.append(
                        f"- `{product.get('sku')}` | {product.get('name')} | "
                        f"{product.get('price')} {product.get('currency')} | "
                        f"{product.get('stock_status')} | {product.get('url')}"
                    )
                lines.append("")
            lines.append("Debug:")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(response.get("debug", {}), ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")
    RUN_REPORT.write_text("\n".join(lines), encoding="utf-8")


def write_analysis_report(
    results: list[dict[str, Any]],
    health: dict[str, Any],
    started_at: str,
    finished_at: str,
    usage_before: float,
    usage_after: float,
) -> None:
    telemetry = llm_telemetry(results)
    counts: dict[str, int] = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    by_priority: dict[str, dict[str, int]] = {}
    for item in results:
        scenario: Scenario = item["scenario"]
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
        by_priority.setdefault(scenario.priority, {"PASS": 0, "PARTIAL": 0, "FAIL": 0})
        by_priority[scenario.priority][item["verdict"]] += 1

    p0_failed = [
        item for item in results if item["scenario"].priority == "P0" and item["verdict"] != "PASS"
    ]
    failed = [item for item in results if item["verdict"] == "FAIL"]
    partial = [item for item in results if item["verdict"] == "PARTIAL"]

    recurring: dict[str, int] = {}
    for item in results:
        for issue in item["issues"]:
            key = issue.split(":")[0]
            recurring[key] = recurring.get(key, 0) + 1
    recurring_sorted = sorted(recurring.items(), key=lambda pair: pair[1], reverse=True)

    lines = [
        "# Анализ ответов Vesta Trading AI-консультанта",
        "",
        f"Прогон: `{started_at}` → `{finished_at}`.",
        f"Health перед запуском: `{json.dumps(health, ensure_ascii=False)}`.",
        f"Снимок каталога оценщика: `{json.dumps(catalog_provenance(), ensure_ascii=False)}`.",
        f"LLM telemetry: `{json.dumps(telemetry, ensure_ascii=False)}`.",
        f"Всего сценариев: `{len(results)}`.",
        f"PASS: `{counts.get('PASS', 0)}`.",
        f"PARTIAL: `{counts.get('PARTIAL', 0)}`.",
        f"FAIL: `{counts.get('FAIL', 0)}`.",
        f"Расход LLM на прогон: `${usage_after - usage_before:.6f}`.",
        "",
        "## Сводка по приоритетам",
        "",
        "| Приоритет | PASS | PARTIAL | FAIL |",
        "|---|---:|---:|---:|",
    ]
    for priority in sorted(by_priority):
        row = by_priority[priority]
        lines.append(f"| {priority} | {row.get('PASS', 0)} | {row.get('PARTIAL', 0)} | {row.get('FAIL', 0)} |")

    lines.extend(["", "## Релизные блокеры", ""])
    if not p0_failed:
        lines.append("P0-сценарии прошли без автоматических FAIL/PARTIAL.")
    else:
        for item in p0_failed:
            scenario: Scenario = item["scenario"]
            lines.append(f"- **{scenario.number}. {scenario.title}** — {item['verdict']}: {'; '.join(item['issues'])}")

    lines.extend(["", "## LLM gate", ""])
    if telemetry["mode"] == "fallback-only":
        lines.append(
            "**НЕ ЗАКРЫТ:** LLM была запрошена, но ни один транспортный вызов не завершился "
            "успешно. Результаты сценариев подтверждают только безопасный fallback."
        )
    elif telemetry["mode"] == "llm-transport-without-accepted-output":
        lines.append(
            "**НЕ ЗАКРЫТ:** транспорт LLM работал, но ни один её ответ не был принят guardrails."
        )
    elif telemetry["mode"] == "live-llm":
        lines.append(
            f"Live-LLM подтверждена: принято ответов `{telemetry['output_accepted']}` "
            f"из `{telemetry['requested']}` запрошенных ходов."
        )
    else:
        lines.append("В этом наборе LLM не запрашивалась; live-LLM gate не оценивался.")

    lines.extend(["", "## Частые причины проблем", ""])
    if recurring_sorted:
        for issue, count in recurring_sorted[:12]:
            lines.append(f"- `{issue}` — {count}")
    else:
        lines.append("Повторяющихся проблем автоматическая проверка не выявила.")

    lines.extend(["", "## По сценариям", ""])
    for item in results:
        scenario: Scenario = item["scenario"]
        lines.append(f"### {scenario.number}. {scenario.title} — {item['verdict']}")
        if item["issues"]:
            for issue in item["issues"]:
                lines.append(f"- {issue}")
        else:
            lines.append("- Критичных замечаний по автоматическим проверкам нет.")

    lines.extend(
        [
            "",
            "## Общий вывод",
            "",
            "Автоматическая проверка оценивает фактические ответы по ключевым QA-маркерам из PDF: наличие URL у карточек, exact SKU, сохранение контекста, уточнения перед подбором, сортировку по цене, фильтр наличия, осторожность по комплектации и недопустимость слабого котла как равного варианта. Это не заменяет ручную экспертную ревизию формулировок, но быстро показывает, где бот нарушает критические сценарии.",
        ]
    )

    ANALYSIS_REPORT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    run()
