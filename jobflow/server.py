"""HTTP server and REST-style routing for JobFlow."""

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .database import (
    FIELDS,
    SCHEMA_VERSION,
    Database,
    ProtectedEventError,
    ProtectedArtifactError,
    RequestIdConflict,
    TransitionError,
    VersionConflict,
)
from .validation import (
    ALLOWED_FIELDS,
    ARTIFACT_KINDS,
    CURRENCIES,
    OUTCOMES,
    REQUIREMENT_ASSESSMENTS,
    REQUIREMENT_CATEGORIES,
    SALARY_PERIODS,
    STAGES,
    STATUSES,
    TASK_KINDS,
    validate_as_of,
    WORK_MODES,
    ValidationError,
    validate_event,
    validate_requirement,
    validate_application,
    validate_artifact,
    validate_submission,
    validate_task,
    validate_transition,
    find_duplicate_matches,
    duplicate_reason,
    application_fingerprint,
)


APPLICATION_ROUTE = re.compile(r"^/api/applications/(\d+)$")
EVENTS_ROUTE = re.compile(r"^/api/applications/(\d+)/events$")
EVENT_ROUTE = re.compile(r"^/api/applications/(\d+)/events/(\d+)$")
TRANSITIONS_ROUTE = re.compile(r"^/api/applications/(\d+)/transitions$")
TASKS_ROUTE = re.compile(r"^/api/applications/(\d+)/tasks$")
WORKSPACE_ROUTE = re.compile(r"^/api/applications/(\d+)/workspace$")
TASK_ROUTE = re.compile(r"^/api/tasks/(\d+)$")
REQUIREMENTS_ROUTE = re.compile(r"^/api/applications/(\d+)/requirements$")
REQUIREMENT_ROUTE = re.compile(r"^/api/requirements/(\d+)$")
ARTIFACTS_ROUTE = re.compile(r"^/api/applications/(\d+)/artifacts$")
ARTIFACT_ROUTE = re.compile(r"^/api/artifacts/(\d+)$")
SUBMISSIONS_ROUTE = re.compile(r"^/api/applications/(\d+)/submissions$")
SUBMISSION_ROUTE = re.compile(r"^/api/submissions/(\d+)$")
IMPORT_PREVIEW_ROUTE = re.compile(r"^/api/import/preview$")
TASK_COMPLETE_ROUTE = re.compile(r"^/api/tasks/(\d+)/complete$")
TASK_SNOOZE_ROUTE = re.compile(r"^/api/tasks/(\d+)/snooze$")
LIST_PARAMETERS = {"search", "status", "stage", "work_mode", "sort", "direction", "view", "page", "limit"}
SORT_FIELDS = {"updated_at", "applied_date", "next_action_date", "company", "status", "stage"}
VIEWS = {"all", "active", "follow-up", "interview", "offers"}
INSIGHTS_WINDOWS = {"30", "90", "all"}
IMPORT_MERGE_FIELDS = frozenset({
    "company", "role", "location", "work_mode", "source", "url", "salary_min",
    "salary_max", "salary_period", "currency", "applied_date", "notes",
})
IMPORT_DECISION_ACTIONS = frozenset({"create", "separate", "skip", "merge"})
IMPORT_PREVIEW_FIELDS = ("id", "company", "role", "location", "work_mode", "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "notes")


def import_preview_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep duplicate previews small and limited to fields users can review."""
    if record is None:
        return None
    return {field: record.get(field) for field in IMPORT_PREVIEW_FIELDS if field in record}


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
    request_id = ""

    def parse_request(self) -> bool:  # noqa: D401
        """Parse headers before deriving the correlation id for this request."""
        parsed = super().parse_request()
        if parsed:
            self.request_id = self._request_id_from_header()
        return parsed

    def handle_one_request(self) -> None:  # noqa: D401
        """Attach a request id and turn unexpected failures into safe JSON."""
        try:
            super().handle_one_request()
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                self.log_error("request_id=%s database busy", self.request_id)
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "The workspace is busy. Please retry shortly.", code="DATABASE_BUSY", retryable=True)
            else:
                self.log_error("request_id=%s database operation failed: %s", self.request_id, type(error).__name__)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "The server could not complete the request.", code="INTERNAL_ERROR")
        except Exception:
            self.log_error("request_id=%s unexpected server error", self.request_id)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "The server could not complete the request.", code="INTERNAL_ERROR")

    def _request_id_from_header(self) -> str:
        candidate = self.headers.get("X-Request-ID", "").strip()
        if 1 <= len(candidate) <= 128 and re.fullmatch(r"[A-Za-z0-9._:-]+", candidate):
            return candidate
        return uuid.uuid4().hex

    def send_response(self, code: int, message: str | None = None) -> None:  # noqa: D401
        """Add the correlation id to every HTTP response, including static files."""
        super().send_response(code, message)
        self.send_header("X-Request-ID", self.request_id)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "service": "jobflow", "version": __version__, "schema_version": SCHEMA_VERSION})
        elif parsed.path == "/api/meta/options":
            self._json({
                "schema_version": SCHEMA_VERSION,
                "statuses": STATUSES,
                "stages": STAGES,
                "outcomes": OUTCOMES,
                "work_modes": WORK_MODES,
                "currencies": CURRENCIES,
                "salary_periods": SALARY_PERIODS,
                "task_kinds": TASK_KINDS,
                "requirement_categories": REQUIREMENT_CATEGORIES,
                "requirement_assessments": REQUIREMENT_ASSESSMENTS,
                "artifact_kinds": ARTIFACT_KINDS,
                "views": sorted(VIEWS),
            })
        elif parsed.path == "/api/analytics":
            self._json(self.server.database.analytics())
        elif parsed.path == "/api/insights":
            query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
            try:
                unknown = set(query) - {"window"}
                if unknown:
                    raise ValidationError({"query": f"Unknown parameters: {', '.join(sorted(unknown))}."})
                window = query.get("window", "all")
                if window not in INSIGHTS_WINDOWS:
                    raise ValidationError({"window": "Choose 30, 90, or all."})
                self._json(self.server.database.insights(window))
            except ValidationError as error:
                self._json({"error": "Invalid insights parameters.", "fields": error.errors}, HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/api/today":
            query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
            try:
                unknown = set(query) - {"as_of"}
                if unknown:
                    raise ValidationError({"query": f"Unknown parameters: {', '.join(sorted(unknown))}."})
                self._json(self.server.database.today(validate_as_of(query.get("as_of"))))
            except ValidationError as error:
                self._json({"error": "Invalid query parameters.", "fields": error.errors}, HTTPStatus.BAD_REQUEST)
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
        elif match := WORKSPACE_ROUTE.match(parsed.path):
            workspace = self.server.database.get_workspace(int(match.group(1)))
            self._json(workspace) if workspace else self._error(HTTPStatus.NOT_FOUND, "Application not found.")
        elif match := EVENTS_ROUTE.match(parsed.path):
            application_id = int(match.group(1))
            if not self.server.database.get_application(application_id):
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
            else:
                self._json(self.server.database.list_events(application_id))
        elif match := TASKS_ROUTE.match(parsed.path):
            application_id = int(match.group(1))
            if self.server.database.get_application(application_id) is None:
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
            else:
                self._json(self.server.database.list_tasks(application_id))
        elif match := REQUIREMENTS_ROUTE.match(parsed.path):
            application_id = int(match.group(1))
            if self.server.database.get_application(application_id) is None:
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
            else:
                self._json(self.server.database.list_requirements(application_id))
        elif match := ARTIFACTS_ROUTE.match(parsed.path):
            application_id = int(match.group(1))
            if self.server.database.get_application(application_id) is None:
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
            else:
                self._json(self.server.database.list_artifacts(application_id))
        elif match := SUBMISSIONS_ROUTE.match(parsed.path):
            application_id = int(match.group(1))
            if self.server.database.get_application(application_id) is None:
                self._error(HTTPStatus.NOT_FOUND, "Application not found.")
            else:
                self._json(self.server.database.list_submissions(application_id))
        elif match := SUBMISSION_ROUTE.match(parsed.path):
            submission = self.server.database.get_submission(int(match.group(1)))
            self._json(submission) if submission else self._error(HTTPStatus.NOT_FOUND, "Submission not found.")
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
        if IMPORT_PREVIEW_ROUTE.match(parsed.path):
            self._import_preview()
            return
        if parsed.path == "/api/import":
            self._import(parsed)
            return
        if match := TASK_COMPLETE_ROUTE.match(parsed.path):
            try:
                payload = self._read_json(allow_empty=True)
                if not isinstance(payload, dict) or set(payload) - {"expected_version"}:
                    raise ValidationError({"body": "Only expected_version is accepted."})
                cleaned = validate_task(payload, partial=True)
                task = self.server.database.complete_task(
                    int(match.group(1)), expected_version=cleaned.get("expected_version")
                )
                if task is None:
                    self._error(HTTPStatus.NOT_FOUND, "Task not found.")
                else:
                    self._json(task)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except VersionConflict as error:
                self._json({"error": str(error), "code": "VERSION_CONFLICT", "current": error.current}, HTTPStatus.CONFLICT)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        if match := TASK_SNOOZE_ROUTE.match(parsed.path):
            try:
                payload = validate_task(self._read_json(), partial=True)
                if "due_date" not in payload or "expected_version" not in payload:
                    raise ValidationError({"body": "Snooze requires due_date and expected_version."})
                task = self.server.database.snooze_task(
                    int(match.group(1)),
                    due_date=payload["due_date"],
                    expected_version=payload["expected_version"],
                )
                if task is None:
                    self._error(HTTPStatus.NOT_FOUND, "Task not found.")
                else:
                    self._json(task)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except VersionConflict as error:
                self._json({"error": str(error), "code": "VERSION_CONFLICT", "current": error.current}, HTTPStatus.CONFLICT)
            except TransitionError as error:
                self._json({"error": str(error), "code": "INVALID_TASK", "fields": {error.field: str(error)}}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        if match := TASKS_ROUTE.match(parsed.path):
            try:
                task = self.server.database.create_task(int(match.group(1)), validate_task(self._read_json()))
                if task is None:
                    self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                else:
                    self._json(task, HTTPStatus.CREATED)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except TransitionError as error:
                self._json({"error": str(error), "code": "INVALID_TASK", "fields": {error.field: str(error)}}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        if match := REQUIREMENTS_ROUTE.match(parsed.path):
            try:
                requirement = self.server.database.create_requirement(
                    int(match.group(1)), validate_requirement(self._read_json())
                )
                if requirement is None:
                    self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                else:
                    self._json(requirement, HTTPStatus.CREATED)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        if match := ARTIFACTS_ROUTE.match(parsed.path):
            try:
                artifact = self.server.database.create_artifact(
                    int(match.group(1)), validate_artifact(self._read_json())
                )
                if artifact is None:
                    self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                else:
                    self._json(artifact, HTTPStatus.CREATED)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        if match := SUBMISSIONS_ROUTE.match(parsed.path):
            try:
                submission = self.server.database.create_submission(
                    int(match.group(1)), validate_submission(self._read_json())
                )
                if submission is None:
                    self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                else:
                    self._json(submission, HTTPStatus.CREATED)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except ValueError as error:
                self._json({"error": str(error), "code": "INVALID_SUBMISSION"}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        if match := TRANSITIONS_ROUTE.match(parsed.path):
            try:
                transition = validate_transition(self._read_json())
                result = self.server.database.transition_application(int(match.group(1)), transition)
                if result is None:
                    self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                else:
                    self._json(result)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except VersionConflict as error:
                self._json(
                    {"error": str(error), "code": "VERSION_CONFLICT", "current": error.current},
                    HTTPStatus.CONFLICT,
                )
            except RequestIdConflict as error:
                self._json(
                    {"error": str(error), "code": "REQUEST_ID_CONFLICT", "application_id": error.application_id},
                    HTTPStatus.CONFLICT,
                )
            except TransitionError as error:
                self._json(
                    {"error": str(error), "code": "INVALID_TRANSITION", "fields": {error.field: str(error)}},
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            except RequestBodyError as error:
                self._error(error.status, str(error))
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
        path = urlparse(self.path).path
        requirements_order_match = REQUIREMENTS_ROUTE.match(path)
        if requirements_order_match and not partial:
            try:
                payload = self._read_json()
                if not isinstance(payload, dict) or set(payload) != {"ordered_ids"}:
                    raise ValidationError({"body": "Provide only an ordered_ids array."})
                ordered_ids = payload.get("ordered_ids")
                if not isinstance(ordered_ids, list) or any(
                    isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in ordered_ids
                ):
                    raise ValidationError({"ordered_ids": "Expected an array of positive integer IDs."})
                requirements = self.server.database.reorder_requirements(
                    int(requirements_order_match.group(1)), ordered_ids
                )
                if requirements is None:
                    self._error(HTTPStatus.NOT_FOUND, "Application not found.")
                else:
                    self._json(requirements)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except ValueError as error:
                self._json({"error": str(error), "fields": {"ordered_ids": str(error)}}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        task_match = TASK_ROUTE.match(path)
        if task_match:
            if not partial:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Tasks support PATCH only.")
                return
            try:
                task = self.server.database.update_task(int(task_match.group(1)), validate_task(self._read_json(), partial=True))
                if task is None:
                    self._error(HTTPStatus.NOT_FOUND, "Task not found.")
                else:
                    self._json(task)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except VersionConflict as error:
                self._json({"error": str(error), "code": "VERSION_CONFLICT", "current": error.current}, HTTPStatus.CONFLICT)
            except TransitionError as error:
                self._json({"error": str(error), "code": "INVALID_TASK", "fields": {error.field: str(error)}}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        artifact_match = ARTIFACT_ROUTE.match(path)
        if artifact_match:
            if not partial:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Artifacts support PATCH only.")
                return
            try:
                artifact = self.server.database.update_artifact(
                    int(artifact_match.group(1)), validate_artifact(self._read_json(), partial=True)
                )
                if artifact is None:
                    self._error(HTTPStatus.NOT_FOUND, "Artifact not found.")
                else:
                    self._json(artifact)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        requirement_match = REQUIREMENT_ROUTE.match(path)
        if requirement_match:
            if not partial:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Requirements support PATCH only.")
                return
            try:
                requirement = self.server.database.update_requirement(
                    int(requirement_match.group(1)), validate_requirement(self._read_json(), partial=True)
                )
                if requirement is None:
                    self._error(HTTPStatus.NOT_FOUND, "Requirement not found.")
                else:
                    self._json(requirement)
            except ValidationError as error:
                self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except RequestBodyError as error:
                self._error(error.status, str(error))
            return
        match = APPLICATION_ROUTE.match(path)
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
                if merged.get("stage") == "Closed" and not merged.get("outcome") and merged.get("status") == "Rejected":
                    merged["outcome"] = "Rejected"
                    merged["closed_at"] = merged.get("closed_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
                data = validate_application(merged)
            else:
                data = validate_application(payload)
            record = self.server.database.update_application(application_id, data)
            self._json(record)
        except ValidationError as error:
            self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except TransitionError as error:
            self._json(
                {"error": str(error), "code": "INVALID_TRANSITION", "fields": {error.field: str(error)}},
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        except RequestBodyError as error:
            self._error(error.status, str(error))

    def do_DELETE(self) -> None:  # noqa: N802
        event_match = EVENT_ROUTE.match(urlparse(self.path).path)
        if event_match:
            application_id, event_id = map(int, event_match.groups())
            try:
                deleted = self.server.database.delete_event(application_id, event_id)
            except ProtectedEventError as error:
                self._json(
                    {"error": str(error), "code": "PROTECTED_EVENT", "origin": error.origin},
                    HTTPStatus.FORBIDDEN,
                )
                return
            if deleted:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._error(HTTPStatus.NOT_FOUND, "Event not found.")
            return
        artifact_match = ARTIFACT_ROUTE.match(urlparse(self.path).path)
        if artifact_match:
            try:
                deleted = self.server.database.delete_artifact(int(artifact_match.group(1)))
            except ProtectedArtifactError as error:
                self._json(
                    {"error": str(error), "code": "ARTIFACT_IN_USE", "package_ids": error.package_ids},
                    HTTPStatus.CONFLICT,
                )
                return
            if deleted:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._error(HTTPStatus.NOT_FOUND, "Artifact not found.")
            return
        requirement_match = REQUIREMENT_ROUTE.match(urlparse(self.path).path)
        if requirement_match:
            if self.server.database.delete_requirement(int(requirement_match.group(1))):
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._error(HTTPStatus.NOT_FOUND, "Requirement not found.")
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

    def _read_json(self, *, allow_empty: bool = False) -> Any:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise RequestBodyError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise RequestBodyError(HTTPStatus.BAD_REQUEST, "Content-Length must be a non-negative integer.")
        if length <= 0 and allow_empty:
            return {}
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
        if query.get("stage") and query["stage"] not in STAGES:
            errors["stage"] = f"Choose one of: {', '.join(STAGES)}."
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

    def _import_preview(self) -> None:
        """Validate import rows and return duplicate candidates without writing."""
        try:
            payload = self._read_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("applications"), list):
                raise ValidationError({"body": "Expected an object with an applications array."})
            records = payload["applications"]
            if len(records) > 5_000:
                raise ValidationError({"applications": "A backup can contain at most 5,000 records."})
            valid: list[dict[str, Any]] = []
            valid_indexes: list[int] = []
            invalid: list[dict[str, Any]] = []
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    invalid.append({"incoming_index": index, "source_index": index, "errors": {"body": "Expected an object."}})
                    continue
                unknown = set(record) - ALLOWED_FIELDS - {"id", "created_at", "updated_at", "events", "tasks", "requirements", "artifacts", "submissions"}
                if unknown:
                    invalid.append({"incoming_index": index, "source_index": index, "errors": {"body": f"Unknown fields: {', '.join(sorted(unknown))}."}})
                    continue
                try:
                    valid.append(validate_application({key: record[key] for key in ALLOWED_FIELDS if key in record}))
                    valid_indexes.append(index)
                except ValidationError as error:
                    invalid.append({"incoming_index": index, "source_index": index, "errors": error.errors})
            existing = self.server.database.export_applications()
            raw_matches = find_duplicate_matches(valid, existing)
            matches: list[dict[str, Any]] = []
            for match in raw_matches:
                incoming_index = match["incoming_index"]
                source_index = valid_indexes[incoming_index]
                existing_record = next((item for item in existing if item.get("id") == match["existing_application_id"]), None)
                matches.append({
                    **match,
                    "source_index": source_index,
                    "incoming": import_preview_record(valid[incoming_index]),
                    "existing": import_preview_record(existing_record),
                    "reason_label": "Same canonical job URL" if match["reason"] == "canonical_url" else "Same company, role, and location",
                })
            for relative_index, record in enumerate(valid):
                if any(item["incoming_index"] == relative_index for item in matches):
                    continue
                for previous_index, previous in enumerate(valid[:relative_index]):
                    reason = duplicate_reason(record, previous)
                    if reason:
                        matches.append({
                            "incoming_index": relative_index,
                            "source_index": valid_indexes[relative_index],
                            "matched_incoming_index": previous_index,
                            "matched_source_index": valid_indexes[previous_index],
                            "existing_application_id": None,
                            "reason": reason,
                            "reason_label": "Duplicate row in this import",
                            "fingerprint": application_fingerprint(record),
                            "incoming": import_preview_record(record),
                            "existing": import_preview_record(previous),
                        })
                        break
            self._json({"valid_count": len(valid), "valid_records": valid, "invalid": invalid, "conflicts": matches})
        except ValidationError as error:
            self._json({"error": "Import preview failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except RequestBodyError as error:
            self._error(error.status, str(error))

    def _import(self, parsed: Any) -> None:
        try:
            payload = self._read_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("applications"), list):
                raise ValidationError({"body": "Expected an object with an applications array."})
            unknown_top_level = set(payload) - {"schema_version", "exported_at", "applications", "duplicate_decisions"}
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
            metadata_fields = {"id", "created_at", "updated_at", "events", "tasks", "requirements", "artifacts", "submissions"}
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
                            event_fields = {
                                "event_type", "title", "details", "occurred_at", "from_stage", "to_stage",
                                "origin", "payload_json", "request_id",
                            }
                            unknown_event = set(event) - event_fields - event_metadata
                            if unknown_event:
                                errors[f"applications.{index}.events.{event_index}"] = f"Unknown fields: {', '.join(sorted(unknown_event))}."
                                continue
                            event_payload = {key: event[key] for key in event_fields if key in event}
                            cleaned_events.append(validate_event(event_payload))
                        except ValidationError as event_error:
                            for field, message in event_error.errors.items():
                                errors[f"applications.{index}.events.{event_index}.{field}"] = message
                    cleaned_record["events"] = cleaned_events
                    raw_tasks = record.get("tasks", [])
                    if not isinstance(raw_tasks, list):
                        errors[f"applications.{index}.tasks"] = "Expected an array."
                        continue
                    if len(raw_tasks) > 100:
                        errors[f"applications.{index}.tasks"] = "An application can contain at most 100 tasks."
                        continue
                    cleaned_tasks: list[dict[str, Any]] = []
                    for task_index, task in enumerate(raw_tasks):
                        if not isinstance(task, dict):
                            errors[f"applications.{index}.tasks.{task_index}"] = "Expected an object."
                            continue
                        try:
                            task_metadata = {"id", "application_id", "created_at", "updated_at"}
                            unknown_task = set(task) - {"kind", "title", "due_date", "completed_at", "version"} - task_metadata
                            if unknown_task:
                                errors[f"applications.{index}.tasks.{task_index}"] = f"Unknown fields: {', '.join(sorted(unknown_task))}."
                                continue
                            task_payload = {key: task[key] for key in ("kind", "title", "due_date", "completed_at", "version") if key in task}
                            cleaned_tasks.append(validate_task(task_payload))
                        except ValidationError as task_error:
                            for field, message in task_error.errors.items():
                                errors[f"applications.{index}.tasks.{task_index}.{field}"] = message
                    cleaned_record["tasks"] = cleaned_tasks
                    raw_requirements = record.get("requirements", [])
                    if not isinstance(raw_requirements, list):
                        errors[f"applications.{index}.requirements"] = "Expected an array."
                        continue
                    if len(raw_requirements) > 100:
                        errors[f"applications.{index}.requirements"] = "An application can contain at most 100 requirements."
                        continue
                    cleaned_requirements: list[dict[str, Any]] = []
                    for requirement_index, requirement in enumerate(raw_requirements):
                        if not isinstance(requirement, dict):
                            errors[f"applications.{index}.requirements.{requirement_index}"] = "Expected an object."
                            continue
                        try:
                            requirement_metadata = {"id", "application_id", "created_at", "updated_at"}
                            requirement_fields = {"criterion", "category", "assessment", "evidence", "weight", "position"}
                            unknown_requirement = set(requirement) - requirement_fields - requirement_metadata
                            if unknown_requirement:
                                errors[f"applications.{index}.requirements.{requirement_index}"] = f"Unknown fields: {', '.join(sorted(unknown_requirement))}."
                                continue
                            requirement_payload = {key: requirement[key] for key in requirement_fields if key in requirement}
                            cleaned_requirements.append(validate_requirement(requirement_payload))
                        except ValidationError as requirement_error:
                            for field, message in requirement_error.errors.items():
                                errors[f"applications.{index}.requirements.{requirement_index}.{field}"] = message
                    cleaned_record["requirements"] = cleaned_requirements
                    raw_artifacts = record.get("artifacts", [])
                    if not isinstance(raw_artifacts, list):
                        errors[f"applications.{index}.artifacts"] = "Expected an array."
                        continue
                    if len(raw_artifacts) > 100:
                        errors[f"applications.{index}.artifacts"] = "An application can contain at most 100 materials."
                        continue
                    cleaned_artifacts: list[dict[str, Any]] = []
                    artifact_ids: set[int] = set()
                    for artifact_index, artifact in enumerate(raw_artifacts):
                        if not isinstance(artifact, dict):
                            errors[f"applications.{index}.artifacts.{artifact_index}"] = "Expected an object."
                            continue
                        try:
                            artifact_metadata = {"id", "application_id", "created_at", "updated_at"}
                            artifact_fields = {"kind", "label", "uri", "version_label", "notes"}
                            unknown_artifact = set(artifact) - artifact_fields - artifact_metadata
                            if unknown_artifact:
                                errors[f"applications.{index}.artifacts.{artifact_index}"] = f"Unknown fields: {', '.join(sorted(unknown_artifact))}."
                                continue
                            artifact_payload = {key: artifact[key] for key in artifact_fields if key in artifact}
                            cleaned_artifact = validate_artifact(artifact_payload)
                            raw_artifact_id = artifact.get("id")
                            if isinstance(raw_artifact_id, bool) or not isinstance(raw_artifact_id, int) or raw_artifact_id < 1:
                                errors[f"applications.{index}.artifacts.{artifact_index}.id"] = "Material IDs must be positive integers."
                                continue
                            if raw_artifact_id in artifact_ids:
                                errors[f"applications.{index}.artifacts.{artifact_index}.id"] = "Material IDs must be unique within an application."
                                continue
                            artifact_ids.add(raw_artifact_id)
                            cleaned_artifact["id"] = raw_artifact_id
                            cleaned_artifacts.append(cleaned_artifact)
                        except ValidationError as artifact_error:
                            for field, message in artifact_error.errors.items():
                                errors[f"applications.{index}.artifacts.{artifact_index}.{field}"] = message
                    cleaned_record["artifacts"] = cleaned_artifacts
                    raw_submissions = record.get("submissions", [])
                    if not isinstance(raw_submissions, list):
                        errors[f"applications.{index}.submissions"] = "Expected an array."
                        continue
                    if len(raw_submissions) > 100:
                        errors[f"applications.{index}.submissions"] = "An application can contain at most 100 submission snapshots."
                        continue
                    cleaned_submissions: list[dict[str, Any]] = []
                    for submission_index, submission in enumerate(raw_submissions):
                        if not isinstance(submission, dict):
                            errors[f"applications.{index}.submissions.{submission_index}"] = "Expected an object."
                            continue
                        try:
                            submission_metadata = {"id", "application_id", "created_at"}
                            unknown_submission = set(submission) - {"submitted_at", "notes", "items"} - submission_metadata
                            if unknown_submission:
                                errors[f"applications.{index}.submissions.{submission_index}"] = f"Unknown fields: {', '.join(sorted(unknown_submission))}."
                                continue
                            raw_items = submission.get("items")
                            if not isinstance(raw_items, list) or not raw_items:
                                errors[f"applications.{index}.submissions.{submission_index}.items"] = "Choose at least one material."
                                continue
                            if len(raw_items) > 100:
                                errors[f"applications.{index}.submissions.{submission_index}.items"] = "A submission can contain at most 100 materials."
                                continue
                            item_ids: list[int] = []
                            cleaned_items: list[dict[str, Any]] = []
                            for item_index, item in enumerate(raw_items):
                                if not isinstance(item, dict):
                                    errors[f"applications.{index}.submissions.{submission_index}.items.{item_index}"] = "Expected an object."
                                    continue
                                item_allowed = {"package_id", "artifact_id", "position", "snapshot_kind", "snapshot_label", "snapshot_uri", "snapshot_version_label", "snapshot_notes"}
                                unknown_item = set(item) - item_allowed
                                if unknown_item:
                                    errors[f"applications.{index}.submissions.{submission_index}.items.{item_index}"] = f"Unknown fields: {', '.join(sorted(unknown_item))}."
                                    continue
                                artifact_id = item.get("artifact_id")
                                if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id < 1:
                                    errors[f"applications.{index}.submissions.{submission_index}.items.{item_index}.artifact_id"] = "Material IDs must be positive integers."
                                    continue
                                if artifact_id in item_ids:
                                    errors[f"applications.{index}.submissions.{submission_index}.items.{item_index}.artifact_id"] = "A material can only be selected once."
                                    continue
                                if artifact_id not in artifact_ids:
                                    errors[f"applications.{index}.submissions.{submission_index}.items.{item_index}.artifact_id"] = "Material does not belong to this application."
                                    continue
                                item_ids.append(artifact_id)
                                clean_item = {"artifact_id": artifact_id}
                                for field, maximum in (("snapshot_kind", 40), ("snapshot_label", 160), ("snapshot_uri", 500), ("snapshot_version_label", 80), ("snapshot_notes", 2000)):
                                    if field in item:
                                        value = str(item[field]).strip()
                                        if len(value) > maximum:
                                            errors[f"applications.{index}.submissions.{submission_index}.items.{item_index}.{field}"] = f"Must be {maximum} characters or fewer."
                                        clean_item[field] = value
                                if "snapshot_kind" in clean_item and clean_item["snapshot_kind"] not in ARTIFACT_KINDS:
                                    errors[f"applications.{index}.submissions.{submission_index}.items.{item_index}.snapshot_kind"] = f"Choose one of: {', '.join(ARTIFACT_KINDS)}."
                                cleaned_items.append(clean_item)
                            submission_payload = validate_submission({
                                "artifact_ids": item_ids,
                                "notes": submission.get("notes", ""),
                                "submitted_at": submission.get("submitted_at"),
                            })
                            cleaned_submissions.append({"submitted_at": submission_payload["submitted_at"], "notes": submission_payload["notes"], "items": cleaned_items})
                        except ValidationError as submission_error:
                            for field, message in submission_error.errors.items():
                                errors[f"applications.{index}.submissions.{submission_index}.{field}"] = message
                    cleaned_record["submissions"] = cleaned_submissions
                    cleaned.append(cleaned_record)
                except ValidationError as error:
                    for field, message in error.errors.items():
                        errors[f"applications.{index}.{field}"] = message
            if errors:
                raise ValidationError(errors)
            mode = parse_qs(parsed.query).get("mode", ["append"])[0]
            if mode not in {"append", "replace"}:
                raise ValidationError({"mode": "Choose append or replace."})
            decisions_raw = payload.get("duplicate_decisions", [])
            if decisions_raw in (None, ""):
                decisions_raw = []
            if not isinstance(decisions_raw, list):
                raise ValidationError({"duplicate_decisions": "Expected an array."})
            if len(decisions_raw) > 5_000:
                raise ValidationError({"duplicate_decisions": "At most 5,000 duplicate decisions are allowed."})

            existing = self.server.database.export_applications() if mode == "append" else []
            candidates = find_duplicate_matches(cleaned, existing)
            for relative_index, record in enumerate(cleaned):
                if any(item["incoming_index"] == relative_index for item in candidates):
                    continue
                for previous_index, previous in enumerate(cleaned[:relative_index]):
                    reason = duplicate_reason(record, previous)
                    if reason:
                        candidates.append({
                            "incoming_index": relative_index,
                            "matched_incoming_index": previous_index,
                            "existing_application_id": None,
                            "reason": reason,
                            "fingerprint": application_fingerprint(record),
                        })
                        break

            candidate_by_index = {item["incoming_index"]: item for item in candidates}
            decisions: dict[int, dict[str, Any]] = {}
            for decision_index, raw_decision in enumerate(decisions_raw):
                if not isinstance(raw_decision, dict):
                    raise ValidationError({f"duplicate_decisions.{decision_index}": "Expected an object."})
                allowed_decision_fields = {"incoming_index", "action", "existing_application_id", "fields"}
                unknown_decision = set(raw_decision) - allowed_decision_fields
                if unknown_decision:
                    raise ValidationError({f"duplicate_decisions.{decision_index}": f"Unknown fields: {', '.join(sorted(unknown_decision))}."})
                incoming_index = raw_decision.get("incoming_index")
                if isinstance(incoming_index, bool) or not isinstance(incoming_index, int) or incoming_index < 0 or incoming_index >= len(cleaned):
                    raise ValidationError({f"duplicate_decisions.{decision_index}.incoming_index": "Choose a valid incoming row."})
                if incoming_index not in candidate_by_index:
                    raise ValidationError({f"duplicate_decisions.{decision_index}.incoming_index": "This row does not have a duplicate conflict."})
                if incoming_index in decisions:
                    raise ValidationError({f"duplicate_decisions.{decision_index}.incoming_index": "Only one decision is allowed per conflicting row."})
                action = str(raw_decision.get("action", "")).strip().lower()
                if action not in IMPORT_DECISION_ACTIONS:
                    raise ValidationError({f"duplicate_decisions.{decision_index}.action": "Choose create, separate, skip, or merge."})
                candidate = candidate_by_index[incoming_index]
                target_id = raw_decision.get("existing_application_id")
                if target_id is not None and (isinstance(target_id, bool) or not isinstance(target_id, int) or target_id < 1):
                    raise ValidationError({f"duplicate_decisions.{decision_index}.existing_application_id": "The merge target must be a positive integer."})
                if action == "merge":
                    if candidate.get("existing_application_id") is None or target_id != candidate.get("existing_application_id"):
                        raise ValidationError({f"duplicate_decisions.{decision_index}.existing_application_id": "Merge is only available for an existing application conflict."})
                    fields = raw_decision.get("fields")
                    if not isinstance(fields, list) or not fields:
                        raise ValidationError({f"duplicate_decisions.{decision_index}.fields": "Choose at least one field to merge."})
                    if any(not isinstance(field, str) or field not in IMPORT_MERGE_FIELDS for field in fields) or len(set(fields)) != len(fields):
                        raise ValidationError({f"duplicate_decisions.{decision_index}.fields": "Choose only mapped, mergeable fields."})
                    non_empty = [field for field in fields if cleaned[incoming_index].get(field) not in (None, "")]
                    if not non_empty:
                        raise ValidationError({f"duplicate_decisions.{decision_index}.fields": "Choose a field with a non-empty incoming value."})
                    raw_decision = {**raw_decision, "fields": non_empty, "existing_application_id": target_id}
                elif target_id is not None and target_id != candidate.get("existing_application_id"):
                    raise ValidationError({f"duplicate_decisions.{decision_index}.existing_application_id": "The target does not match this conflict."})
                decisions[incoming_index] = {**raw_decision, "action": action}

            if candidates:
                existing_by_id = {item.get("id"): item for item in existing}
                for candidate in candidates:
                    candidate["incoming"] = import_preview_record(cleaned[candidate["incoming_index"]])
                    if candidate.get("existing_application_id") is not None:
                        candidate["existing"] = import_preview_record(existing_by_id.get(candidate["existing_application_id"]))
                    else:
                        candidate["existing"] = import_preview_record(cleaned[candidate["matched_incoming_index"]])
                missing = [item for item in candidates if item["incoming_index"] not in decisions]
                if missing:
                    self._json({
                        "error": "Duplicate applications need a decision before import.",
                        "code": "DUPLICATES_FOUND",
                        "conflicts": missing,
                    }, HTTPStatus.CONFLICT)
                    return

            records_to_insert: list[dict[str, Any]] = []
            merge_records: list[tuple[int, dict[str, Any], list[str]]] = []
            skipped = 0
            for index, record in enumerate(cleaned):
                decision = decisions.get(index)
                if decision and decision["action"] == "skip":
                    skipped += 1
                    continue
                if decision and decision["action"] == "merge":
                    merge_records.append((int(decision["existing_application_id"]), record, list(decision["fields"])))
                    continue
                records_to_insert.append(record)
            imported = self.server.database.import_applications(
                records_to_insert,
                replace=mode == "replace",
                merge_records=merge_records,
            )
            self._json({
                "imported": imported,
                "merged": len(merge_records),
                "skipped": skipped,
                "mode": mode,
            }, HTTPStatus.CREATED)
        except ValidationError as error:
            self._json({"error": "Import validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except RequestBodyError as error:
            self._error(error.status, str(error))

    def _error_code(self, status: int) -> str:
        return {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            413: "PAYLOAD_TOO_LARGE",
            415: "UNSUPPORTED_MEDIA_TYPE",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            500: "INTERNAL_ERROR",
            503: "SERVICE_UNAVAILABLE",
        }.get(int(status), "REQUEST_FAILED")

    def _error_payload(self, payload: Any, status: int) -> dict[str, Any]:
        """Normalize legacy route errors into the public error envelope."""
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            envelope = dict(payload)
            info = dict(payload["error"])
            info.setdefault("code", self._error_code(status))
            info.setdefault("message", "The request could not be completed.")
            info.setdefault("fields", {})
            info.setdefault("retryable", int(status) in {408, 425, 429, 502, 503, 504})
            info.setdefault("request_id", self.request_id)
            envelope["error"] = info
            return envelope

        details = dict(payload) if isinstance(payload, dict) else {}
        message = details.pop("error", None)
        if not isinstance(message, str) or not message.strip():
            message = "The request could not be completed."
        if int(status) == 404 and "deleted" not in message.lower():
            message = f"{message} It may have been deleted or the link may be stale."
        code = str(details.get("code") or self._error_code(status))
        fields = details.get("fields") if isinstance(details.get("fields"), dict) else {}
        retryable = bool(details.get("retryable", int(status) in {408, 425, 429, 502, 503, 504}))
        info = {
            "code": code,
            "message": message,
            "fields": fields,
            "retryable": retryable,
            "request_id": self.request_id,
        }
        # Keep route-specific data available to clients while standardizing the error member.
        envelope: dict[str, Any] = {"error": info, "status": int(status), "message": message, "code": code, "fields": fields, "retryable": retryable, "request_id": self.request_id}
        envelope.update({key: value for key, value in details.items() if key not in {"error", "fields", "code", "retryable", "request_id", "status"}})
        return envelope

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        if int(status) >= 400:
            payload = self._error_payload(payload, status)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if int(status) in {429, 503}:
            self.send_header("Retry-After", "1")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, *, code: str | None = None, fields: dict[str, Any] | None = None, retryable: bool | None = None, **details: Any) -> None:
        payload: dict[str, Any] = {"error": message}
        if code is not None:
            payload["code"] = code
        if fields is not None:
            payload["fields"] = fields
        if retryable is not None:
            payload["retryable"] = retryable
        payload.update(details)
        self._json(payload, status)

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
        print(f"{self.address_string()} request_id={self.request_id} - {format % args}")


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
