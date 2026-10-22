"""Deterministic financial calculations for finance API operations."""

from decimal import ROUND_HALF_UP, Decimal


def calculate_operating_metrics(
    revenue: Decimal,
    operating_expenses: Decimal,
) -> tuple[Decimal, Decimal | None]:
    """Calculate operating profit and a two-decimal operating margin percentage.

    Revenue and operating expenses must be finite, non-negative Decimal values.
    A margin is unavailable when revenue is zero because division is undefined.
    """
    for value, field_name in (
        (revenue, "revenue"),
        (operating_expenses, "operating_expenses"),
    ):
        # Decimal-only inputs prevent implicit float precision loss in reporting.
        if not isinstance(value, Decimal):
            raise ValueError(f"{field_name} must be a Decimal")
        if not value.is_finite():
            raise ValueError(f"{field_name} must be finite")
        if value < Decimal("0"):
            raise ValueError(f"{field_name} must not be negative")

    operating_profit = revenue - operating_expenses

    # Zero revenue has no defined percentage margin, even if expenses are zero.
    if revenue == Decimal("0"):
        return operating_profit, None

    operating_margin_percent = (
        (operating_profit / revenue) * Decimal("100")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return operating_profit, operating_margin_percent
