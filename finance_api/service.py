"""Internal service for one authorized operating-margin request.

The future HTTP or MCP adapter will call this service after it creates trusted context.
This module coordinates E3, the repository, and deterministic financial calculation.
"""

from typing import Protocol

from finance_api.authorization import OperatingMarginAuthorizer
from finance_api.calculations import calculate_operating_metrics
from finance_api.context import (
    AuthorizedOperatingMarginQuery,
    FinanceRequestContext,
    MonotonicDeadline,
    OperatingMarginAggregate,
)
from finance_api.contracts import OperatingMarginRequest, OperatingMarginResponse


class DeadlineExceededError(Exception):
    """Signal that the shared request deadline expired before work could continue."""


class DataNotAvailableError(Exception):
    """Signal that no approved aggregate exists for an authorized query."""


class ServiceIntegrityError(Exception):
    """Signal that an authorizer or repository returned inconsistent approved data."""


class OperatingMarginRepository(Protocol):
    """Define the planned repository read used after E3 has approved a query.

    A future PostgreSQL repository will implement this method. It receives only the
    approved query and shared deadline, and it returns one aggregate or no data.
    """

    def get_operating_margin_aggregate(
        self,
        query: AuthorizedOperatingMarginQuery,
        deadline: MonotonicDeadline,
    ) -> OperatingMarginAggregate | None:
        """Read one authorized reporting aggregate before the deadline expires."""
        ...


class OperatingMarginService:
    """Coordinate E3, an authorized aggregate read, and Decimal calculations.

    The future Finance API service layer creates this with real E3 and repository
    dependencies. It never uses request context or E2 approval as access proof.
    """

    def __init__(
        self,
        authorizer: OperatingMarginAuthorizer,
        repository: OperatingMarginRepository,
    ) -> None:
        """Require both boundaries so no authorization or data-read bypass exists."""
        if authorizer is None:
            raise ValueError("authorizer is required")
        if repository is None:
            raise ValueError("repository is required")

        self._authorizer = authorizer
        self._repository = repository

    def get_operating_margin(
        self,
        request: OperatingMarginRequest,
        context: FinanceRequestContext,
        deadline: MonotonicDeadline,
    ) -> OperatingMarginResponse:
        """Return one calculated result after independent E3 and repository checks.

        Authorization errors are allowed to stop the request unchanged. Other checks
        fail closed before financial data is returned or a repository read is made.
        """
        self._require_time_remaining(deadline)

        # E3 must run before any repository access, even when E2 approved the tool call.
        query = self._authorizer.authorize(request, context, deadline)

        self._require_time_remaining(deadline)
        self._validate_authorized_query(request, context, query)

        aggregate = self._repository.get_operating_margin_aggregate(query, deadline)
        self._require_time_remaining(deadline)
        if aggregate is None:
            raise DataNotAvailableError("operating-margin data is not available")

        self._validate_aggregate(query, aggregate)
        operating_profit, operating_margin_percent = calculate_operating_metrics(
            aggregate.revenue,
            aggregate.operating_expenses,
        )

        self._require_time_remaining(deadline)
        return OperatingMarginResponse(
            business_unit_code=aggregate.business_unit_code,
            financial_year=aggregate.financial_year,
            quarter=aggregate.quarter,
            currency=aggregate.currency,
            revenue=aggregate.revenue,
            operating_expenses=aggregate.operating_expenses,
            operating_profit=operating_profit,
            operating_margin_percent=operating_margin_percent,
            source=aggregate.source,
        )

    @staticmethod
    def _require_time_remaining(deadline: MonotonicDeadline) -> None:
        """Stop before a dependency call when the shared deadline has expired."""
        if deadline.is_expired():
            raise DeadlineExceededError("operating-margin request deadline exceeded")

    @staticmethod
    def _validate_authorized_query(
        request: OperatingMarginRequest,
        context: FinanceRequestContext,
        query: AuthorizedOperatingMarginQuery,
    ) -> None:
        """Fail closed if E3 did not return the exact requested trusted context."""
        if (
            query.business_unit_code != request.business_unit_code
            or query.financial_year != request.financial_year
            or query.quarter != request.quarter
            or query.operation != context.operation
            or query.principal_id != context.principal_id
            or query.workload_id != context.workload_id
            or query.correlation_id != context.correlation_id
        ):
            raise ServiceIntegrityError("authorized query does not match the request")

    @staticmethod
    def _validate_aggregate(
        query: AuthorizedOperatingMarginQuery,
        aggregate: OperatingMarginAggregate,
    ) -> None:
        """Fail closed if repository data is outside the E3-approved reporting grain."""
        if (
            aggregate.business_unit_code != query.business_unit_code
            or aggregate.financial_year != query.financial_year
            or aggregate.quarter != query.quarter
        ):
            raise ServiceIntegrityError("aggregate does not match the authorized query")
