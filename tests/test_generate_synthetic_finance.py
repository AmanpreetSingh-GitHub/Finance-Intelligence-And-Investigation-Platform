"""Validation tests for the deterministic synthetic finance dataset."""

from __future__ import annotations

import calendar
import csv
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from generate_synthetic_finance import (  # noqa: E402
    CSV_FIELDS,
    DEFAULT_SEED,
    generate_dataset,
    summarize_by_currency,
    write_dataset,
)


class SyntheticFinanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = generate_dataset(DEFAULT_SEED)
        cls.transactions = cls.dataset["financial_transactions"]
        cls.totals = summarize_by_currency(cls.transactions)

    def total(
        self, quarter: int, transaction_type: str, category_code: str = ""
    ) -> Decimal:
        return self.totals[("EU", quarter, "EUR", transaction_type, category_code)]

    def test_repeated_generation_is_identical(self) -> None:
        self.assertEqual(generate_dataset(DEFAULT_SEED), generate_dataset(DEFAULT_SEED))
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            write_dataset(generate_dataset(DEFAULT_SEED), Path(first))
            write_dataset(generate_dataset(DEFAULT_SEED), Path(second))
            for first_file in sorted(Path(first).glob("*.csv")):
                second_file = Path(second) / first_file.name
                self.assertEqual(first_file.read_bytes(), second_file.read_bytes())

    def test_europe_quarterly_reconciliation(self) -> None:
        expected = {
            1: {
                ("REVENUE", ""): Decimal("100000000.00"),
                ("EXPENSE", "LOGISTICS"): Decimal("10000000.00"),
                ("EXPENSE", "RAW_MATERIALS"): Decimal("35000000.00"),
                ("EXPENSE", "OTHER_OPERATING"): Decimal("37000000.00"),
            },
            2: {
                ("REVENUE", ""): Decimal("100000000.00"),
                ("EXPENSE", "LOGISTICS"): Decimal("12500000.00"),
                ("EXPENSE", "RAW_MATERIALS"): Decimal("36500000.00"),
                ("EXPENSE", "OTHER_OPERATING"): Decimal("37000000.00"),
            },
        }
        for quarter, targets in expected.items():
            for (transaction_type, category), amount in targets.items():
                self.assertEqual(self.total(quarter, transaction_type, category), amount)

    def test_europe_operating_margins(self) -> None:
        expected_margins = {1: Decimal("0.18"), 2: Decimal("0.14")}
        for quarter, expected_margin in expected_margins.items():
            revenue = self.total(quarter, "REVENUE")
            expenses = sum(
                self.total(quarter, "EXPENSE", category)
                for category in ("LOGISTICS", "RAW_MATERIALS", "OTHER_OPERATING")
            )
            self.assertEqual((revenue - expenses) / revenue, expected_margin)

    def test_europe_cost_increases(self) -> None:
        self.assertEqual(
            self.total(2, "EXPENSE", "LOGISTICS")
            - self.total(1, "EXPENSE", "LOGISTICS"),
            Decimal("2500000.00"),
        )
        self.assertEqual(
            self.total(2, "EXPENSE", "RAW_MATERIALS")
            - self.total(1, "EXPENSE", "RAW_MATERIALS"),
            Decimal("1500000.00"),
        )

    def test_amounts_are_positive_and_posted(self) -> None:
        for transaction in self.transactions:
            self.assertGreater(transaction["amount"], Decimal("0.00"))
            self.assertEqual(transaction["amount"].as_tuple().exponent, -2)
            self.assertEqual(transaction["posting_status"], "POSTED")

    def test_posting_dates_match_periods(self) -> None:
        periods = {
            period["period_key"]: period for period in self.dataset["financial_periods"]
        }
        for transaction in self.transactions:
            period = periods[transaction["period_key"]]
            self.assertLessEqual(period["period_start"], transaction["posting_date"])
            self.assertLessEqual(transaction["posting_date"], period["period_end"])
            self.assertEqual(
                period["period_end"].day,
                calendar.monthrange(period["financial_year"], period["month_number"])[1],
            )

    def test_references_and_category_rules(self) -> None:
        business_units = {
            row["business_unit_code"] for row in self.dataset["business_units"]
        }
        categories = {row["category_code"] for row in self.dataset["cost_categories"]}
        suppliers = {row["supplier_code"] for row in self.dataset["suppliers"]}
        periods = {row["period_key"] for row in self.dataset["financial_periods"]}
        for transaction in self.transactions:
            self.assertIn(transaction["business_unit_code"], business_units)
            self.assertIn(transaction["period_key"], periods)
            if transaction["transaction_type"] == "REVENUE":
                self.assertEqual(transaction["category_code"], "")
                self.assertEqual(transaction["supplier_code"], "")
            else:
                self.assertIn(transaction["category_code"], categories)
                self.assertIn(transaction["supplier_code"], suppliers)
        for contract in self.dataset["contracts"]:
            self.assertIn(contract["business_unit_code"], business_units)
            self.assertIn(contract["supplier_code"], suppliers)

    def test_currency_boundaries_are_preserved(self) -> None:
        unit_currencies = {
            row["business_unit_code"]: row["base_currency"]
            for row in self.dataset["business_units"]
        }
        self.assertEqual(set(unit_currencies.values()), {"EUR", "INR", "USD"})
        for transaction in self.transactions:
            self.assertEqual(
                transaction["currency_code"],
                unit_currencies[transaction["business_unit_code"]],
            )
        for key in self.totals:
            business_unit_code, _, currency_code, _, _ = key
            self.assertEqual(currency_code, unit_currencies[business_unit_code])

    def test_csv_headers_use_stable_relationship_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_dataset(self.dataset, Path(directory))
            with (Path(directory) / "financial_transactions.csv").open(
                encoding="utf-8", newline=""
            ) as csv_file:
                headers = csv.DictReader(csv_file).fieldnames
            self.assertIn("business_unit_code", headers)
            self.assertIn("period_key", headers)
            self.assertNotIn("business_unit_id", headers)
            self.assertNotIn("period_id", headers)

    def test_source_references_are_unique_within_business_units(self) -> None:
        scoped_references = [
            (transaction["business_unit_code"], transaction["source_reference"])
            for transaction in self.transactions
        ]
        self.assertEqual(len(scoped_references), len(set(scoped_references)))

    def test_csv_round_trip_preserves_all_records_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            write_dataset(self.dataset, output_directory)

            for entity, fields in CSV_FIELDS.items():
                with self.subTest(entity=entity):
                    with (output_directory / f"{entity}.csv").open(
                        encoding="utf-8", newline=""
                    ) as csv_file:
                        serialized_records = list(csv.DictReader(csv_file))

                    expected_records = self.dataset[entity]
                    self.assertEqual(len(serialized_records), len(expected_records))
                    for expected, serialized in zip(expected_records, serialized_records):
                        self.assertEqual(tuple(serialized), fields)
                        for field in fields:
                            expected_value = expected[field]
                            serialized_value = serialized[field]
                            if isinstance(expected_value, bool):
                                self.assertEqual(serialized_value, str(expected_value).lower())
                            elif isinstance(expected_value, Decimal):
                                self.assertEqual(Decimal(serialized_value), expected_value)
                            elif isinstance(expected_value, date):
                                self.assertEqual(date.fromisoformat(serialized_value), expected_value)
                            elif isinstance(expected_value, int):
                                self.assertEqual(int(serialized_value), expected_value)
                            else:
                                self.assertEqual(serialized_value, expected_value)


if __name__ == "__main__":
    unittest.main()
