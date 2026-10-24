"""
PURPOSE
- Validate PostgreSQLOperatingMarginRepository with six fake tests and three
  opt-in real PostgreSQL tests; no fixture records, roles, or RLS settings are changed.

INTERFACES / PROTOCOLS
- RUN_DATABASE_TESTS is true only for FIN_AI_RUN_DB_TESTS=1; normal runs skip
  the real tests, while explicit runs fail for missing Psycopg, config, connectivity, or fixture.
- DatabaseConfig supplies existing environment-based settings; password values are not printed.

CLASSES
- DeadlineProbe: __init__(expired) and is_expired() provide deterministic deadline states.
- CursorProbe: __init__(rows), context methods, execute(), and fetchall() record fake SQL use.
- ConnectionProbe: __init__(rows), context methods, and cursor() provide one fake cursor.
- PostgreSQLOperatingMarginRepositoryFakeTests: fake connection/cursor tests; they prove local behavior only.
- PostgreSQLOperatingMarginRepositoryIntegrationTests: read-only tests against an existing EU 2026 fixture.

FUNCTIONS / METHODS
- PostgreSQLOperatingMarginRepositoryFakeTests.setUp: builds one valid E3-shaped EU Q2 query.
- PostgreSQLOperatingMarginRepositoryFakeTests.repository_with_rows: injects fake rows into the repository.
- test_no_rows_returns_none: fake test; no result rows must remain missing data, not zero totals.
- test_uses_fixed_sql_and_approved_parameters: fake test; verifies fixed SQL and bound EU/2026/Q2 values.
- test_zero_decimal_totals_remain_valid: fake test; zero totals remain valid Decimal values.
- test_multiple_currencies_raise_integrity_error: fake test; currency groups must not be merged.
- test_expired_deadline_prevents_connection: fake test; expiry stops connection creation.
- test_database_errors_are_not_authorization_denials: fake test; driver failure is not relabeled as E3 denial.
- PostgreSQLOperatingMarginRepositoryIntegrationTests.setUpClass: requires Psycopg, config,
  connectivity, and POSTED EU Q1/Q2 fixture rows when database tests are explicitly enabled.
- connection_factory: opens the configured connection used by each real repository read.
- approved_query(quarter): creates the E3-shaped query required by the repository interface.
- get_aggregate(quarter): reads one real fixture quarter through the concrete repository.
- test_reads_europe_q1_totals: real DB test; expects EU/EUR 100M revenue and 82M expenses.
- test_reads_europe_q2_totals: real DB test; expects EU/EUR 100M revenue and 86M expenses.
- test_absent_reporting_period_returns_none: real DB test; an absent period must return None.

CONSTANTS / CONFIGURATION
- PROJECT_ROOT locates the existing importer module; RUN_DATABASE_TESTS controls opt-in DB execution.
- Fake tests use controlled probes; real tests use existing credentials and pre-imported data only.

IMPORTANT LIMITATIONS
- Fake tests do not prove Psycopg or PostgreSQL behavior; real tests validate only the configured fixture.
- These tests do not prove authentication, entitlements, E3 enforcement, RLS, least-privilege roles,
  timeout cancellation, pooling, concurrency behavior, production performance, or operational guarantees.
"""

from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    import psycopg

    from import_synthetic_finance import DatabaseConfig
    from finance_api.context import AuthorizedOperatingMarginQuery, MonotonicDeadline
    from finance_api.repository import (
        MixedCurrencyAggregateError,
        OPERATING_MARGIN_SOURCE,
        OPERATING_MARGIN_SQL,
        PostgreSQLOperatingMarginRepository,
    )
    from finance_api.service import DeadlineExceededError
except ModuleNotFoundError:
    psycopg = None


RUN_DATABASE_TESTS = os.environ.get("FIN_AI_RUN_DB_TESTS") == "1"


class DeadlineProbe:
    """Return a controlled deadline state without waiting for clock time."""

    def __init__(self, expired: bool) -> None:
        self._expired = expired

    def is_expired(self) -> bool:
        """Return the configured deadline state."""
        return self._expired


class CursorProbe:
    """Provide fixed database rows and record the repository query."""

    def __init__(self, rows: list[tuple[object, object, object]]) -> None:
        self._rows = rows
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "CursorProbe":
        return self

    def __exit__(self, exception_type, exception, traceback) -> bool:
        return False

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        """Record the fixed statement and its bound parameters."""
        self.executions.append((sql, parameters))

    def fetchall(self) -> list[tuple[object, object, object]]:
        """Return the rows chosen for this focused repository test."""
        return self._rows


class ConnectionProbe:
    """Provide one cursor without opening a real database connection."""

    def __init__(self, rows: list[tuple[object, object, object]]) -> None:
        self.cursor_probe = CursorProbe(rows)

    def __enter__(self) -> "ConnectionProbe":
        return self

    def __exit__(self, exception_type, exception, traceback) -> bool:
        return False

    def cursor(self) -> CursorProbe:
        """Return the test cursor used by the repository."""
        return self.cursor_probe


