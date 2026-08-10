"""Focused live OpenRouter recheck for failures found by domain_depth_audit.

Only the local catalogue cache is loaded (``refresh=False``).  The script does
not start the ASGI lifespan, refresh a feed, or request any Vestatrade URL.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.orchestrator import ChatOrchestrator  # noqa: E402


def _compact(value: object, limit: int = 900) -> str:
    rendered = " ".join(str(value or "").split())
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def main() -> int:
    bot = ChatOrchestrator()
    count, source = bot.reload_products(refresh=False)
    assert count > 1000
    assert source == "cache"
    assert bot.settings.llm_enabled
    assert bot.settings.llm_provider == "openrouter"

    print(
        f"LOCAL CACHE ONLY: {count} products; model={bot.settings.llm_model}",
        flush=True,
    )
    llm_used = 0

    def turn(session_id: str, message: str):
        nonlocal llm_used
        response = bot.handle_chat(session_id, message)
        debug = response.debug or {}
        llm_used += int(bool(debug.get("any_llm_used")))
        print(f"\nUSER [{session_id}]: {message}", flush=True)
        print(f"BOT: {_compact(response.answer)}", flush=True)
        print(
            "META: "
            f"category={debug.get('category')}; "
            f"slots={_compact(debug.get('slots'), 550)}; "
            f"products={','.join(product.sku for product in response.products) or '—'}; "
            f"llm={debug.get('any_llm_used')}; "
            f"source={debug.get('final_answer_source')}",
            flush=True,
        )
        return response

    sewer = turn(
        "recheck-sewer",
        (
            "Под раковиной треснула серая пластиковая штука примерно 50 мм "
            "толщиной и полметра длиной. Не знаю, как называется. Что искать?"
        ),
    )
    assert sewer.debug["category"] == "sewer"
    assert sewer.debug["slots"]["element_type"] == "труба"
    assert "поддон" not in sewer.answer.lower()

    pnd = turn(
        "recheck-pnd",
        "ПНД ПЭ100 SDR11 от колодца до дома, 32-я, трасса метров 40. Что посоветуете?",
    )
    assert pnd.debug["slots"]["diameter_mm"] == 32
    assert pnd.debug["slots"]["total_length_m"] == 40.0
    assert pnd.debug["slots"]["pipe_purpose"] == "водоснабжение"
    assert "для чего она" not in pnd.answer.lower()

    pert = turn(
        "recheck-pert",
        "Нужно PE-RT 16x2 для водяного тёплого пола, примерно 600 метров.",
    )
    assert pert.debug["slots"]["total_length_m"] == 600.0
    assert "площад" not in pert.answer.lower()
    assert "3 бухты" in pert.answer.lower()

    dirty = turn(
        "recheck-dirty",
        (
            "В подвале после дождя вода с песком и мелким мусором. Нужно откачать, "
            "не знаю, как называется такой насос."
        ),
    )
    assert dirty.debug["slots"]["pump_type"] == "дренажный"
    assert "размер частиц" in dirty.answer.lower()
    assert "вертикальный подъём" in dirty.answer.lower()
    assert "мощност" not in dirty.answer.lower()

    well = turn(
        "recheck-well",
        (
            "Колодец пять колец, вода примерно с третьего. До дома 40 метров, "
            "хочу душ и два крана. Какой насос нужен?"
        ),
    )
    assert well.debug["slots"]["water_level_ring_count"] == 3
    assert well.debug["slots"]["water_level_reference"] == "ambiguous"

    marking = turn(
        "recheck-marking",
        (
            "Сгорел Wilo Star-RS 25/6-180. Хочу нормальную замену, "
            "объясните ещё, что означают цифры."
        ),
    )
    marking_answer = marking.answer.lower()
    assert "dn 25" in marking_answer
    assert "6 м" in marking_answer and "не расход" in marking_answer
    assert "180 мм" in marking_answer and "монтажная длина" in marking_answer

    valve = turn(
        "recheck-valve",
        (
            "Нужна штука с ручкой, чтобы перекрыть воду перед унитазом. "
            "Резьба вроде полдюйма."
        ),
    )
    assert valve.debug["slots"]["application"] == "вода"
    assert valve.debug["slots"]["water_temperature"] == "холодная"
    assert "для чего нужен кран" not in valve.answer.lower()
    assert valve.products == []
    assert "угловой запорный кран" in valve.answer.lower()

    turn(
        "recheck-ppr",
        (
            "Делаю воду в квартире. Нужны белые пластиковые палки, которые паяют "
            "утюгом. Будет и горячая, и холодная вода, названия не знаю."
        ),
    )
    ppr = turn(
        "recheck-ppr",
        "От стояка к кранам, спрячем в стену. Диаметр не знаю — что надо измерить?",
    )
    assert ppr.debug["slots"]["pipe_material"] == "ppr"
    assert "точки водоразбора" in ppr.answer.lower()
    assert "управляющ" in ppr.answer.lower()

    vrs = next(
        product
        for product in bot.search_agent.products
        if product.sku.upper() == "VRS.256.18.0" and product.documents
    )
    turn("recheck-passport", f"Покажите насос {vrs.sku}")
    package = turn(
        "recheck-passport",
        "Что точно входит в коробку по паспорту?",
    )
    assert "согласно паспорту изделия" in package.answer.lower()
    assert ".pdf" not in package.answer.lower()
    assert "стр. 3" not in package.answer.lower()

    assert llm_used > 0
    print(
        f"\nRESULT: 9/9 critical flows passed across 11 turns; "
        f"LLM used on {llm_used} turns",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
