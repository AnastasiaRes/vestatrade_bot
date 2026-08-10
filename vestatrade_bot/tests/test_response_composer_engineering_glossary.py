from __future__ import annotations

from app.agents.response_composer import ResponseComposerAgent
from app.agents.utils import normalize_text


class _NoLLM:
    def complete(self, *args, **kwargs):  # pragma: no cover - failure path only
        raise AssertionError("known engineering terms must be answered deterministically")


def _answer(message: str) -> str:
    return ResponseComposerAgent(llm_client=_NoLLM()).compose_term_consult(message)


def test_closed_circulation_head_is_based_on_hydraulic_losses_not_building_height() -> None:
    answer = normalize_text(
        _answer(
            "Как подбирают напор циркуляционного насоса в закрытой системе отопления?"
        )
    )

    assert "гидравлическ" in answer
    assert "расчетн" in answer and "кольц" in answer
    assert "геометрическую высоту здания к напору не прибавляют" in answer
    assert "всегда больше геометрической высоты" not in answer


def test_general_head_definition_explicitly_separates_lift_from_closed_circulation() -> None:
    answer = normalize_text(_answer("Что такое напор насоса?"))

    assert "для подъема воды" in answer
    assert "в закрытой циркуляционной системе" in answer
    assert "геометрическую высоту не прибавляют" in answer
    assert "гидравлическим потерям" in answer


def test_boiler_contour_is_not_described_as_an_underfloor_heating_loop() -> None:
    answer = normalize_text(_answer("Что означает контур котла?"))

    assert "функциональный тракт" in answer
    assert "одноконтурный" in answer and "двухконтурный" in answer
    assert "гвс" in answer
    assert "не то же самое" in answer and "петля теплого пола" in answer


def test_underfloor_heating_contour_is_a_collector_loop_not_boiler_circuitry() -> None:
    answer = normalize_text(_answer("Что такое контур тёплого пола?"))

    assert "отдельная петля трубы" in answer
    assert "подающего коллектора" in answer
    assert "обратный коллектор" in answer
    assert "не контурность котла" in answer
    assert "двухконтурный дополнительно" not in answer


def test_bare_contour_term_asks_which_typed_meaning_is_intended() -> None:
    answer = normalize_text(_answer("Что такое контур?"))

    assert "в двух разных смыслах" in answer
    assert "у котла" in answer
    assert "у теплого пола" in answer
    assert "уточните" in answer


def test_mounting_length_is_face_to_face_between_connection_planes() -> None:
    answer = normalize_text(_answer("Что такое монтажная длина насоса?"))

    assert "face-to-face" in answer
    assert "по оси изделия" in answer
    assert answer.count("присоединительн") >= 2
    assert "без учета накидных гаек" in answer
    assert "между гайками" not in answer