@unittest.skipUnless(psycopg is not None, "requires Psycopg 3")
class PostgreSQLOperatingMarginRepositoryFakeTests(unittest.TestCase):
    """Verify edge cases with test doubles, not a PostgreSQL server."""

    def setUp(self) -> None:
        """Create one approved query used by each repository call."""
        self.query = AuthorizedOperatingMarginQuery(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
            principal_id="user-123",
            workload_id="finance-api",
            operation="get_operating_margin",
            correlation_id="corr-123",
        )

    @staticmethod
    def repository_with_rows(
        rows: list[tuple[object, object, object]],
    ) -> tuple[PostgreSQLOperatingMarginRepository, ConnectionProbe]:
        """Build a repository with one test-only connection and cursor."""
        connection = ConnectionProbe(rows)
        return PostgreSQLOperatingMarginRepository(lambda: connection), connection

    def test_no_rows_returns_none(self) -> None:
        """No posted rows are different from a valid zero-valued aggregate."""
        repository, _ = self.repository_with_rows([])

        self.assertIsNone(
            repository.get_operating_margin_aggregate(self.query, DeadlineProbe(False))
        )

    def test_uses_fixed_sql_and_approved_parameters(self) -> None:
        """The repository binds the approved reporting grain without SQL interpolation."""
        repository, connection = self.repository_with_rows(
            [("EUR", Decimal("100.00"), Decimal("82.00"))]
        )

        aggregate = repository.get_operating_margin_aggregate(
            self.query,
            DeadlineProbe(False),
        )

        self.assertEqual("EUR", aggregate.currency)
        self.assertEqual(Decimal("100.00"), aggregate.revenue)
        self.assertEqual(Decimal("82.00"), aggregate.operating_expenses)
        self.assertEqual(
            [(OPERATING_MARGIN_SQL, ("EU", 2026, 2))],
            connection.cursor_probe.executions,
        )

    def test_zero_decimal_totals_remain_valid(self) -> None:
        """Matching rows may have zero totals for either financial direction."""
        repository, _ = self.repository_with_rows(
            [("EUR", Decimal("0.00"), Decimal("0.00"))]
        )

        aggregate = repository.get_operating_margin_aggregate(
            self.query,
            DeadlineProbe(False),
        )

        self.assertEqual(Decimal("0.00"), aggregate.revenue)
        self.assertEqual(Decimal("0.00"), aggregate.operating_expenses)
        self.assertIsInstance(aggregate.revenue, Decimal)
        self.assertIsInstance(aggregate.operating_expenses, Decimal)

    def test_multiple_currencies_raise_integrity_error(self) -> None:
        """Separate currency groups must never be added together by the repository."""
        repository, _ = self.repository_with_rows(
            [
                ("EUR", Decimal("100.00"), Decimal("82.00")),
                ("USD", Decimal("100.00"), Decimal("82.00")),
            ]
        )

        with self.assertRaises(MixedCurrencyAggregateError):
            repository.get_operating_margin_aggregate(self.query, DeadlineProbe(False))

    def test_expired_deadline_prevents_connection(self) -> None:
        """The repository does not start database work after deadline expiry."""
        factory_called = False

        def unused_factory() -> ConnectionProbe:
            nonlocal factory_called
            factory_called = True
            raise AssertionError("expired deadline must prevent connection creation")

        repository = PostgreSQLOperatingMarginRepository(unused_factory)

        with self.assertRaises(DeadlineExceededError):
            repository.get_operating_margin_aggregate(self.query, DeadlineProbe(True))

        self.assertFalse(factory_called)

    def test_database_errors_are_not_authorization_denials(self) -> None:
        """Driver failures remain available for later service error mapping."""

        class FailingCursor(CursorProbe):
            def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
                raise RuntimeError("database unavailable")

        class FailingConnection(ConnectionProbe):
            def __init__(self) -> None:
                self.cursor_probe = FailingCursor([])

        repository = PostgreSQLOperatingMarginRepository(FailingConnection)

        with self.assertRaises(RuntimeError) as error_context:
            repository.get_operating_margin_aggregate(self.query, DeadlineProbe(False))

        self.assertEqual("database unavailable", str(error_context.exception))


