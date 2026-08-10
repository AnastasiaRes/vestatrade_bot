"""Live OpenRouter smoke test for the customer-facing consultation pipeline.

The script deliberately loads only the local product cache
(``refresh=False``).  It never requests the Vestatrade website or feed.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.orchestrator import ChatOrchestrator  # noqa: E402
from app.agents.utils import normalize_text  # noqa: E402


def _short(value: str, limit: int = 900) -> str:
    compact = " ".join(str(value).split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def main() -> int:
    bot = ChatOrchestrator()
    count, source = bot.reload_products(refresh=False)
    assert bot.settings.llm_provider == "openrouter"
    assert bot.settings.llm_enabled
    assert count > 1000

    llm_used_turns = 0
    llm_requested_turns = 0
    failures: list[str] = []

    print(
        f"LOCAL CATALOG: {count} products; source={source}; "
        f"LLM={bot.settings.llm_model}"
    )

    def turn(session_id: str, message: str):
        nonlocal llm_used_turns, llm_requested_turns
        response = bot.handle_chat(session_id, message)
        debug = response.debug or {}
        used = bool(debug.get("any_llm_used"))
        requested = bool(
            debug.get("llm_requested")
            or debug.get("engineering_llm_requested")
            or debug.get("intent_llm_requested")
            or debug.get("response_llm_requested")
            or debug.get("consultant_llm_requested")
        )
        llm_used_turns += int(used)
        llm_requested_turns += int(requested)
        skus = ", ".join(card.sku for card in response.products) or "—"
        print(f"\nUSER [{session_id}]: {message}")
        print(f"BOT: {_short(response.answer)}")
        print(
            f"META: products={skus}; requested_llm={requested}; used_llm={used}; "
            f"source={debug.get('final_answer_source', '—')}"
        )
        if "**" in response.answer or "```" in response.answer:
            failures.append(f"markdown leaked in {session_id}: {message}")
        return response

    # Free-form customer conversation must actually traverse OpenRouter.
    turn("small-talk", "Как дела? И с чем ты можешь помочь?")

    pipe = turn(
        "numeric-pipe",
        "Нужна PPR труба 20 мм для горячей воды, максимум 70 градусов и 10 бар",
    )
    pipe_slots = pipe.debug["slots"]
    if pipe_slots.get("operating_temperature_c") != 70:
        failures.append("70 °C was not retained as temperature")
    if pipe_slots.get("max_price") is not None:
        failures.append("70 °C leaked into max_price")

    valve = turn("numeric-valve", 'Нужен кран 3/4" с американкой для воды')
    valve_slots = valve.debug["slots"]
    if valve_slots.get("operating_temperature_c") == 4:
        failures.append("3/4 inch leaked into temperature=4")

    pump = turn(
        "pump-replacement",
        "Сгорел насос, стоял Wilo Star RS 25/6 180",
    )
    pump_slots = pump.debug["slots"]
    if float(pump_slots.get("head_m") or 0) != 6:
        failures.append("pump head 6 m was not retained")
    if int(pump_slots.get("mounting_length_mm") or 0) != 180:
        failures.append("pump mounting length 180 mm was not retained")
    if len(pump.products) > 1:
        package = turn("pump-replacement", "Что входит в комплект поставки?")
        if "по какой из показанных моделей" in normalize_text(package.answer):
            failures.append("open package comparison asked to choose a model")
        nuts = turn("pump-replacement", "А гайки в комплекте есть?")
        if "по какой из показанных моделей" in normalize_text(nuts.answer):
            failures.append("package follow-up lost all-card scope")
        choice = turn(
            "pump-replacement",
            "Какой из предложенных вы бы выбрали и почему?",
        )
        if "рекоменд" not in normalize_text(choice.answer):
            failures.append("choice answer has no explicit recommendation")

    turn("pump-refusal", "Нужен циркуляционный насос для отопления")
    pump_details = turn(
        "pump-refusal",
        "Новый подбор: расход не знаю, напор 6 м, монтажная длина 180 мм",
    )
    details_slots = pump_details.debug["slots"]
    deferred = set(details_slots.get("deferred_slot_keys") or [])
    if float(details_slots.get("head_m") or details_slots.get("required_head_m") or 0) != 6:
        failures.append("known pump head was lost beside a refused flow")
    if details_slots.get("mounting_length_mm") != 180:
        failures.append("known mounting length was lost beside a refused flow")
    if deferred.intersection({"head_m", "required_head_m", "mounting_length_mm"}):
        failures.append("known pump dimensions remained deferred")

    turn("warm-floor", "Хочу сделать тёплый пол, что нужно?")
    calculation = turn("warm-floor", "120 м², водяной от котла")
    calculated_slots = calculation.debug["slots"]
    if not {
        "warm_floor_pipe_min_m",
        "warm_floor_pipe_max_m",
        "warm_floor_contours",
    }.issubset(calculated_slots):
        failures.append("warm-floor calculation did not persist canonical facts")
    recall = turn("warm-floor", "Сколько трубы и сколько контуров?")
    recall_text = normalize_text(recall.answer)
    if "в карточке не указано" in recall_text:
        failures.append("warm-floor calculation lost precedence to product cards")
    before_links = list(bot.sessions.get("warm-floor").last_products)
    if len(before_links) > 1:
        links = turn("warm-floor", "Дай ссылки на все показанные товары")
        if len(links.products) != len(before_links):
            failures.append("mixed-view link request dropped shown products")

    boiler = turn(
        "boiler-memory",
        "Нужен газовый двухконтурный котёл на 100 м²",
    )
    remembered_type = turn("boiler-memory", "И какой тип я просил?")
    remembered_text = normalize_text(remembered_type.answer)
    if "газов" not in remembered_text or "двухконтур" not in remembered_text:
        failures.append("saved boiler type/contours were not recalled")

    # Use a real local catalogue identity whose card explicitly confirms a
    # built-in pump, then ask the exact customer question that used to erase it.
    builtin_boiler = next(
        (
            product
            for product in bot.search_agent.products
            if bot.search_agent.canonical_category(product) == "boilers"
            and bot.guardrails.builtin_part_states(product, ["насос"]).get("насос") is True
        ),
        None,
    )
    if builtin_boiler is None:
        failures.append("local catalogue has no boiler with a confirmed built-in pump")
    else:
        shown = turn(
            "builtin-pump",
            f"Покажи артикул {builtin_boiler.sku}",
        )
        if not shown.products:
            failures.append("exact built-in-pump boiler SKU was not shown")
        else:
            necessity = turn("builtin-pump", "А насос к нему нужен?")
            necessity_text = normalize_text(necessity.answer)
            necessity_state = bot.sessions.get("builtin-pump")
            if "уже встроен" not in necessity_text:
                failures.append("built-in pump was not acknowledged")
            if necessity_state.pending_category == "pumps":
                failures.append("built-in pump question started a new pump funnel")

    if llm_used_turns == 0:
        failures.append("no live turn successfully used OpenRouter")

    print(
        f"\nSUMMARY: requested_llm_turns={llm_requested_turns}; "
        f"used_llm_turns={llm_used_turns}; failures={len(failures)}"
    )
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("RESULT: LIVE OPENROUTER SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
