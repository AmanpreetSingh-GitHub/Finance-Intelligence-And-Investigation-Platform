# Finance API Architecture and Implementation

**Project:** `Finance_Intelligence_And_Investigation_Platform`
**Scope:** Finance API contracts and deterministic operating-margin calculations
**Status:** Application-layer foundation implemented; API, database integration, and security controls planned

## 1. Executive Technical Summary

I implemented a small application-layer foundation for exposing governed operating-margin results in a future Finance API. The business question is to return a business unit's revenue, operating expenses, operating profit, and operating margin for a requested financial year and quarter without losing monetary precision or accepting ambiguous input.

The implemented scope is deliberately narrow:

- Pydantic v2 request and response contracts for an operating-margin operation.
- Strict validation and stable API error names.
- A pure Python `Decimal` calculation function for operating profit and margin.
- Automated tests for those two components.

No HTTP endpoint, database repository, authentication, authorization, RLS policy, connection pool, or production observability component exists yet. PostgreSQL remains the financial source of record; this document does not treat a SQL reconciliation query as a production Finance API. The database schema, ACID import behavior, transaction integrity, and reconciliation evidence are documented separately in [Database_And_Financial_Data_Foundation.md](Database_And_Financial_Data_Foundation.md).

**Takeaway:** I established the validated application contracts and deterministic arithmetic first, while retaining a clear boundary between implemented code and planned service capabilities.

## 2. Architecture and Component Boundaries

The intended request path has distinct responsibilities:

```text
Planned HTTP endpoint
        |
Pydantic request validation              Implemented
        |
Planned PostgreSQL aggregate repository
        |
Pure Decimal metric calculation          Implemented
        |
Pydantic response serialization          Implemented
```

PostgreSQL is the governed financial source of record. It owns persisted transactions, accounting periods, business-unit ownership, currency attributes, and integrity rules. A future repository will query that data at the correct business-unit, financial-period, posting-status, and currency grain.

The Python application/service layer owns request validation, orchestration, deterministic arithmetic, response creation, and API-specific error handling. I kept the calculation independent of Pydantic and PostgreSQL so it can be tested without a database or HTTP framework.

Pydantic contracts own the external data shape. They prevent accidental input coercion and define how exact financial values appear in JSON. They do not authenticate a caller, verify business-unit entitlements, or query financial data.

MCP, LangGraph, and LLM integration are not implemented. If introduced later, they should consume approved, deterministic financial outputs and evidence rather than generate or calculate financial facts. They are not part of the current request path.

## 3. Finance API Contract Implementation

I selected Pydantic v2 for the API boundary because it provides declarative validation, immutable model support, explicit extra-field handling, and JSON serialization while keeping the contract definitions concise. The project bounds the dependency as `pydantic>=2.12,<3`.

### `OperatingMarginRequest`

`OperatingMarginRequest` identifies the requested business unit, financial year, and quarter:

```python
class OperatingMarginRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    business_unit_code: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
    ]
    financial_year: Annotated[int, Field(gt=0)]
    quarter: Annotated[int, Field(ge=1, le=4)]
```

I configured strict validation to reject type coercion. For example, a numeric string cannot become a financial year and a boolean cannot become an integer quarter or year. Whitespace is trimmed from the business-unit code before its non-empty rule is applied. The model is frozen after successful validation, which prevents a handler from mutating validated request values later in its processing path. `extra="forbid"` rejects fields outside the published contract.

The contract validates structural input only. It does not confirm that a business-unit code exists, that a caller is entitled to use it, or that the requested period has available posted data; those are repository and authorization responsibilities.

### `OperatingMarginResponse`

`OperatingMarginResponse` contains the business-unit and period identifiers, currency, revenue, operating expenses, operating profit, operating margin percentage, and a source identifier. Its current fields are:

```python
business_unit_code: str
financial_year: int
quarter: int
currency: str
revenue: Decimal
operating_expenses: Decimal
operating_profit: Decimal
operating_margin_percent: Decimal | None
source: str
```

The response model is also strict, frozen, and configured to forbid unknown fields. Financial values use `Decimal`, not `float`. Pydantic serializes `Decimal` values as JSON strings through `model_dump_json()`, preserving the exact decimal representation exposed by the application boundary. `operating_margin_percent` permits `None` for a zero-revenue result because no percentage can be calculated when the denominator is zero.

Pydantic validates response types and declared field constraints; it does not independently reconcile revenue, operating expenses, operating profit, or margin. `calculate_operating_metrics()` owns the arithmetic. Future integrated tests must verify that PostgreSQL aggregates, deterministic calculations, and the returned response carry consistent values.

The `source` field identifies the origin chosen by a future application flow; the contracts do not currently populate it from PostgreSQL.

### `FinanceApiErrorName`

I defined five stable error names:

