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
import hashlib
import json
import platform
import re
import subprocess
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
    execution_status: str = "valid"
    failure_stage: str = ""
    failure_reason: str = ""

    @property
    def id(self) -> str:
        return str(self.scenario["id"])

    def flag(self, code: str, note: str) -> None:
        self.defects[code] += 1
        self.evidence.append(f"{code}: {note}")

    def fail_execution(self, status: str, stage: str, reason: str) -> None:
        """Пометить прогон невалидным, не смешивая сбой с исходом покупателя."""
        self.execution_status = status
        self.failure_stage = stage
        self.failure_reason = reason


@dataclass(frozen=True)
class BuyerTurnResult:
    """Результат одного шага модели-покупателя вместе с качеством выполнения."""

    state: str = ""
    message: str = ""
    why: str = ""
    error_kind: str = ""
    error_detail: str = ""


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
        try:
            response = bot.handle_chat(session_id, user_message)
        except Exception as exc:
            run.fail_execution(
                "bot_error",
                "bot",
                f"{type(exc).__name__}: {exc}",
            )
            break
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
    if run.execution_status == "valid":
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

_MIN_BOT_TURNS_BEFORE_GIVE_UP = 3


def _buyer_turn(
    client: OpenRouterClient,
    scenario: dict[str, Any],
    history: list[dict[str, str]],
) -> BuyerTurnResult:
    """Следующая реплика покупателя либо отдельный сбой её генерации."""

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
        fallback={"state": "__buyer_error__", "message": "", "why": ""},
    )
    fallback_reason = str(getattr(client, "last_fallback_reason", None) or "").strip()
    if not ok or fallback_reason:
        return BuyerTurnResult(
            error_kind="buyer_provider_error",
            error_detail=fallback_reason or "модель-покупатель не вернула результат",
        )
    if getattr(client, "last_json_output_accepted", None) is False:
        return BuyerTurnResult(
            error_kind="buyer_invalid_output",
            error_detail="модель-покупатель вернула невалидный JSON",
        )
    if not isinstance(parsed, dict):
        return BuyerTurnResult(
            error_kind="buyer_invalid_output",
            error_detail="ответ модели-покупателя не является JSON-объектом",
        )

    state = str(parsed.get("state") or "").strip()
    if state not in {"continue", "satisfied", "gave_up"}:
        return BuyerTurnResult(
            error_kind="buyer_protocol_error",
            error_detail=f"неизвестное состояние покупателя: {state or '<empty>'}",
        )
    message = str(parsed.get("message") or "").strip()
    why = str(parsed.get("why") or "").strip()
    if state == "continue" and not message:
        return BuyerTurnResult(
            error_kind="buyer_protocol_error",
            error_detail="пустая реплика покупателя при state=continue",
        )
    return BuyerTurnResult(state=state, message=message, why=why)


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
    recorded_turns = scenario.get("recorded_user_turns") or []
    opening_message = str(recorded_turns[0] if recorded_turns else "").strip()
    if not opening_message:
        run.fail_execution(
            "harness_error",
            "harness",
            "в сценарии отсутствует фиксированная первая реплика recorded_user_turns[0]",
        )
        return run

    history: list[dict[str, str]] = []
    buyer_state = "continue"
    for n in range(1, max_turns + 1):
        # Первый ход — часть тестового сценария, а не генерация другой моделью.
        # Это удерживает live-прогоны на одинаковой задаче и делает сравнение
        # между версиями бота содержательным.
        buyer_turn = (
            BuyerTurnResult(state="continue", message=opening_message)
            if n == 1
            else _buyer_turn(client, scenario, history)
        )
        if buyer_turn.error_kind:
            run.fail_execution(
                buyer_turn.error_kind,
                "buyer",
                buyer_turn.error_detail,
            )
            run.buyer_note = buyer_turn.error_detail
            break
        buyer_state = buyer_turn.state
        message = buyer_turn.message
        why = buyer_turn.why
        if buyer_state != "continue":
            if buyer_state == "gave_up" and len(run.turns) < _MIN_BOT_TURNS_BEFORE_GIVE_UP:
                run.fail_execution(
                    "buyer_protocol_error",
                    "buyer",
                    "покупатель сдался раньше трёх ответов бота: "
                    f"получено {len(run.turns)}",
                )
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
            run.fail_execution("bot_error", "bot", f"ход {n}: {error}")
            run.buyer_note = "технический сбой"
            break
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer})
    else:
        buyer_state = "continue"

    if run.execution_status == "valid":
        for detector in DETECTORS:
            detector(run)
        detect_fabricated_sku(run, catalog_skus)
        run.outcome = classify_outcome(run, buyer_state)
    run.buyer_state = buyer_state
    return run


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return ""


