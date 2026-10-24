"""
PURPOSE
- Read one approved operating-margin aggregate for the Finance API service.
- Use fixed SQL and bound values; raw caller, agent, and model filters are not accepted.

INTERFACES / PROTOCOLS
- OperatingMarginRepository is imported from finance_api.service; this file's
  PostgreSQLOperatingMarginRepository implements its aggregate-read method.
- AuthorizedOperatingMarginQuery supplies the approved reporting grain; it is not
  authorization proof. MonotonicDeadline carries the caller's shared time budget.

CLASSES
- MixedCurrencyAggregateError: internal error when one reporting grain has more
  than one currency; the repository will not select or add currencies.
- PostgreSQLOperatingMarginRepository: concrete Psycopg read boundary using an
  injected connection factory so configuration and credentials remain outside it.

FUNCTIONS / METHODS
- __init__(connection_factory): stores a required factory without opening a connection.
- get_operating_margin_aggregate(query, deadline): checks time, runs the fixed query,
  returns None for no rows, an aggregate for one currency, or an error for many.
- PostgreSQL NUMERIC values pass through as Decimal; profit and margin stay in
  calculate_operating_metrics(), not SQL.

CONSTANTS / CONFIGURATION
- OPERATING_MARGIN_SOURCE: provenance identifier copied to returned aggregates.
- OPERATING_MARGIN_SQL: one parameterized query for approved business unit, year,
  quarter, POSTED transactions, and separate REVENUE/EXPENSE totals.
- GROUP BY ft.currency_code prevents silent cross-currency aggregation; no FX rule exists.

IMPORTANT LIMITATIONS
- Missing rows and zero totals differ: no row returns None; a zero grouped total is valid.
- E3 authorization, RLS, least-privilege roles, driver/database timeout enforcement,
  pooling, retries, cancellation, and production observability are not implemented here.
- The deadline check stops work before connection creation; it cannot cancel a running query.
"""

from collections.abc import Callable
from decimal import Decimal

from psycopg import Connection

from finance_api.context import (
    AuthorizedOperatingMarginQuery,
    MonotonicDeadline,
    OperatingMarginAggregate,
)
from finance_api.service import DeadlineExceededError, OperatingMarginRepository


OPERATING_MARGIN_SOURCE = "postgresql:operating-margin-v1"

# This is the only financial aggregation query exposed by this repository.
# Currency grouping makes a mixed-currency reporting grain visible instead of merged.
OPERATING_MARGIN_SQL = """
    SELECT
        ft.currency_code,
        COALESCE(
            SUM(ft.amount) FILTER (WHERE ft.transaction_type = 'REVENUE'),
            0::NUMERIC
        ) AS revenue,
        COALESCE(
            SUM(ft.amount) FILTER (WHERE ft.transaction_type = 'EXPENSE'),
            0::NUMERIC
        ) AS operating_expenses
    FROM public.financial_transactions AS ft
    INNER JOIN public.business_units AS bu
        ON bu.business_unit_id = ft.business_unit_id
    INNER JOIN public.financial_periods AS fp
        ON fp.period_id = ft.period_id
    WHERE bu.business_unit_code = %s
      AND fp.financial_year = %s
      AND fp.quarter = %s
      AND ft.posting_status = 'POSTED'
      AND ft.transaction_type IN ('REVENUE', 'EXPENSE')
    GROUP BY ft.currency_code
    ORDER BY ft.currency_code
"""


class MixedCurrencyAggregateError(RuntimeError):
    """Signal that one requested reporting grain has more than one currency."""


class PostgreSQLOperatingMarginRepository(OperatingMarginRepository):
    """Read one authorized margin aggregate from PostgreSQL.

    The Finance API service creates this with a caller-provided connection factory.
    It opens one short read transaction, runs fixed SQL, and returns Decimal totals.
    An approved query object is required, but it is not proof of authorization itself.
    """

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        """Store the factory so credentials and connection details stay outside this class."""
        if connection_factory is None:
            raise ValueError("connection_factory is required")
        self._connection_factory = connection_factory

    def get_operating_margin_aggregate(
        self,
        query: AuthorizedOperatingMarginQuery,
        deadline: MonotonicDeadline,
    ) -> OperatingMarginAggregate | None:
        """Return one posted aggregate, no data, or a mixed-currency integrity error.

        The service passes an E3-approved query and its original deadline. This method
        checks time before database work but does not enforce a driver-side timeout.
        """
        if deadline.is_expired():
            raise DeadlineExceededError("operating-margin request deadline exceeded")

        # The connection context keeps this single-statement read transaction short.
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    OPERATING_MARGIN_SQL,
                    (
                        query.business_unit_code,
                        query.financial_year,
                        query.quarter,
                    ),
                )
                rows = cursor.fetchall()

        if not rows:
            return None
        if len(rows) != 1:
            raise MixedCurrencyAggregateError(
                "operating-margin aggregate contains multiple currencies"
            )

        currency, revenue, operating_expenses = rows[0]
        return OperatingMarginAggregate(
            business_unit_code=query.business_unit_code,
            financial_year=query.financial_year,
            quarter=query.quarter,
            currency=currency,
            revenue=revenue,
            operating_expenses=operating_expenses,
            source=OPERATING_MARGIN_SOURCE,
        )
