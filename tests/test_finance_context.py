"""Automated tests for internal Finance API context values."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
import unittest
from unittest.mock import patch

from finance_api.context import (
    AuthorizedOperatingMarginQuery,
    FinanceRequestContext,
    MonotonicDeadline,
    OperatingMarginAggregate,
)


class FinanceRequestContextTests(unittest.TestCase):
    """Verify structural validation for request context values."""

    def test_accepts_valid_context(self) -> None:
        """A valid context carries its declared request scope."""
        context = FinanceRequestContext(
            principal_id="user-123",
            workload_id="finance-api",
            operation="get_operating_margin",
            authorized_business_unit_codes=frozenset({"EU", "IN"}),
            correlation_id="corr-123",
        )

        self.assertEqual("user-123", context.principal_id)
        self.assertEqual(frozenset({"EU", "IN"}), context.authorized_business_unit_codes)

    def test_rejects_blank_required_identifiers(self) -> None:
        """A context needs usable identifiers for later correlation and policy work."""
        for field_name in ("principal_id", "workload_id", "correlation_id"):
            values = {
                "principal_id": "user-123",
                "workload_id": "finance-api",
                "operation": "get_operating_margin",
                "authorized_business_unit_codes": frozenset({"EU"}),
                "correlation_id": "corr-123",
            }
            values[field_name] = " "

            with self.subTest(field_name=field_name):
                with self.assertRaises(ValueError):
                    FinanceRequestContext(**values)

    def test_accepts_only_the_supported_operation(self) -> None:
        """This API slice exposes only the operating-margin operation."""
        for operation in ("", "delete_transactions", 7):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    FinanceRequestContext(
                        principal_id="user-123",
                        workload_id="finance-api",
                        operation=operation,
                        authorized_business_unit_codes=frozenset({"EU"}),
                        correlation_id="corr-123",
                    )

    def test_rejects_empty_or_mutable_business_unit_scope(self) -> None:
        """Scope must be a nonempty immutable set of business-unit codes."""
        for scope in (frozenset(), {"EU"}):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    FinanceRequestContext(
                        principal_id="user-123",
                        workload_id="finance-api",
                        operation="get_operating_margin",
                        authorized_business_unit_codes=scope,
                        correlation_id="corr-123",
                    )

    def test_rejects_invalid_business_unit_codes(self) -> None:
        """Every declared scope item must be a nonblank string."""
        for scope in (frozenset({" "}), frozenset({7})):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    FinanceRequestContext(
                        principal_id="user-123",
                        workload_id="finance-api",
                        operation="get_operating_margin",
                        authorized_business_unit_codes=scope,
                        correlation_id="corr-123",
                    )

    def test_context_is_immutable(self) -> None:
        """Request context must not change after a trusted component creates it."""
        context = FinanceRequestContext(
            principal_id="user-123",
            workload_id="finance-api",
            operation="get_operating_margin",
            authorized_business_unit_codes=frozenset({"EU"}),
            correlation_id="corr-123",
        )

        with self.assertRaises(FrozenInstanceError):
            context.principal_id = "user-456"


class AuthorizedOperatingMarginQueryTests(unittest.TestCase):
    """Verify structural validation for post-authorization query values."""

    def test_accepts_valid_query(self) -> None:
        """A valid query identifies one business unit and reporting quarter."""
        query = AuthorizedOperatingMarginQuery(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
            principal_id="user-123",
            workload_id="finance-api",
            operation="get_operating_margin",
            correlation_id="corr-123",
        )

        self.assertEqual("EU", query.business_unit_code)
        self.assertEqual(2, query.quarter)

    def test_rejects_invalid_year_and_quarter(self) -> None:
        """The query needs a positive year and one of four quarters."""
        for financial_year, quarter in ((0, 2), (-1, 2), (2026, 0), (2026, 5)):
            with self.subTest(financial_year=financial_year, quarter=quarter):
                with self.assertRaises(ValueError):
                    AuthorizedOperatingMarginQuery(
                        "EU",
                        financial_year,
                        quarter,
                        "user-123",
                        "finance-api",
                        "get_operating_margin",
                        "corr-123",
                    )

    def test_rejects_boolean_year_and_quarter(self) -> None:
        """Booleans are not valid reporting-period integers."""
        for financial_year, quarter in ((True, 2), (2026, True)):
            with self.subTest(financial_year=financial_year, quarter=quarter):
                with self.assertRaises(ValueError):
                    AuthorizedOperatingMarginQuery(
                        "EU",
                        financial_year,
                        quarter,
                        "user-123",
                        "finance-api",
                        "get_operating_margin",
                        "corr-123",
                    )

    def test_rejects_unsupported_operation_and_blank_identifiers(self) -> None:
        """A query needs the approved operation and nonblank identifiers."""
        invalid_values = (
            {"operation": "delete_transactions"},
            {"business_unit_code": " "},
            {"principal_id": " "},
            {"workload_id": " "},
            {"correlation_id": " "},
        )
        for changes in invalid_values:
            values = {
                "business_unit_code": "EU",
                "financial_year": 2026,
                "quarter": 2,
                "principal_id": "user-123",
                "workload_id": "finance-api",
                "operation": "get_operating_margin",
                "correlation_id": "corr-123",
            }
            values.update(changes)

            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    AuthorizedOperatingMarginQuery(**values)

    def test_query_is_immutable(self) -> None:
        """A service must receive the exact query E3 produced."""
        query = AuthorizedOperatingMarginQuery(
            "EU",
            2026,
            2,
            "user-123",
            "finance-api",
            "get_operating_margin",
            "corr-123",
        )

        with self.assertRaises(FrozenInstanceError):
            query.quarter = 3


class OperatingMarginAggregateTests(unittest.TestCase):
    """Verify structural validation for repository aggregate values."""

    def test_accepts_decimal_totals_and_zero_totals(self) -> None:
        """Decimal aggregation totals may be positive or zero."""
        aggregate = OperatingMarginAggregate(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
            currency="EUR",
            revenue=Decimal("100.00"),
            operating_expenses=Decimal("82.00"),
            source="postgresql:reconciliation",
        )
        zero_aggregate = OperatingMarginAggregate(
            "EU",
            2026,
            2,
            "EUR",
            Decimal("0.00"),
            Decimal("0.00"),
            "postgresql:reconciliation",
        )

        self.assertEqual(Decimal("100.00"), aggregate.revenue)
        self.assertEqual(Decimal("0.00"), zero_aggregate.operating_expenses)

    def test_rejects_float_and_string_totals(self) -> None:
        """Repository totals must remain Decimal values without coercion."""
        for revenue, expenses in ((100.0, Decimal("0.00")), ("100.00", Decimal("0.00"))):
            with self.subTest(revenue=revenue, expenses=expenses):
                with self.assertRaises(ValueError):
                    OperatingMarginAggregate(
                        "EU",
                        2026,
                        2,
                        "EUR",
                        revenue,
                        expenses,
                        "postgresql:reconciliation",
                    )

    def test_rejects_negative_and_non_finite_totals(self) -> None:
        """Financial aggregates must be non-negative finite Decimal values."""
        for revenue, expenses in (
            (Decimal("-1.00"), Decimal("0.00")),
            (Decimal("0.00"), Decimal("-1.00")),
            (Decimal("NaN"), Decimal("0.00")),
            (Decimal("Infinity"), Decimal("0.00")),
            (Decimal("0.00"), Decimal("NaN")),
            (Decimal("0.00"), Decimal("Infinity")),
        ):
            with self.subTest(revenue=revenue, expenses=expenses):
                with self.assertRaises(ValueError):
                    OperatingMarginAggregate(
                        "EU",
                        2026,
                        2,
                        "EUR",
                        revenue,
                        expenses,
                        "postgresql:reconciliation",
                    )

    def test_rejects_invalid_reporting_identifiers(self) -> None:
        """An aggregate needs valid reporting keys, currency, and source."""
        invalid_values = (
            {"business_unit_code": " "},
            {"financial_year": 0},
            {"quarter": 5},
            {"currency": " "},
            {"source": " "},
        )
        for changes in invalid_values:
            values = {
                "business_unit_code": "EU",
                "financial_year": 2026,
                "quarter": 2,
                "currency": "EUR",
                "revenue": Decimal("100.00"),
                "operating_expenses": Decimal("82.00"),
                "source": "postgresql:reconciliation",
            }
            values.update(changes)

            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    OperatingMarginAggregate(**values)

    def test_aggregate_is_immutable(self) -> None:
        """A service must not alter totals returned by the repository."""
        aggregate = OperatingMarginAggregate(
            "EU",
            2026,
            2,
            "EUR",
            Decimal("100.00"),
            Decimal("82.00"),
            "postgresql:reconciliation",
        )

        with self.assertRaises(FrozenInstanceError):
            aggregate.revenue = Decimal("101.00")


class MonotonicDeadlineTests(unittest.TestCase):
    """Verify deadline behavior without waiting for clock time."""

    def test_constructs_deadline_from_valid_timeout(self) -> None:
        """A timeout becomes one absolute monotonic deadline."""
        with patch("finance_api.context.time.monotonic", return_value=100.0):
            deadline = MonotonicDeadline.from_timeout_seconds(2.5)

        self.assertEqual(102.5, deadline.expires_at_monotonic)

    def test_rejects_invalid_timeout_values(self) -> None:
        """Timeouts must be finite, non-negative numeric values, not booleans."""
        for timeout_seconds in (-1, float("nan"), float("inf"), True, "1", None):
            with self.subTest(timeout_seconds=timeout_seconds):
                with self.assertRaises(ValueError):
                    MonotonicDeadline.from_timeout_seconds(timeout_seconds)

    def test_remaining_time_never_becomes_negative(self) -> None:
        """Past deadlines return zero remaining time instead of a negative value."""
        deadline = MonotonicDeadline(100.0)

        with patch("finance_api.context.time.monotonic", return_value=101.0):
            self.assertEqual(0.0, deadline.remaining_seconds())

    def test_expiry_uses_the_monotonic_clock(self) -> None:
        """Expiry changes at the deadline without using a real sleep."""
        deadline = MonotonicDeadline(100.0)

        with patch("finance_api.context.time.monotonic", return_value=99.0):
            self.assertFalse(deadline.is_expired())
        with patch("finance_api.context.time.monotonic", return_value=100.0):
            self.assertTrue(deadline.is_expired())

    def test_deadline_is_immutable(self) -> None:
        """Request handlers must share the same deadline value."""
        deadline = MonotonicDeadline(100.0)

        with self.assertRaises(FrozenInstanceError):
            deadline.expires_at_monotonic = 101.0


if __name__ == "__main__":
    unittest.main()
