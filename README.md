# JobFlow

[![CI](https://github.com/T98765SREDT/jobflow/actions/workflows/ci.yml/badge.svg)](https://github.com/T98765SREDT/jobflow/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](app.py)
[![Runtime](https://img.shields.io/badge/runtime-Python%20stdlib-306998)](app.py)
[![License](https://img.shields.io/badge/license-MIT-0b6e99)](LICENSE)

JobFlow is a local-first job application tracker built with Python, SQLite, and vanilla JavaScript. It keeps applications, follow-up dates, notes, and pipeline metrics in one responsive dashboard. The local app requires Python 3.9+ and no package installation.

[Interactive demo](https://t98765sredt.github.io/jobflow/static/?demo=1) · [Screenshot](docs/jobflow-dashboard.png) · [Quick start](#quick-start) · [API](#api-overview) · [Architecture](ARCHITECTURE.md)

![JobFlow dashboard](docs/jobflow-dashboard.png)

## Demo

The [GitHub Pages demo](https://t98765sredt.github.io/jobflow/static/?demo=1) supports the same add, edit, delete, search, filter, sort, and analytics interactions as the local interface. It uses an in-browser data adapter because GitHub Pages cannot run the Python API; changes reset when the page reloads.

Run the repository locally to exercise the full HTTP and SQLite path:

```text
Browser UI → JSON API → validation layer → SQLite
```

## What it does

- Tracks role, company, status, work mode, salary range, source, dates, URL, and notes.
- Supports create, edit, delete, text search, status/work-mode filters, and configurable sorting.
- Calculates totals, active applications, interview/offer response signal, stage distribution, and the next five follow-ups.
- Validates required fields, enum values, URL and ISO-date formats, salary ordering, field lengths, and request size at the server boundary.
- Persists data through parameterized SQLite queries, transaction boundaries, and indexes used by common pipeline queries.
- Seeds six synthetic applications on first run so the main states are visible without manual setup.

## Quick start

Clone the standalone repository and start the server:

```bash
git clone https://github.com/T98765SREDT/jobflow.git
cd jobflow
python3 app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The first run creates `data/jobflow.db`. Host, port, and database path are configurable:

```bash
python3 app.py --host 0.0.0.0 --port 8080 --db /tmp/jobflow-demo.db
```

## Tests and checks

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/demo-data.js
```

The `unittest` suite covers validation, CRUD operations, filters, analytics, static-file delivery, and API status behavior. It uses temporary databases and does not modify `data/jobflow.db`. GitHub Actions runs the Python suite on Python 3.9, 3.11, and 3.12 and checks both JavaScript files for syntax errors.

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service health and version |
| `GET` | `/api/meta/options` | Valid status, mode, and currency options |
| `GET` | `/api/applications` | List, search, filter, and sort applications |
| `POST` | `/api/applications` | Validate and create an application |
| `GET` | `/api/applications/:id` | Retrieve one application |
| `PATCH` | `/api/applications/:id` | Validate a partial update |
| `DELETE` | `/api/applications/:id` | Delete an application |
| `GET` | `/api/analytics` | Return pipeline totals and upcoming actions |

List query parameters: `search`, `status`, `work_mode`, `sort`, and `direction`.

Example:

```bash
curl "http://127.0.0.1:8000/api/applications?status=Interview&work_mode=Remote"
```

## Architecture

```text
Browser UI (HTML/CSS/JS)
        │ fetch + JSON
        ▼
ThreadingHTTPServer + route handler
        │ validated dictionaries
        ▼
Validation layer ── Database query layer
                         │ transactions
                         ▼
                       SQLite
```

- `app.py` parses runtime configuration and starts the service.
- `jobflow/server.py` handles routing, JSON responses, static files, and HTTP status codes.
- `jobflow/validation.py` centralizes normalization and user-facing field errors.
- `jobflow/database.py` owns schema initialization, parameterized SQL, transactions, seeding, and analytics.
- `static/` contains a framework-free responsive frontend with accessible labels, semantic tables, modal forms, and loading/empty/error states.
- `tests/` covers the validation, persistence, and HTTP boundaries independently.

See [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions and interview discussion notes.

### Project structure

```text
app.py                  command-line entry point
jobflow/server.py       HTTP routing, JSON responses, static files
jobflow/validation.py   payload normalization and field errors
jobflow/database.py     schema, queries, transactions, analytics
static/                 responsive browser UI and demo adapter
tests/                  validation, database, and API tests
```

## LinkedIn-ready project entry

**JobFlow — Full-Stack Remote Job Application Tracker**  
Python · SQLite · REST APIs · JavaScript · HTML/CSS · Testing

Built a local-first application tracker with a responsive dashboard, a Python JSON API, server-side validation, and SQLite persistence.

- Designed and implemented searchable CRUD workflows, multi-field filtering, sorting, five-stage pipeline tracking, and upcoming-action analytics.
- Built a transactional SQLite data layer and centralized validation for URLs, ISO dates, salary ranges, enum values, required fields, and malformed payloads.
- Created a responsive vanilla JavaScript interface and a `unittest` suite covering validation, CRUD, filters, analytics, static delivery, and API status behavior.

## Refreshing the screenshot

Run `python3 app.py` with a fresh database, open `http://127.0.0.1:8000`, and capture the seeded, unfiltered dashboard. Replace `docs/jobflow-dashboard.png` with the new image.

## Security and reliability notes

- SQL values are always passed as parameters; sort columns are selected from an allowlist.
- Static paths are resolved and checked to prevent directory traversal.
- Request bodies are limited to 1 MB and validation failures return structured HTTP 422 errors.
- Database mutations are committed atomically and rolled back on failure.
- The app intentionally binds to `127.0.0.1` by default. It is a portfolio/local tool, not a production authentication system.

See [SECURITY.md](SECURITY.md) for deployment scope and private reporting guidance. Contribution checks are documented in [CONTRIBUTING.md](CONTRIBUTING.md), and version notes are listed in [CHANGELOG.md](CHANGELOG.md).

## Future improvements

- User authentication and per-user workspaces
- CSV import/export and calendar reminders
- Kanban drag-and-drop view
- Production WSGI/ASGI adapter and deployment configuration
- End-to-end browser tests

## License

[MIT](LICENSE)
