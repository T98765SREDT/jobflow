"""HTTP server and REST-style routing for JobFlow."""

from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .database import Database
from .validation import CURRENCIES, STATUSES, WORK_MODES, ValidationError, validate_application


APPLICATION_ROUTE = re.compile(r"^/api/applications/(\d+)$")


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
            self._json({"status": "ok", "service": "jobflow", "version": __version__})
        elif parsed.path == "/api/meta/options":
            self._json({"statuses": STATUSES, "work_modes": WORK_MODES, "currencies": CURRENCIES})
        elif parsed.path == "/api/analytics":
            self._json(self.server.database.analytics())
        elif parsed.path == "/api/applications":
            query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
            records = self.server.database.list_applications(query)
            self._json({"items": records, "count": len(records)})
        elif match := APPLICATION_ROUTE.match(parsed.path):
            record = self.server.database.get_application(int(match.group(1)))
            self._json(record) if record else self._error(HTTPStatus.NOT_FOUND, "Application not found.")
        elif parsed.path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "API route not found.")
        else:
            self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/applications":
            self._error(HTTPStatus.NOT_FOUND, "API route not found.")
            return
        try:
            record = self.server.database.create_application(validate_application(self._read_json()))
            self._json(record, HTTPStatus.CREATED)
        except ValidationError as error:
            self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except json.JSONDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "Request body must contain valid JSON.")

    def do_PATCH(self) -> None:  # noqa: N802
        self._update()

    def do_PUT(self) -> None:  # noqa: N802
        self._update()

    def _update(self) -> None:
        match = APPLICATION_ROUTE.match(urlparse(self.path).path)
        if not match:
            self._error(HTTPStatus.NOT_FOUND, "API route not found.")
            return
        try:
            data = validate_application(self._read_json(), partial=True)
            record = self.server.database.update_application(int(match.group(1)), data)
            self._json(record) if record else self._error(HTTPStatus.NOT_FOUND, "Application not found.")
        except ValidationError as error:
            self._json({"error": "Validation failed.", "fields": error.errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except json.JSONDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "Request body must contain valid JSON.")

    def do_DELETE(self) -> None:  # noqa: N802
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
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise json.JSONDecodeError("Invalid content length", "", 0)
        if length > 1_000_000:
            raise ValidationError({"body": "Request body is too large."})
        return json.loads(self.rfile.read(length).decode("utf-8"))

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
            target = self.server.static_dir / "index.html"
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
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def build_server(host: str, port: int, *, database_path: str | Path, static_dir: str | Path) -> JobFlowServer:
    database = Database(database_path)
    database.initialize()
    return JobFlowServer((host, port), JobFlowHandler, database=database, static_dir=Path(static_dir))
