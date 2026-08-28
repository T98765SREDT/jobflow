# JobFlow architecture and interview notes

## Design choices

JobFlow uses a small layered architecture instead of a single script. The HTTP handler translates network input into ordinary Python data, the validation module enforces domain rules, and the database module owns parameterized SQL and transactions. This separation makes each boundary testable without a web framework.

The frontend is intentionally framework-free. A small state object, reusable API helper, escaped templates, event delegation, and focused render functions provide the interaction patterns needed by this product while keeping the runtime dependency-free. URL-synchronised filters, latest-request-wins refreshes, and a persistent error banner make stale or failed network responses visible instead of silently replacing the workspace.

SQLite is appropriate for this local-first portfolio tool because it gives durable, transactional storage without running another service. A production multi-user version would place the same API contract over a production server and database, then add authentication and authorization.

## Request lifecycle

1. The browser sends JSON with `fetch`.
2. `JobFlowHandler` matches an allowlisted route and parses the request body.
3. `validate_application` normalizes values and returns all field errors together.
4. `Database` runs parameterized SQL inside a connection context that commits or rolls back.
5. The handler returns JSON with an accurate status such as 201, 204, 404, or 422.
6. The UI updates the application list and analytics concurrently; a successful write is reported separately from a later refresh failure.

## Backup and restore boundary

`GET /api/export` returns a versioned JSON document. `POST /api/import` strips record metadata, validates every application against the current domain contract, and then inserts the batch in one transaction. Replace mode deletes existing rows only inside that same transaction, so a malformed record cannot leave a partially restored workspace. The browser demo mirrors this contract with a versioned local-storage record and the same append/replace choice.

## Five likely interview questions

### 1. Why did you avoid Flask or React?

The goal was to demonstrate the underlying web fundamentals: HTTP routing, status codes, JSON contracts, SQL transactions, DOM state, validation, and responsive CSS. A framework would reduce boilerplate in a larger product, but the dependency-free version is easy to run and makes those fundamentals visible.

### 2. How do you prevent SQL injection?

Every user value is passed through SQLite parameter placeholders. The only dynamic SQL identifiers are sort columns, and the database layer selects those from a fixed allowlist before building the query.

### 3. Why validate on the server if the form already has browser validation?

Browser validation improves usability but can be bypassed by any API client. The server owns the actual contract and checks required fields, known enum values, maximum lengths, ISO dates, complete URLs, non-negative salaries, and salary ordering.

### 4. How would you scale this for multiple users?

I would add authentication and authorization, user ownership on every record, pagination, a production WSGI/ASGI API layer, PostgreSQL, database migrations, rate limiting, and deployment observability. The existing browser/API/data boundaries allow those parts to change independently.

### 5. How does the UI remain useful when a refresh fails?

The last successfully loaded records remain visible, while a persistent alert explains whether the write succeeded and the refresh failed. A retry action re-issues both reads. Request serials prevent a slower, older search response from overwriting a newer one.

### 6. What was the most important reliability decision?

Keeping validation and persistence outside the HTTP handler. It avoids duplicating rules, makes errors consistent, permits fast unit tests, and ensures database writes use a commit-or-rollback transaction boundary.
