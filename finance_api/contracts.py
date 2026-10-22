"""Request, response, and error contracts for finance API operations."""

from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class FinanceApiErrorName(str, Enum):
    """Stable error names that API transports can expose to clients."""

    INVALID_INPUT = "INVALID_INPUT"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    ACCESS_DENIED = "ACCESS_DENIED"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class OperatingMarginRequest(BaseModel):
    """Identify a validated business unit and quarter for margin calculation."""

    # Strict input prevents coercion such as "2026" or True into integer fields.
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    business_unit_code: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
    ]
    financial_year: Annotated[int, Field(gt=0)]
    quarter: Annotated[int, Field(ge=1, le=4)]


class OperatingMarginResponse(BaseModel):
    """Return reconciled operating-margin values and their source identifier."""

    # JSON uses strings for Decimal values so clients do not lose financial precision.
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
    )

    business_unit_code: str
    financial_year: int
    quarter: int
    currency: str
    revenue: Decimal
    operating_expenses: Decimal
    operating_profit: Decimal
    operating_margin_percent: Decimal | None
    source: str
