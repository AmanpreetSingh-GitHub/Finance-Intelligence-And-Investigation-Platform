"""Automated tests for the internal operating-margin service."""

from decimal import Decimal
from types import SimpleNamespace
import unittest

from finance_api.authorization import AuthorizationDeniedError
from finance_api.context import (
    AuthorizedOperatingMarginQuery,
    FinanceRequestContext,
    OperatingMarginAggregate,
)
from finance_api.contracts import OperatingMarginRequest
from finance_api.service import (
    DataNotAvailableError,
    DeadlineExceededError,
    OperatingMarginService,
    ServiceIntegrityError,
)


class DeadlineProbe:
    """Return controlled expiry states without waiting for real time."""

    def __init__(self, expiry_states: list[bool]) -> None:
        self._expiry_states = iter(expiry_states)

    def is_expired(self) -> bool:
        """Return the next controlled state used by the service."""
        return next(self._expiry_states)


class FakeAuthorizer:
    """Test-only E3 dependency that records the call and returns a chosen result."""

    def __init__(self, result: object, calls: list[str]) -> None:
        self._result = result
        self._calls = calls
        self.received_request: OperatingMarginRequest | None = None
        self.received_context: FinanceRequestContext | None = None
        self.received_deadline: object | None = None

    def authorize(
        self,
        request: OperatingMarginRequest,
        context: FinanceRequestContext,
        deadline: object,
    ) -> object:
        """Record the E3 call or raise the chosen denial error."""
        self._calls.append("authorizer")
        self.received_request = request
        self.received_context = context
        self.received_deadline = deadline
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeRepository:
    """Test-only repository dependency that records the approved query it receives."""

    def __init__(self, result: object, calls: list[str]) -> None:
        self._result = result
        self._calls = calls
        self.received_query: object | None = None
        self.received_deadline: object | None = None

    def get_operating_margin_aggregate(self, query: object, deadline: object) -> object:
        """Record the repository call or raise the chosen repository error."""
        self._calls.append("repository")
        self.received_query = query
        self.received_deadline = deadline
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class OperatingMarginServiceTests(unittest.TestCase):
    """Verify service ordering, data integrity, and deadline behavior."""

    def setUp(self) -> None:
        """Create one valid Europe Q2 request and its expected approved data."""
        self.request = OperatingMarginRequest(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
        )
        self.context = FinanceRequestContext(
            principal_id="user-123",
            workload_id="finance-api",
            operation="get_operating_margin",
            authorized_business_unit_codes=frozenset({"EU"}),
            correlation_id="corr-123",
        )
        self.query = AuthorizedOperatingMarginQuery(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
            principal_id="user-123",
            workload_id="finance-api",
            operation="get_operating_margin",
            correlation_id="corr-123",
        )
        self.aggregate = OperatingMarginAggregate(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
            currency="EUR",
            revenue=Decimal("100000000.00"),
            operating_expenses=Decimal("86000000.00"),
            source="postgresql:operating-margin-v1",
        )

    def test_returns_europe_q2_metrics_after_e3(self) -> None:
        """E3 approves before the repository returns the expected Q2 result."""
        calls: list[str] = []
        authorizer = FakeAuthorizer(self.query, calls)
        repository = FakeRepository(self.aggregate, calls)
        deadline = DeadlineProbe([False, False, False, False])

        response = OperatingMarginService(authorizer, repository).get_operating_margin(
            self.request,
            self.context,
            deadline,
        )

        self.assertEqual(["authorizer", "repository"], calls)
        self.assertIs(self.request, authorizer.received_request)
        self.assertIs(self.context, authorizer.received_context)
        self.assertIs(self.query, repository.received_query)
        self.assertIs(deadline, authorizer.received_deadline)
        self.assertIs(deadline, repository.received_deadline)
        self.assertEqual("EUR", response.currency)
        self.assertEqual("postgresql:operating-margin-v1", response.source)
        self.assertEqual(Decimal("100000000.00"), response.revenue)
        self.assertEqual(Decimal("86000000.00"), response.operating_expenses)
        self.assertEqual(Decimal("14000000.00"), response.operating_profit)
        self.assertEqual(Decimal("14.00"), response.operating_margin_percent)

    def test_rejects_missing_dependencies(self) -> None:
        """The service cannot be created without E3 and repository boundaries."""
        calls: list[str] = []
        authorizer = FakeAuthorizer(self.query, calls)
        repository = FakeRepository(self.aggregate, calls)

        with self.assertRaises(ValueError):
            OperatingMarginService(None, repository)
        with self.assertRaises(ValueError):
            OperatingMarginService(authorizer, None)

    def test_authorization_denial_propagates_without_repository_access(self) -> None:
        """A denial ends processing before any financial data read."""
        calls: list[str] = []
        authorizer = FakeAuthorizer(AuthorizationDeniedError("denied"), calls)
        repository = FakeRepository(self.aggregate, calls)

        with self.assertRaises(AuthorizationDeniedError):
            OperatingMarginService(authorizer, repository).get_operating_margin(
                self.request,
                self.context,
                DeadlineProbe([False]),
            )

        self.assertEqual(["authorizer"], calls)

    def test_query_mismatches_stop_before_repository_access(self) -> None:
        """E3 output must exactly match the request and trusted context."""
        expected_values = {
            "business_unit_code": self.query.business_unit_code,
            "financial_year": self.query.financial_year,
            "quarter": self.query.quarter,
            "principal_id": self.query.principal_id,
            "workload_id": self.query.workload_id,
            "operation": self.query.operation,
            "correlation_id": self.query.correlation_id,
        }
        mismatches = {
            "business_unit_code": "IN",
            "financial_year": 2027,
            "quarter": 3,
            "principal_id": "user-456",
            "workload_id": "other-workload",
            "operation": "delete_transactions",
            "correlation_id": "corr-456",
        }

        for field_name, mismatched_value in mismatches.items():
            values = expected_values | {field_name: mismatched_value}
            mismatched_query = SimpleNamespace(**values)
            calls: list[str] = []
            authorizer = FakeAuthorizer(mismatched_query, calls)
            repository = FakeRepository(self.aggregate, calls)

            with self.subTest(field_name=field_name):
                with self.assertRaises(ServiceIntegrityError):
                    OperatingMarginService(authorizer, repository).get_operating_margin(
                        self.request,
                        self.context,
                        DeadlineProbe([False, False]),
                    )
                self.assertEqual(["authorizer"], calls)

    def test_missing_aggregate_raises_data_not_available(self) -> None:
        """No matching approved aggregate is not treated as a zero result."""
        calls: list[str] = []
        authorizer = FakeAuthorizer(self.query, calls)
        repository = FakeRepository(None, calls)

        with self.assertRaises(DataNotAvailableError):
            OperatingMarginService(authorizer, repository).get_operating_margin(
                self.request,
                self.context,
                DeadlineProbe([False, False, False]),
            )

        self.assertEqual(["authorizer", "repository"], calls)

    def test_aggregate_mismatches_fail_closed(self) -> None:
        """Repository data must remain within the E3-approved reporting grain."""
        mismatches = {
            "business_unit_code": "IN",
            "financial_year": 2027,
            "quarter": 3,
        }
        for field_name, mismatched_value in mismatches.items():
            values = {
                "business_unit_code": self.aggregate.business_unit_code,
                "financial_year": self.aggregate.financial_year,
                "quarter": self.aggregate.quarter,
                "currency": self.aggregate.currency,
                "revenue": self.aggregate.revenue,
                "operating_expenses": self.aggregate.operating_expenses,
                "source": self.aggregate.source,
            }
            values[field_name] = mismatched_value
            mismatched_aggregate = OperatingMarginAggregate(**values)
            calls: list[str] = []

            with self.subTest(field_name=field_name):
                with self.assertRaises(ServiceIntegrityError):
                    OperatingMarginService(
                        FakeAuthorizer(self.query, calls),
                        FakeRepository(mismatched_aggregate, calls),
                    ).get_operating_margin(
                        self.request,
                        self.context,
                        DeadlineProbe([False, False, False]),
                    )

    def test_repository_failure_is_not_authorization_denial(self) -> None:
        """Unexpected repository failures remain distinct for later error mapping."""
        calls: list[str] = []
        repository_error = RuntimeError("repository unavailable")

        with self.assertRaises(RuntimeError) as error_context:
            OperatingMarginService(
                FakeAuthorizer(self.query, calls),
                FakeRepository(repository_error, calls),
            ).get_operating_margin(
                self.request,
                self.context,
                DeadlineProbe([False, False]),
            )

        self.assertEqual("repository unavailable", str(error_context.exception))
        self.assertEqual(["authorizer", "repository"], calls)

    def test_zero_revenue_returns_no_margin(self) -> None:
        """A valid zero-revenue aggregate returns a null margin."""
        zero_aggregate = OperatingMarginAggregate(
            "EU",
            2026,
            2,
            "EUR",
            Decimal("0.00"),
            Decimal("0.00"),
            "postgresql:operating-margin-v1",
        )

        response = OperatingMarginService(
            FakeAuthorizer(self.query, []),
            FakeRepository(zero_aggregate, []),
        ).get_operating_margin(
            self.request,
            self.context,
            DeadlineProbe([False, False, False, False]),
        )

        self.assertEqual(Decimal("0.00"), response.operating_profit)
        self.assertIsNone(response.operating_margin_percent)

    def test_expenses_greater_than_revenue_returns_negative_metrics(self) -> None:
        """A valid aggregate can represent an operating loss."""
        loss_aggregate = OperatingMarginAggregate(
            "EU",
            2026,
            2,
            "EUR",
            Decimal("100.00"),
            Decimal("125.00"),
            "postgresql:operating-margin-v1",
        )

        response = OperatingMarginService(
            FakeAuthorizer(self.query, []),
            FakeRepository(loss_aggregate, []),
        ).get_operating_margin(
            self.request,
            self.context,
            DeadlineProbe([False, False, False, False]),
        )

        self.assertEqual(Decimal("-25.00"), response.operating_profit)
        self.assertEqual(Decimal("-25.00"), response.operating_margin_percent)

    def test_expired_before_e3_calls_no_dependencies(self) -> None:
        """An expired request stops before authorization or data access."""
        calls: list[str] = []

        with self.assertRaises(DeadlineExceededError):
            OperatingMarginService(
                FakeAuthorizer(self.query, calls),
                FakeRepository(self.aggregate, calls),
            ).get_operating_margin(
                self.request,
                self.context,
                DeadlineProbe([True]),
            )

        self.assertEqual([], calls)

    def test_expired_after_e3_calls_no_repository(self) -> None:
        """A deadline reached during E3 stops before data access."""
        calls: list[str] = []

        with self.assertRaises(DeadlineExceededError):
            OperatingMarginService(
                FakeAuthorizer(self.query, calls),
                FakeRepository(self.aggregate, calls),
            ).get_operating_margin(
                self.request,
                self.context,
                DeadlineProbe([False, True]),
            )

        self.assertEqual(["authorizer"], calls)

    def test_expired_after_repository_returns_no_result(self) -> None:
        """A deadline reached during the repository read rejects its data."""
        calls: list[str] = []

        with self.assertRaises(DeadlineExceededError):
            OperatingMarginService(
                FakeAuthorizer(self.query, calls),
                FakeRepository(self.aggregate, calls),
            ).get_operating_margin(
                self.request,
                self.context,
                DeadlineProbe([False, False, True]),
            )

        self.assertEqual(["authorizer", "repository"], calls)

    def test_expired_before_response_returns_no_result(self) -> None:
        """A deadline reached during calculation prevents response construction."""
        calls: list[str] = []

        with self.assertRaises(DeadlineExceededError):
            OperatingMarginService(
                FakeAuthorizer(self.query, calls),
                FakeRepository(self.aggregate, calls),
            ).get_operating_margin(
                self.request,
                self.context,
                DeadlineProbe([False, False, False, True]),
            )

        self.assertEqual(["authorizer", "repository"], calls)


if __name__ == "__main__":
    unittest.main()
