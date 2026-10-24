"""Small internal value types for the planned Finance API service.

These types carry validated values between future Finance API components.
They do not validate credentials, implement E3 authorization, or grant access.
"""

from dataclasses import dataclass
from decimal import Decimal
import math
import time
from typing import Literal


def _require_identifier(value: str, field_name: str) -> None:
    """Reject missing identifiers without changing the supplied value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")


def _require_operating_margin_operation(value: object) -> None:
    """Allow only the internal operation supported by this API slice."""
    if value != "get_operating_margin":
        raise ValueError("operation must be get_operating_margin")


def _require_positive_integer(value: int, field_name: str) -> None:
    """Reject booleans, non-integers, and nonpositive integer values."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_quarter(value: int) -> None:
    """Require one of the four calendar reporting quarters."""
    if type(value) is not int or value not in (1, 2, 3, 4):
        raise ValueError("quarter must be an integer from 1 through 4")


def _require_financial_total(value: Decimal, field_name: str) -> None:
    """Require a finite, non-negative Decimal total from the repository."""
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if value < Decimal("0"):
        raise ValueError(f"{field_name} must not be negative")


@dataclass(frozen=True, slots=True)
class FinanceRequestContext:
    """Carry minimal request context between trusted Finance API components.

    A future trusted inbound adapter creates this after credential validation.
    E3 and the operating-margin service use it to evaluate request scope.
    Its fields are immutable and validate the basic request identifiers.
    Creating this object alone does not prove identity or authorization.
    """

    principal_id: str
    workload_id: str
    operation: Literal["get_operating_margin"]
    authorized_business_unit_codes: frozenset[str]
    correlation_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.principal_id, "principal_id")
        _require_identifier(self.workload_id, "workload_id")
        _require_operating_margin_operation(self.operation)
        _require_identifier(self.correlation_id, "correlation_id")

        if not isinstance(self.authorized_business_unit_codes, frozenset):
            raise ValueError("authorized_business_unit_codes must be a frozenset")
        if not self.authorized_business_unit_codes:
            raise ValueError("authorized_business_unit_codes must not be empty")
        for business_unit_code in self.authorized_business_unit_codes:
            _require_identifier(
                business_unit_code,
                "authorized_business_unit_codes item",
            )


@dataclass(frozen=True, slots=True)
class AuthorizedOperatingMarginQuery:
    """Carry one operating-margin query after a future E3 approval.

    E3 will create this for the service after checking unit and operation access.
    The service will pass it to the repository instead of raw transport values.
    Its fields are immutable and define one reporting period and business unit.
    The object is not proof of authorization merely because it exists.
    """

    business_unit_code: str
    financial_year: int
    quarter: int
    principal_id: str
    workload_id: str
    operation: Literal["get_operating_margin"]
    correlation_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.business_unit_code, "business_unit_code")
        _require_positive_integer(self.financial_year, "financial_year")
        _require_quarter(self.quarter)
        _require_identifier(self.principal_id, "principal_id")
        _require_identifier(self.workload_id, "workload_id")
        _require_operating_margin_operation(self.operation)
        _require_identifier(self.correlation_id, "correlation_id")


@dataclass(frozen=True, slots=True)
class OperatingMarginAggregate:
    """Carry one PostgreSQL operating-margin aggregate to the service.

    A future repository creates this after an authorized database query.
    The operating-margin service uses it with the Decimal calculation function.
    It keeps one reporting grain, currency, totals, and source identifier immutable.
    It does not calculate profit or margin and does not prove data authorization.
    """

    business_unit_code: str
    financial_year: int
    quarter: int
    currency: str
    revenue: Decimal
    operating_expenses: Decimal
    source: str

    def __post_init__(self) -> None:
        _require_identifier(self.business_unit_code, "business_unit_code")
        _require_positive_integer(self.financial_year, "financial_year")
        _require_quarter(self.quarter)
        _require_identifier(self.currency, "currency")
        _require_financial_total(self.revenue, "revenue")
        _require_financial_total(self.operating_expenses, "operating_expenses")
        _require_identifier(self.source, "source")


@dataclass(frozen=True, slots=True)
class MonotonicDeadline:
    """Share one absolute request deadline across planned Finance API steps.

    A future adapter creates it when a request enters the Finance API.
    E3, the service, and the repository use its remaining time for their work.
    It compares only monotonic clock values and never returns negative time.
    It does not cancel work, create timers, or enforce access controls.
    """

    expires_at_monotonic: float

    def __post_init__(self) -> None:
        if type(self.expires_at_monotonic) is not float:
            raise ValueError("expires_at_monotonic must be a float")
        if not math.isfinite(self.expires_at_monotonic):
            raise ValueError("expires_at_monotonic must be finite")

    @classmethod
    def from_timeout_seconds(cls, timeout_seconds: float | int) -> "MonotonicDeadline":
        """Create one absolute deadline from a finite, non-negative timeout."""
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds,
            (float, int),
        ):
            raise ValueError("timeout_seconds must be an int or float")
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        return cls(expires_at_monotonic=time.monotonic() + timeout_seconds)

    def remaining_seconds(self) -> float:
        """Return the remaining time without exposing a negative value."""
        return max(0.0, self.expires_at_monotonic - time.monotonic())

    def is_expired(self) -> bool:
        """Report whether the shared deadline has reached zero remaining time."""
        return self.remaining_seconds() == 0.0