| Name | Current role |
|---|---|
| `INVALID_INPUT` | Request input fails contract validation |
| `UNAUTHENTICATED` | Reserved for a future identity failure |
| `ACCESS_DENIED` | Reserved for a future entitlement failure |
| `DATA_NOT_AVAILABLE` | Reserved for an absent or unavailable financial result |
| `SERVICE_UNAVAILABLE` | Reserved for an unavailable dependency or service failure |

Only the names are implemented. HTTP status mapping and API error response bodies are planned.

**Trade-off:** Pydantic adds a runtime dependency compared with dataclasses, but the explicit strict validation and JSON behavior are useful at an API boundary. Dataclasses would require separate validation and serialization code, increasing the chance that behavior diverges across callers.

## 4. Deterministic Financial Calculations

I implemented `calculate_operating_metrics()` as a pure function with no Pydantic, PostgreSQL, MCP, LangGraph, or AWS dependency:

```python
def calculate_operating_metrics(
    revenue: Decimal,
    operating_expenses: Decimal,
) -> tuple[Decimal, Decimal | None]:
```

The function applies the following formulas:

```text
operating_profit = revenue - operating_expenses
operating_margin_percent = operating_profit / revenue * 100
```

It accepts only finite, non-negative `Decimal` inputs. Non-Decimal values, including floats and strings, negative values, `NaN`, and infinity raise `ValueError`. This avoids implicit binary floating-point precision loss and makes invalid calculations fail before they enter a response.

When revenue is zero, the function returns the calculated profit and `None` for the margin. It does not substitute `0%`, because the margin is undefined rather than zero. When expenses exceed revenue, negative operating profit and a negative margin are valid outputs.

I selected `ROUND_HALF_UP` and `Decimal("0.01")` as the initial reporting convention for percentage values. For example, a calculated `12.345%` rounds to `12.35%`. This policy is provisional: a future finance-control decision may specify a different rounding mode, currency-specific display rule, or reporting convention. Operating profit itself is not rounded by this function; it remains the exact Decimal difference of its inputs.

LLMs do not calculate finances in this architecture. A language model can later explain an already-approved calculation or help investigate evidence, but it must not become the source of revenue, expense, profit, or margin arithmetic. The calculation function and database aggregation provide reproducible behavior that can be directly tested and reconciled.

**Trade-off:** Keeping the calculation in Python makes the formula independently testable and reusable. PostgreSQL remains responsible for aggregating governed transaction facts. A future service must ensure both layers use consistent definitions for filters, posting status, currency, and report grain.

## 5. Planned Finance API Operation

The proposed operation is:

```text
GET /v1/finance/operating-margin
```

No endpoint exists yet. The proposed request mapping is:

```text
business_unit_code=EU&financial_year=2026&quarter=2
```

HTTP GET query parameters arrive as strings. Because `OperatingMarginRequest` deliberately uses strict integer fields, a future HTTP adapter or framework integration must explicitly validate and convert `financial_year` and `quarter` to integers before constructing the request model. I will not relax strict validation to accommodate transport input. This adapter is not implemented yet.

The proposed implementation sequence is:

1. Parse query parameters into `OperatingMarginRequest`.
2. Authenticate the caller and evaluate business-unit entitlement.
3. Query PostgreSQL for posted transactions at the requested business-unit, year, quarter, and currency grain.
4. Aggregate revenue and operating expenses in PostgreSQL.
5. Pass the aggregate `Decimal` values to `calculate_operating_metrics()`.
6. Construct `OperatingMarginResponse` with the requested identifiers, currency, calculations, and source.
7. Map known failures to the published error contract without disclosing restricted data.

The proposed successful response maps directly to the current response model. The future repository, not the Pydantic request model, must determine whether a code exists and whether data is available. The future endpoint must not aggregate across currencies or business units without an explicit, authorized reporting rule.

## 6. Security and Authorization Design

All controls in this section are designed, not implemented.

The future API needs authentication and token validation before it evaluates financial access. A validated user identity and associated claims should propagate through the service layer without relying on caller-provided business-unit values as authority. The request's `business_unit_code` is a query parameter, not an entitlement.

Business-unit entitlements and role-based access control (RBAC) should determine whether a user can read a requested unit. A regional finance user and a global CFO may have different permitted scopes. The service should fail closed: a missing, invalid, or unrecognized identity/entitlement must not produce financial data. `UNAUTHENTICATED` and `ACCESS_DENIED` are reserved error names for that future behavior.

The database connection should use a least-privilege role limited to the required read operations. PostgreSQL Row-Level Security (RLS) can provide defense in depth, but no RLS policies or user-to-database identity mappings exist today. A future implementation may set an authenticated principal and permitted scope as transaction-local database context, then use RLS policies that reference that context. Transaction-local context is not proof of identity: verified identity and entitlements must originate at the authenticated application boundary. Database permissions and RLS configuration must also prevent unauthorized bypass paths. This design requires careful connection-pool reset behavior so one request's context cannot leak to another request. These controls are not yet implemented.

