from __future__ import annotations

from scripts.run_pdf_dialog_tests import Scenario, evaluate, has_marker_groups, llm_telemetry


def _turn(answer: str, *, products=None, debug=None, need_handoff: bool = False) -> dict:
    return {
        "response": {
            "answer": answer,
            "products": products or [],
            "debug": debug or {},
            "need_handoff": need_handoff,
        }
    }


def test_marker_groups_accept_inflections_and_synonyms() -> None:
    assert has_marker_groups(
        "Уточните артикул или модель насоса.",
        ["насос", ["парамет", "модел", "артикул"]],
    )
    assert has_marker_groups(
        "Для холодной или горячей воды, какой диаметр?",
        ["для", "вод", "диаметр"],
    )


def test_p1_issue_is_not_promoted_to_fail_by_word_nedostatochno() -> None:
    scenario = Scenario(
        1,
        "evaluator",
        "other",
        "P1",
        ["message"],
        {"clarify_first": ["ожидаемый маркер"]},
    )

    verdict, _ = evaluate(scenario, [_turn("Данных пока недостаточно.")])

    assert verdict == "PARTIAL"


def test_quantity_check_does_not_use_digit_inside_sku() -> None:
    scenario = Scenario(
        1,
        "quantity",
        "other",
        "P0",
        ["есть 2?", "2202210"],
        {"quantity_later": 2},
    )

    verdict, issues = evaluate(
        scenario,
        [
            _turn("Какой товар?"),
            _turn("Артикул 2202210 найден, товар в наличии."),
        ],
    )

    assert verdict == "FAIL"
    assert any("достаточный qty" in issue for issue in issues)


def test_symptom_flow_uses_structured_debug_evidence() -> None:
    scenario = Scenario(
        1,
        "symptom",
        "other",
        "P0",
        ["надо чтобы вода шла"],
        {"symptom_first": True},
    )

    verdict, issues = evaluate(
        scenario,
        [
            _turn(
                "Источник воды какой: скважина, колодец или водопровод?",
                debug={
                    "category": "pumps",
                    "slots": {"symptom": "проблема с подачей воды"},
                },
            )
        ],
    )

    assert verdict == "PASS"
    assert issues == []


def test_pump_connection_can_be_confirmed_by_model_not_union_thread() -> None:
    scenario = Scenario(
        14,
        "pump model evidence",
        "pumps",
        "P0",
        ["25/6 130"],
        {
            "pump_constraints": {
                "connection_size": 25,
                "head_m": 6,
                "mounting_length_mm": 130,
            }
        },
    )

    verdict, issues = evaluate(
        scenario,
        [
            _turn(
                "Насос циркуляционный Rommer 25/60-130",
                products=[
                    {
                        "sku": "RCP-0002-2561301",
                        "name": "Насос циркуляционный Rommer 25/60-130",
                        "url": "https://example.test/rommer",
                    }
                ],
                debug={
                    "category": "pumps",
                    "slots": {
                        "connection_size": 25,
                        "head_m": 6.0,
                        "mounting_length_mm": 130,
                    },
                },
            )
        ],
    )

    assert verdict == "PASS"
    assert issues == []


def test_llm_telemetry_does_not_label_fallback_as_live() -> None:
    results = [
        {
            "turns": [
                {
                    "response": {
                        "debug": {
                            "llm_requested": True,
                            "llm_transport_succeeded": False,
                            "llm_output_accepted": False,
                        }
                    }
                }
            ]
        }
    ]

    assert llm_telemetry(results)["mode"] == "fallback-only"
