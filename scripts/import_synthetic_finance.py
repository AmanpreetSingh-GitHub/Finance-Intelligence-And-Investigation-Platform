"""Import the six FIN-AI synthetic CSV datasets into PostgreSQL atomically."""

from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIRECTORY = PROJECT_ROOT / "data" / "synthetic"
IMPORT_LOCK_ID = 6012026

CSV_FIELDS = {
    "business_units": (
        "business_unit_code",
        "business_unit_name",
        "region",
        "base_currency",
        "is_active",
    ),
    "financial_periods": (
        "period_key",
        "financial_year",
        "quarter",
        "month_number",
        "period_start",
        "period_end",
        "is_closed",
    ),
    "cost_categories": (
        "category_code",
        "category_name",
        "is_operating_cost",
    ),
    "suppliers": (
        "supplier_code",
        "supplier_name",
        "country",
        "is_active",
    ),
    "financial_transactions": (
        "business_unit_code",
        "period_key",
        "category_code",
        "supplier_code",
        "posting_date",
        "transaction_type",
        "amount",
        "currency_code",
        "source_reference",
        "posting_status",
    ),
    "contracts": (
        "supplier_code",
        "business_unit_code",
        "contract_number",
        "effective_from",
        "effective_to",
        "document_uri",
        "access_classification",
        "document_version",
    ),
}

IMPORT_ORDER = (
    "business_units",
    "financial_periods",
    "cost_categories",
    "suppliers",
    "financial_transactions",
    "contracts",
)


class ImportConflictError(RuntimeError):
    """Raised when a natural key exists with values different from the CSV record."""


class DataValidationError(ValueError):
    """Raised when CSV structure or typed values are invalid."""


