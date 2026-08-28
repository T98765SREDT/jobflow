# JobFlow

[![CI](https://github.com/T98765SREDT/jobflow/actions/workflows/ci.yml/badge.svg)](https://github.com/T98765SREDT/jobflow/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](app.py)
[![Runtime](https://img.shields.io/badge/runtime-Python%20stdlib-306998)](app.py)
[![License](https://img.shields.io/badge/license-MIT-0b6e99)](LICENSE)

JobFlow is a local-first job application tracker built with Python, SQLite, and vanilla JavaScript. It keeps applications, follow-up dates, notes, saved views, and pipeline metrics in one workspace. The complete local app starts with one Python command and requires no package installation.

[Dashboard screenshot](docs/jobflow-dashboard.png) · [Browser demo source](static/index.html) · [Run locally](#run-the-complete-local-app) · [HTTP API](#http-api) · [Architecture](ARCHITECTURE.md) · [Security](SECURITY.md)

> **The Pages build and local app are different runtimes.** When GitHub Pages is enabled, it runs the browser interface with resettable synthetic data stored in `localStorage`; it cannot run the Python API or SQLite. Clone the repository to use the complete browser → API → validation → database path.

![JobFlow dashboard](docs/jobflow-dashboard.png)

## What you can do

- Create, edit, inspect, and delete applications with role, company, status, work mode, compensation, dates, source, URL, and notes.
- Search the workspace, combine status and work-mode filters, sort results, and move through paginated records.
- Switch between All, Active, Needs follow-up, Interviews, and Offers views; the current filters remain in the page URL.
- See overdue and due-soon actions, a prioritized needs-attention queue, the next five follow-ups, stage totals, and interview/offer response rate.
- Download a versioned JSON backup or spreadsheet-safe CSV export.
- Import a CSV from a spreadsheet with a preview, automatic header matching, manual column mapping, duplicate-row skipping, and one validated write.
- Export dated next actions as an iCalendar (`.ics`) file for a personal calendar.
- Restore a JSON backup in append or replace mode. Every record and its activity timeline are validated before the database changes, and replace mode runs in one transaction.
- Review an activity timeline for each application; status, follow-up-date, and note changes are recorded automatically, while interviews and custom updates can be added manually.
- Use a keyboard-friendly confirmation flow for destructive actions, focus the search box with `Cmd/Ctrl+K`, and keep screen-reader status updates attached to result changes.

## Browser demo

The static demo in [`static/`](static/) is ready to publish with GitHub Pages. It supports CRUD operations, search, saved views, filters, sorting, pagination, analytics, JSON backup/restore, CSV import/export, and calendar export without a server. A ready-to-try mapping fixture is included at [`examples/applications.csv`](examples/applications.csv). Until the Pages deployment is enabled, use the dashboard screenshot above or run the complete app locally.

The demo contains fictional applications only. Changes remain in the current browser until you use **Reset demo** or clear the site's local storage.

## Run the complete local app

Requirements: Python 3.9 or newer.

```bash
git clone https://github.com/T98765SREDT/jobflow.git
cd jobflow
python3 app.py --seed-demo
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The first run creates `data/jobflow.db`; `--seed-demo` adds six fictional applications only when the database is new. Omit that flag to start with an empty workspace.

Host, port, and database path can be changed from the command line:

```bash
python3 app.py --host 127.0.0.1 --port 8080 --db /tmp/jobflow-demo.db
```

The same values can be set with `JOBFLOW_HOST`, `JOBFLOW_PORT`, and `JOBFLOW_DB`.

## HTTP API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service health and version |
| `GET` | `/api/meta/options` | Valid status, work-mode, currency, and salary-period options |
| `GET` | `/api/applications` | Search, filter, sort, and paginate applications |
| `POST` | `/api/applications` | Validate and create an application |
| `GET` | `/api/applications/:id` | Retrieve one application |
| `PATCH` | `/api/applications/:id` | Validate and apply a partial update |
| `DELETE` | `/api/applications/:id` | Delete an application |
| `GET` | `/api/applications/:id/events` | Retrieve the application activity timeline |
| `POST` | `/api/applications/:id/events` | Add an interview, follow-up, note, or custom activity |
| `DELETE` | `/api/applications/:id/events/:event_id` | Remove one timeline entry |
| `GET` | `/api/analytics` | Pipeline totals, upcoming actions, and a prioritized attention queue |
| `GET` | `/api/export` | Versioned JSON backup |
| `POST` | `/api/import?mode=append\|replace` | Validate and restore a JSON backup atomically |

List parameters are `search`, `status`, `work_mode`, `sort`, `direction`, `view`, `page`, and `limit`.

```bash
curl "http://127.0.0.1:8000/api/applications?view=active&work_mode=Remote&page=1&limit=20"
curl "http://127.0.0.1:8000/api/export" > jobflow-backup.json
```

The response rate shown in the dashboard is the number of applications currently at Interview or Offer divided by all submitted applications; Wishlist records are excluded.

## Tests and verification

The repository has 27 Python tests plus 3 browser-side CSV checks across validation, SQLite migrations and transactions, CRUD behavior, saved views, pagination, analytics and attention prioritization, activity events, HTTP status handling, static-file delivery, CSV mapping, and backup/restore.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py jobflow tests
node --check static/app.js
node --check static/demo-data.js
node --check static/csv.js
node --test tests/csv.test.mjs
```

Tests create temporary databases and do not modify `data/jobflow.db`. GitHub Actions runs the suite on Python 3.9, 3.11, and 3.12 and checks all browser JavaScript files plus the CSV parser tests.

## Architecture

```text
Browser UI (HTML/CSS/JavaScript)
        │ fetch + JSON
        ▼
ThreadingHTTPServer + route handler
        │ normalized records
        ▼
Validation layer ── SQLite query layer
                         │ transaction
                         ▼
                     local database
```

- `app.py` reads runtime options and starts the server.
- `jobflow/server.py` owns routes, request parsing, JSON responses, static files, and HTTP status codes.
- `jobflow/validation.py` normalizes application data and returns field-level errors.
- `jobflow/database.py` owns the schema, migrations, parameterized SQL, transactions, seeding, filters, and analytics.
- `static/` contains the responsive interface, CSV mapping helpers, and the browser-only demo adapter.
- `tests/` checks the validation, persistence, and HTTP boundaries separately.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the request lifecycle, backup boundary, and design decisions.

## Reliability and security boundaries

- SQL values use parameters; selectable sort columns come from an allowlist.
- Static paths are resolved against the public directory and checked before files are served.
- JSON request bodies are limited to 1 MB, and invalid input returns structured field errors.
- SQLite uses schema versioning, WAL mode, a busy timeout, and commit-or-rollback transactions.
- Replace imports validate every application before existing records are deleted.
- The server binds to `127.0.0.1` by default.

JobFlow is a single-user local application. It has no authentication, authorization, encrypted storage, or public deployment configuration. Do not expose it directly to the internet or place confidential application data in the public browser demo. See [SECURITY.md](SECURITY.md) for the supported security scope.

## Repository map

```text
app.py                  local command-line entry point
jobflow/server.py       HTTP routing and JSON/static responses
jobflow/validation.py   normalization and field validation
jobflow/database.py     SQLite schema, queries, transactions, analytics
static/                 responsive UI, CSV mapping helpers, and GitHub Pages demo adapter
tests/                  validation, database, and API tests
examples/               import fixtures for the browser workflow
docs/                   screenshot guidance and published image
```

Contribution checks are in [CONTRIBUTING.md](CONTRIBUTING.md), screenshot guidance is in [docs/README.md](docs/README.md), and user-visible changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## Planned next steps

- Add an optional Kanban view without replacing the table workflow.
- Add a browser-level smoke test for the primary application workflow.

## License

[MIT](LICENSE)
