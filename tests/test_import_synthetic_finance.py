"""Opt-in PostgreSQL integration tests for the synthetic finance importer."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    import psycopg

    from import_synthetic_finance import (
        DatabaseConfig,
        IMPORT_ORDER,
        ImportConflictError,
        import_dataset,
        load_dataset,
    )
except ModuleNotFoundError:
    psycopg = None


RUN_DATABASE_TESTS = os.environ.get("FIN_AI_RUN_DB_TESTS") == "1"


@unittest.skipUnless(
    psycopg is not None and RUN_DATABASE_TESTS,
    "requires Psycopg 3 and FIN_AI_RUN_DB_TESTS=1",
)
class SyntheticFinanceImporterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = DatabaseConfig.from_environment()
        source = load_dataset()
        cls.dataset = cls._isolated_dataset(source)

    @staticmethod
    def _isolated_dataset(source):
        dataset = copy.deepcopy(source)
        business_units = {row["business_unit_code"]: f"T6{row['business_unit_code']}" for row in dataset["business_units"]}
        categories = {row["category_code"]: f"T6_{row['category_code']}" for row in dataset["cost_categories"]}
        suppliers = {row["supplier_code"]: f"T6-{row['supplier_code'][4:]}" for row in dataset["suppliers"]}

        for row in dataset["business_units"]:
            row["business_unit_code"] = business_units[row["business_unit_code"]]
        for row in dataset["cost_categories"]:
            row["category_code"] = categories[row["category_code"]]
        for row in dataset["suppliers"]:
            row["supplier_code"] = suppliers[row["supplier_code"]]
        for row in dataset["financial_periods"]:
            row["financial_year"] = 2126
            row["period_key"] = f"2126-{row['month_number']:02d}"
            row["period_start"] = row["period_start"].replace(year=2126)
            row["period_end"] = row["period_end"].replace(year=2126)
        for row in dataset["financial_transactions"]:
            row["business_unit_code"] = business_units[row["business_unit_code"]]
            row["period_key"] = f"2126-{row['period_key'][-2:]}"
            row["category_code"] = categories.get(row["category_code"])
            row["supplier_code"] = suppliers.get(row["supplier_code"])
            row["posting_date"] = row["posting_date"].replace(year=2126)
            row["source_reference"] = f"T6-{row['source_reference']}"
        for row in dataset["contracts"]:
            row["business_unit_code"] = business_units[row["business_unit_code"]]
            row["supplier_code"] = suppliers[row["supplier_code"]]
            row["contract_number"] = f"T6-{row['contract_number']}"
            row["effective_from"] = row["effective_from"].replace(year=2126)
            row["effective_to"] = row["effective_to"].replace(year=2126)
        return dataset

    def setUp(self) -> None:
        self.connection = psycopg.connect(
            **self.config.connection_parameters(), autocommit=True
        )
        self.connection.execute("BEGIN")

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def _test_record_counts(self) -> dict[str, int]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.business_units WHERE business_unit_code = ANY(%s)",
                ([row["business_unit_code"] for row in self.dataset["business_units"]],),
            )
            business_units = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM public.financial_periods WHERE financial_year = %s",
                (2126,),
            )
            financial_periods = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM public.cost_categories WHERE category_code = ANY(%s)",
                ([row["category_code"] for row in self.dataset["cost_categories"]],),
            )
            cost_categories = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM public.suppliers WHERE supplier_code = ANY(%s)",
                ([row["supplier_code"] for row in self.dataset["suppliers"]],),
            )
            suppliers = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*)
                FROM public.financial_transactions AS ft
                JOIN public.business_units AS bu USING (business_unit_id)
                JOIN public.financial_periods AS fp USING (period_id)
                LEFT JOIN public.cost_categories AS cc USING (category_id)
                LEFT JOIN public.suppliers AS s USING (supplier_id)
                WHERE bu.business_unit_code = ANY(%s)
                  AND fp.financial_year = %s
                  AND (ft.category_id IS NULL OR cc.category_code = ANY(%s))
                  AND (ft.supplier_id IS NULL OR s.supplier_code = ANY(%s))
                """,
                (
                    [row["business_unit_code"] for row in self.dataset["business_units"]],
                    2126,
                    [row["category_code"] for row in self.dataset["cost_categories"]],
                    [row["supplier_code"] for row in self.dataset["suppliers"]],
                ),
            )
            financial_transactions = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM public.contracts WHERE contract_number = ANY(%s)",
                ([row["contract_number"] for row in self.dataset["contracts"]],),
            )
            contracts = cursor.fetchone()[0]

        return {
            "business_units": business_units,
            "financial_periods": financial_periods,
            "cost_categories": cost_categories,
            "suppliers": suppliers,
            "financial_transactions": financial_transactions,
            "contracts": contracts,
        }

    def test_all_six_datasets_load_and_counts_match_sources(self) -> None:
        results = import_dataset(self.connection, self.dataset)
        self.assertEqual(tuple(results), IMPORT_ORDER)
        for entity in IMPORT_ORDER:
            self.assertEqual(results[entity].processed, len(self.dataset[entity]))
            self.assertEqual(results[entity].inserted, len(self.dataset[entity]))
            self.assertEqual(results[entity].existing, 0)

        self.assertEqual(
            self._test_record_counts(),
            {entity: len(self.dataset[entity]) for entity in IMPORT_ORDER},
        )

    def test_repeated_import_is_idempotent(self) -> None:
        first = import_dataset(self.connection, self.dataset)
        counts_after_first = self._test_record_counts()
        second = import_dataset(self.connection, self.dataset)
        counts_after_second = self._test_record_counts()
        for entity in IMPORT_ORDER:
            self.assertEqual(first[entity].inserted, len(self.dataset[entity]))
            self.assertEqual(second[entity].inserted, 0)
            self.assertEqual(second[entity].existing, len(self.dataset[entity]))
        self.assertEqual(counts_after_second, counts_after_first)
        self.assertEqual(
            counts_after_second,
            {entity: len(self.dataset[entity]) for entity in IMPORT_ORDER},
        )

    def test_conflicting_existing_record_is_rejected(self) -> None:
        import_dataset(self.connection, self.dataset)
        conflicting = copy.deepcopy(self.dataset)
        conflicting["financial_transactions"][0]["amount"] += Decimal("0.01")
        with self.assertRaises(ImportConflictError):
            import_dataset(self.connection, conflicting)

    def test_invalid_input_rolls_back_complete_import(self) -> None:
        invalid = copy.deepcopy(self.dataset)
        invalid["financial_transactions"][-1]["amount"] = Decimal("-1.00")
        with self.assertRaises(psycopg.errors.CheckViolation):
            import_dataset(self.connection, invalid)

        self.assertEqual(
            self._test_record_counts(),
            {entity: 0 for entity in IMPORT_ORDER},
        )


if __name__ == "__main__":
    unittest.main()
