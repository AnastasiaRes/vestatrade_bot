from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.cutover_v2.manifest import build_release_manifest
from app.dialogue_v2.contracts import DialogueStateV2
from app.models import (
    ChatRequest,
    ChatResponse,
    IdempotentResponseRecord,
    SessionState,
)
from app.session_store import InMemorySessionStore, RedisSessionStore
from scripts.run_stage6_release_gate import (
    _junit_summary,
    _shadow_environment,
    _telemetry_summary,
)


@pytest.mark.parametrize("raw", ["garbage", "-1", "6", "99", "1.5"])
def test_invalid_canary_percent_fails_closed(monkeypatch, raw: str) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("DIALOGUE_V2_INTERNAL_CANARY_PERCENT", raw)
    try:
        assert get_settings().dialogue_v2_internal_canary_percent == 0
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("raw", ["0", "1", "3", "5"])
def test_policy_bounded_canary_percent_is_preserved(monkeypatch, raw: str) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("DIALOGUE_V2_INTERNAL_CANARY_PERCENT", raw)
    try:
        assert get_settings().dialogue_v2_internal_canary_percent == int(raw)
    finally:
        get_settings.cache_clear()


def test_local_preview_flag_is_opt_in_and_defaults_off(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.delenv("DIALOGUE_V2_LOCAL_PREVIEW_ENABLED", raising=False)
    try:
        assert get_settings().dialogue_v2_local_preview_enabled is False
        get_settings.cache_clear()
        monkeypatch.setenv("DIALOGUE_V2_LOCAL_PREVIEW_ENABLED", "true")
        assert get_settings().dialogue_v2_local_preview_enabled is True
    finally:
        get_settings.cache_clear()


def test_per_request_qa_controls_require_explicit_switch_and_token(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.delenv("DIALOGUE_V2_QA_CONTROLS_ENABLED", raising=False)
    monkeypatch.delenv("DIALOGUE_V2_QA_CONTROL_TOKEN", raising=False)
    try:
        defaults = get_settings()
        assert defaults.dialogue_v2_qa_controls_enabled is False
        assert defaults.dialogue_v2_qa_control_token is None

        get_settings.cache_clear()
        monkeypatch.setenv("DIALOGUE_V2_QA_CONTROLS_ENABLED", "true")
        monkeypatch.setenv("DIALOGUE_V2_QA_CONTROL_TOKEN", "qa-secret")
        configured = get_settings()
        assert configured.dialogue_v2_qa_controls_enabled is True
        assert configured.dialogue_v2_qa_control_token == "qa-secret"
    finally:
        get_settings.cache_clear()


def test_release_manifest_is_reproducible_and_filters_secrets(tmp_path) -> None:
    catalog = tmp_path / "catalog.json"
    feed = tmp_path / "feed.xml"
    passports = tmp_path / "passports"
    passports.mkdir()
    (passports / "pump.pdf").write_bytes(b"verified passport")
    passport_index = tmp_path / "passport_index.json"
    passport_index.write_text(
        json.dumps(
            {
                "version": "2",
                "model": "test/embedding",
                "dimension": 3,
                "chunks": [{"document": "pump.pdf"}],
                "vectors": "",
            }
        ),
        encoding="utf-8",
    )
    catalog.write_bytes(b"stable catalog")
    feed.write_bytes(b"stable feed")
    flags = {
        "DIALOGUE_V2_ROUTING_ENABLED": True,
        "DIALOGUE_V2_INTERNAL_CANARY_PERCENT": 1,
        "OPENROUTER_API_KEY": "must-never-be-persisted",
        "SESSION_STORE_URL": "redis://user:password@example.test/0",
    }

    first = build_release_manifest(
        tmp_path,
        catalog_path=catalog,
        feed100_path=feed,
        registry_revision="registry-sha",
        llm_provider="openrouter",
        llm_model="test/model",
        embedding_model="test/embedding",
        embeddings_enabled=True,
        passport_index_path=passport_index,
        passport_dirs=(passports,),
        feature_flags=flags,
    )
    second = build_release_manifest(
        tmp_path,
        catalog_path=catalog,
        feed100_path=feed,
        registry_revision="registry-sha",
        llm_provider="openrouter",
        llm_model="test/model",
        embedding_model="test/embedding",
        embeddings_enabled=True,
        passport_index_path=passport_index,
        passport_dirs=(passports,),
        feature_flags=flags,
    )

    assert first == second
    serialized = json.dumps(first, sort_keys=True)
    assert "must-never-be-persisted" not in serialized
    assert "password" not in serialized
    assert first["feature_flags"] == {
        "DIALOGUE_V2_INTERNAL_CANARY_PERCENT": 1,
        "DIALOGUE_V2_ROUTING_ENABLED": True,
    }
    assert first["source_tree_sha256"]
    assert first["catalog_count"] is None
    assert first["prompt_contracts"]["semantic_sha256"]
    assert first["prompt_contracts"]["renderer_sha256"]
    assert first["run_timestamp"] is None
    assert first["retrieval"]["embedding_model"] == "test/embedding"
    assert first["retrieval"]["embeddings_enabled"] is True
    assert first["retrieval"]["passport_index"]["model"] == "test/embedding"
    assert first["retrieval"]["passport_index"]["chunk_count"] == 1
    assert first["retrieval"]["passport_index"]["sha256"]
    assert first["retrieval"]["passport_corpus"]["file_count"] == 1
    assert first["retrieval"]["passport_corpus"]["sha256"]


def test_live_state_and_idempotent_response_round_trip_in_session_stores() -> None:
    response = ChatResponse(session_id="cutover-session", answer="Проверенный ответ")
    state = SessionState(
        session_id="cutover-session",
        session_revision=7,
        live_dialogue_state_v2=DialogueStateV2(
            turn_number=3,
            live_epoch_id="epoch-1",
        ),
        v2_live_epoch_id="epoch-1",
        v2_sticky_assignment_id="assignment-1",
        v2_migration_cell_id="cell-1",
        idempotent_responses=[
            IdempotentResponseRecord(
                client_turn_id="turn-1",
                response_payload=response.model_dump(mode="json"),
                response_digest="a" * 64,
                session_revision=7,
            )
        ],
    )

    memory = InMemorySessionStore()
    memory.save(state.model_copy(deep=True))
    restored_memory = memory.snapshot(state.session_id)
    restored_redis = RedisSessionStore._decode(RedisSessionStore._encode(state))

    for restored in (restored_memory, restored_redis):
        assert restored.session_revision == 7
        assert restored.live_dialogue_state_v2 is not None
        assert restored.live_dialogue_state_v2.live_epoch_id == "epoch-1"
        assert restored.v2_sticky_assignment_id == "assignment-1"
        assert restored.idempotent_responses[0].response_payload["answer"] == (
            "Проверенный ответ"
        )


@pytest.mark.parametrize(
    "client_turn_id",
    ["", "contains whitespace", "x" * 161, "slash/not-allowed"],
)
def test_client_turn_id_rejects_unsafe_or_unbounded_values(
    client_turn_id: str,
) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(
            session_id="valid-session",
            message="Покажите цену",
            client_turn_id=client_turn_id,
        )


def test_release_lane_forces_shadow_and_aggregates_without_dialogue_text(
    tmp_path,
) -> None:
    env = _shadow_environment(tmp_path)
    assert env["DIALOGUE_V2_SHADOW_COMPARE_ENABLED"] == "true"
    assert env["DIALOGUE_V2_LIVE_DELIVERY_ENABLED"] == "false"
    assert env["DIALOGUE_V2_INTERNAL_CANARY_ENABLED"] == "false"
    assert env["COMMERCE_EXTERNAL_EXECUTION_ENABLED"] == "false"

    trace_path = tmp_path / "shadow_turns.jsonl"
    secret_dialogue = "мой телефон +7 999 123-45-67"
    trace_path.write_text(
        json.dumps(
            {
                "current_message": secret_dialogue,
                "cutover_v2": {
                    "decision": {"owner_candidate": "legacy"},
                    "candidate": {
                        "eligible_for_delivery": False,
                        "rejection_reason_codes": ["public_card_limit_exceeded"],
                    },
                    "parity": {"status": "regression"},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = _telemetry_summary(trace_path)
    serialized = json.dumps(summary, ensure_ascii=False)
    assert secret_dialogue not in serialized
    assert summary["decision_owners"] == {"legacy": 1}
    assert summary["candidate_rejection_reasons"] == {
        "public_card_limit_exceeded": 1
    }


def test_release_lane_counts_xfail_in_gate_denominator(tmp_path) -> None:
    junit = tmp_path / "targeted.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1">
<testcase name="pass-one"/><testcase name="pass-two"/>
<testcase name="semantic-upstream"><skipped type="pytest.xfail" message="upstream"/></testcase>
</testsuite></testsuites>""",
        encoding="utf-8",
    )
    summary = _junit_summary(junit)
    assert summary == {
        "tests": 3,
        "passed": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 1,
        "xfailed": 1,
        "gate_denominator": 3,
    }
