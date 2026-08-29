"""Customer text for an already checked CalculationResult."""

from __future__ import annotations

from decimal import Decimal

from .contracts import CalculationResult, CalculationResultStatus, CalculationUnit, StockAssessment


def _number(value: Decimal | int | None) -> str:
    if value is None:
        return ""
    decimal = Decimal(value)
    return str(int(decimal)) if decimal == decimal.to_integral_value() else format(decimal.normalize(), "f")


def _unit(unit: CalculationUnit | None) -> str:
    return "шт." if unit == CalculationUnit.PIECE else "м" if unit == CalculationUnit.METRE else ""


def _currency(currency: str | None) -> str:
    return "₽" if currency == "RUB" else (currency or "")


def render_calculation_result(result: CalculationResult) -> str:
    if result.status == CalculationResultStatus.NEED_CLARIFICATION:
        return result.clarification or "Уточните товар и количество для расчёта."
    if result.status == CalculationResultStatus.NOT_CALCULABLE:
        return result.clarification or "Не могу подтвердить данные для расчёта стоимости."
    if result.status == CalculationResultStatus.REJECTED:
        return "Не могу безопасно посчитать стоимость: состав товара или версия каталога уже не совпадают. Покажите товар заново или назовите актуальный артикул."

    quantity = _number(result.quantity)
    unit = _unit(result.quantity_unit)
    price_unit = "/м" if result.price_basis_unit == CalculationUnit.METRE else "/шт."
    lines = [
        f"«{result.product_name}» ({result.sku}): {quantity} {unit} × "
        f"{_number(result.unit_price)} {_currency(result.currency)}{price_unit} = "
        f"{_number(result.total)} {_currency(result.currency)}."
    ]
    if result.stock_assessment == StockAssessment.SUFFICIENT and result.stock_qty is not None:
        lines.append(
            f"Остаток по фиду: {result.stock_qty} шт.; на {quantity} шт. хватает, "
            f"после расчётного количества останется {_number(result.stock_delta)} шт."
        )
    elif result.stock_assessment == StockAssessment.INSUFFICIENT and result.stock_qty is not None:
        lines.append(
            f"Остаток по фиду: {result.stock_qty} шт.; для {quantity} шт. не хватает "
            f"{_number(-result.stock_delta if result.stock_delta is not None else None)} шт."
        )
    elif result.stock_assessment == StockAssessment.UNKNOWN:
        lines.append("Числовой остаток для этого количества в фиде не подтверждён; возможность купить весь объём нужно уточнить отдельно.")
    elif result.stock_assessment == StockAssessment.UNIT_UNCONFIRMED and result.stock_qty is not None:
        lines.append(
            f"В фиде указан остаток {result.stock_qty}, но единица складского учёта не подтверждена; "
            f"поэтому наличие именно {quantity} шт. нужно уточнить отдельно."
        )
    lines.append("Это сумма по текущей цене каталога без доставки и скидки за объём.")
    return "\n".join(lines)
