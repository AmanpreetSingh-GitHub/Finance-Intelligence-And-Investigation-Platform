-- FIN-AI initial finance schema migration (PostgreSQL 17).
-- Establishes six core tables, integrity rules, triggers, and indexes for multi-BU investigations.
BEGIN;
SET LOCAL search_path = public;

-- Defines organizational ownership for financial facts, contracts, and future RLS scope.
CREATE TABLE business_units (
    business_unit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_unit_code VARCHAR(10) NOT NULL UNIQUE,
    business_unit_name VARCHAR(150) NOT NULL,
    region VARCHAR(100) NOT NULL,
    base_currency CHAR(3) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    -- Prevents unusable external identifiers for business-unit integrations.
    CONSTRAINT ck_business_units_code_nonempty
        CHECK (btrim(business_unit_code) <> ''),
    -- Ensures every business unit has a display name for reporting.
    CONSTRAINT ck_business_units_name_nonempty
        CHECK (btrim(business_unit_name) <> ''),
    -- Ensures regional investigation results have a defined geographic grouping.
    CONSTRAINT ck_business_units_region_nonempty
        CHECK (btrim(region) <> ''),
    -- Restricts base currencies to normalized three-letter uppercase codes.
    CONSTRAINT ck_business_units_base_currency_format
        CHECK (base_currency ~ '^[A-Z]{3}$')
);

-- Defines canonical monthly accounting periods for validation and quarterly reporting.
CREATE TABLE financial_periods (
    period_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    financial_year INTEGER NOT NULL,
    quarter SMALLINT NOT NULL,
    month_number SMALLINT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    is_closed BOOLEAN NOT NULL DEFAULT FALSE,
    -- Allows only one canonical accounting period for each calendar month.
    CONSTRAINT uq_financial_periods_year_month
        UNIQUE (financial_year, month_number),
    -- Excludes non-calendar year values from period construction.
    CONSTRAINT ck_financial_periods_year_positive
        CHECK (financial_year > 0),
    -- Limits reporting quarters to the supported calendar range.
    CONSTRAINT ck_financial_periods_quarter
        CHECK (quarter BETWEEN 1 AND 4),
    -- Limits accounting periods to valid calendar months.
    CONSTRAINT ck_financial_periods_month
        CHECK (month_number BETWEEN 1 AND 12),
    -- Keeps the stored quarter consistent with its calendar month.
    CONSTRAINT ck_financial_periods_quarter_mapping
        CHECK (quarter = ((month_number - 1) / 3) + 1),
    -- Requires each accounting period to start on the first day of its month.
    CONSTRAINT ck_financial_periods_start
        CHECK (period_start = make_date(financial_year, month_number, 1)),
    -- Requires each accounting period to end on the last day of its month.
    CONSTRAINT ck_financial_periods_end
        CHECK (
            period_end =
                (make_date(financial_year, month_number, 1)
                 + INTERVAL '1 month' - INTERVAL '1 day')::DATE
        )
);

-- Classifies expenses for cost-driver and operating-margin analysis.
CREATE TABLE cost_categories (
    category_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_code VARCHAR(30) NOT NULL UNIQUE,
    category_name VARCHAR(100) NOT NULL,
    is_operating_cost BOOLEAN NOT NULL,
    -- Prevents unusable category identifiers in financial classifications.
    CONSTRAINT ck_cost_categories_code_nonempty
        CHECK (btrim(category_code) <> ''),
    -- Ensures categorized costs have a meaningful reporting label.
    CONSTRAINT ck_cost_categories_name_nonempty
        CHECK (btrim(category_name) <> '')
);

-- Identifies supplier counterparties referenced by transactions and contracts.
CREATE TABLE suppliers (
    supplier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_code VARCHAR(30) NOT NULL UNIQUE,
    supplier_name VARCHAR(150) NOT NULL,
    country VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    -- Prevents unusable supplier identifiers in source data.
    CONSTRAINT ck_suppliers_code_nonempty
        CHECK (btrim(supplier_code) <> ''),
    -- Ensures supplier evidence has a meaningful counterparty name.
    CONSTRAINT ck_suppliers_name_nonempty
        CHECK (btrim(supplier_name) <> ''),
    -- Ensures supplier analysis retains a geographic attribution.
    CONSTRAINT ck_suppliers_country_nonempty
        CHECK (btrim(country) <> '')
);

