\set ON_ERROR_STOP on
\pset pager off

BEGIN;
SET LOCAL search_path = public;

CREATE TEMP TABLE integration_test_results (
    test_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    details TEXT NOT NULL
) ON COMMIT DROP;

CREATE FUNCTION pg_temp.expect_error(
    test_name TEXT,
    statement TEXT,
    expected_sqlstate TEXT,
    expected_constraint TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    actual_sqlstate TEXT;
    actual_message TEXT;
    actual_constraint TEXT;
BEGIN
    BEGIN
        EXECUTE statement;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS
            actual_sqlstate = RETURNED_SQLSTATE,
            actual_message = MESSAGE_TEXT,
            actual_constraint = CONSTRAINT_NAME;

        IF actual_sqlstate = expected_sqlstate
           AND (expected_constraint IS NULL OR actual_constraint = expected_constraint) THEN
            INSERT INTO integration_test_results
                (test_name, status, details)
            VALUES
                (
                    test_name,
                    'PASS',
                    format(
                        'Rejected with SQLSTATE %s%s',
                        actual_sqlstate,
                        CASE
                            WHEN expected_constraint IS NULL THEN ''
                            ELSE format(' and constraint %s', actual_constraint)
                        END
                    )
                );
        ELSE
            INSERT INTO integration_test_results
                (test_name, status, details)
            VALUES
                (
                    test_name,
                    'FAIL',
                    format(
                        'Expected SQLSTATE %s%s, received %s with constraint %s: %s',
                        expected_sqlstate,
                        CASE
                            WHEN expected_constraint IS NULL THEN ''
                            ELSE format(' and constraint %s', expected_constraint)
                        END,
                        actual_sqlstate,
                        COALESCE(actual_constraint, '<none>'),
                        actual_message
                    )
                );
        END IF;
        RETURN;
    END;

    INSERT INTO integration_test_results (test_name, status, details)
    VALUES (test_name, 'FAIL', format('Expected SQLSTATE %s, but operation succeeded', expected_sqlstate));
END;
$$;

CREATE FUNCTION pg_temp.expect_count(
    test_name TEXT,
    statement TEXT,
    expected_count BIGINT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    actual_count BIGINT;
BEGIN
    EXECUTE statement INTO actual_count;
    INSERT INTO integration_test_results (test_name, status, details)
    VALUES (
        test_name,
        CASE WHEN actual_count = expected_count THEN 'PASS' ELSE 'FAIL' END,
        format('Expected %s, found %s', expected_count, actual_count)
    );
END;
$$;

SELECT pg_temp.expect_count(
    'Required tables',
    $query$
        SELECT count(*)
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND c.relname = ANY (ARRAY[
              'business_units', 'financial_periods', 'cost_categories',
              'suppliers', 'financial_transactions', 'contracts'
          ])
    $query$,
    6
);

SELECT pg_temp.expect_count(
    'Required trigger functions',
    $query$
        SELECT count(*)
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname = ANY (ARRAY[
              'validate_transaction_posting_period',
              'validate_period_transaction_dates'
          ])
    $query$,
    2
);

SELECT pg_temp.expect_count(
    'Required posting-period triggers',
    $query$
        SELECT count(*)
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND NOT t.tgisinternal
          AND t.tgname = ANY (ARRAY[
              'trg_financial_transactions_posting_period',
              'trg_financial_periods_transaction_dates'
          ])
    $query$,
    2
);

SELECT pg_temp.expect_count(
    'Required foreign-key indexes',
    $query$
        SELECT count(*)
        FROM pg_catalog.pg_indexes
        WHERE schemaname = 'public'
          AND indexname = ANY (ARRAY[
              'ix_financial_transactions_period_id',
              'ix_financial_transactions_category_id',
              'ix_financial_transactions_supplier_id',
              'ix_contracts_supplier_id',
              'ix_contracts_business_unit_id'
          ])
    $query$,
    5
);

INSERT INTO business_units (
    business_unit_code, business_unit_name, region, base_currency
)
VALUES ('IT6B', 'Integration Test Unit', 'Test Region', 'EUR')
RETURNING business_unit_id AS test_bu_id \gset

INSERT INTO integration_test_results VALUES
    ('Valid business unit', 'PASS', 'Accepted a valid business unit');

INSERT INTO financial_periods (
    financial_year, quarter, month_number, period_start, period_end
)
VALUES (2098, 1, 1, DATE '2098-01-01', DATE '2098-01-31')
RETURNING period_id AS test_period_id \gset

INSERT INTO integration_test_results VALUES
    ('Valid calendar period', 'PASS', 'Accepted January 2098 as Q1');

INSERT INTO cost_categories (
    category_code, category_name, is_operating_cost
)
VALUES ('IT6B_COST', 'Integration Test Cost', TRUE)
RETURNING category_id AS test_category_id \gset

INSERT INTO suppliers (
    supplier_code, supplier_name, country
)
VALUES ('IT6B_SUP', 'Integration Test Supplier', 'Test Country')
RETURNING supplier_id AS test_supplier_id \gset

SELECT pg_temp.expect_error(
    'Primary key enforcement',
    format(
        'INSERT INTO business_units '
        '(business_unit_id, business_unit_code, business_unit_name, region, base_currency) '
        'OVERRIDING SYSTEM VALUE VALUES (%s, ''IT6B_PK'', ''Duplicate PK'', ''Test'', ''EUR'')',
        :test_bu_id
    ),
    '23505'
);

SELECT pg_temp.expect_error(
    'Unique business-unit code',
    'INSERT INTO business_units '
    '(business_unit_code, business_unit_name, region, base_currency) '
    'VALUES (''IT6B'', ''Duplicate Code'', ''Test'', ''EUR'')',
    '23505'
);

SELECT pg_temp.expect_error(
    'Business-unit currency check',
    'INSERT INTO business_units '
    '(business_unit_code, business_unit_name, region, base_currency) '
    'VALUES (''IT6B_CUR'', ''Bad Currency'', ''Test'', ''eur'')',
    '23514'
);

SELECT pg_temp.expect_error(
    'Unique financial year and month',
    'INSERT INTO financial_periods '
    '(financial_year, quarter, month_number, period_start, period_end) '
    'VALUES (2098, 1, 1, DATE ''2098-01-01'', DATE ''2098-01-31'')',
    '23505'
);

SELECT pg_temp.expect_error(
    'Quarter-to-month mapping',
    'INSERT INTO financial_periods '
    '(financial_year, quarter, month_number, period_start, period_end) '
    'VALUES (2098, 2, 2, DATE ''2098-02-01'', DATE ''2098-02-28'')',
    '23514'
);

SELECT pg_temp.expect_error(
    'Calendar-month start boundary',
    'INSERT INTO financial_periods '
    '(financial_year, quarter, month_number, period_start, period_end) '
    'VALUES (2098, 1, 2, DATE ''2098-02-02'', DATE ''2098-02-28'')',
    '23514'
);

SELECT pg_temp.expect_error(
    'Calendar-month end boundary',
    'INSERT INTO financial_periods '
    '(financial_year, quarter, month_number, period_start, period_end) '
    'VALUES (2098, 1, 2, DATE ''2098-02-01'', DATE ''2098-02-27'')',
    '23514'
);

INSERT INTO financial_transactions (
    business_unit_id, period_id, category_id, supplier_id, posting_date,
    transaction_type, amount, currency_code, source_reference, posting_status
)
VALUES (
    :test_bu_id, :test_period_id, :test_category_id, :test_supplier_id,
    DATE '2098-01-15', 'EXPENSE', 125.50, 'EUR', 'IT6B-EXP-001', 'POSTED'
);

INSERT INTO financial_transactions (
    business_unit_id, period_id, category_id, supplier_id, posting_date,
    transaction_type, amount, currency_code, source_reference, posting_status
)
VALUES (
    :test_bu_id, :test_period_id, NULL, NULL,
    DATE '2098-01-20', 'REVENUE', 500.00, 'EUR', 'IT6B-REV-001', 'POSTED'
);

INSERT INTO integration_test_results VALUES
    ('Valid financial transactions', 'PASS', 'Accepted valid expense and revenue rows');

SELECT pg_temp.expect_error(
    'Financial-transaction foreign key',
    format(
        'INSERT INTO financial_transactions '
        '(business_unit_id, period_id, category_id, posting_date, transaction_type, '
        'amount, currency_code, source_reference, posting_status) '
        'VALUES (%s, %s, %s, DATE ''2098-01-10'', ''EXPENSE'', 1.00, ''EUR'', '
        '''IT6B-BAD-FK'', ''POSTED'')',
        :test_bu_id + 1000000000,
        :test_period_id,
        :test_category_id
    ),
    '23503'
);

SELECT pg_temp.expect_error(
    'Unique source reference within business unit',
    format(
        'INSERT INTO financial_transactions '
        '(business_unit_id, period_id, posting_date, transaction_type, amount, '
        'currency_code, source_reference, posting_status) '
        'VALUES (%s, %s, DATE ''2098-01-21'', ''REVENUE'', 1.00, ''EUR'', '
        '''IT6B-REV-001'', ''POSTED'')',
        :test_bu_id,
        :test_period_id
    ),
    '23505'
);

SELECT pg_temp.expect_error(
    'Positive transaction amount',
    format(
        'INSERT INTO financial_transactions '
        '(business_unit_id, period_id, posting_date, transaction_type, amount, '
        'currency_code, source_reference, posting_status) '
        'VALUES (%s, %s, DATE ''2098-01-10'', ''REVENUE'', 0.00, ''EUR'', '
        '''IT6B-ZERO'', ''POSTED'')',
        :test_bu_id,
        :test_period_id
    ),
    '23514'
);

SELECT pg_temp.expect_error(
    'Explicit transaction type',
    format(
        'INSERT INTO financial_transactions '
        '(business_unit_id, period_id, posting_date, transaction_type, amount, '
        'currency_code, source_reference, posting_status) '
        'VALUES (%s, %s, DATE ''2098-01-10'', ''TRANSFER'', 1.00, ''EUR'', '
        '''IT6B-TYPE'', ''POSTED'')',
        :test_bu_id,
        :test_period_id
    ),
    '23514'
);