@dataclass(frozen=True)
class DatabaseConfig:
    """Hold connection settings without exposing the password in object output."""

    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "DatabaseConfig":
        """Build connection settings from environment variables, requiring credentials."""
        # Credentials have no source-code defaults to prevent accidental secret storage.
        missing = [
            name
            for name in ("FIN_AI_DB_USER", "FIN_AI_DB_PASSWORD")
            if not os.environ.get(name)
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(
            host=os.environ.get("FIN_AI_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("FIN_AI_DB_PORT", "55432")),
            dbname=os.environ.get("FIN_AI_DB_NAME", "fin_ai_test"),
            user=os.environ["FIN_AI_DB_USER"],
            password=os.environ["FIN_AI_DB_PASSWORD"],
        )

    def connection_parameters(self) -> dict[str, Any]:
        """Return keyword arguments accepted by Psycopg's connection function."""
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
        }


@dataclass(frozen=True)
class EntityImportResult:
    """Report how many source rows were processed, inserted, or reused."""

    processed: int
    inserted: int
    existing: int


def _read_csv(data_directory: Path, entity: str) -> list[dict[str, str]]:
    """Read one required CSV and reject missing, reordered, or unexpected columns."""
    path = data_directory / f"{entity}.csv"
    if not path.is_file():
        raise DataValidationError(f"Required CSV file not found: {path}")
    with path.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        expected_fields = CSV_FIELDS[entity]
        if tuple(reader.fieldnames or ()) != expected_fields:
            raise DataValidationError(
                f"Unexpected columns in {path.name}: expected {expected_fields}, "
                f"received {tuple(reader.fieldnames or ())}"
            )
        return [dict(row) for row in reader]


def _boolean(value: str, location: str) -> bool:
    """Convert the generator's lowercase Boolean format with location-aware errors."""
    if value == "true":
        return True
    if value == "false":
        return False
    raise DataValidationError(f"Expected true or false at {location}, received {value!r}")


def _integer(value: str, location: str) -> int:
    """Convert an integer field and identify its CSV location when invalid."""
    try:
        return int(value)
    except ValueError as error:
        raise DataValidationError(f"Expected integer at {location}, received {value!r}") from error


def _decimal(value: str, location: str) -> Decimal:
    """Convert money to a finite Decimal containing exactly two fractional digits."""
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise DataValidationError(f"Expected decimal at {location}, received {value!r}") from error
    if not amount.is_finite() or amount.as_tuple().exponent != -2:
        raise DataValidationError(f"Expected a finite two-decimal amount at {location}")
    return amount


def _date(value: str, location: str) -> date:
    """Convert an ISO calendar date and identify its CSV location when invalid."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise DataValidationError(f"Expected ISO date at {location}, received {value!r}") from error


def load_dataset(data_directory: Path = DEFAULT_DATA_DIRECTORY) -> dict[str, list[dict[str, Any]]]:
    """Read and type all CSV records before opening an import transaction."""
    # Validate every file and header before any database work can begin.
    raw = {entity: _read_csv(data_directory, entity) for entity in IMPORT_ORDER}
    dataset: dict[str, list[dict[str, Any]]] = {entity: [] for entity in IMPORT_ORDER}

    # Business-unit rows establish ownership and preserve active-state Booleans.
    for row_number, row in enumerate(raw["business_units"], start=2):
        dataset["business_units"].append(
            {
                **row,
                "is_active": _boolean(row["is_active"], f"business_units.csv:{row_number}"),
            }
        )

    # Period rows become typed calendar values before database constraints inspect them.
    for row_number, row in enumerate(raw["financial_periods"], start=2):
        year = _integer(row["financial_year"], f"financial_periods.csv:{row_number}")
        month = _integer(row["month_number"], f"financial_periods.csv:{row_number}")
        # period_key is the CSV natural key and must agree with its typed date dimensions.
        if row["period_key"] != f"{year:04d}-{month:02d}":
            raise DataValidationError(
                f"period_key does not match year and month at financial_periods.csv:{row_number}"
            )
        dataset["financial_periods"].append(
            {
                **row,
                "financial_year": year,
                "quarter": _integer(row["quarter"], f"financial_periods.csv:{row_number}"),
                "month_number": month,
                "period_start": _date(row["period_start"], f"financial_periods.csv:{row_number}"),
                "period_end": _date(row["period_end"], f"financial_periods.csv:{row_number}"),
                "is_closed": _boolean(row["is_closed"], f"financial_periods.csv:{row_number}"),
            }
        )

    # Cost-category rows preserve whether each category participates in operating costs.
    for row_number, row in enumerate(raw["cost_categories"], start=2):
        dataset["cost_categories"].append(
            {
                **row,
                "is_operating_cost": _boolean(
                    row["is_operating_cost"], f"cost_categories.csv:{row_number}"
                ),
            }
        )

    # Supplier rows provide the natural keys referenced by expenses and contracts.
    for row_number, row in enumerate(raw["suppliers"], start=2):
        dataset["suppliers"].append(
            {
                **row,
                "is_active": _boolean(row["is_active"], f"suppliers.csv:{row_number}"),
            }
        )

    # Transaction rows preserve Decimal money and typed posting dates.
    for row_number, row in enumerate(raw["financial_transactions"], start=2):
        # Blank relationship keys represent the nullable schema columns used by revenue.
        dataset["financial_transactions"].append(
            {
                **row,
                "category_code": row["category_code"] or None,
                "supplier_code": row["supplier_code"] or None,
                "posting_date": _date(
                    row["posting_date"], f"financial_transactions.csv:{row_number}"
                ),
                "amount": _decimal(row["amount"], f"financial_transactions.csv:{row_number}"),
            }
        )

    # Contract rows convert validity dates and map a blank document URI to SQL NULL.
    for row_number, row in enumerate(raw["contracts"], start=2):
        dataset["contracts"].append(
            {
                **row,
                "effective_from": _date(row["effective_from"], f"contracts.csv:{row_number}"),
                "effective_to": _date(row["effective_to"], f"contracts.csv:{row_number}"),
                "document_uri": row["document_uri"] or None,
            }
        )

    return dataset


def _assert_match(entity: str, natural_key: str, actual: tuple[Any, ...], expected: tuple[Any, ...]) -> None:
    """Reject reuse of a natural key when its stored business values differ."""
    if actual != expected:
        raise ImportConflictError(
            f"Conflicting {entity} record for natural key {natural_key!r}; import aborted"
        )


def import_dataset(
    connection: Connection[Any], dataset: dict[str, list[dict[str, Any]]]
) -> dict[str, EntityImportResult]:
    """Import a typed dataset in one transaction, reusing only exact existing records."""
    results: dict[str, EntityImportResult] = {}
    business_unit_ids: dict[str, int] = {}
    period_ids: dict[str, int] = {}
    category_ids: dict[str, int] = {}
    supplier_ids: dict[str, int] = {}

    # One transaction makes the six-table import atomic; any exception rolls it all back.
    with connection.transaction():
        with connection.cursor() as cursor:
            # Serialize this importer to prevent concurrent natural-key races.
            cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s)", (IMPORT_LOCK_ID,))

            # Business units are loaded first because transactions and contracts require ownership.
            inserted = 0
            for row in dataset["business_units"]:
                # The stable business code locates an existing row without assuming its BIGINT ID.
                cursor.execute(
                    """
                    SELECT business_unit_id, business_unit_name, region, base_currency, is_active
                    FROM public.business_units
                    WHERE business_unit_code = %s
                    """,
                    (row["business_unit_code"],),
                )
                existing = cursor.fetchone()
                expected = (
                    row["business_unit_name"],
                    row["region"],
                    row["base_currency"],
                    row["is_active"],
                )
                # Exact matches are idempotent; differing values are conflicts, not updates.
                if existing:
                    _assert_match("business unit", row["business_unit_code"], existing[1:], expected)
                    business_unit_ids[row["business_unit_code"]] = existing[0]
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.business_units
                            (business_unit_code, business_unit_name, region, base_currency, is_active)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING business_unit_id
                        """,
                        (row["business_unit_code"], *expected),
                    )
                    business_unit_ids[row["business_unit_code"]] = cursor.fetchone()[0]
                    inserted += 1
            results["business_units"] = EntityImportResult(
                len(dataset["business_units"]), inserted, len(dataset["business_units"]) - inserted
            )

            # Periods map the CSV year-month key to the identity used by each transaction.
            inserted = 0
            for row in dataset["financial_periods"]:
                cursor.execute(
                    """
                    SELECT period_id, quarter, period_start, period_end, is_closed
                    FROM public.financial_periods
                    WHERE financial_year = %s AND month_number = %s
                    """,
                    (row["financial_year"], row["month_number"]),
                )
                existing = cursor.fetchone()
                expected = (row["quarter"], row["period_start"], row["period_end"], row["is_closed"])
                if existing:
                    _assert_match("financial period", row["period_key"], existing[1:], expected)
                    period_ids[row["period_key"]] = existing[0]
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.financial_periods
                            (financial_year, quarter, month_number, period_start, period_end, is_closed)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING period_id
                        """,
                        (
                            row["financial_year"],
                            row["quarter"],
                            row["month_number"],
                            row["period_start"],
                            row["period_end"],
                            row["is_closed"],
                        ),
                    )
                    period_ids[row["period_key"]] = cursor.fetchone()[0]
                    inserted += 1
            results["financial_periods"] = EntityImportResult(
                len(dataset["financial_periods"]), inserted, len(dataset["financial_periods"]) - inserted
            )

            # Categories are shared reference data used to classify expense transactions.
            inserted = 0
            for row in dataset["cost_categories"]:
                cursor.execute(
                    """
                    SELECT category_id, category_name, is_operating_cost
                    FROM public.cost_categories
                    WHERE category_code = %s
                    """,
                    (row["category_code"],),
                )
                existing = cursor.fetchone()
                expected = (row["category_name"], row["is_operating_cost"])
                if existing:
                    _assert_match("cost category", row["category_code"], existing[1:], expected)
                    category_ids[row["category_code"]] = existing[0]
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.cost_categories
                            (category_code, category_name, is_operating_cost)
                        VALUES (%s, %s, %s)
                        RETURNING category_id
                        """,
                        (row["category_code"], *expected),
                    )
                    category_ids[row["category_code"]] = cursor.fetchone()[0]
                    inserted += 1
            results["cost_categories"] = EntityImportResult(
                len(dataset["cost_categories"]), inserted, len(dataset["cost_categories"]) - inserted
            )

            # Suppliers are loaded before the transactions and contracts that may reference them.
            inserted = 0
            for row in dataset["suppliers"]:
                cursor.execute(
                    """
                    SELECT supplier_id, supplier_name, country, is_active
                    FROM public.suppliers
                    WHERE supplier_code = %s
                    """,
                    (row["supplier_code"],),
                )
                existing = cursor.fetchone()
                expected = (row["supplier_name"], row["country"], row["is_active"])
                if existing:
                    _assert_match("supplier", row["supplier_code"], existing[1:], expected)
                    supplier_ids[row["supplier_code"]] = existing[0]
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.suppliers
                            (supplier_code, supplier_name, country, is_active)
                        VALUES (%s, %s, %s, %s)
                        RETURNING supplier_id
                        """,
                        (row["supplier_code"], *expected),
                    )
                    supplier_ids[row["supplier_code"]] = cursor.fetchone()[0]
                    inserted += 1
            results["suppliers"] = EntityImportResult(
                len(dataset["suppliers"]), inserted, len(dataset["suppliers"]) - inserted
            )

            # Financial facts resolve every CSV natural key to its database foreign key.
            inserted = 0
            for row in dataset["financial_transactions"]:
                try:
                    business_unit_id = business_unit_ids[row["business_unit_code"]]
                    period_id = period_ids[row["period_key"]]
                    category_id = (
                        category_ids[row["category_code"]] if row["category_code"] else None
                    )
                    supplier_id = supplier_ids[row["supplier_code"]] if row["supplier_code"] else None
                except KeyError as error:
                    # Missing reference data is rejected before PostgreSQL receives the row.
                    raise DataValidationError(
                        f"Unresolved transaction reference {error.args[0]!r}"
                    ) from error

                cursor.execute(
                    """
                    SELECT transaction_id, period_id, category_id, supplier_id, posting_date,
                           transaction_type, amount, currency_code, posting_status
                    FROM public.financial_transactions
                    WHERE business_unit_id = %s AND source_reference = %s
                    """,
                    (business_unit_id, row["source_reference"]),
                )
                existing = cursor.fetchone()
                expected = (
                    period_id,
                    category_id,
                    supplier_id,
                    row["posting_date"],
                    row["transaction_type"],
                    row["amount"],
                    row["currency_code"],
                    row["posting_status"],
                )
                natural_key = f"{row['business_unit_code']}:{row['source_reference']}"
                # Reuse an exact source record, but reject any changed financial value.
                if existing:
                    _assert_match("financial transaction", natural_key, existing[1:], expected)
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.financial_transactions
                            (business_unit_id, period_id, category_id, supplier_id, posting_date,
                             transaction_type, amount, currency_code, source_reference, posting_status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            business_unit_id,
                            period_id,
                            category_id,
                            supplier_id,
                            row["posting_date"],
                            row["transaction_type"],
                            row["amount"],
                            row["currency_code"],
                            row["source_reference"],
                            row["posting_status"],
                        ),
                    )
                    inserted += 1
            results["financial_transactions"] = EntityImportResult(
                len(dataset["financial_transactions"]),
                inserted,
                len(dataset["financial_transactions"]) - inserted,
            )

            # Contracts resolve supplier and owner keys before storing agreement metadata.
            inserted = 0
            for row in dataset["contracts"]:
                try:
                    supplier_id = supplier_ids[row["supplier_code"]]
                    business_unit_id = business_unit_ids[row["business_unit_code"]]
                except KeyError as error:
                    raise DataValidationError(f"Unresolved contract reference {error.args[0]!r}") from error

                cursor.execute(
                    """
                    SELECT contract_id, supplier_id, business_unit_id, effective_from,
                           effective_to, document_uri, access_classification, document_version
                    FROM public.contracts
                    WHERE contract_number = %s
                    """,
                    (row["contract_number"],),
                )
                existing = cursor.fetchone()
                expected = (
                    supplier_id,
                    business_unit_id,
                    row["effective_from"],
                    row["effective_to"],
                    row["document_uri"],
                    row["access_classification"],
                    row["document_version"],
                )
                # Contract numbers are idempotency keys and must not hide metadata changes.
                if existing:
                    _assert_match("contract", row["contract_number"], existing[1:], expected)
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.contracts
                            (supplier_id, business_unit_id, contract_number, effective_from,
                             effective_to, document_uri, access_classification, document_version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            supplier_id,
                            business_unit_id,
                            row["contract_number"],
                            row["effective_from"],
                            row["effective_to"],
                            row["document_uri"],
                            row["access_classification"],
                            row["document_version"],
                        ),
                    )
                    inserted += 1
            results["contracts"] = EntityImportResult(
                len(dataset["contracts"]), inserted, len(dataset["contracts"]) - inserted
            )

    return results


def main() -> None:
    """Load, validate, import, and summarize the configured synthetic dataset."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-directory", type=Path, default=DEFAULT_DATA_DIRECTORY)
    arguments = parser.parse_args()

    # Parse every source record before opening a database connection.
    dataset = load_dataset(arguments.data_directory)
    config = DatabaseConfig.from_environment()
    # The connection and import transaction close cleanly on success or failure.
    with psycopg.connect(**config.connection_parameters()) as connection:
        results = import_dataset(connection, dataset)

    # Report counts only; connection settings and credentials are never printed.
    for entity in IMPORT_ORDER:
        result = results[entity]
        print(
            f"{entity}: processed={result.processed} "
            f"inserted={result.inserted} existing={result.existing}"
        )


if __name__ == "__main__":
    main()