@unittest.skipUnless(
    RUN_DATABASE_TESTS,
    "requires FIN_AI_RUN_DB_TESTS=1",
)
class PostgreSQLOperatingMarginRepositoryIntegrationTests(unittest.TestCase):
    """Read the pre-imported 2026 Europe fixture without changing database data."""

    @classmethod
    def setUpClass(cls) -> None:
        """Require Psycopg, database settings, connectivity, and both fixture quarters."""
        if psycopg is None:
            raise RuntimeError("FIN_AI_RUN_DB_TESTS=1 requires Psycopg 3")

        try:
            cls.config = DatabaseConfig.from_environment()
        except RuntimeError as error:
            raise RuntimeError(
                f"FIN_AI_RUN_DB_TESTS=1 requires database configuration: {error}"
            ) from None

        try:
            with psycopg.connect(**cls.config.connection_parameters()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT fp.quarter
                        FROM public.financial_transactions AS ft
                        INNER JOIN public.business_units AS bu
                            ON bu.business_unit_id = ft.business_unit_id
                        INNER JOIN public.financial_periods AS fp
                            ON fp.period_id = ft.period_id
                        WHERE bu.business_unit_code = %s
                          AND fp.financial_year = %s
                          AND fp.quarter IN (%s, %s)
                          AND ft.posting_status = 'POSTED'
                          AND ft.transaction_type IN ('REVENUE', 'EXPENSE')
                        GROUP BY fp.quarter
                        """,
                        ("EU", 2026, 1, 2),
                    )
                    available_quarters = {row[0] for row in cursor.fetchall()}
        except psycopg.Error:
            raise RuntimeError(
                "FIN_AI_RUN_DB_TESTS=1 cannot connect to the configured PostgreSQL test database"
            ) from None

        if available_quarters != {1, 2}:
            raise RuntimeError(
                "FIN_AI_RUN_DB_TESTS=1 requires pre-imported POSTED EU financial "
                "transactions for 2026 Q1 and Q2"
            )

    def connection_factory(self):
        """Open a new read connection from the configured test environment."""
        return psycopg.connect(**self.config.connection_parameters())

    @staticmethod
    def approved_query(quarter: int) -> AuthorizedOperatingMarginQuery:
        """Build the E3-shaped query required by the repository interface."""
        return AuthorizedOperatingMarginQuery(
            business_unit_code="EU",
            financial_year=2026,
            quarter=quarter,
            principal_id="test-principal",
            workload_id="test-workload",
            operation="get_operating_margin",
            correlation_id=f"repository-q{quarter}",
        )

    def get_aggregate(self, quarter: int):
        """Read one fixture quarter through the concrete repository."""
        repository = PostgreSQLOperatingMarginRepository(self.connection_factory)
        return repository.get_operating_margin_aggregate(
            self.approved_query(quarter),
            MonotonicDeadline.from_timeout_seconds(10),
        )

    def test_reads_europe_q1_totals(self) -> None:
        """The real Q1 fixture returns its recorded revenue and expense totals."""
        aggregate = self.get_aggregate(1)

        self.assertIsNotNone(aggregate)
        self.assertEqual("EU", aggregate.business_unit_code)
        self.assertEqual(2026, aggregate.financial_year)
        self.assertEqual(1, aggregate.quarter)
        self.assertEqual("EUR", aggregate.currency)
        self.assertEqual(Decimal("100000000.00"), aggregate.revenue)
        self.assertEqual(Decimal("82000000.00"), aggregate.operating_expenses)
        self.assertIsInstance(aggregate.revenue, Decimal)
        self.assertIsInstance(aggregate.operating_expenses, Decimal)
        self.assertEqual(OPERATING_MARGIN_SOURCE, aggregate.source)

    def test_reads_europe_q2_totals(self) -> None:
        """The real Q2 fixture returns its recorded revenue and expense totals."""
        aggregate = self.get_aggregate(2)

        self.assertIsNotNone(aggregate)
        self.assertEqual("EU", aggregate.business_unit_code)
        self.assertEqual(2026, aggregate.financial_year)
        self.assertEqual(2, aggregate.quarter)
        self.assertEqual("EUR", aggregate.currency)
        self.assertEqual(Decimal("100000000.00"), aggregate.revenue)
        self.assertEqual(Decimal("86000000.00"), aggregate.operating_expenses)
        self.assertIsInstance(aggregate.revenue, Decimal)
        self.assertIsInstance(aggregate.operating_expenses, Decimal)
        self.assertEqual(OPERATING_MARGIN_SOURCE, aggregate.source)

    def test_absent_reporting_period_returns_none(self) -> None:
        """A reporting period with no fixture rows is not reported as zero totals."""
        repository = PostgreSQLOperatingMarginRepository(self.connection_factory)
        absent_query = AuthorizedOperatingMarginQuery(
            business_unit_code="EU",
            financial_year=9999,
            quarter=1,
            principal_id="test-principal",
            workload_id="test-workload",
            operation="get_operating_margin",
            correlation_id="repository-absent",
        )

        self.assertIsNone(
            repository.get_operating_margin_aggregate(
                absent_query,
                MonotonicDeadline.from_timeout_seconds(10),
            )
        )


if __name__ == "__main__":
    unittest.main()
