"""Контракты воспроизводимости и валидности live/replay harness."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from typing import Any

from app.models import Product
from scripts import run_live_dialogues as harness


def _scenario(**updates: Any) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "id": "T01",
        "block": "Тестовый блок",
        "priority": "P0",
        "persona": "Покупатель",
        "goal": "Получить ответ",
        "recorded_user_turns": ["Фиксированная первая реплика"],
    }
    scenario.update(updates)
    return scenario


class _BuyerClient:
    def __init__(
        self,
        parsed: Any,
        *,
        ok: bool = True,
        fallback_reason: str | None = None,
        json_accepted: bool | None = True,
    ) -> None:
        self.parsed = parsed
        self.ok = ok
        self.last_fallback_reason = fallback_reason
        self.last_json_output_accepted = json_accepted
        self.calls = 0

    def complete_json(self, **_kwargs: Any) -> tuple[Any, bool]:
        self.calls += 1
        return self.parsed, self.ok


class _NeverBuyerClient:
    calls = 0

    def complete_json(self, **_kwargs: Any) -> tuple[dict[str, Any], bool]:
        self.calls += 1
        raise AssertionError("модель-покупатель не должна вызываться")


class _RecordingBot:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def handle_chat(self, _session_id: str, message: str) -> Any:
        self.messages.append(message)
        return SimpleNamespace(
            answer="Содержательный тестовый ответ консультанта.",
            products=[],
            debug={"final_answer_source": "deterministic", "intent": "test"},
        )


class _FailingBot:
    def handle_chat(self, _session_id: str, _message: str) -> Any:
        raise RuntimeError("bot exploded")


def test_buyer_provider_failure_is_not_user_gave_up() -> None:
    client = _BuyerClient(
        {"state": "__buyer_error__", "message": "", "why": ""},
        ok=False,
        fallback_reason="TLS handshake timeout",
        json_accepted=False,
    )

    result = harness._buyer_turn(client, _scenario(), [])

    assert result.error_kind == "buyer_provider_error"
    assert "TLS" in result.error_detail
    assert result.state == ""


def test_malformed_buyer_json_is_invalid_output() -> None:
    client = _BuyerClient(
        {"state": "__buyer_error__", "message": "", "why": ""},
        json_accepted=False,
    )

    result = harness._buyer_turn(client, _scenario(), [])

    assert result.error_kind == "buyer_invalid_output"
    assert result.state == ""


def test_invalid_buyer_state_is_protocol_error() -> None:
    client = _BuyerClient({"state": "stop", "message": "", "why": "done"})

    result = harness._buyer_turn(client, _scenario(), [])

    assert result.error_kind == "buyer_protocol_error"
    assert "stop" in result.error_detail


def test_empty_continue_message_is_protocol_error() -> None:
    client = _BuyerClient({"state": "continue", "message": "", "why": ""})

    result = harness._buyer_turn(client, _scenario(), [])

    assert result.error_kind == "buyer_protocol_error"
    assert "пустая" in result.error_detail


def test_live_first_turn_is_fixed_and_does_not_call_buyer_model() -> None:
    bot = _RecordingBot()
    client = _NeverBuyerClient()
    opening = "Именно эта первая реплика должна сохраниться дословно"

    run = harness.run_live(
        bot,
        client,
        _scenario(recorded_user_turns=[opening, "старая вторая реплика"]),
        set(),
        max_turns=1,
    )

    assert client.calls == 0
    assert bot.messages == [opening]
    assert run.turns[0].user == opening
    assert run.execution_status == "valid"


def test_buyer_cannot_give_up_before_three_bot_answers() -> None:
    bot = _RecordingBot()
    client = _BuyerClient(
        {"state": "gave_up", "message": "", "why": "не хочу продолжать"}
    )

    run = harness.run_live(
        bot,
        client,
        _scenario(),
        set(),
        max_turns=4,
    )

    assert bot.messages == ["Фиксированная первая реплика"]
    assert client.calls == 1
    assert run.execution_status == "buyer_protocol_error"
    assert run.failure_stage == "buyer"
    assert "раньше трёх" in run.failure_reason
    assert run.outcome == ""
    assert run.defects == {}


def test_missing_fixed_opening_is_harness_error_not_product_outcome() -> None:
    bot = _FailingBot()
    client = _NeverBuyerClient()

    run = harness.run_live(
        bot,
        client,
        _scenario(recorded_user_turns=[]),
        set(),
        max_turns=2,
    )

    assert run.execution_status == "harness_error"
    assert run.failure_stage == "harness"
    assert run.outcome == ""
    assert run.turns == []
    assert run.defects == {}
    assert client.calls == 0


def test_bot_exception_is_separate_from_conversation_outcome() -> None:
    run = harness.run_live(
        _FailingBot(),
        _NeverBuyerClient(),
        _scenario(),
        set(),
        max_turns=2,
    )

    assert run.execution_status == "bot_error"
    assert run.failure_stage == "bot"
    assert "bot exploded" in run.failure_reason
    assert run.outcome == ""
    assert "tech_error" not in run.defects


def test_replay_bot_exception_returns_invalid_run_instead_of_raising() -> None:
    run = harness.run_replay(_FailingBot(), _scenario(), set())

    assert run.execution_status == "bot_error"
    assert run.outcome == ""
    assert run.turns == []


def test_report_excludes_invalid_runs_from_product_metrics() -> None:
    valid = harness.DialogueRun(scenario=_scenario(id="T01"), session_id="valid")
    valid.outcome = "user_gave_up"
    valid.flag("no_cards", "нет карточек")
    valid.turns.append(
        harness.Turn(
            n=1,
            user="вопрос",
            bot="ответ",
            products=[],
            source="deterministic",
            latency_sec=1.25,
        )
    )
    invalid = harness.DialogueRun(scenario=_scenario(id="T02"), session_id="invalid")
    invalid.fail_execution("buyer_provider_error", "buyer", "timeout")
    # Даже если старый код успел поставить такой флаг, агрегатор обязан его
    # исключить из продуктового знаменателя.
    invalid.flag("no_cards", "не должно учитываться")

    report = harness.build_report(
        [valid, invalid],
        "live",
        2.0,
        {"schema_version": 1},
    )

    assert report["dialogues"] == 2
    assert report["dialogues_attempted"] == 2
    assert report["dialogues_valid"] == 1
    assert report["dialogues_invalid"] == 1
    assert report["metric_denominator_dialogues"] == 1
    assert report["outcomes"] == {"user_gave_up": 1}
    assert report["defect_hits"] == {"no_cards": 1}
    assert report["turns"] == 1
    assert report["execution_outcomes"] == {
        "valid": 1,
        "buyer_provider_error": 1,
    }
    assert report["manifest"] == {"schema_version": 1}


def test_catalog_hash_is_order_independent_and_ignores_load_time() -> None:
    first = Product(sku="B", name="Второй", price=20, updated_at="2026-01-01T00:00:00Z")
    second = Product(sku="A", name="Первый", price=10, updated_at="2026-02-01T00:00:00Z")
    same_second = second.model_copy(update={"updated_at": "2030-01-01T00:00:00Z"})

    assert harness._catalog_sha256([first, second]) == harness._catalog_sha256(
        [same_second, first]
    )


def _manifest_bot(secret: str) -> Any:
    settings = SimpleNamespace(
        llm_provider="openrouter",
        llm_model="buyer/model",
        llm_model_strong="strong/model",
        llm_timeout_seconds=60.0,
        llm_request_timeout_seconds=180.0,
        llm_max_retries=2,
        llm_retry_delay_seconds=1.0,
        openrouter_api_key=secret,
    )
    return SimpleNamespace(
        search_agent=SimpleNamespace(
            products=[Product(sku="A-1", name="Товар", price=123.0)]
        ),
        llm_client=SimpleNamespace(settings=settings),
        products_loaded_from="test-catalog",
    )


def _manifest_args(testset: Any) -> Namespace:
    return Namespace(
        mode="live",
        testset=testset,
        workers=3,
        max_turns=12,
        limit=None,
        only="T01",
    )


def test_manifest_is_stable_and_changes_with_testset(tmp_path: Any) -> None:
    testset = tmp_path / "testset.json"
    testset.write_text('{"scenarios": []}\n', encoding="utf-8")
    args = _manifest_args(testset)
    bot = _manifest_bot("do-not-record")

    first = harness.build_manifest(args, bot, [_scenario()])
    repeated = harness.build_manifest(args, bot, [_scenario()])
    testset.write_text('{"scenarios": [{"id": "changed"}]}\n', encoding="utf-8")
    changed = harness.build_manifest(args, bot, [_scenario()])

    assert first == repeated
    assert first["inputs"]["testset_sha256"] != changed["inputs"]["testset_sha256"]


def test_manifest_has_run_inputs_and_never_contains_api_key(tmp_path: Any) -> None:
    secret = "sk-test-super-secret"
    testset = tmp_path / "testset.json"
    testset.write_text('{"scenarios": []}\n', encoding="utf-8")

    manifest = harness.build_manifest(
        _manifest_args(testset),
        _manifest_bot(secret),
        [_scenario()],
    )
    serialized = json.dumps(manifest, ensure_ascii=False)

    assert manifest["schema_version"] == 1
    assert manifest["inputs"]["scenario_ids"] == ["T01"]
    assert manifest["inputs"]["catalog_products"] == 1
    assert manifest["run"]["workers"] == 3
    assert manifest["run"]["max_turns"] == 12
    assert manifest["llm"]["model"] == "buyer/model"
    assert secret not in serialized
    assert "openrouter_api_key" not in serialized


def test_markdown_separates_invalid_dialogues() -> None:
    invalid = harness.DialogueRun(scenario=_scenario(), session_id="invalid")
    invalid.fail_execution("buyer_provider_error", "buyer", "TLS timeout")
    report = harness.build_report([invalid], "live", 1.0, {"schema_version": 1})

    markdown = harness.render_markdown(report)

    assert "1** попыток / **0** валидных / **1** невалидных" in markdown
    assert "buyer_provider_error" in markdown
    assert "TLS timeout" in markdown
    assert "| — | 0 | 0 |" in markdown
