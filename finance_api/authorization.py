"""Contract for the planned independent Finance API E3 authorization check.

The future Finance API service calls E3 after E2 approves a tool call.
This module defines the boundary only; it does not authenticate or authorize anyone.
"""

from typing import Protocol

from finance_api.context import (
    AuthorizedOperatingMarginQuery,
    FinanceRequestContext,
    MonotonicDeadline,
)
from finance_api.contracts import OperatingMarginRequest


class AuthorizationDeniedError(Exception):
    """Signal that E3 could not establish access to a requested finance result.

    A future E3 implementation raises this when trusted authority is missing or denies
    the workload, user, operation, or business unit. The service must fail closed.
    """


class OperatingMarginAuthorizer(Protocol):
    """Define the independent E3 check required before an operating-margin read.

    The future Finance API service calls this interface after request validation and E2.
    A successful result means E3 approved this exact query for downstream processing.
    Real implementations must use a trusted authority; context fields and E2 approval
    alone are not authorization proof. This protocol does not enforce security itself.
    """

    def authorize(
        self,
        request: OperatingMarginRequest,
        context: FinanceRequestContext,
        deadline: MonotonicDeadline,
    ) -> AuthorizedOperatingMarginQuery:
        """Return an E3-approved query or raise AuthorizationDeniedError.

        A future implementation must verify workload, user authority, operation, and
        business-unit access through a trusted authority before returning a query.
        """
        ...
