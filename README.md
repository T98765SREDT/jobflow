# JobFlow

[![CI](https://github.com/T98765SREDT/jobflow/actions/workflows/ci.yml/badge.svg)](https://github.com/T98765SREDT/jobflow/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](app.py)
[![Runtime](https://img.shields.io/badge/runtime-Python%20stdlib-306998)](app.py)
[![License](https://img.shields.io/badge/license-MIT-0b6e99)](LICENSE)

JobFlow is a polished, full-stack application tracker for remote job seekers. It combines a responsive dashboard, a REST-style JSON API, strong server-side validation, SQLite persistence, live search and filtering, and pipeline analytics—using only the Python standard library and browser-native HTML, CSS, and JavaScript.

[Try the interactive UI preview](https://t98765sredt.github.io/jobflow/static/?demo=1) · [View the dashboard](docs/jobflow-dashboard.png) · [Read the architecture](ARCHITECTURE.md) · [Review the API](#api-overview) · [Run the test suite](#run-tests)

> **Portfolio preview:** the interactive preview runs entirely in the browser so it can be explored without an account. Its sample data resets on reload; the complete application uses the Python HTTP server and SQLite database described below.

![JobFlow dashboard](docs/jobflow-dashboard.png)

## Why I built it

Remote opportunities are easy to lose across bookmarks and spreadsheets. JobFlow keeps the role, source, stage, salary, notes, and next action in one focused workspace so that every application has a clear follow-up.

## Features

- Create, edit, delete, search, filter, and sort job applications from a responsive UI.
- Track five pipeline stages, work mode, dates, salary range, source, job URL, and notes.
- View real-time totals, active pipeline, response signal, stage distribution, and upcoming actions.
- Enforce field-level validation for required values, dates, URLs, enum values, salary ranges, and payload size.
- Persist records with transactional SQLite queries and indexes for common pipeline queries.
- Serve the frontend and REST-style API from a threaded, dependency-free Python HTTP server.
- Start with six realistic demo applications so the dashboard is immediately screenshot-ready.
- Verify validation, CRUD, filtering, analytics, static delivery, and HTTP behavior with 10 automated `unittest` checks.

## Run locally

Requirements: Python 3.9 or later. No package installation is required.

```bash
python3 app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The first run creates `data/jobflow.db` and seeds the demo workspace. To use another address or database:

```bash
python3 app.py --host 0.0.0.0 --port 8080 --db /tmp/jobflow-demo.db
```

## Run tests

```bash
python3 -m unittest discover -s tests -v
```

The tests use temporary databases and do not modify local demo data.

## Engineering notes

- The service is intentionally dependency-free and is exercised on supported Python versions by GitHub Actions.
- The API boundary uses structured JSON error responses, strict request-size limits, and parameterized SQL.
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for the data flow, design trade-offs, and interview discussion points.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for the local verification checklist and [SECURITY.md](SECURITY.md) for scope and reporting guidance.

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

## LinkedIn-ready project entry

**JobFlow — Full-Stack Remote Job Application Tracker**  
Python · SQLite · REST APIs · JavaScript · HTML/CSS · Testing

Built a dependency-free full-stack application that helps remote job seekers manage opportunities, follow-ups, and pipeline analytics through a responsive dashboard and REST-style JSON API.

- Designed and implemented searchable CRUD workflows, multi-field filtering, sorting, five-stage pipeline tracking, and upcoming-action analytics.
- Built a transactional SQLite data layer and centralized validation for URLs, ISO dates, salary ranges, enum values, required fields, and malformed payloads.
- Created a responsive vanilla JavaScript interface and an automated `unittest` suite covering validation, CRUD, filters, analytics, static delivery, and API status behavior.

## Refreshing the screenshot

Run `python3 app.py` with a fresh database, open `http://127.0.0.1:8000`, and capture the seeded, unfiltered dashboard. Replace `docs/jobflow-dashboard.png` with the new image.

## Security and reliability notes

- SQL values are always passed as parameters; sort columns are selected from an allowlist.
- Static paths are resolved and checked to prevent directory traversal.
- Request bodies are limited to 1 MB and validation failures return structured HTTP 422 errors.
- Database mutations are committed atomically and rolled back on failure.
- The app intentionally binds to `127.0.0.1` by default. It is a portfolio/local tool, not a production authentication system.

## Future improvements

- User authentication and per-user workspaces
- CSV import/export and calendar reminders
- Kanban drag-and-drop view
- Production WSGI/ASGI adapter and deployment configuration
- End-to-end browser tests

## License

[MIT](LICENSE)
