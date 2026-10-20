"""Generate deterministic synthetic finance CSVs for Meridian Global Manufacturing."""

from __future__ import annotations

import argparse
import calendar
import csv
import random
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SEED = 20260106
TWOPLACES = Decimal("0.01")
OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "synthetic"

# CSVs use stable natural keys in place of database identity columns. During ingestion,
# *_code and period_key resolve to V001 BIGINT foreign keys; blank optional keys become NULL.
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

BUSINESS_UNITS = (
    ("EU", "Europe", "Europe", "EUR"),
    ("IN", "India", "India", "INR"),
    ("US", "United States", "North America", "USD"),
)

COST_CATEGORIES = (
    ("LOGISTICS", "Logistics", True),
    ("RAW_MATERIALS", "Raw Materials", True),
    ("OTHER_OPERATING", "Other Operating Costs", True),
)

SUPPLIERS = (
    ("SUP-ALPINE", "Alpine Freight Partners", "Germany"),
    ("SUP-NORTHSEA", "North Sea Logistics", "Netherlands"),
    ("SUP-METALWORKS", "Continental Metalworks", "Poland"),
    ("SUP-CHEMCORE", "ChemCore Materials", "Belgium"),
    ("SUP-BHARAT", "Bharat Industrial Supply", "India"),
    ("SUP-DECCAN", "Deccan Freight Systems", "India"),
    ("SUP-LIBERTY", "Liberty Manufacturing Supply", "United States"),
    ("SUP-PACIFIC", "Pacific Haulage Group", "United States"),
)

SUPPLIER_POOLS = {
    "EU": {
        "LOGISTICS": ("SUP-ALPINE", "SUP-NORTHSEA"),
        "RAW_MATERIALS": ("SUP-METALWORKS", "SUP-CHEMCORE"),
        "OTHER_OPERATING": ("SUP-ALPINE", "SUP-CHEMCORE"),
    },
    "IN": {
        "LOGISTICS": ("SUP-DECCAN", "SUP-BHARAT"),
        "RAW_MATERIALS": ("SUP-BHARAT", "SUP-CHEMCORE"),
        "OTHER_OPERATING": ("SUP-DECCAN", "SUP-BHARAT"),
    },
    "US": {
        "LOGISTICS": ("SUP-PACIFIC", "SUP-LIBERTY"),
        "RAW_MATERIALS": ("SUP-LIBERTY", "SUP-METALWORKS"),
        "OTHER_OPERATING": ("SUP-PACIFIC", "SUP-LIBERTY"),
    },
}

# Europe exactly implements the approved investigation targets. India and US values
# provide independent-currency comparison data without introducing exchange rates.
QUARTERLY_TARGETS = {
    "EU": {
        1: ("100000000.00", "10000000.00", "35000000.00", "37000000.00"),
        2: ("100000000.00", "12500000.00", "36500000.00", "37000000.00"),
    },
    "IN": {
        1: ("3000000000.00", "240000000.00", "1100000000.00", "1050000000.00"),
        2: ("3150000000.00", "265000000.00", "1160000000.00", "1080000000.00"),
    },
    "US": {
        1: ("70000000.00", "6200000.00", "24500000.00", "26000000.00"),
        2: ("73500000.00", "6800000.00", "25750000.00", "26750000.00"),
    },
}

TARGET_LABELS = (None, "LOGISTICS", "RAW_MATERIALS", "OTHER_OPERATING")


def allocate_exact(total: Decimal, count: int, rng: random.Random) -> list[Decimal]:
    """Allocate a monetary total with variation while preserving cent-level equality."""
    # Integer weights create variation without introducing binary floating-point values.
    weights = [Decimal(rng.randint(80, 120)) for _ in range(count)]
    weight_total = sum(weights, Decimal("0"))
    allocations: list[Decimal] = []
    allocated = Decimal("0")
    for weight in weights[:-1]:
        amount = (total * weight / weight_total).quantize(TWOPLACES, rounding=ROUND_DOWN)
        allocations.append(amount)
        allocated += amount
    # The final allocation absorbs rounding cents so the parts reconcile exactly.
    allocations.append((total - allocated).quantize(TWOPLACES))
    return allocations


def _business_units() -> list[dict[str, Any]]:
    return [
        {
            "business_unit_code": code,
            "business_unit_name": name,
            "region": region,
            "base_currency": currency,
            "is_active": True,
        }
        for code, name, region, currency in BUSINESS_UNITS
    ]


