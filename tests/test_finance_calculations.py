"""Automated tests for deterministic finance calculations."""

from decimal import Decimal
import unittest

from finance_api.calculations import calculate_operating_metrics


class CalculateOperatingMetricsTests(unittest.TestCase):
    """Verify operating-profit and operating-margin reporting rules."""

    def test_europe_q1_metrics(self) -> None:
        """Europe Q1 reconciles to its 18.00 percent operating margin."""
        profit, margin = calculate_operating_metrics(
            Decimal("100000000.00"),
            Decimal("82000000.00"),
        )

        self.assertEqual(Decimal("18000000.00"), profit)
        self.assertEqual(Decimal("18.00"), margin)

    def test_europe_q2_metrics(self) -> None:
        """Europe Q2 reconciles to its 14.00 percent operating margin."""
        profit, margin = calculate_operating_metrics(
            Decimal("100000000.00"),
            Decimal("86000000.00"),
        )

        self.assertEqual(Decimal("14000000.00"), profit)
        self.assertEqual(Decimal("14.00"), margin)

    def test_zero_revenue_and_zero_expenses_returns_no_margin(self) -> None:
        """A percentage is undefined when there is no revenue."""
        profit, margin = calculate_operating_metrics(
            Decimal("0.00"),
            Decimal("0.00"),
        )

        self.assertEqual(Decimal("0.00"), profit)
        self.assertIsNone(margin)

    def test_zero_revenue_with_expenses_returns_negative_profit_and_no_margin(self) -> None:
        """Operating expenses remain visible even when margin is undefined."""
        profit, margin = calculate_operating_metrics(
            Decimal("0.00"),
            Decimal("250.00"),
        )

        self.assertEqual(Decimal("-250.00"), profit)
        self.assertIsNone(margin)

    def test_expenses_greater_than_revenue_returns_negative_metrics(self) -> None:
        """Losses are represented by negative profit and margin values."""
        profit, margin = calculate_operating_metrics(
            Decimal("100.00"),
            Decimal("125.00"),
        )

        self.assertEqual(Decimal("-25.00"), profit)
        self.assertEqual(Decimal("-25.00"), margin)

    def test_rounds_percentage_half_up_to_two_decimal_places(self) -> None:
        """The initial reporting convention rounds a half-cent upward."""
        profit, margin = calculate_operating_metrics(
            Decimal("20000.00"),
            Decimal("17531.00"),
        )

        self.assertEqual(Decimal("2469.00"), profit)
        self.assertEqual(Decimal("12.35"), margin)

    def test_rejects_negative_revenue_or_expenses(self) -> None:
        """Reported revenue and expense inputs must be non-negative."""
        for revenue, expenses in (
            (Decimal("-1.00"), Decimal("0.00")),
            (Decimal("1.00"), Decimal("-1.00")),
        ):
            with self.subTest(revenue=revenue, expenses=expenses):
                with self.assertRaises(ValueError):
                    calculate_operating_metrics(revenue, expenses)

    def test_rejects_nan_and_infinite_inputs(self) -> None:
        """Non-finite Decimal values cannot be used in financial reporting."""
        for revenue, expenses in (
            (Decimal("NaN"), Decimal("0.00")),
            (Decimal("Infinity"), Decimal("0.00")),
            (Decimal("1.00"), Decimal("NaN")),
            (Decimal("1.00"), Decimal("Infinity")),
        ):
            with self.subTest(revenue=revenue, expenses=expenses):
                with self.assertRaises(ValueError):
                    calculate_operating_metrics(revenue, expenses)

    def test_rejects_float_and_string_inputs(self) -> None:
        """Only Decimal inputs preserve the intended financial precision."""
        for revenue, expenses in (
            (100.00, Decimal("0.00")),
            (Decimal("100.00"), 0.00),
            ("100.00", Decimal("0.00")),
            (Decimal("100.00"), "0.00"),
        ):
            with self.subTest(revenue=revenue, expenses=expenses):
                with self.assertRaises(ValueError):
                    calculate_operating_metrics(revenue, expenses)

    def test_returns_decimal_financial_values(self) -> None:
        """Profit and an available margin remain Decimal values."""
        profit, margin = calculate_operating_metrics(
            Decimal("100.00"),
            Decimal("82.00"),
        )

        self.assertIsInstance(profit, Decimal)
        self.assertIsInstance(margin, Decimal)


if __name__ == "__main__":
    unittest.main()
