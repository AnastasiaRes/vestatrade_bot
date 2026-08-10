"""Live domain-depth audit for pipes, pumps, terminology and passports.

The audit intentionally uses only the local product cache (``refresh=False``).
It never starts the ASGI lifespan and never requests the Vestatrade site/feed.
OpenRouter is exercised through the same ``ChatOrchestrator.handle_chat`` path
used by the customer-facing API.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.orchestrator import ChatOrchestrator  # noqa: E402


def _one_line(value: object, limit: int = 1200) -> str:
    compact = " ".join(str(value or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def main() -> int:
    bot = ChatOrchestrator()
    count, source = bot.reload_products(refresh=False)
    assert bot.settings.llm_provider == "openrouter"
    assert bot.settings.llm_enabled
    assert count > 1000

    requested = 0
    used = 0
    turns = 0

    print(
        f"LOCAL CATALOG ONLY: {count} products; source={source}; "
        f"LLM={bot.settings.llm_model}"
    )

    def turn(session_id: str, message: str) -> None:
        nonlocal requested, used, turns
        response = bot.handle_chat(session_id, message)
        debug = response.debug or {}
        was_requested = bool(
            debug.get("llm_requested")
            or debug.get("engineering_llm_requested")
            or debug.get("intent_llm_requested")
            or debug.get("response_llm_requested")
            or debug.get("consultant_llm_requested")
        )
        was_used = bool(debug.get("any_llm_used"))
        turns += 1
        requested += int(was_requested)
        used += int(was_used)
        print(f"\nUSER [{session_id}]: {message}")
        print(f"BOT: {_one_line(response.answer)}")
        print(
            "META: "
            f"category={debug.get('category')}; "
            f"slots={_one_line(debug.get('slots'), 650)}; "
            f"products={','.join(card.sku for card in response.products) or '—'}; "
            f"requested_llm={was_requested}; used_llm={was_used}; "
            f"source={debug.get('final_answer_source')}"
        )

    scenarios: list[tuple[str, list[str]]] = [
        (
            "pipe-novice-ppr",
            [
                "Делаю воду в квартире. Нужны белые пластиковые палки, которые паяют утюгом. "
                "Будет и горячая, и холодная вода, названия не знаю.",
                "От стояка к кранам, спрячем в стену. Диаметр тоже не знаю — подскажите, что надо измерить.",
            ],
        ),
        (
            "pipe-novice-sewer",
            [
                "Под раковиной треснула серая пластиковая штука примерно 50 мм толщиной и полметра длиной. "
                "Не знаю, как называется. Что искать?",
            ],
        ),
        (
            "sewer-bend",
            ["Нужна штука, чтобы повернуть серую канализацию 110 мм на 45 градусов."],
        ),
        (
            "pipe-pert-no-noun",
            ["Нужно PE-RT 16x2 для водяного тёплого пола, примерно 600 метров."],
        ),
        (
            "pipe-piece-total",
            ["Нужна PPR труба 25 мм: одна палка длиной 2 метра, а всего по трассе нужно 20 метров."],
        ),
        (
            "pipe-pnd-jargon",
            ["ПНД ПЭ100 SDR11 от колодца до дома, 32-я, трасса метров 40. Что посоветуете?"],
        ),
        (
            "pump-low-pressure",
            [
                "В частном доме вода из центрального водопровода еле течёт из душа. "
                "Мне нужен какой-то насос, но я в этом не разбираюсь.",
                "Манометра нет, особенно плохо вечером. Что мне сначала проверить?",
            ],
        ),
        (
            "pump-well-novice",
            [
                "Колодец пять колец, вода примерно с третьего. До дома 40 метров, "
                "хочу душ и два крана. Какой насос нужен?",
            ],
        ),
        (
            "pump-dirty-water",
            [
                "В подвале после дождя вода с песком и мелким мусором. Нужно откачать, "
                "не знаю, как называется такой насос.",
            ],
        ),
        (
            "pump-kns",
            ["Нужна КНС для санузла в подвале: самотёком стоки не уходят. Что подобрать?"],
        ),
        (
            "pump-more-power",
            ["Батареи плохо греют. Давайте просто поставим циркуляционный насос помощнее — какой взять?"],
        ),
        (
            "pump-old-marking",
            ["Сгорел Wilo Star-RS 25/6-180. Хочу нормальную замену, объясните ещё, что означают цифры."],
        ),
        ("term-american", ["Объясните простыми словами, что сантехник называет американкой?"]),
        ("term-pump-size", ["Что означает 25/6-180 на циркуляционном насосе?"]),
        ("term-closed-loop", ["В закрытом отоплении к напору насоса надо прибавлять высоту второго этажа?"]),
        (
            "novice-valve",
            ["Нужна штука с ручкой, чтобы перекрыть воду перед унитазом. Резьба вроде полдюйма."],
        ),
        ("jargon-thread", ["Мне нужен полдюймовый кран мама-папа. Вы понимаете, какое это соединение?"]),
    ]

    for session_id, messages in scenarios:
        for message in messages:
            turn(session_id, message)

    vrs = next(
        (
            product
            for product in bot.search_agent.products
            if product.sku.upper() == "VRS.256.18.0" and product.docs_text
        ),
        None,
    )
    if vrs:
        for message in [
            f"Покажите насос {vrs.sku}",
            "Что точно входит в коробку по паспорту?",
            "Какое у него присоединение?",
            "А электрическое подключение какое — можно просто в розетку?",
        ]:
            turn("passport-vrs", message)
    else:
        print("\nWARN: VRS.256.18.0 with attached docs was not found")

    no_doc_pump = next(
        (
            product
            for product in bot.search_agent.products
            if bot.search_agent.canonical_category(product) == "pumps"
            and not product.docs_text
            and product.sku
            and product.stock_qty > 0
        ),
        None,
    )
    if no_doc_pump:
        turn("passport-missing", f"Покажите артикул {no_doc_pump.sku}")
        turn("passport-missing", "Что входит в комплект поставки именно по паспорту?")

    print(
        f"\nSUMMARY: turns={turns}; requested_llm_turns={requested}; "
        f"used_llm_turns={used}"
    )
    if used == 0:
        print("FAIL: no turn successfully used OpenRouter")
        return 1
    print("RESULT: LIVE DOMAIN AUDIT COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