RLS must not be the only control. Service-level authorization provides meaningful API errors and protects application flow; database enforcement protects against a bypassing query path. Both require explicit negative-access tests before they can be considered effective.

## 7. Reliability and Operational Design

The implemented models provide stable error names, but they do not yet provide an HTTP error envelope or status mapping. The following is a proposed mapping, not implemented behavior:

| Error name | Proposed HTTP status |
|---|---:|
| `INVALID_INPUT` | 400 |
| `UNAUTHENTICATED` | 401 |
| `ACCESS_DENIED` | 403 |
| `DATA_NOT_AVAILABLE` | 404 or 422, subject to product semantics |
| `SERVICE_UNAVAILABLE` | 503 |

The ambiguity for `DATA_NOT_AVAILABLE` is intentional. A missing business unit, missing period, or temporarily incomplete data source can have different product and information-disclosure implications. I have not selected a final mapping.

The future service needs bounded database and downstream timeouts, cancellation-aware request handling, and failure mapping that avoids leaking connection details or authorization boundaries. It also needs a connection-pooling strategy that safely resets transaction-local authorization context before reusing a connection.

Logging should record request correlation identifiers, validated non-sensitive request dimensions where authorized, selected error names, and dependency failures. It must not log secrets, raw tokens, or unnecessarily detailed financial payloads. Tracing should connect an API request to database access and calculation execution; metrics should cover request volume, errors by class, database latency, and dependency saturation. No numerical SLOs or latency budgets are approved or implemented. Any future budget should be based on expected consumer behavior, database measurements, and failure-recovery requirements.

## 8. Validation and Evidence

I validated the contract models with 16 focused tests. They cover valid requests, whitespace trimming, blank values, year and quarter bounds, boolean and string rejection, `None` rejection, extra-field rejection, frozen models, Decimal response values, float rejection, JSON Decimal serialization, nullable margin values, and stable error names.

I validated the calculation function with 10 focused tests. They cover the Europe Q1 and Q2 scenarios, zero-revenue behavior, loss scenarios, half-up percentage rounding, negative inputs, non-finite inputs, float and string rejection, and Decimal result types.

The project-level test result recorded for this milestone is **41 passed, 0 skipped**. This is a full-project result, not an API-only count. Exactly **26 tests** cover the Finance API contracts and calculation function; the remaining tests cover other project components. The 41-test aggregate was not used to claim endpoint, authorization, database-to-API, RLS, or resilience coverage.

Not tested yet:

- An HTTP endpoint and request routing.
- Authenticated and denied business-unit access.
- PostgreSQL aggregate retrieval into an API response.
- RLS behavior and transaction-local identity context.
- Connection pooling, timeouts, retries, and resilience behavior.
- Logging, tracing, metrics, and operational alerting.

**Takeaway:** The current evidence establishes the isolated Python contract and arithmetic behavior. It does not establish an integrated or production-ready Finance API.

## 9. Design Decisions and Trade-offs

| Decision | Implemented or proposed | Rationale | Main trade-off |
|---|---|---|---|
| Pydantic v2 contracts rather than dataclasses | Implemented | Strict validation, immutability, extra-field rejection, and JSON support at the boundary | Adds a dependency and runtime validation cost |
| `Decimal` rather than `float` | Implemented | Preserves exact decimal financial values and rejects accidental binary floats | Callers must construct Decimal values explicitly |
| Pure calculation function rather than LLM arithmetic | Implemented | Formula is deterministic, inspectable, and testable | Requires a separate evidence/LLM layer for narrative analysis |
| PostgreSQL aggregation with Python arithmetic | Proposed integration; both components exist separately | Database handles governed data selection and aggregation; Python owns reusable final arithmetic | Definitions and currency grain must be kept consistent |
| Separate contracts and calculations | Implemented | Each boundary can be validated without HTTP or database infrastructure | Service orchestration remains to be built |

## 10. Known Gaps and Next Steps

The following items remain planned work:

1. Implement the API route and HTTP framework integration.
2. Implement a read-only PostgreSQL repository/query for the approved operating-margin grain.
3. Add authentication, authorization, business-unit entitlements, and fail-closed behavior.
4. Implement and validate PostgreSQL RLS as defense in depth.
5. Add database-to-API integration tests and negative-access tests.
6. Add controlled connection pooling, timeouts, error handling, logging, tracing, metrics, and alerting.
7. Decide final reporting and rounding policy with finance stakeholders before treating the provisional `ROUND_HALF_UP` rule as a governed reporting standard.

## 11. Takeaway

I implemented a narrow, testable Finance API foundation: strict Pydantic contracts, exact Decimal response handling, stable error names, and deterministic operating-margin calculation. PostgreSQL remains the governed source of financial facts, while the future API will orchestrate validated requests, authorized aggregation, deterministic calculation, and response serialization. Security, database integration, endpoint delivery, and operations are explicitly planned capabilities rather than implied by the current code.
