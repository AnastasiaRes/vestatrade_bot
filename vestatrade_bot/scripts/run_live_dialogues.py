#!/usr/bin/env python3
"""Прогон тест-набора живых диалогов с машинными детекторами дефектов.

Зачем это нужно. Скрипт, которым получен прогон 23.08.2026, в репозитории
отсутствовал, а исходы диалогов («покупатель сдался», «бот ходит по кругу»)
проставляла та же модель, что играла покупателя. Ни то ни другое не позволяет
сравнить «до» и «после»: набор не воспроизводим, а разметка плывёт от прогона
к прогону.

Здесь набор лежит в репозитории (``data/live_dialogue_testset.json``), а всё,
что считается метрикой, считается детерминированно по стенограмме.

Два режима
----------

``replay`` (по умолчанию)
    Боту подаются те же 622 реплики покупателя, что и в исходном прогоне.
    Модель для покупателя не нужна, прогон бесплатный и полностью
    воспроизводимый. Это основной режим для сравнения «до/после»: вход
    зафиксирован, поэтому любое расхождение — следствие правок в коде.

    Оговорка: после исправлений живой покупатель написал бы другое. Режим
    измеряет наличие дефектов, а не удовлетворённость клиента.

``live``
    Покупателя играет модель (нужен OpenRouter). Даёт сквозную метрику
    «покупатель получил своё», но стоит денег и воспроизводим лишь частично.

Детекторы
---------

Каждый детектор назван по коду дефекта из разбора прогона и срабатывает только
на объективно проверяемом признаке — повтор ответа, повторный запрос уже
названного контакта, артикул вне каталога, обрыв на середине слова.

Запуск::

    .venv/bin/python scripts/run_live_dialogues.py --mode replay \\
        --output-dir reports/replay_$(date +%F)

    .venv/bin/python scripts/run_live_dialogues.py --limit 5   # быстрая проверка
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.orchestrator import ChatOrchestrator  # noqa: E402
from app.agents.utils import normalize_text  # noqa: E402
from app.openrouter_client import OpenRouterClient  # noqa: E402

DEFAULT_TESTSET = PROJECT_ROOT / "data" / "live_dialogue_testset.json"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")
# Просьба оставить контакт — в любой из формулировок бота.
_ASKS_CONTACT_RE = re.compile(
    r"(?:оставьте|нужен|укажите|напишите)[^.!?]{0,40}(?:телефон|email|почт)",
    re.IGNORECASE,
)
# Артикул вида VT.331.N.04 / VTp.700.FB20.20.
_SKU_TOKEN_RE = re.compile(r"\b[A-Za-zА-Яа-я]{2,}[.\-][A-Za-zА-Яа-я0-9.\-/]*[A-Za-zА-Яа-я0-9]\b")
_LABELLED_SKU_RE = re.compile(
    r"\b(?:артикул|арт|sku)\b\.?\s*[:№#-]?\s*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/\-]{2,})",
    re.IGNORECASE,
)


# Реквизиты, а не способ связи: рядом с этими словами длинная последовательность
# цифр телефоном не является.
_IDENTIFIER_CONTEXT_RE = re.compile(
    r"(?:инн|огрн(?:ип)?|кпп|окпо|бик|расчетн\w*\s+счет\w*|"
    r"номер\s+заказа|заказ\w*\s*№?)\s*[:№#-]?\s*$",
    re.IGNORECASE,
)


def _contains_phone(text: str) -> bool:
    for match in _PHONE_RE.finditer(text):
        prefix = text[max(0, match.start() - 40) : match.start()]
        if _IDENTIFIER_CONTEXT_RE.search(prefix.rstrip()):
            continue
        return True
    return False


def _norm_sku(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().replace("ё", "е"))


# ---------------------------------------------------------------------------
# Стенограмма
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    n: int
    user: str
    bot: str
    products: list[str]
    source: str
    latency_sec: float
    intent: str | None = None
    category: str | None = None


@dataclass
class DialogueRun:
    scenario: dict[str, Any]
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    defects: Counter = field(default_factory=Counter)
    evidence: list[str] = field(default_factory=list)
    outcome: str = ""
    buyer_state: str = ""
    buyer_note: str = ""

    @property
    def id(self) -> str:
        return str(self.scenario["id"])

    def flag(self, code: str, note: str) -> None:
        self.defects[code] += 1
        self.evidence.append(f"{code}: {note}")


# ---------------------------------------------------------------------------
# Детекторы. Каждый — по коду дефекта из разбора прогона.
# ---------------------------------------------------------------------------


def detect_repeated_answer(run: DialogueRun) -> None:
    """Д11: бот повторяет один и тот же ответ.

    Две метрики, потому что они измеряют разное. ``Д11`` — любой дословный
    повтор: так видно, что покупатель получил ту же реплику второй раз.
    ``Д11-круг`` — тот же ответ три раза и более: это уже буксование, ради
    которого и делался разрыв. Политика намеренно допускает один повтор
    (существующие лестницы эскалации на нём дают подсказку по конкретному
    слоту), поэтому ``Д11`` в ноль не уйдёт и не должен.
    """
    seen: Counter = Counter()
    for turn in run.turns:
        key = normalize_text(turn.bot)
        if len(key) < 40:
            continue
        seen[key] += 1
        if seen[key] == 2:
            run.flag("Д11", f"ход {turn.n}: ответ повторён дословно")
        elif seen[key] == 3:
            run.flag("Д11-круг", f"ход {turn.n}: тот же ответ третий раз")


def detect_contact_reasked(run: DialogueRun) -> None:
    """Д2: бот просит контакт, который покупатель уже назвал.

    Контактом считается только способ связи. ИНН, ОГРН и номер заказа — это
    реквизиты запроса: бот обязан спросить телефон, даже если покупатель уже
    продиктовал десять цифр ИНН.
    """
    given_at: int | None = None
    for turn in run.turns:
        if given_at is not None and _ASKS_CONTACT_RE.search(turn.bot):
            run.flag(
                "Д2",
                f"ход {turn.n}: контакт назван на ходу {given_at}, но запрошен снова",
            )
            return
        if _EMAIL_RE.search(turn.user) or _contains_phone(turn.user):
            given_at = turn.n


def detect_dead_command(run: DialogueRun) -> None:
    """Д3: бот предложил команду, а на неё же не отреагировал.

    Считается только голая команда. «Мощность 30 кВт — перебор, покажите
    варианты на 15–18 кВт с надёжным бойлером» несёт собственные параметры:
    это обычный запрос, и подменять его выдачей по категории было бы потерей
    того, что покупатель уже сказал.
    """
    offered = False
    for turn in run.turns:
        user_norm = normalize_text(turn.user).replace("покажи ", "покажите ")
        is_bare_command = (
            "покажите варианты" in user_norm and len(user_norm.split()) <= 6
        )
        if offered and is_bare_command:
            if not turn.products:
                run.flag("Д3", f"ход {turn.n}: «покажите варианты» не показали варианты")
            offered = False
        offered = "покажите варианты" in normalize_text(turn.bot)


def detect_truncated_answer(run: DialogueRun) -> None:
    """Д12: ответ оборван по лимиту токенов."""
    for turn in run.turns:
        text = turn.bot.rstrip()
        if not text:
            continue
        if text.endswith("…") and not text.endswith(("...", ". …")):
            run.flag("Д12", f"ход {turn.n}: ответ обрывается многоточием")
        elif re.search(r"[А-Яа-яA-Za-z]{2,}$", text) and not text.endswith(
            (".", "!", "?", ":", ")", "»", "—", "/")
        ):
            run.flag("Д12", f"ход {turn.n}: ответ обрывается на середине слова")


def detect_unattributed_llm(run: DialogueRun) -> None:
    """Д4/Д5: текст модели попал в ответ мимо проверок достоверности."""
    for turn in run.turns:
        if turn.source == "llm_unattributed":
            run.flag("Д4", f"ход {turn.n}: непроверенный текст модели в ответе")


def detect_fabricated_sku(run: DialogueRun, catalog_skus: set[str]) -> None:
    """Д4: бот назвал артикул, которого нет в каталоге.

    Артикул, который придумал сам покупатель, выдумкой бота не является: в
    сводке для менеджера бот обязан процитировать запрос дословно. Поэтому всё,
    что встречалось в репликах покупателя, из проверки исключается.
    """
    if not catalog_skus:
        return
    quoted_by_user: set[str] = set()
    reported: set[str] = set()
    for turn in run.turns:
        for raw in _SKU_TOKEN_RE.findall(turn.user) + _LABELLED_SKU_RE.findall(turn.user):
            quoted_by_user.add(_norm_sku(raw.rstrip(".,;:!?")))
        for raw in _LABELLED_SKU_RE.findall(turn.bot):
            key = _norm_sku(raw.rstrip(".,;:!?"))
            if len(key) < 4 or key in catalog_skus or key in quoted_by_user:
                continue
            # Артикул в фиде может нести хвост через пробел («KW.ST900.304.2212 RU»),
            # который шаблон обрывает, а название товара — ссылаться на соседнюю
            # позицию («Инвертор потока для косого фильтра, арт. VT.192 1"»).
            # Ни то ни другое выдумкой не является.
            if any(sku.startswith(key) for sku in catalog_skus):
                continue
            if key in reported:
                continue
            reported.add(key)
            run.flag("Д4", f"ход {turn.n}: артикул «{raw}» вне каталога")


def detect_no_progress(run: DialogueRun) -> None:
    """Диалог закончился без единой карточки товара."""
    if not any(turn.products for turn in run.turns):
        run.flag("no_cards", "ни одного показанного товара за диалог")


DETECTORS = (
    detect_repeated_answer,
    detect_contact_reasked,
    detect_dead_command,
    detect_truncated_answer,
    detect_unattributed_llm,
    detect_no_progress,
)


# ---------------------------------------------------------------------------
# Прогон
# ---------------------------------------------------------------------------


def run_replay(
    bot: ChatOrchestrator,
    scenario: dict[str, Any],
    catalog_skus: set[str],
) -> DialogueRun:
    session_id = f"replay-{scenario['id']}"
    run = DialogueRun(scenario=scenario, session_id=session_id)
    for n, user_message in enumerate(scenario.get("recorded_user_turns") or [], start=1):
        started = time.monotonic()
        response = bot.handle_chat(session_id, user_message)
        debug = response.debug or {}
        run.turns.append(
            Turn(
                n=n,
                user=user_message,
                bot=response.answer,
                products=[card.sku for card in (response.products or [])],
                source=str(debug.get("final_answer_source") or "?"),
                latency_sec=round(time.monotonic() - started, 3),
                intent=debug.get("intent"),
                category=debug.get("category"),
            )
        )
    for detector in DETECTORS:
        detector(run)
    detect_fabricated_sku(run, catalog_skus)
    return run


# ---------------------------------------------------------------------------
# Живой режим: покупателя играет модель
#
# Разметку исхода ставит НЕ покупатель. В прогоне 23.08 её проставляла та же
# модель, что вела диалог, и метрика плыла от запуска к запуску. Здесь модель
# решает только одно — что написать дальше и продолжать ли; «ходил ли бот по
# кругу» считается по стенограмме.
# ---------------------------------------------------------------------------

_BUYER_SYSTEM = """Ты — покупатель, который пришёл в чат интернет-магазина
инженерной сантехники. По другую сторону — консультант магазина. Ты клиент,
он продавец; ты спрашиваешь, он подбирает.