def _financial_periods() -> list[dict[str, Any]]:
    periods = []
    # Calendar boundaries are generated once and shared by every business unit.
    for month in range(1, 7):
        last_day = calendar.monthrange(2026, month)[1]
        periods.append(
            {
                "period_key": f"2026-{month:02d}",
                "financial_year": 2026,
                "quarter": ((month - 1) // 3) + 1,
                "month_number": month,
                "period_start": date(2026, month, 1),
                "period_end": date(2026, month, last_day),
                "is_closed": False,
            }
        )
    return periods


def _cost_categories() -> list[dict[str, Any]]:
    return [
        {
            "category_code": code,
            "category_name": name,
            "is_operating_cost": is_operating,
        }
        for code, name, is_operating in COST_CATEGORIES
    ]


def _suppliers() -> list[dict[str, Any]]:
    return [
        {
            "supplier_code": code,
            "supplier_name": name,
            "country": country,
            "is_active": True,
        }
        for code, name, country in SUPPLIERS
    ]


def _transactions(rng: random.Random) -> list[dict[str, Any]]:
    currencies = {code: currency for code, _, _, currency in BUSINESS_UNITS}
    transactions: list[dict[str, Any]] = []

    # Each target is first split across months, then into varied source transactions.
    for business_unit_code, quarter_targets in QUARTERLY_TARGETS.items():
        for quarter, target_values in quarter_targets.items():
            months = range(1 + (quarter - 1) * 3, 4 + (quarter - 1) * 3)
            for category_code, target_text in zip(TARGET_LABELS, target_values):
                monthly_amounts = allocate_exact(Decimal(target_text), 3, rng)
                for month, monthly_amount in zip(months, monthly_amounts):
                    transaction_amounts = allocate_exact(monthly_amount, 3, rng)
                    for sequence, amount in enumerate(transaction_amounts, start=1):
                        transaction_type = "REVENUE" if category_code is None else "EXPENSE"
                        supplier_code = ""
                        # Revenue has no cost category or supplier; expenses retain both links.
                        if category_code is not None:
                            supplier_code = rng.choice(
                                SUPPLIER_POOLS[business_unit_code][category_code]
                            )
                        posting_day = rng.randint(2, calendar.monthrange(2026, month)[1] - 1)
                        category_token = category_code or "REVENUE"
                        transactions.append(
                            {
                                "business_unit_code": business_unit_code,
                                "period_key": f"2026-{month:02d}",
                                "category_code": category_code or "",
                                "supplier_code": supplier_code,
                                "posting_date": date(2026, month, posting_day),
                                "transaction_type": transaction_type,
                                "amount": amount,
                                "currency_code": currencies[business_unit_code],
                                "source_reference": (
                                    f"SYN-{business_unit_code}-2026{month:02d}-"
                                    f"{category_token}-{sequence:02d}"
                                ),
                                "posting_status": "POSTED",
                            }
                        )
    return transactions


def _contracts() -> list[dict[str, Any]]:
    return [
        {
            "supplier_code": "SUP-NORTHSEA",
            "business_unit_code": "EU",
            "contract_number": "MGM-EU-LOG-2026-001",
            "effective_from": date(2026, 1, 1),
            "effective_to": date(2026, 12, 31),
            "document_uri": "",
            "access_classification": "CONFIDENTIAL",
            "document_version": "1.0",
        },
        {
            "supplier_code": "SUP-METALWORKS",
            "business_unit_code": "EU",
            "contract_number": "MGM-EU-MAT-2026-002",
            "effective_from": date(2026, 1, 1),
            "effective_to": date(2026, 12, 31),
            "document_uri": "",
            "access_classification": "CONFIDENTIAL",
            "document_version": "1.0",
        },
        {
            "supplier_code": "SUP-BHARAT",
            "business_unit_code": "IN",
            "contract_number": "MGM-IN-MAT-2026-001",
            "effective_from": date(2026, 1, 1),
            "effective_to": date(2026, 12, 31),
            "document_uri": "",
            "access_classification": "INTERNAL",
            "document_version": "1.0",
        },
        {
            "supplier_code": "SUP-LIBERTY",
            "business_unit_code": "US",
            "contract_number": "MGM-US-MAT-2026-001",
            "effective_from": date(2026, 1, 1),
            "effective_to": date(2026, 12, 31),
            "document_uri": "",
            "access_classification": "INTERNAL",
            "document_version": "1.0",
        },
    ]


def generate_dataset(seed: int = DEFAULT_SEED) -> dict[str, list[dict[str, Any]]]:
    """Return all synthetic records without writing files."""
    # A local seeded generator makes output reproducible without changing global randomness.
    rng = random.Random(seed)
    return {
        "business_units": _business_units(),
        "financial_periods": _financial_periods(),
        "cost_categories": _cost_categories(),
        "suppliers": _suppliers(),
        "financial_transactions": _transactions(rng),
        "contracts": _contracts(),
    }


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def write_dataset(
    dataset: dict[str, list[dict[str, Any]]], output_directory: Path = OUTPUT_DIRECTORY
) -> None:
    """Write records in stable order using schema-oriented CSV names."""
    output_directory.mkdir(parents=True, exist_ok=True)
    # Stable entity and field order makes repeated runs byte-for-byte comparable.
    for entity, fields in CSV_FIELDS.items():
        output_path = output_directory / f"{entity}.csv"
        with output_path.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(
                {field: _csv_value(record[field]) for field in fields}
                for record in dataset[entity]
            )


def summarize_by_currency(
    transactions: Iterable[dict[str, Any]],
) -> dict[tuple[str, int, str, str, str], Decimal]:
    """Aggregate only within BU, quarter, currency, type, and category boundaries."""
    totals: dict[tuple[str, int, str, str, str], Decimal] = defaultdict(Decimal)
    # Currency remains part of the key so totals never imply an unapproved FX conversion.
    for transaction in transactions:
        month = int(transaction["period_key"][-2:])
        quarter = ((month - 1) // 3) + 1
        key = (
            transaction["business_unit_code"],
            quarter,
            transaction["currency_code"],
            transaction["transaction_type"],
            transaction["category_code"],
        )
        totals[key] += transaction["amount"]
    return dict(totals)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIRECTORY)
    arguments = parser.parse_args()
    dataset = generate_dataset(arguments.seed)
    write_dataset(dataset, arguments.output)
    for entity, records in dataset.items():
        print(f"{entity}: {len(records)} records")


if __name__ == "__main__":
    main()
