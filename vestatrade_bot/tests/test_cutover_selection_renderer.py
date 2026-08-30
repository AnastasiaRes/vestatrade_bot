"""Customer-facing wording for source-checked V2 selection results."""

from app.catalog_v2.contracts import (
    ProductKind,
    SelectionFactInput,
    SelectionProductCard,
    SelectionResult,
    SelectionResultStatus,
)
from app.cutover_v2.selection import render_selection_result


def _preliminary_result(
    *,
    product_kind: ProductKind,
    facts: tuple[SelectionFactInput, ...],
) -> SelectionResult:
    return SelectionResult(
        status=SelectionResultStatus.SHOWN,
        selection_id="selection-test",
        task_id="task-test",
        goal_id="goal-test",
        contract_id="contract-test",
        category="test",
        product_kind=product_kind,
        applied_facts=facts,
        ordered_skus=("SKU-1",),
        cards=(
            SelectionProductCard(
                sku="SKU-1",
                name="Тестовый товар",
                price=100.0,
                currency="RUB",
                stock_status="в наличии",
                url="https://example.test/product",
            ),
        ),
        is_preliminary=True,
        catalog_revision="catalog-test",
        outcome_gate_passed=True,
        reason_code="test",
    )


def _fact(name: str, value: str | int | float, unit: str | None = None) -> SelectionFactInput:
    return SelectionFactInput(
        name=name,
        value=value,
        unit=unit,
        status="known",
        source="test",
        source_turn=1,
    )


def test_pump_preliminary_copy_localizes_canonical_facts_and_units() -> None:
    response = render_selection_result(
        _preliminary_result(
            product_kind=ProductKind.CIRCULATION_PUMP,
            facts=(
                _fact("duty_point_flow_l_h", 1500, "l/h"),
                _fact("duty_point_head_m", 4, "m"),
            ),
        )
    )

    assert "Расход в рабочей точке: 1,5 м³/ч" in response
    assert "Напор в рабочей точке: 4 м" in response
    assert "Duty point" not in response
    assert " l/h" not in response


def test_pipe_preliminary_copy_localizes_values_and_implied_temperature_unit() -> None:
    response = render_selection_result(
        _preliminary_result(
            product_kind=ProductKind.PIPE,
            facts=(
                _fact("pipe_service", "heating"),
                _fact("reinforcement", "glass_fiber"),
                _fact("operating_temperature_c", 90),
            ),
        )
    )

    assert "Назначение трубы: отопление" in response
    assert "Тип армирования: стекловолокно" in response
    assert "Рабочая температура: 90 °C" in response
    assert "heating" not in response
    assert "glass_fiber" not in response