-- Stores fixed-precision revenue and expense facts by business unit and accounting period.
CREATE TABLE financial_transactions (
    transaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_unit_id BIGINT NOT NULL,
    period_id BIGINT NOT NULL,
    category_id BIGINT,
    supplier_id BIGINT,
    posting_date DATE NOT NULL,
    transaction_type VARCHAR(10) NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    source_reference VARCHAR(100) NOT NULL,
    posting_status VARCHAR(10) NOT NULL,
    -- Assigns every transaction to its owning business unit.
    CONSTRAINT fk_financial_transactions_business_unit
        FOREIGN KEY (business_unit_id)
        REFERENCES business_units (business_unit_id),
    -- Assigns every transaction to its reporting and posting period.
    CONSTRAINT fk_financial_transactions_period
        FOREIGN KEY (period_id)
        REFERENCES financial_periods (period_id),
    -- Links expenses to an optional shared cost classification.
    CONSTRAINT fk_financial_transactions_category
        FOREIGN KEY (category_id)
        REFERENCES cost_categories (category_id),
    -- Links a transaction to its supplier when counterparty evidence exists.
    CONSTRAINT fk_financial_transactions_supplier
        FOREIGN KEY (supplier_id)
        REFERENCES suppliers (supplier_id),
    -- Prevents duplicate upstream records within the same business unit.
    CONSTRAINT uq_financial_transactions_bu_source
        UNIQUE (business_unit_id, source_reference),
    -- Restricts financial direction to the supported revenue and expense model.
    CONSTRAINT ck_financial_transactions_type
        CHECK (transaction_type IN ('REVENUE', 'EXPENSE')),
    -- Keeps amounts positive because transaction type conveys financial direction.
    CONSTRAINT ck_financial_transactions_amount_positive
        CHECK (amount > 0),
    -- Restricts transaction currencies to normalized three-letter uppercase codes.
    CONSTRAINT ck_financial_transactions_currency_format
        CHECK (currency_code ~ '^[A-Z]{3}$'),
    -- Ensures each transaction retains a usable upstream traceability reference.
    CONSTRAINT ck_financial_transactions_source_nonempty
        CHECK (btrim(source_reference) <> ''),
    -- Limits workflow state to the supported accounting posting lifecycle.
    CONSTRAINT ck_financial_transactions_posting_status
        CHECK (posting_status IN ('DRAFT', 'POSTED', 'REVERSED')),
    -- Requires expense categorization while excluding revenue from the cost taxonomy.
    CONSTRAINT ck_financial_transactions_category_by_type
        CHECK (
            (transaction_type = 'EXPENSE' AND category_id IS NOT NULL)
            OR (transaction_type = 'REVENUE' AND category_id IS NULL)
        )
);

-- Records supplier agreements used as evidence in business-unit investigations.
CREATE TABLE contracts (
    contract_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id BIGINT NOT NULL,
    business_unit_id BIGINT NOT NULL,
    contract_number VARCHAR(100) NOT NULL UNIQUE,
    effective_from DATE NOT NULL,
    effective_to DATE NOT NULL,
    document_uri TEXT,
    access_classification VARCHAR(20) NOT NULL,
    document_version VARCHAR(30) NOT NULL,
    -- Requires every contract to identify its supplier counterparty.
    CONSTRAINT fk_contracts_supplier
        FOREIGN KEY (supplier_id)
        REFERENCES suppliers (supplier_id),
    -- Assigns each contract to the business unit permitted to use it.
    CONSTRAINT fk_contracts_business_unit
        FOREIGN KEY (business_unit_id)
        REFERENCES business_units (business_unit_id),
    -- Ensures contracts retain a usable business identifier.
    CONSTRAINT ck_contracts_number_nonempty
        CHECK (btrim(contract_number) <> ''),
    -- Prevents a contract from expiring before it becomes effective.
    CONSTRAINT ck_contracts_date_range
        CHECK (effective_to >= effective_from),
    -- Rejects blank document locations while allowing contracts without a document.
    CONSTRAINT ck_contracts_document_uri_nonempty
        CHECK (document_uri IS NULL OR btrim(document_uri) <> ''),
    -- Limits contract visibility labels to the approved governance classifications.
    CONSTRAINT ck_contracts_access_classification
        CHECK (access_classification IN ('INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')),
    -- Ensures referenced contract evidence has an identifiable version.
    CONSTRAINT ck_contracts_document_version_nonempty
        CHECK (btrim(document_version) <> '')
);

