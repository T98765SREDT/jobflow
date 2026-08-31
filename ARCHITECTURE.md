# JobFlow architecture and interview notes

## Design choices

JobFlow uses a small layered architecture instead of a single script. The HTTP handler translates network input into ordinary Python data, the validation module enforces domain rules, and the database module owns parameterized SQL and transactions. This separation makes each boundary testable without a web framework.

The frontend is intentionally framework-free. A small state object, reusable API helper, escaped templates, event delegation, and focused render functions provide the interaction patterns needed by this product while keeping the runtime dependency-free. URL-synchronised filters, latest-request-wins refreshes, and a persistent error banner make stale or failed network responses visible instead of silently replacing the workspace.

SQLite is appropriate for this local-first portfolio tool because it gives durable, transactional storage without running another service. A production multi-user version would place the same API contract over a production server and database, then add authentication and authorization.

## Lifecycle compatibility model

Schema version 8 separates an application's active `stage` from its terminal
`outcome` and adds a positive `version` counter for future optimistic-conflict
handling. The older `status` field remains in responses for one compatibility
window and is a projection (`Closed` maps to the legacy `Rejected` value;
`Ready` maps to `Applied`). A v3 database is migrated in place in one SQLite
transaction: legacy `Rejected` rows become `Closed` with outcome `Rejected`,
and their existing update timestamp is retained as `closed_at`. No outcome is
invented for active records. New writes accept either `stage` or deprecated
`status`, reject contradictory pairs, and require both an outcome and a
timestamp when an application is closed.

Every lifecycle transition goes through one transactional service. It
increments `version`, writes an immutable `application_events` row with
`from_stage`, `to_stage`, `origin`, and a compact JSON payload, and supports an
optional idempotency `request_id`. A repeated request returns the original
event instead of creating a duplicate; an `expected_version` mismatch returns
the current record for explicit conflict review. System, import, and legacy
events are protected from deletion; only user-authored notes can be removed.

The v6 task model makes follow-up work first-class. `application_tasks` stores
independent preparation, interview, decision, follow-up, and custom tasks with
their own due dates, completion timestamps, and version counters. The legacy
`applications.next_action_date` field remains a compatibility projection of the
earliest open task. Existing records are migrated by creating one `follow_up`
task, so no reminder is lost. Closing an application completes all open tasks
in the same transaction, and a closed application cannot receive new tasks.
`GET /api/today` groups deterministic overdue, due-today, upcoming, waiting,
and missing-next-step work for a daily action view. Task writes use the same
version-conflict pattern as lifecycle writes; completing an already-completed
task is intentionally idempotent.

The v7 requirements model keeps qualification decisions explicit instead of
pretending to infer fit. `application_requirements` stores a criterion,
category, assessment (`met`, `partial`, `gap`, or `unknown`), supporting
evidence, a 1–5 weight, and a stable display position. The workspace summary
only uses known assessments in its denominator: a met item contributes its
full weight, a partial item contributes half, and an unknown item is reported
separately. Requirements are ordinary CRUD records, are deleted with their
application, and are included in versioned backups. The UI escapes all user
text before rendering; it never turns an unverified skill into an automatic
match claim. The v6-to-v7 migration is additive: existing applications and
tasks are untouched and begin with an empty requirements list.

The v8 materials model keeps the evidence used for a submission traceable
without turning JobFlow into a file-storage service. `application_artifacts`
stores a kind, human-readable label, optional http(s) URI, version label, and
notes; it never reads or uploads a local file. Creating a submission inserts a
`submission_packages` row and copies the selected material fields into
`submission_package_items`. Those copied snapshot fields are read-only, so
renaming a current resume cannot rewrite what was submitted previously.
Foreign keys cascade artifacts and packages with their application, while a
material referenced by a package is protected from deletion. The v7-to-v8
migration is additive and gives existing applications empty material and
submission collections.

## Historical funnel and source insights

`GET /api/insights` is intentionally separate from the current-state
`/api/analytics` endpoint. Current analytics answers “where are the records
now?”; historical insights answer “what happened to applications that were
submitted in this cohort?” The selected window (`30`, `90`, or `all`) is
anchored to the first timestamped `Applied` transition. Wishlist records are
not submissions, and a later Rejected/Closed event does not erase a previously
recorded Interview or Offer.