SELECT pg_temp.expect_error(
    'Expense requires category',
    format(
        'INSERT INTO financial_transactions '
        '(business_unit_id, period_id, posting_date, transaction_type, amount, '
        'currency_code, source_reference, posting_status) '
        'VALUES (%s, %s, DATE ''2098-01-10'', ''EXPENSE'', 1.00, ''EUR'', '
        '''IT6B-NOCAT'', ''POSTED'')',
        :test_bu_id,
        :test_period_id
    ),
    '23514'
);

SELECT pg_temp.expect_error(
    'Revenue excludes category',
    format(
        'INSERT INTO financial_transactions '
        '(business_unit_id, period_id, category_id, posting_date, transaction_type, '
        'amount, currency_code, source_reference, posting_status) '
        'VALUES (%s, %s, %s, DATE ''2098-01-10'', ''REVENUE'', 1.00, ''EUR'', '
        '''IT6B-REVCAT'', ''POSTED'')',
        :test_bu_id,
        :test_period_id,
        :test_category_id
    ),
    '23514'
);

SELECT pg_temp.expect_error(
    'Transaction posting-date trigger',
    format(
        'INSERT INTO financial_transactions '
        '(business_unit_id, period_id, posting_date, transaction_type, amount, '
        'currency_code, source_reference, posting_status) '
        'VALUES (%s, %s, DATE ''2098-02-01'', ''REVENUE'', 1.00, ''EUR'', '
        '''IT6B-BAD-DATE'', ''POSTED'')',
        :test_bu_id,
        :test_period_id
    ),
    '23514',
    'ck_financial_transactions_posting_period'
);

SELECT pg_temp.expect_error(
    'Financial-period update trigger',
    format(
        'UPDATE financial_periods SET financial_year = 2098, quarter = 1, '
        'month_number = 2, period_start = DATE ''2098-02-01'', '
        'period_end = DATE ''2098-02-28'' WHERE period_id = %s',
        :test_period_id
    ),
    '23514',
    'ck_financial_transactions_posting_period'
);

INSERT INTO contracts (
    supplier_id, business_unit_id, contract_number, effective_from, effective_to,
    document_uri, access_classification, document_version
)
VALUES (
    :test_supplier_id, :test_bu_id, 'IT6B-CONTRACT-001',
    DATE '2098-01-01', DATE '2098-12-31', NULL, 'CONFIDENTIAL', '1.0'
);

INSERT INTO integration_test_results VALUES
    ('Valid contract', 'PASS', 'Accepted valid supplier contract metadata');

SELECT pg_temp.expect_error(
    'Contract date range',
    format(
        'INSERT INTO contracts '
        '(supplier_id, business_unit_id, contract_number, effective_from, effective_to, '
        'access_classification, document_version) '
        'VALUES (%s, %s, ''IT6B-CONTRACT-DATE'', DATE ''2098-02-01'', '
        'DATE ''2098-01-31'', ''INTERNAL'', ''1.0'')',
        :test_supplier_id,
        :test_bu_id
    ),
    '23514'
);

SELECT pg_temp.expect_error(
    'Contract access classification',
    format(
        'INSERT INTO contracts '
        '(supplier_id, business_unit_id, contract_number, effective_from, effective_to, '
        'access_classification, document_version) '
        'VALUES (%s, %s, ''IT6B-CONTRACT-CLASS'', DATE ''2098-01-01'', '
        'DATE ''2098-12-31'', ''PUBLIC'', ''1.0'')',
        :test_supplier_id,
        :test_bu_id
    ),
    '23514'
);

SELECT pg_temp.expect_error(
    'Contract supplier foreign key',
    format(
        'INSERT INTO contracts '
        '(supplier_id, business_unit_id, contract_number, effective_from, effective_to, '
        'access_classification, document_version) '
        'VALUES (%s, %s, ''IT6B-CONTRACT-FK'', DATE ''2098-01-01'', '
        'DATE ''2098-12-31'', ''INTERNAL'', ''1.0'')',
        :test_supplier_id + 1000000000,
        :test_bu_id
    ),
    '23503'
);

SELECT pg_temp.expect_error(
    'Unique contract number',
    format(
        'INSERT INTO contracts '
        '(supplier_id, business_unit_id, contract_number, effective_from, effective_to, '
        'access_classification, document_version) '
        'VALUES (%s, %s, ''IT6B-CONTRACT-001'', DATE ''2098-01-01'', '
        'DATE ''2098-12-31'', ''INTERNAL'', ''2.0'')',
        :test_supplier_id,
        :test_bu_id
    ),
    '23505'
);

\echo
\echo 'FIN-AI V001 integration-test results'
SELECT test_name, status, details
FROM integration_test_results
ORDER BY test_name;

SELECT
    count(*) FILTER (WHERE status = 'PASS') AS passed,
    count(*) FILTER (WHERE status = 'FAIL') AS failed,
    count(*) AS total
FROM integration_test_results;

SELECT NOT EXISTS (
    SELECT 1 FROM integration_test_results WHERE status = 'FAIL'
) AS all_tests_passed \gset

ROLLBACK;

\if :all_tests_passed
    \echo 'OVERALL: PASS (all test data rolled back)'
\else
    \echo 'OVERALL: FAIL (all test data rolled back)'
    DO $$
    BEGIN
        RAISE EXCEPTION 'FIN-AI V001 integration tests failed after rollback';
    END;
    $$;
\endif