-- Validates posting dates against periods because cross-table rules cannot use CHECK constraints.
CREATE FUNCTION validate_transaction_posting_period()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    period_start_date DATE;
    period_end_date DATE;
BEGIN
    -- Reads the referenced boundaries and locks the period against concurrent changes.
    SELECT fp.period_start, fp.period_end
     INTO period_start_date, period_end_date
      FROM public.financial_periods AS fp
     WHERE fp.period_id = NEW.period_id
       FOR SHARE;

    -- Defers a missing period to the foreign key for its standard integrity error.
    IF NOT FOUND THEN
        -- Preserves normal trigger flow so foreign-key validation can complete.
        RETURN NEW;
    END IF;

    -- Detects a posting date outside the selected monthly accounting period.
    IF NEW.posting_date < period_start_date
       OR NEW.posting_date > period_end_date THEN
        -- Rejects the write with a check-violation error meaningful to database clients.
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'ck_financial_transactions_posting_period',
            MESSAGE = format(
                'posting_date %s is outside financial period %s (%s through %s)',
                NEW.posting_date,
                NEW.period_id,
                period_start_date,
                period_end_date
            );
    END IF;

    -- Allows the validated transaction row to proceed.
    RETURN NEW;
END;
$$;

-- Fires before transaction inserts or date/period updates to protect period membership.
CREATE TRIGGER trg_financial_transactions_posting_period
BEFORE INSERT OR UPDATE OF period_id, posting_date
ON financial_transactions
FOR EACH ROW
EXECUTE FUNCTION validate_transaction_posting_period();

-- Protects existing transaction dates when an accounting period is changed.
CREATE FUNCTION validate_period_transaction_dates()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    -- Finds whether proposed period boundaries would exclude any assigned transaction.
    IF EXISTS (
        SELECT 1
          FROM public.financial_transactions AS ft
         WHERE ft.period_id = NEW.period_id
           AND ft.posting_date NOT BETWEEN NEW.period_start AND NEW.period_end
    ) THEN
        -- Rejects a period change that would invalidate existing financial facts.
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'ck_financial_transactions_posting_period',
            MESSAGE = format(
                'financial period %s would exclude an existing transaction posting date',
                NEW.period_id
            );
    END IF;

    -- Allows the validated period row to proceed.
    RETURN NEW;
END;
$$;

-- Fires before period-boundary updates to preserve existing transaction validity.
CREATE TRIGGER trg_financial_periods_transaction_dates
BEFORE UPDATE OF financial_year, quarter, month_number, period_start, period_end
ON financial_periods
FOR EACH ROW
EXECUTE FUNCTION validate_period_transaction_dates();

-- Date triggers do not enforce period close, authorization, reconciliation, FX, audit, or RLS controls.

-- Foreign-key indexes support investigation joins and parent checks without duplicating BU coverage.
CREATE INDEX ix_financial_transactions_period_id
    ON financial_transactions (period_id);

CREATE INDEX ix_financial_transactions_category_id
    ON financial_transactions (category_id);

CREATE INDEX ix_financial_transactions_supplier_id
    ON financial_transactions (supplier_id);

CREATE INDEX ix_contracts_supplier_id
    ON contracts (supplier_id);

CREATE INDEX ix_contracts_business_unit_id
    ON contracts (business_unit_id);

COMMIT;