The database derives the first response time from the first later transition
to Interview, Offer, or Closed. It also reports “ever reached” counts for
Interview, Offer, and Closed/Accepted, plus median completed intervals between
distinct lifecycle stages. It never estimates an open interval or invents a
timestamp for a legacy row. Legacy or incomplete histories are surfaced in
`history_quality` (including the selected-cohort count and an all-records
`limited_total`) and the response includes denominator descriptions so a
consumer can explain each percentage. Source conversion groups the same
submitted cohort by the application's source, using that source's submitted
count as its denominator. The browser adapter mirrors these definitions so
GitHub Pages is a useful, honest product preview rather than a second set of
analytics rules.

## Request lifecycle

1. The browser sends JSON with `fetch`.
2. `JobFlowHandler` matches an allowlisted route and parses the request body.
3. `validate_application` normalizes values and returns all field errors together.
4. `Database` runs parameterized SQL inside a connection context that commits or rolls back.
5. The handler returns JSON with an accurate status such as 201, 204, 404, or 422.
6. The UI updates the application list, current analytics, historical insights, and Today feed concurrently; a successful write is reported separately from a later refresh failure. A failed historical window request keeps the last successful result visible.

Error responses use one envelope: `error.code`, `error.message`, `error.fields`,
`error.retryable`, and `error.request_id`. Route-specific conflict data such as
`current`, `conflicts`, or `application_id` remains at the response root so a
client can render a recovery action without parsing a human message. Every
response also carries `X-Request-ID`; clients may supply a safe correlation id
and the server logs it without exposing database paths or stack traces.

The browser request layer aborts a request after a bounded timeout and retries
only idempotent GET/HEAD reads, at most twice, for network and explicitly
retryable 408/429/502/503/504 failures. Writes are never retried implicitly.
Draft input remains in the form until a successful save or an explicit cancel.
Version conflicts offer Review latest, Keep my changes, and Cancel rather than
silently overwriting another tab.

The application workspace endpoint reads the application row, open and
completed tasks, requirements, materials, submission snapshots, and activity events through one database connection. This
prevents the details drawer from showing a task list from one moment and a
timeline from another moment. The table and Board views still use the same
filter/query state; Board cards only add a visual grouping and an explicit
stage-transition control.

## Backup and restore boundary

`GET /api/export` returns a versioned JSON document, including each application's
events, tasks, requirements, material metadata, and submission snapshots. `POST /api/import/preview` validates the application rows without writing and
returns deterministic duplicate candidates. A duplicate fingerprint prefers a
canonical HTTP(S) job URL (host/path normalization, known tracking-parameter
removal, and fragment removal); when both URLs are missing it falls back to
Unicode-normalized company, role, and location. Identity-bearing query
parameters are retained, and credentials are never part of a fingerprint.

`POST /api/import` strips record metadata, validates every application, event,
task, requirement, material, and submission reference against the current
domain contract, and then inserts the accepted batch in one transaction. In
append mode every duplicate must carry an explicit `duplicate_decisions` entry:
`skip`, `separate`/`create`, or `merge` with a positive existing ID and a list
of non-empty fields. Merge changes are limited to ordinary application fields,
show a before/after diff in the UI, increment the record version, and write an
import-origin timeline event. No data is changed when a decision is missing or
validation fails. Older backups without a `tasks` array generate a compatibility
follow-up from `next_action_date`; replace mode deletes existing rows only
inside that same transaction. The browser demo mirrors the preview, decision,
and append/replace behavior with versioned local-storage data. Imported
submission items are remapped to newly inserted material IDs while their
snapshot values are preserved.

## Verification evidence

The cross-layer browser workflow is kept in
[`tests/e2e/jobflow.spec.mjs`](tests/e2e/jobflow.spec.mjs) and is documented in
[`docs/e2e.md`](docs/e2e.md). It starts from a disposable empty workspace,
persists a record through the main lifecycle, and checks recovery paths without
mocking the API. The read benchmark in
[`scripts/benchmark.py`](scripts/benchmark.py) creates a temporary 5,000-record
fixture and reports observations for list, Today, workspace, and Insights
queries; it deliberately does not assert a threshold. CI runs Python 3.9,
3.11, and 3.12 checks before the browser matrix, while Pages deployment waits
for its own verification job.

## CSV import boundary

CSV import is deliberately a browser-side workflow: `static/csv.js` parses quoted fields and multiline records without a runtime dependency, infers common English and Chinese headers, and lets the user correct the mapping before any write occurs. Amounts, status labels, and work modes are normalized through the same browser validation rules used by the demo adapter. Rows missing required fields are reported before import, duplicate candidates are checked against both the workspace and the current file, and the user must choose an action for each one. The accepted batch is sent through the same atomic JSON import boundary; a downloadable row report preserves recovery when a source file needs correction. Spreadsheet exports prefix formula-like cells so opening a CSV cannot turn a job title or note into an unintended formula.

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
