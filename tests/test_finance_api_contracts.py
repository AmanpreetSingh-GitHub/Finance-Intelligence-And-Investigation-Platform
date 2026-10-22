"""Automated validation tests for the FIN-AI finance API contracts."""

from decimal import Decimal
import json
import unittest

from pydantic import ValidationError

from finance_api.contracts import (
    FinanceApiErrorName,
    OperatingMarginRequest,
    OperatingMarginResponse,
)


class OperatingMarginRequestTests(unittest.TestCase):
    """Verify strict input validation for operating-margin requests."""

    def test_accepts_valid_europe_request(self) -> None:
        """A valid EU request identifies the requested calendar quarter."""
        request = OperatingMarginRequest(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
        )

        self.assertEqual("EU", request.business_unit_code)
        self.assertEqual(2026, request.financial_year)
        self.assertEqual(2, request.quarter)

    def test_trims_business_unit_code_whitespace(self) -> None:
        """Business-unit codes are normalized before later authorization use."""
        request = OperatingMarginRequest(
            business_unit_code="  EU  ",
            financial_year=2026,
            quarter=2,
        )

        self.assertEqual("EU", request.business_unit_code)

    def test_rejects_blank_business_unit_code(self) -> None:
        """A request must identify a nonblank business unit."""
        with self.assertRaises(ValidationError):
            OperatingMarginRequest(
                business_unit_code="   ",
                financial_year=2026,
                quarter=2,
            )

    def test_rejects_nonpositive_financial_year(self) -> None:
        """Financial years must be positive integers."""
        for financial_year in (0, -1):
            with self.subTest(financial_year=financial_year):
                with self.assertRaises(ValidationError):
                    OperatingMarginRequest(
                        business_unit_code="EU",
                        financial_year=financial_year,
                        quarter=2,
                    )

    def test_rejects_quarter_outside_calendar_range(self) -> None:
        """Only calendar quarters one through four are valid."""
        for quarter in (0, 5):
            with self.subTest(quarter=quarter):
                with self.assertRaises(ValidationError):
                    OperatingMarginRequest(
                        business_unit_code="EU",
                        financial_year=2026,
                        quarter=quarter,
                    )

    def test_rejects_boolean_integer_fields(self) -> None:
        """Strict validation prevents booleans being accepted as integers."""
        for field_name in ("financial_year", "quarter"):
            values = {
                "business_unit_code": "EU",
                "financial_year": 2026,
                "quarter": 2,
            }
            values[field_name] = True

            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    OperatingMarginRequest(**values)

    def test_rejects_string_financial_year(self) -> None:
        """Strict validation prevents numeric strings being coerced."""
        with self.assertRaises(ValidationError):
            OperatingMarginRequest(
                business_unit_code="EU",
                financial_year="2026",
                quarter=2,
            )

    def test_rejects_none_business_unit_code(self) -> None:
        """A request cannot omit its business-unit identifier."""
        with self.assertRaises(ValidationError):
            OperatingMarginRequest(
                business_unit_code=None,
                financial_year=2026,
                quarter=2,
            )

    def test_rejects_unexpected_request_field(self) -> None:
        """The public request contract does not accept unrecognized fields."""
        with self.assertRaises(ValidationError):
            OperatingMarginRequest(
                business_unit_code="EU",
                financial_year=2026,
                quarter=2,
                unexpected="value",
            )

    def test_request_is_frozen(self) -> None:
        """Validated request values cannot be changed after construction."""
        request = OperatingMarginRequest(
            business_unit_code="EU",
            financial_year=2026,
            quarter=2,
        )

        with self.assertRaises(ValidationError):
            request.quarter = 3


class OperatingMarginResponseTests(unittest.TestCase):
    """Verify precise, strict response data for operating-margin results."""

    @staticmethod
    def response_values() -> dict[str, object]:
        """Provide a valid response payload with exact financial decimals."""
        return {
            "business_unit_code": "EU",
            "financial_year": 2026,
            "quarter": 2,
            "currency": "EUR",
            "revenue": Decimal("100000000.00"),
            "operating_expenses": Decimal("86000000.00"),
            "operating_profit": Decimal("14000000.00"),
            "operating_margin_percent": Decimal("14.00"),
            "source": "postgresql",
        }

    def test_accepts_decimal_financial_values(self) -> None:
        """Financial outputs retain exact Decimal values in Python."""
        response = OperatingMarginResponse(**self.response_values())

        self.assertEqual(Decimal("100000000.00"), response.revenue)
        self.assertEqual(Decimal("14.00"), response.operating_margin_percent)

    def test_rejects_float_monetary_values(self) -> None:
        """Strict response validation rejects imprecise floating-point money."""
        for field_name in (
            "revenue",
            "operating_expenses",
            "operating_profit",
            "operating_margin_percent",
        ):
            values = self.response_values()
            values[field_name] = 1.25

            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    OperatingMarginResponse(**values)

    def test_serializes_decimal_values_as_json_strings(self) -> None:
        """JSON protects financial precision by representing decimals as strings."""
        response = OperatingMarginResponse(**self.response_values())
        payload = json.loads(response.model_dump_json())

        self.assertEqual("100000000.00", payload["revenue"])
        self.assertEqual("86000000.00", payload["operating_expenses"])
        self.assertEqual("14000000.00", payload["operating_profit"])
        self.assertEqual("14.00", payload["operating_margin_percent"])

    def test_accepts_nullable_margin_percentage(self) -> None:
        """A zero-revenue calculation may legitimately have no margin percentage."""
        values = self.response_values()
        values["revenue"] = Decimal("0.00")
        values["operating_margin_percent"] = None

        response = OperatingMarginResponse(**values)

        self.assertIsNone(response.operating_margin_percent)

    def test_rejects_unexpected_response_field(self) -> None:
        """The public response contract does not accept unrecognized fields."""
        values = self.response_values()
        values["unexpected"] = "value"

        with self.assertRaises(ValidationError):
            OperatingMarginResponse(**values)


class FinanceApiErrorNameTests(unittest.TestCase):
    """Verify stable API error names used by future transport layers."""

    def test_error_names_remain_unchanged(self) -> None:
        """Clients can rely on the five documented error values."""
        self.assertEqual("INVALID_INPUT", FinanceApiErrorName.INVALID_INPUT.value)
        self.assertEqual("UNAUTHENTICATED", FinanceApiErrorName.UNAUTHENTICATED.value)
        self.assertEqual("ACCESS_DENIED", FinanceApiErrorName.ACCESS_DENIED.value)
        self.assertEqual("DATA_NOT_AVAILABLE", FinanceApiErrorName.DATA_NOT_AVAILABLE.value)
        self.assertEqual(
            "SERVICE_UNAVAILABLE",
            FinanceApiErrorName.SERVICE_UNAVAILABLE.value,
        )
