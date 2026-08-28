"""HTTP server and REST-style routing for JobFlow."""

from __future__ import annotations

import json
import mimetypes
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .database import FIELDS, SCHEMA_VERSION, Database
from .validation import (
    ALLOWED_FIELDS,
    CURRENCIES,
    SALARY_PERIODS,
    STATUSES,
    WORK_MODES,
    ValidationError,
    validate_event,
    validate_application,
)


APPLICATION_ROUTE = re.compile(r"^/api/applications/(\d+)$")
EVENTS_ROUTE = re.compile(r"^/api/applications/(\d+)/events$")
EVENT_ROUTE = re.compile(r"^/api/applications/(\d+)/events/(\d+)$")
LIST_PARAMETERS = {"search", "status", "work_mode", "sort", "direction", "view", "page", "limit"}
SORT_FIELDS = {"updated_at", "applied_date", "next_action_date", "company", "status"}
VIEWS = {"all", "active", "follow-up", "interview", "offers"}


class RequestBodyError(ValueError):
    """A client request body that cannot be parsed safely."""

    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class JobFlowServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], *, database: Database, static_dir: Path):
        super().__init__(address, handler)
        self.database = database
        self.static_dir = static_dir.resolve()


class JobFlowHandler(BaseHTTPRequestHandler):
    server: JobFlowServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "service": "jobflow", "version": __version__, "schema_version": SCHEMA_VERSION})
        elif parsed.path == "/api/meta/options":
            self._json({
                "statuses": STATUSES,
                "work_modes": WORK_MODES,
                "currencies": CURRENCIES,
                "salary_periods": SALARY_PERIODS,
                "views": sorted(VIEWS),
            })
        elif parsed.path == "/api/analytics":
            self._json(self.server.database.analytics())
        elif parsed.path == "/api/export":
            self._json({
                "schema_version": SCHEMA_VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "applications": self.server.database.export_applications(),
            })
        elif parsed.path == "/api/applications":
            query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
            try:
                self._json(self.server.database.list_applications(self._validate_list_query(query)))
            except ValidationError as error:
                self._json({"error": "Invalid query parameters.", "fields": error.errors}, HTTPStatus.BAD_REQUEST)
        elif match := EVENTS_ROUTE.match(parsed.path):
            application_id = int(match.group(1))
            if not self.server.database.get_application(application_id):
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
            else:
                self._json(self.server.database.list_events(application_id))
        elif match := EVENT_ROUTE.match(parsed.path):
            application_id, event_id = map(int, match.groups())
            if not self.server.database.get_application(application_id):
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
            else:
                events = self.server.database.list_events(application_id)
                event = next((item for item in events if item["id"] == event_id), None)
                self._json(event) if event else self._error(HTTPStatus.NOT_FOUND, "Event not found.")
        elif match := APPLICATION_ROUTE.match(parsed.path):
            record = self.server.database.get_application(int(match.group(1)))
            self._json(record) if record else self._error(HTTPStatus.NOT_FOUND, "Application not found.")
        elif parsed.path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "API route not found.")
        else:
            self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/import":
            self._import(parsed)
            return
        if match := EVENTS_ROUTE.match(parsed.path):
            try:
                event = self.server.database.create_event(int(match.group(1)), validate_event(self._read_json()))
                if event is None:
                    self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                else:
                    self._json(event, HTTPStatus.CREATED)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        if parsed.path != "/api/applications":
            self._error(HTTPStatus.NOT_FOUND, "API route not found.")
            return
        try:
            record = self.server.database.create_application(validate_application(self._read_json()))
            self._json(record, HTTPStatus.CREATED)
        except ValidationError as error:
            self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except RequestBodyError as error:
            self._error(error.status, str(error))

    def do_PATCH(self) -> None:  # noqa: N802
        self._update(partial=True)

    def do_PUT(self) -> None:  # noqa: N802
        self._update(partial=False)

    def _update(self, *, partial: bool) -> None:
        match = APPLICATION_ROUTE.match(urlparse(self.path).path)
        if not match:
            self._error(HTTPStatus.NOT_FOUND, "API route not found.")
            return
        try:
            application_id = int(match.group(1))
            existing = self.server.database.get_application(application_id)
            if not existing:
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                return
            payload = self._read_json()
            if partial:
                changes = validate_application(payload, partial=True)
                merged = {field: existing.get(field) for field in FIELDS}
                merged.update(changes)
                data = validate_application(merged)
            else:
                data = validate_application(payload)
            record = self.server.database.update_application(application_id, data)
            self._json(record)
        except ValidationError as error:
            self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except RequestBodyError as error:
            self._error(error.status, str(error))

    def do_DELETE(self) -> None:  # noqa: N802
        event_match = EVENT_ROUTE.match(urlparse(self.path).path)
        if event_match:
            application_id, event_id = map(int, event_match.groups())
            if self.server.database.delete_event(application_id, event_id):
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._error(HTTPStatus.NOT_FOUND, "Event not found.")
            return
        match = APPLICATION_ROUTE.match(urlparse(self.path).path)
        if not match:
            self._error(HTTPStatus.NOT_FOUND, "API route not found.")
            return
        if self.server.database.delete_application(int(match.group(1))):
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._error(HTTPStatus.NOT_FOUND, "Application not found.")

    def _read_json(self) -> Any:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise RequestBodyError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise RequestBodyError(HTTPStatus.BAD_REQUEST, "Content-Length must be a non-negative integer.")
        if length <= 0:
            raise RequestBodyError(HTTPStatus.BAD_REQUEST, "Request body must contain JSON.")
        if length > 1_000_000:
            raise RequestBodyError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body exceeds the 1 MB limit.")
        try:
            text = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError as error:
            raise RequestBodyError(HTTPStatus.BAD_REQUEST, "Request body must be valid UTF-8.") from error
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise RequestBodyError(HTTPStatus.BAD_REQUEST, "Request body must contain valid JSON.") from error

    def _validate_list_query(self, query: dict[str, str]) -> dict[str, str]:
        errors: dict[str, str] = {}
        unknown = set(query) - LIST_PARAMETERS
        if unknown:
            errors["query"] = f"Unknown parameters: {', '.join(sorted(unknown))}."
        if query.get("status") and query["status"] not in STATUSES:
            errors["status"] = f"Choose one of: {', '.join(STATUSES)}."
        if query.get("work_mode") and query["work_mode"] not in WORK_MODES:
            errors["work_mode"] = f"Choose one of: {', '.join(WORK_MODES)}."
        if query.get("sort") and query["sort"] not in SORT_FIELDS:
            errors["sort"] = f"Choose one of: {', '.join(sorted(SORT_FIELDS))}."
        if query.get("direction") and query["direction"].lower() not in {"asc", "desc"}:
            errors["direction"] = "Choose asc or desc."
        if query.get("view") and query["view"] not in VIEWS:
            errors["view"] = f"Choose one of: {', '.join(sorted(VIEWS))}."
        for field, maximum in (("page", 100_000), ("limit", 100)):
            if field not in query:
                continue
            try:
                value = int(query[field])
                if value < 1 or value > maximum:
                    raise ValueError
            except ValueError:
                errors[field] = f"Enter an integer from 1 to {maximum}."
        if errors:
            raise ValidationError(errors)
        return query

    def _import(self, parsed: Any) -> None:
        try:
            payload = self._read_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("applications"), list):
                raise ValidationError({"body": "Expected an object with an applications array."})
            unknown_top_level = set(payload) - {"schema_version", "exported_at", "applications"}
            if unknown_top_level:
                raise ValidationError({"body": f"Unknown backup fields: {', '.join(sorted(unknown_top_level))}."})
            schema_version = payload.get("schema_version", SCHEMA_VERSION)
            if not isinstance(schema_version, int) or schema_version < 1:
                raise ValidationError({"schema_version": "Schema version must be a positive integer."})
            if schema_version > SCHEMA_VERSION:
                raise ValidationError({"schema_version": f"Backup schema {schema_version} is newer than supported schema {SCHEMA_VERSION}."})
            records = payload["applications"]
            if len(records) > 5_000:
                raise ValidationError({"applications": "A backup can contain at most 5,000 records."})
            cleaned: list[dict[str, Any]] = []
            errors: dict[str, str] = {}
            metadata_fields = {"id", "created_at", "updated_at", "events"}
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    errors[f"applications.{index}"] = "Expected an object."
                    continue
                unknown = set(record) - ALLOWED_FIELDS - metadata_fields
                if unknown:
                    errors[f"applications.{index}"] = f"Unknown fields: {', '.join(sorted(unknown))}."
                    continue
                try:
                    cleaned_record = validate_application({key: record[key] for key in ALLOWED_FIELDS if key in record})
                    raw_events = record.get("events", [])
                    if not isinstance(raw_events, list):
                        errors[f"applications.{index}.events"] = "Expected an array."
                        continue
                    if len(raw_events) > 100:
                        errors[f"applications.{index}.events"] = "An application can contain at most 100 events."
                        continue
                    cleaned_events: list[dict[str, Any]] = []
                    for event_index, event in enumerate(raw_events):
                        if not isinstance(event, dict):
                            errors[f"applications.{index}.events.{event_index}"] = "Expected an object."
                            continue
                        try:
                            event_metadata = {"id", "application_id", "created_at"}
                            unknown_event = set(event) - {"event_type", "title", "details", "occurred_at"} - event_metadata
                            if unknown_event:
                                errors[f"applications.{index}.events.{event_index}"] = f"Unknown fields: {', '.join(sorted(unknown_event))}."
                                continue
                            event_payload = {key: event[key] for key in ("event_type", "title", "details", "occurred_at") if key in event}
                            cleaned_events.append(validate_event(event_payload))
                        except ValidationError as event_error:
                            for field, message in event_error.errors.items():
                                errors[f"applications.{index}.events.{event_index}.{field}"] = message
                    cleaned_record["events"] = cleaned_events
                    cleaned.append(cleaned_record)
                except ValidationError as error:
                    for field, message in error.errors.items():
                        errors[f"applications.{index}.{field}"] = message
            if errors:
                raise ValidationError(errors)
            mode = parse_qs(parsed.query).get("mode", ["append"])[0]
            if mode not in {"append", "replace"}:
                raise ValidationError({"mode": "Choose append or replace."})
            imported = self.server.database.import_applications(cleaned, replace=mode == "replace")
            self._json({"imported": imported, "mode": mode}, HTTPStatus.CREATED)
        except ValidationError as error:
            self._json({"error": "Import validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except RequestBodyError as error:
            self._error(error.status, str(error))

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message, "status": int(status)}, status)

    def _static(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        target = (self.server.static_dir / relative).resolve()
        if self.server.static_dir not in target.parents and target != self.server.static_dir:
            self._error(HTTPStatus.FORBIDDEN, "Invalid path.")
            return
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "File not found.")
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._error(HTTPStatus.NOT_FOUND, "File not found.")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def build_server(
    host: str,
    port: int,
    *,
    database_path: str | Path,
    static_dir: str | Path,
    seed_demo: bool = False,
) -> JobFlowServer:
    database = Database(database_path)
    database.initialize(seed=seed_demo)
    return JobFlowServer((host, port), JobFlowHandler, database=database, static_dir=Path(static_dir))
