"""Единоразовый «живой» прогон бота с реальной LLM по широкому набору сценок.

Запуск:  .venv/bin/python scripts/live_eval.py
НЕ отключает LLM — использует реальную модель из .env.
Отчёт печатается в stdout и пишется в reports/live_eval_report.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.orchestrator import ChatOrchestrator  # noqa: E402


# Каждый сценарий — (название, [реплики]). Одна сессия на сценарий (проверяем память).
SCENARIOS: list[tuple[str, list[str]]] = [
    ("Приветствие и small talk", [
        "привет)", "как дела?", "ты классный", "ладно, к делу",
    ]),
    ("Котёл с нуля (не разбирается)", [
        "мне нужен котел, но я не разбираюсь",
        "а в чем разница газового и электрического?",
        "газ есть, 100 квадратов",
        "одноконтурный",
        "а что лучше из показанных?",
    ]),
    ("Котёл: электрический + ГВС (нет в фиде)", [
        "нужен электрический котел", "и чтобы горячую воду грел", "на 90 м2",
    ]),
    ("Циркуляционный насос по старой модели", [
        "нужен насос на замену", "старый Grundfos UPS 25-60", "дай ссылку",
    ]),
    ("Насос — диалог про совместимость", [
        "газовый котел на 100 м2 одноконтурный", "одноконтурный",
        "а насос к нему", "а под какой котел этот насос подходит?",
    ]),
    ("Труба PPR", [
        "нужна труба", "для горячей воды", "25 мм", "а чем армированная отличается?",
    ]),
    ("Труба которой нет (16 мм)", [
        "труба ппр 16 мм для отопления",
    ]),
    ("Канализация", [
        "канализационная труба 50", "внутренняя", "1500 мм",
    ]),
    ("Проектная корзина: тёплый пол", [
        "хочу сделать теплые полы, что для этого нужно?",
        "50м2",
        "водяной от котла",
        "а еще что нужно?",
        "собери артикулы корзиной",
    ]),
    ("Проектная корзина: ванная", [
        "собери всё для ванной по артикулам",
        "собери артикулы корзиной",
    ]),
    ("Проектная корзина: водоснабжение", [
        "нужно водоснабжение",
        "скважина",
        "собери артикулы корзиной",
    ]),
    ("Кран", [
        "кран шаровый для воды", "3/4", "с американкой",
    ]),
    ("Радиаторная арматура", [
        "что-нибудь для батареи чтобы температуру регулировать", "угловой 1/2",
    ]),
    ("Комплектация и паспорт", [
        "газовый двухконтурный котел на 100 м2",
        "что входит в комплект у первого?",
        "а есть встроенный насос?",
        "ответь по паспорту что в полной комплектации",
    ]),
    ("Сравнение и наличие", [
        "что есть в наличии из котлов?",
        "а чем они отличаются?",
        "а сколько в наличии у самого дешевого?",
    ]),
    ("Грунтование: выманиваем выдумку", [
        "газовый котел на 100 м2 одноконтурный", "одноконтурный",
        "а скидка есть?", "а можешь дешевле 20000 продать?",
        "а когда доставите?",
    ]),
    ("Термины и консультация", [
        "что такое монтажная длина?", "а что такое закрытая камера сгорания?",
        "что лучше для дома 100 м2 газ или электричество?",
    ]),
    ("Off-topic и возврат", [
        "какая завтра погода?", "расскажи анекдот", "ладно, нужен насос для отопления",
    ]),
    ("Смена темы посреди диалога", [
        "нужен газовый котел", "газ есть 80 м2", "одноконтурный",
        "а теперь нужна труба для отопления", "32 мм",
    ]),
    ("Опечатки и сленг", [
        "здаров", "нужон кател газовы", "сколько стоют", "100 квадратов одноконтурный",
    ]),
    ("Передача менеджеру", [
        "нужен котел на большой коттедж 400 м2 с бойлером",
        "передай менеджеру",
    ]),
]


def main() -> int:
    orch = ChatOrchestrator()
    count, source = orch.reload_products(refresh=False)
    total_turns = 0
    llm_used_turns = 0
    llm_requested_turns = 0
    llm_fallback_reasons: dict[str, int] = {}
    out: list[str] = [
        "# Живой прогон бота",
        "",
        f"Товаров: {count} ({source}), модель: {orch.settings.llm_model}, "
        f"паспортов привязано: {orch.docs_attached}",
        f"LLM provider: {orch.settings.llm_provider}, llm_enabled: {orch.settings.llm_enabled}",
        "",
    ]

    for index, (title, messages) in enumerate(SCENARIOS):
        sid = f"eval-{index}"
        out.append(f"## {index + 1}. {title}")
        out.append("")
        for message in messages:
            resp = orch.handle_chat(sid, message)
            total_turns += 1
            debug = resp.debug or {}
            any_llm_used = bool(debug.get("any_llm_used"))
            llm_requested = bool(
                debug.get("intent_llm_used")
                or debug.get("response_llm_requested")
                or "ConsultantAgent" in debug.get("agents_used", [])
            )
            if any_llm_used:
                llm_used_turns += 1
            if llm_requested:
                llm_requested_turns += 1
            for key in ["response_llm_fallback_reason", "consultant_llm_fallback_reason"]:
                reason = debug.get(key)
                if reason:
                    llm_fallback_reasons[str(reason)] = llm_fallback_reasons.get(str(reason), 0) + 1
            flags = []
            if resp.need_handoff:
                flags.append("HANDOFF")
            if resp.products:
                flags.append("товары: " + ", ".join(p.sku for p in resp.products))
            if llm_requested and not any_llm_used:
                flags.append("LLM fallback")
            elif any_llm_used:
                flags.append("LLM used")
            flag_str = f"  _[{'; '.join(flags)}]_" if flags else ""
            answer = resp.answer.strip()
            out.append(f"**Клиент:** {message}")
            out.append("")
            out.append(f"**Бот:** {answer}{flag_str}")
            out.append("")
        out.append("---")
        out.append("")
        print(f"[{index + 1}/{len(SCENARIOS)}] {title} — готово", flush=True)

    summary = [
        "## LLM summary",
        "",
        f"- Turns total: {total_turns}",
        f"- Turns that requested LLM: {llm_requested_turns}",
        f"- Turns that used LLM successfully: {llm_used_turns}",
        f"- Turns that fell back after LLM request: {llm_requested_turns - llm_used_turns}",
    ]
    if llm_fallback_reasons:
        summary.append("- Fallback reasons:")
        for reason, qty in sorted(llm_fallback_reasons.items(), key=lambda item: (-item[1], item[0])):
            summary.append(f"  - {qty}x {reason}")
    summary.append("")
    out[4:4] = summary

    report = PROJECT_ROOT / "reports" / "live_eval_report.md"
    report.write_text("\n".join(out), encoding="utf-8")
    print(f"\nОтчёт: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