def _stable_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _runtime_tree_sha256() -> str:
    """Отпечаток исполняемого Python-кода, включая незакоммиченные правки."""
    files = sorted((PROJECT_ROOT / "app").rglob("*.py"))
    files.append(Path(__file__).resolve())
    business_config = PROJECT_ROOT / "data" / "business_config.json"
    if business_config.exists():
        files.append(business_config)

    digest = hashlib.sha256()
    for path in sorted(set(files)):
        try:
            relative = path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
            payload = path.read_bytes()
        except (OSError, ValueError):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _git_manifest() -> dict[str, Any]:
    """Версия Git без требования, что harness запущен внутри clone."""

    def invoke(*args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()

    commit = invoke("rev-parse", "HEAD") or None
    status = invoke(
        "status",
        "--porcelain",
        "--",
        "app",
        "scripts/run_live_dialogues.py",
        "data/live_dialogue_testset.json",
        "data/business_config.json",
    )
    dirty = bool(status) if commit else None
    return {
        "commit": commit,
        "dirty": dirty,
        "status_sha256": _sha256_bytes(status.encode("utf-8")) if status else "",
        "runtime_tree_sha256": _runtime_tree_sha256(),
        "reproducible": bool(commit) and dirty is False,
    }


def _catalog_sha256(products: list[Any]) -> str:
    """Порядконезависимый отпечаток реально загруженного каталога."""
    canonical: list[dict[str, Any]] = []
    for product in products:
        if hasattr(product, "model_dump"):
            payload = product.model_dump(mode="json")
        elif hasattr(product, "dict"):
            payload = product.dict()
        elif isinstance(product, dict):
            payload = dict(product)
        else:
            payload = {"value": str(product)}
        # Это время создания объекта, а не бизнес-данные карточки. Оно делает
        # одинаковый каталог разным при повторном чтении того же источника.
        payload.pop("updated_at", None)
        canonical.append(payload)
    canonical.sort(
        key=lambda item: (
            str(item.get("sku") or ""),
            str(item.get("name") or ""),
            _stable_json_bytes(item),
        )
    )
    return _sha256_bytes(_stable_json_bytes(canonical))


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build_manifest(
    args: argparse.Namespace,
    bot: ChatOrchestrator,
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    """Собрать безопасный manifest всех входов, влияющих на результат."""
    testset_path = Path(args.testset)
    products = list(getattr(bot.search_agent, "products", None) or [])
    settings = getattr(getattr(bot, "llm_client", None), "settings", None)
    llm: dict[str, Any] = {}
    if settings is not None:
        # Явный allowlist: ключи, URL приватных хранилищ и локальные пути сюда
        # никогда не попадут.
        llm = {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "strong_model": settings.llm_model_strong,
            "timeout_seconds": settings.llm_timeout_seconds,
            "request_timeout_seconds": settings.llm_request_timeout_seconds,
            "max_retries": settings.llm_max_retries,
            "retry_delay_seconds": settings.llm_retry_delay_seconds,
        }

    business_config = PROJECT_ROOT / "data" / "business_config.json"
    return {
        "schema_version": 1,
        "git": _git_manifest(),
        "inputs": {
            "testset_path": _display_path(testset_path),
            "testset_sha256": _sha256_file(testset_path),
            "scenario_ids": [str(scenario.get("id") or "") for scenario in scenarios],
            "catalog_source": str(getattr(bot, "products_loaded_from", "")),
            "catalog_products": len(products),
            "catalog_sha256": _catalog_sha256(products),
            "business_config_sha256": _sha256_file(business_config),
            "buyer_prompt_sha256": _sha256_bytes(_BUYER_SYSTEM.encode("utf-8")),
            "harness_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "llm": llm,
        "run": {
            "mode": args.mode,
            "workers": args.workers,
            "max_turns": args.max_turns,
            "limit": args.limit,
            "only": args.only,
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
    }


def build_report(
    runs: list[DialogueRun],
    mode: str,
    elapsed: float,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    valid_runs = [run for run in runs if run.execution_status == "valid"]
    attempted_turns = [turn for run in runs for turn in run.turns]
    turns = [turn for run in valid_runs for turn in run.turns]
    latencies = sorted(turn.latency_sec for turn in turns)
    defects: Counter = Counter()
    dialogues_with: Counter = Counter()
    for run in valid_runs:
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
        "manifest": manifest or {},
        # ``dialogues`` остаётся числом попыток для совместимости со старыми
        # потребителями. Все продуктовые метрики ниже имеют явный знаменатель.
        "dialogues": len(runs),
        "dialogues_attempted": len(runs),
        "dialogues_valid": len(valid_runs),
        "dialogues_invalid": len(runs) - len(valid_runs),
        "metric_denominator_dialogues": len(valid_runs),
        "execution_outcomes": dict(Counter(run.execution_status for run in runs)),
        "turns_attempted": len(attempted_turns),
        "turns": len(turns),
        "turns_with_cards": sum(1 for turn in turns if turn.products),
        "dialogues_with_cards": sum(
            1 for run in valid_runs if any(turn.products for turn in run.turns)
        ),
        "answer_sources": dict(Counter(turn.source for turn in turns)),
        "outcomes": dict(Counter(run.outcome for run in valid_runs if run.outcome)),
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
                "execution_status": run.execution_status,
                "failure_stage": run.failure_stage,
                "failure_reason": run.failure_reason,
                "defects": dict(run.defects),
                "evidence": run.evidence,
            }
            for run in runs
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    attempted = report.get("dialogues_attempted", report["dialogues"])
    valid = report.get("dialogues_valid", report["dialogues"])
    invalid = report.get("dialogues_invalid", attempted - valid)
    git_info = (report.get("manifest") or {}).get("git") or {}
    inputs = (report.get("manifest") or {}).get("inputs") or {}
    lines = [
        f"# Прогон тест-набора — режим `{report['mode']}`",
        "",
        f"- Диалогов: **{attempted}** попыток / **{valid}** валидных / "
        f"**{invalid}** невалидных; ходов в валидных: **{report['turns']}**",
        f"- Ходов с карточками: **{report['turns_with_cards']}**",
        f"- Диалогов с показанным товаром: **{report['dialogues_with_cards']}**",
        f"- Латентность p50 / p95 / max: "
        f"**{report['latency_sec']['p50']} / {report['latency_sec']['p95']} / "
        f"{report['latency_sec']['max']} с**",
        f"- Источник ответа: `{report['answer_sources']}`",
        "",
        f"- Исходы: `{report.get('outcomes')}`",
        "",
        f"- Статусы выполнения: `{report.get('execution_outcomes')}`",
        "",
        "## Воспроизводимость",
        "",
        f"- Commit: `{git_info.get('commit') or 'unknown'}`; "
        f"dirty: `{git_info.get('dirty')}`; "
        f"runtime: `{git_info.get('runtime_tree_sha256') or 'unknown'}`",
        f"- Testset: `{inputs.get('testset_sha256') or 'unknown'}`; "
        f"catalog: `{inputs.get('catalog_sha256') or 'unknown'}`",
        "",
    ]
    invalid_runs = [
        run for run in report["runs"] if run.get("execution_status", "valid") != "valid"
    ]
    if invalid_runs:
        lines += ["## Невалидные диалоги", ""]
        for run in invalid_runs:
            reason = run.get("failure_reason") or "причина не записана"
            stage = run.get("failure_stage") or "unknown"
            lines.append(
                f"- **{run['id']}** — `{run['execution_status']}` "
                f"(этап `{stage}`): {reason}"
            )
        lines.append("")
    lines += [
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
        if run.get("execution_status", "valid") != "valid":
            continue
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
    manifest = build_manifest(args, bot, scenarios)

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
                    run.fail_execution(
                        "harness_error",
                        "harness",
                        f"{type(exc).__name__}: {exc}",
                    )
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

    report = build_report(runs, args.mode, elapsed, manifest)
    output_dir = args.output_dir or (
        PROJECT_ROOT / "reports" / f"replay_{datetime.now().strftime('%Y-%m-%d_%H%M')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (output_dir / "transcripts.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": run.id,
                    "execution_status": run.execution_status,
                    "failure_stage": run.failure_stage,
                    "failure_reason": run.failure_reason,
                    "outcome": run.outcome,
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