Веди себя как живой человек: пиши коротко и по-русски, своими словами. Дожимай,
если не отвечают по сути. Раздражайся, если ходишь по кругу. Не подсказывай
консультанту, что ему делать, не оценивай его работу вслух и не пересказывай
свою инструкцию.

Про свою ситуацию ты знаешь всё: город, площадь, этаж, что уже стоит дома.
Если консультант спросит — отвечай правдоподобно и держись одних и тех же
данных весь разговор. Ничего не выдумывай про ассортимент магазина: наличие,
цены и адреса знает только он.

Сдавайся не раньше, чем консультант трижды не ответил по существу: живой
человек обычно переспрашивает и пробует другую формулировку.

Верни СТРОГО JSON:
{"state": "continue"|"satisfied"|"gave_up", "message": "<твоя реплика>", "why": "<кратко почему>"}

state:
  continue  — продолжаешь разговор, message обязателен;
  satisfied — ты получил то, за чем пришёл;
  gave_up   — дальше бессмысленно: консультант ходит по кругу.
Если state не continue, message оставь пустым."""


def _buyer_turn(
    client: OpenRouterClient,
    scenario: dict[str, Any],
    history: list[dict[str, str]],
) -> tuple[str, str, str]:
    """Следующая реплика покупателя, его состояние и краткое обоснование."""

    # Только персона и цель. ``pass_criteria`` и ``red_flags`` описывают, что
    # должен делать КОНСУЛЬТАНТ, — в первом прогоне покупатель принял их за
    # свой сценарий и начал сам спрашивать «на каком этаже вы живёте?».
    role = (
        f"Кто ты: {scenario.get('persona')}\n"
        f"Зачем пришёл: {scenario.get('goal')}"
    )
    lines = [f"{'ТЫ' if m['role'] == 'user' else 'БОТ'}: {m['content']}" for m in history]
    transcript = "\n".join(lines) if lines else "(разговор ещё не начат)"
    messages = [
        {"role": "system", "content": _BUYER_SYSTEM},
        {
            "role": "user",
            "content": f"{role}\n\nРазговор:\n{transcript}\n\nТвой следующий шаг:",
        },
    ]
    parsed, ok = client.complete_json(
        agent="LiveBuyer",
        messages=messages,
        fallback={"state": "gave_up", "message": "", "why": "покупатель не ответил"},
    )
    state = str(parsed.get("state") or "gave_up")
    if state not in {"continue", "satisfied", "gave_up"}:
        state = "gave_up"
    message = str(parsed.get("message") or "").strip()
    why = str(parsed.get("why") or "").strip()
    if state == "continue" and not message:
        state, why = "gave_up", why or "пустая реплика покупателя"
    if not ok and state == "continue":
        state, why = "gave_up", "не удалось получить реплику покупателя"
    return state, message, why


def classify_outcome(run: "DialogueRun", buyer_state: str) -> str:
    """Исход диалога — по стенограмме, а не по мнению модели.

    Приоритет у машинных признаков: дословный повтор ответа бота и повтор
    реплики покупателя проверяются объективно. Самооценка покупателя
    используется только там, где объективного признака нет.
    """
    answers = [normalize_text(t.bot) for t in run.turns if len(normalize_text(t.bot)) >= 40]
    if len(answers) - len(set(answers)) >= 2:
        return "bot_loop"
    users = [normalize_text(t.user) for t in run.turns]
    if len(users) - len(set(users)) >= 1:
        return "user_repeated"
    if buyer_state == "satisfied":
        return "satisfied"
    if buyer_state == "gave_up":
        return "user_gave_up"
    return "turn_limit"


def run_live(
    bot: ChatOrchestrator,
    client: OpenRouterClient,
    scenario: dict[str, Any],
    catalog_skus: set[str],
    *,
    max_turns: int = 45,
) -> DialogueRun:
    session_id = f"live-{scenario['id']}-{uuid.uuid4().hex[:8]}"
    run = DialogueRun(scenario=scenario, session_id=session_id)
    history: list[dict[str, str]] = []
    buyer_state = "continue"
    for n in range(1, max_turns + 1):
        buyer_state, message, why = _buyer_turn(client, scenario, history)
        if buyer_state != "continue":
            run.buyer_note = why
            break
        started = time.monotonic()
        try:
            response = bot.handle_chat(session_id, message)
            answer, products, debug = response.answer, response.products or [], response.debug or {}
            error = None
        except Exception as exc:  # прогон не должен падать из-за одного диалога
            answer, products, debug = "", [], {}
            error = f"{type(exc).__name__}: {exc}"
        latency = round(time.monotonic() - started, 3)
        run.turns.append(
            Turn(
                n=n,
                user=message,
                bot=answer,
                products=[card.sku for card in products],
                source=str(debug.get("final_answer_source") or "?"),
                latency_sec=latency,
                intent=debug.get("intent"),
                category=debug.get("category"),
            )
        )
        if error:
            run.flag("tech_error", f"ход {n}: {error}")
            run.buyer_note = "технический сбой"
            break
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer})
    else:
        buyer_state = "continue"

    for detector in DETECTORS:
        detector(run)
    detect_fabricated_sku(run, catalog_skus)
    run.outcome = classify_outcome(run, buyer_state)
    run.buyer_state = buyer_state
    return run


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------


def build_report(runs: list[DialogueRun], mode: str, elapsed: float) -> dict[str, Any]:
    turns = [turn for run in runs for turn in run.turns]
    latencies = sorted(turn.latency_sec for turn in turns)
    defects: Counter = Counter()
    dialogues_with: Counter = Counter()
    for run in runs:
        defects.update(run.defects)
        for code in run.defects:
            dialogues_with[code] += 1

    def pct(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        return round(values[min(len(values) - 1, int(len(values) * q))], 2)

    return {
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "dialogues": len(runs),
        "turns": len(turns),
        "turns_with_cards": sum(1 for turn in turns if turn.products),
        "dialogues_with_cards": sum(
            1 for run in runs if any(turn.products for turn in run.turns)
        ),
        "answer_sources": dict(Counter(turn.source for turn in turns)),
        "outcomes": dict(Counter(run.outcome for run in runs if run.outcome)),
        "latency_sec": {
            "p50": pct(latencies, 0.50),
            "p95": pct(latencies, 0.95),
            "max": round(latencies[-1], 2) if latencies else 0.0,
        },
        "defect_hits": dict(defects.most_common()),
        "defect_dialogues": dict(dialogues_with.most_common()),
        "runs": [
            {
                "id": run.id,
                "block": run.scenario["block"],
                "priority": run.scenario["priority"],
                "turns": len(run.turns),
                "cards_shown": sum(1 for turn in run.turns if turn.products),
                "outcome": run.outcome,
                "buyer_state": run.buyer_state,
                "buyer_note": run.buyer_note,
                "defects": dict(run.defects),
                "evidence": run.evidence,
            }
            for run in runs
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Прогон тест-набора — режим `{report['mode']}`",
        "",
        f"- Диалогов: **{report['dialogues']}**, ходов: **{report['turns']}**",
        f"- Ходов с карточками: **{report['turns_with_cards']}**",
        f"- Диалогов с показанным товаром: **{report['dialogues_with_cards']}**",
        f"- Латентность p50 / p95 / max: "
        f"**{report['latency_sec']['p50']} / {report['latency_sec']['p95']} / "
        f"{report['latency_sec']['max']} с**",
        f"- Источник ответа: `{report['answer_sources']}`",
        "",
        f"- Исходы: `{report.get('outcomes')}`",
        "",
        "## Дефекты",
        "",
        "| Код | Срабатываний | Диалогов |",
        "|---|---:|---:|",
    ]
    for code, hits in report["defect_hits"].items():
        lines.append(f"| {code} | {hits} | {report['defect_dialogues'].get(code, 0)} |")
    if not report["defect_hits"]:
        lines.append("| — | 0 | 0 |")
    lines += ["", "## Диалоги с замечаниями", ""]
    for run in report["runs"]:
        if not run["defects"]:
            continue
        lines.append(f"**{run['id']}** ({run['priority']}) — {run['defects']}")
        for note in run["evidence"]:
            lines.append(f"  - {note}")
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--testset", type=Path, default=DEFAULT_TESTSET)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="взять первые N сценариев")
    parser.add_argument("--only", default=None, help="список id через запятую: A01,B08")
    parser.add_argument(
        "--workers", type=int, default=5, help="параллельных диалогов в режиме live"
    )
    parser.add_argument(
        "--max-turns", type=int, default=45, help="лимит ходов покупателя в режиме live"
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    payload = json.loads(args.testset.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        scenarios = [s for s in scenarios if s["id"] in wanted]
    if args.limit:
        scenarios = scenarios[: args.limit]

    bot = ChatOrchestrator()
    bot._ensure_products_loaded()
    catalog_skus = {
        _norm_sku(product.sku) for product in bot.search_agent.products if product.sku
    }
    print(
        f"Каталог: {len(bot.search_agent.products)} позиций "
        f"({bot.products_loaded_from}); сценариев: {len(scenarios)}",
        file=sys.stderr,
    )

    started = time.monotonic()
    runs: list[DialogueRun] = []
    if args.mode == "replay":
        for index, scenario in enumerate(scenarios, start=1):
            run = run_replay(bot, scenario, catalog_skus)
            runs.append(run)
            print(
                f"[{index:>3}/{len(scenarios)}] {run.id:<4} "
                f"ходов {len(run.turns):>2}  дефекты {dict(run.defects) or '—'}",
                file=sys.stderr,
            )
    else:
        # Диалоги независимы: оркестратор изолирует состояние по сессиям и
        # сериализует только ходы одной. Последовательный прогон ста диалогов
        # с двумя вызовами модели на ход занимает часы.
        client = OpenRouterClient()
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    run_live, bot, client, scenario, catalog_skus, max_turns=args.max_turns
                ): scenario
                for scenario in scenarios
            }
            for future in as_completed(futures):
                scenario = futures[future]
                try:
                    run = future.result()
                except Exception as exc:
                    run = DialogueRun(scenario=scenario, session_id="")
                    run.outcome = "harness_error"
                    run.flag("tech_error", f"{type(exc).__name__}: {exc}")
                runs.append(run)
                done += 1
                print(
                    f"[{done:>3}/{len(scenarios)}] {run.id:<4} "
                    f"ходов {len(run.turns):>2}  исход {run.outcome:<14} "
                    f"дефекты {dict(run.defects) or '—'}",
                    file=sys.stderr,
                )
        runs.sort(key=lambda item: item.id)
    elapsed = time.monotonic() - started

    report = build_report(runs, args.mode, elapsed)
    output_dir = args.output_dir or (
        PROJECT_ROOT / "reports" / f"replay_{datetime.now().strftime('%Y-%m-%d_%H%M')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (output_dir / "transcripts.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": run.id,
                    "turns": [turn.__dict__ for turn in run.turns],
                },
                ensure_ascii=False,
            )
            + "\n"
            for run in runs
        ),
        encoding="utf-8",
    )

    print(f"\nОтчёт: {output_dir}", file=sys.stderr)
    print(json.dumps(report["defect_hits"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
