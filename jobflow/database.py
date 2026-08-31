"""SQLite persistence and query layer for JobFlow."""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterator

from .validation import (
    EVENT_ORIGINS,
    LEGACY_STATUS_TO_STAGE,
    STAGES,
    STAGE_TO_LEGACY_STATUS,
    TASK_KINDS,
)
from .validation import REQUIREMENT_ASSESSMENTS, REQUIREMENT_CATEGORIES


SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    work_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'Wishlist',
    outcome TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    closed_at TEXT,
    waiting_until TEXT,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    salary_min REAL,
    salary_max REAL,
    salary_period TEXT NOT NULL DEFAULT 'Annual',
    currency TEXT NOT NULL DEFAULT 'USD',
    applied_date TEXT,
    next_action_date TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_next_action ON applications(next_action_date);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company);
CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    from_stage TEXT,
    to_stage TEXT,
    origin TEXT NOT NULL DEFAULT 'system',
    payload_json TEXT NOT NULL DEFAULT '{}',
    request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_application_events_application ON application_events(application_id, occurred_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS application_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    due_date TEXT NOT NULL,
    completed_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_application_tasks_open_due ON application_tasks(application_id, completed_at, due_date, id);
CREATE TABLE IF NOT EXISTS application_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    criterion TEXT NOT NULL,
    category TEXT NOT NULL,
    assessment TEXT NOT NULL DEFAULT 'unknown',
    evidence TEXT NOT NULL DEFAULT '',
    weight INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_application_requirements_order ON application_requirements(application_id, position, id);
CREATE TABLE IF NOT EXISTS application_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    uri TEXT NOT NULL DEFAULT '',
    version_label TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_application_artifacts_application ON application_artifacts(application_id, created_at ASC, id ASC);
CREATE TABLE IF NOT EXISTS submission_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_submission_packages_application ON submission_packages(application_id, submitted_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS submission_package_items (
    package_id INTEGER NOT NULL REFERENCES submission_packages(id) ON DELETE CASCADE,
    artifact_id INTEGER NOT NULL REFERENCES application_artifacts(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL DEFAULT 0,
    snapshot_kind TEXT NOT NULL,
    snapshot_label TEXT NOT NULL,
    snapshot_uri TEXT NOT NULL DEFAULT '',
    snapshot_version_label TEXT NOT NULL DEFAULT '',
    snapshot_notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (package_id, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_submission_package_items_artifact ON submission_package_items(artifact_id);
"""

SCHEMA_VERSION = 8

FIELDS = (
    "company", "role", "location", "work_mode", "status", "stage", "outcome", "version", "closed_at", "waiting_until",
    "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes",
)


class VersionConflict(Exception):
    """Raised when a write was based on a stale application version."""

    def __init__(self, current: dict[str, Any]):
        super().__init__("The application was changed by another request.")
        self.current = current


class RequestIdConflict(Exception):
    """Raised when a request id is already attached to another application."""

    def __init__(self, application_id: int):
        super().__init__("Request ID was already used for another application.")
        self.application_id = application_id


class TransitionError(ValueError):
    """Raised when a lifecycle transition is not allowed."""

    def __init__(self, message: str, field: str = "transition"):
        super().__init__(message)
        self.field = field


class ProtectedEventError(Exception):
    """Raised when a system-generated timeline event is deleted."""

    def __init__(self, origin: str):
        super().__init__(f"Events with origin '{origin}' cannot be deleted.")
        self.origin = origin


class ProtectedArtifactError(Exception):
    """Raised when a material is part of an immutable submission package."""

    def __init__(self, package_ids: list[int]):
        super().__init__("This material is referenced by an immutable submission package.")
        self.package_ids = package_ids


ALLOWED_TRANSITIONS = {
    "Wishlist": {"Ready", "Applied", "Closed"},
    "Ready": {"Wishlist", "Applied", "Closed"},
    "Applied": {"Wishlist", "Ready", "Interview", "Closed"},
    "Interview": {"Applied", "Offer", "Closed"},
    "Offer": {"Interview", "Closed"},
    "Closed": set(),
}


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, *, seed: bool = False) -> None:
        database_path = Path(self.path)
        is_new_database = not database_path.exists() or database_path.stat().st_size == 0
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}."
                )
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(applications)")}
            if "salary_period" not in columns:
                connection.execute(
                    "ALTER TABLE applications ADD COLUMN salary_period TEXT NOT NULL DEFAULT 'Annual'"
                )
                columns.add("salary_period")
            if "waiting_until" not in columns:
                connection.execute("ALTER TABLE applications ADD COLUMN waiting_until TEXT")
                columns.add("waiting_until")
            if version < 4:
                # SQLite DDL and the backfill remain in one transaction. If
                # anything fails, the caller's v3 database is left intact.
                if "stage" not in columns:
                    connection.execute("ALTER TABLE applications ADD COLUMN stage TEXT NOT NULL DEFAULT 'Wishlist'")
                if "outcome" not in columns:
                    connection.execute("ALTER TABLE applications ADD COLUMN outcome TEXT")
                if "version" not in columns:
                    connection.execute("ALTER TABLE applications ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
                if "closed_at" not in columns:
                    connection.execute("ALTER TABLE applications ADD COLUMN closed_at TEXT")
                connection.execute(
                    """
                    UPDATE applications
                    SET stage = CASE status
                        WHEN 'Wishlist' THEN 'Wishlist'
                        WHEN 'Applied' THEN 'Applied'
                        WHEN 'Interview' THEN 'Interview'
                        WHEN 'Offer' THEN 'Offer'
                        WHEN 'Rejected' THEN 'Closed'
                        ELSE 'Wishlist'
                    END,
                    outcome = CASE WHEN status = 'Rejected' THEN 'Rejected' ELSE NULL END,
                    closed_at = CASE WHEN status = 'Rejected' THEN COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) ELSE NULL END,
                    version = CASE WHEN version < 1 OR version IS NULL THEN 1 ELSE version END
                    """
                )
                connection.execute("PRAGMA user_version = 4")
            if version < 5:
                event_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(application_events)")
                }
                # v4 databases already have the events table, but not the
                # structured transition metadata introduced in v5.
                if "from_stage" not in event_columns:
                    connection.execute("ALTER TABLE application_events ADD COLUMN from_stage TEXT")
                if "to_stage" not in event_columns:
                    connection.execute("ALTER TABLE application_events ADD COLUMN to_stage TEXT")
                if "origin" not in event_columns:
                    connection.execute("ALTER TABLE application_events ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy'")
                if "payload_json" not in event_columns:
                    connection.execute("ALTER TABLE application_events ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
                if "request_id" not in event_columns:
                    connection.execute("ALTER TABLE application_events ADD COLUMN request_id TEXT")
                connection.execute(
                    "UPDATE application_events SET origin = 'legacy' WHERE origin IS NULL OR origin = ''"
                )
                connection.execute(
                    "UPDATE application_events SET payload_json = '{}' WHERE payload_json IS NULL OR payload_json = ''"
                )
            if version < 6:
                # Preserve the old single-date contract as one actionable
                # follow-up task. Re-running initialization is idempotent.
                connection.execute(
                    """
                    INSERT INTO application_tasks (application_id, kind, title, due_date, version)
                    SELECT a.id, 'follow_up', 'Follow up', a.next_action_date, 1
                    FROM applications AS a
                    WHERE a.next_action_date IS NOT NULL
                      AND a.status != 'Rejected'
                      AND NOT EXISTS (
                        SELECT 1 FROM application_tasks AS t
                        WHERE t.application_id = a.id AND t.due_date = a.next_action_date
                      )
                    """
                )
                connection.execute("UPDATE applications SET next_action_date = NULL, waiting_until = NULL WHERE stage = 'Closed'")
                connection.execute(
                    """
                    UPDATE applications
                    SET next_action_date = (
                        SELECT MIN(t.due_date) FROM application_tasks AS t
                        WHERE t.application_id = applications.id AND t.completed_at IS NULL
                    )
                    WHERE stage != 'Closed'
                    """
                )
            if version < 7:
                # Requirements are additive. The CREATE IF NOT EXISTS in the
                # schema makes this migration safe for empty and v6 stores,
                # while keeping the original data untouched.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS application_requirements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                        criterion TEXT NOT NULL,
                        category TEXT NOT NULL,
                        assessment TEXT NOT NULL DEFAULT 'unknown',
                        evidence TEXT NOT NULL DEFAULT '',
                        weight INTEGER NOT NULL DEFAULT 1,
                        position INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_application_requirements_order "
                    "ON application_requirements(application_id, position, id)"
                )
            if version < 8:
                # Material metadata and submission snapshots are additive. The
                # CREATE IF NOT EXISTS statements in SCHEMA make this safe for
                # both v7 stores and databases created directly at v8.
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_application_artifacts_application "
                    "ON application_artifacts(application_id, created_at ASC, id ASC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_submission_packages_application "
                    "ON submission_packages(application_id, submitted_at DESC, id DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_submission_package_items_artifact "
                    "ON submission_package_items(artifact_id)"
                )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_applications_stage ON applications(stage)")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_application_events_request_id "
                "ON application_events(request_id) WHERE request_id IS NOT NULL"
            )
            if seed and is_new_database:
                self._seed(connection)

    def _seed(self, connection: sqlite3.Connection) -> None:
        today = date.today()
        examples = [
            ("Northstar Labs", "Python Backend Developer", "Worldwide", "Remote", "Interview", "LinkedIn", "https://example.com/jobs/northstar", 32, 48, "Hourly", "USD", str(today - timedelta(days=8)), str(today + timedelta(days=1)), "Prepare API design examples and questions for the engineering team."),
            ("Lumen AI", "AI Code Evaluator — Mandarin", "Japan", "Remote", "Applied", "Company site", "https://example.com/jobs/lumen", 28, 40, "Hourly", "USD", str(today - timedelta(days=3)), str(today + timedelta(days=4)), "Submitted coding assessment. Follow up if there is no response."),
            ("Sora Systems", "Junior Full-Stack Engineer", "Tokyo, Japan", "Hybrid", "Wishlist", "Referral", "https://example.com/jobs/sora", 4200000, 5500000, "Annual", "JPY", None, str(today + timedelta(days=2)), "Tailor portfolio summary to the product dashboard requirements."),
            ("Orbit QA", "Freelance Software Tester", "Worldwide", "Remote", "Offer", "Remote board", "https://example.com/jobs/orbit", 22, 28, "Hourly", "USD", str(today - timedelta(days=14)), str(today + timedelta(days=2)), "Review contractor agreement and weekly availability."),
            ("Maple Cloud", "Web Developer", "Singapore", "Remote", "Rejected", "LinkedIn", "https://example.com/jobs/maple", 3000, 4500, "Monthly", "USD", str(today - timedelta(days=25)), None, "Good practice interview; strengthen system-design examples."),
            ("Kite Data", "Technical Data Analyst", "Japan", "Remote", "Applied", "Company site", "https://example.com/jobs/kite", 250000, 350000, "Monthly", "JPY", str(today - timedelta(days=1)), str(today + timedelta(days=6)), "Highlight SQL validation and structured-data experience."),
        ]
        legacy_fields = (
            "company", "role", "location", "work_mode", "status", "source", "url",
            "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes",
        )
        canonical = [self._canonicalize(dict(zip(legacy_fields, example))) for example in examples]
        connection.executemany(
            f"INSERT INTO applications ({', '.join(FIELDS)}) VALUES ({', '.join('?' for _ in FIELDS)})",
            [[record.get(field) for field in FIELDS] for record in canonical],
        )
        inserted = connection.execute("SELECT id FROM applications ORDER BY id DESC LIMIT ?", (len(canonical),)).fetchall()
        for row, record in zip(reversed(inserted), canonical):
            application_id = int(row["id"])
            event_at = lambda days_ago: f"{today - timedelta(days=days_ago)}T09:00:00+00:00"
            stage = record.get("stage") or "Wishlist"
            if stage == "Wishlist":
                self._insert_event(
                    connection, application_id, "custom", "Added to shortlist",
                    "Seed activity for the local demo.", event_at(2), to_stage="Wishlist",
                    payload={"to_stage": "Wishlist"},
                )
            else:
                self._insert_event(
                    connection, application_id, "applied", "Application submitted",
                    "Seed activity for the local demo.", event_at(25 if stage == "Closed" else 14 if stage == "Offer" else 10 if stage == "Interview" else 3),
                    to_stage="Applied", payload={"to_stage": "Applied"},
                )
                if stage in {"Interview", "Offer", "Closed"}:
                    self._insert_event(
                        connection, application_id, "status_changed", "Interview completed",
                        "Seed activity for the local demo.", event_at(20 if stage == "Closed" else 10 if stage == "Offer" else 6),
                        from_stage="Applied", to_stage="Interview",
                        payload={"from_stage": "Applied", "to_stage": "Interview"},
                    )
                if stage in {"Offer", "Closed"}:
                    self._insert_event(
                        connection, application_id, "status_changed", "Offer received" if stage == "Offer" else "Offer declined",
                        "Seed activity for the local demo.", event_at(5 if stage == "Offer" else 18),
                        from_stage="Interview", to_stage="Offer",
                        payload={"from_stage": "Interview", "to_stage": "Offer"},
                    )
                if stage == "Closed":
                    self._insert_event(
                        connection, application_id, "status_changed", "Application rejected",
                        "Seed activity for the local demo.", event_at(16),
                        from_stage="Offer", to_stage="Closed", payload={"from_stage": "Offer", "to_stage": "Closed", "outcome": "Rejected"},
                    )
            if record.get("next_action_date") and record.get("stage") != "Closed":
                self._create_task_in_connection(
                    connection,
                    application_id,
                    kind="follow_up",
                    title="Follow up",
                    due_date=record["next_action_date"],
                )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def list_applications(self, filters: dict[str, str]) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        for field in ("status", "stage", "work_mode"):
            if filters.get(field):
                clauses.append(f"{field} = ?")
                values.append(filters[field])
        if filters.get("search"):
            clauses.append(
                "(company LIKE ? ESCAPE '\\' OR role LIKE ? ESCAPE '\\' "
                "OR location LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')"
            )
            literal = filters["search"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            search = f"%{literal}%"
            values.extend([search] * 4)

        view = filters.get("view", "all")
        if view == "active":
            clauses.append("status IN ('Applied', 'Interview', 'Offer')")
        elif view == "follow-up":
            clauses.append("next_action_date IS NOT NULL AND next_action_date <= ? AND status != 'Rejected'")
            values.append(str(date.today() + timedelta(days=7)))
        elif view == "interview":
            clauses.append("status = 'Interview'")
        elif view == "offers":
            clauses.append("status = 'Offer'")

        allowed_sort = {"updated_at", "applied_date", "next_action_date", "company", "status", "stage"}
        sort = filters.get("sort", "updated_at")
        if sort not in allowed_sort:
            sort = "updated_at"
        direction = "ASC" if filters.get("direction", "desc").lower() == "asc" else "DESC"
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        nulls_last = f"CASE WHEN {sort} IS NULL THEN 1 ELSE 0 END, " if sort in {"applied_date", "next_action_date"} else ""
        limit = int(filters.get("limit", "20"))
        page = int(filters.get("page", "1"))
        offset = (page - 1) * limit
        query = (
            f"SELECT * FROM applications{where} "
            f"ORDER BY {nulls_last}{sort} {direction}, id DESC LIMIT ? OFFSET ?"
        )
        with self.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM applications{where}", values).fetchone()[0]
            rows = connection.execute(query, [*values, limit, offset]).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "count": len(rows),
                "total": total,
                "page": page,
                "page_size": limit,
            }

    def get_application(self, application_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._row(connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone())

    def get_workspace(self, application_id: int) -> dict[str, Any] | None:
        """Return one consistent snapshot for the application workspace drawer."""
        with self.connect() as connection:
            application_row = connection.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            if application_row is None:
                return None
            open_tasks = connection.execute(
                "SELECT * FROM application_tasks WHERE application_id = ? AND completed_at IS NULL "
                "ORDER BY due_date ASC, id ASC",
                (application_id,),
            ).fetchall()
            completed_tasks = connection.execute(
                "SELECT * FROM application_tasks WHERE application_id = ? AND completed_at IS NOT NULL "
                "ORDER BY completed_at DESC, id DESC",
                (application_id,),
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM application_events WHERE application_id = ? "
                "ORDER BY occurred_at DESC, id DESC",
                (application_id,),
            ).fetchall()
            requirements = connection.execute(
                "SELECT * FROM application_requirements WHERE application_id = ? "
                "ORDER BY position ASC, id ASC",
                (application_id,),
            ).fetchall()
            open_items = [dict(row) for row in open_tasks]
            completed_items = [dict(row) for row in completed_tasks]
            event_items = [dict(row) for row in events]
            requirement_items = [dict(row) for row in requirements]
            artifact_items = [dict(row) for row in connection.execute(
                "SELECT * FROM application_artifacts WHERE application_id = ? ORDER BY created_at ASC, id ASC",
                (application_id,),
            ).fetchall()]
            submission_items = self._list_submissions_in_connection(connection, application_id)
            return {
                "application": dict(application_row),
                "open_tasks": open_items,
                "completed_tasks": completed_items,
                "events": event_items,
                "requirements": requirement_items,
                "requirement_summary": self.summarize_requirements(requirement_items),
                "artifacts": artifact_items,
                "submissions": submission_items,
                "summary": {
                    "open_tasks": len(open_items),
                    "completed_tasks": len(completed_items),
                    "activity_count": len(event_items),
                    "next_task": open_items[0] if open_items else None,
                },
            }

    def list_artifacts(self, application_id: int) -> list[dict[str, Any]] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            rows = connection.execute(
                "SELECT * FROM application_artifacts WHERE application_id = ? ORDER BY created_at ASC, id ASC",
                (application_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_artifact(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            cursor = connection.execute(
                "INSERT INTO application_artifacts (application_id, kind, label, uri, version_label, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    application_id, data["kind"], data["label"], data.get("uri", ""),
                    data.get("version_label", ""), data.get("notes", ""),
                ),
            )
            return dict(connection.execute("SELECT * FROM application_artifacts WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def update_artifact(self, artifact_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM application_artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if row is None:
                return None
            changes = {field: data[field] for field in ("kind", "label", "uri", "version_label", "notes") if field in data}
            if not changes:
                return dict(row)
            assignments = ", ".join(f"{field} = ?" for field in changes)
            values = [changes[field] for field in changes] + [artifact_id]
            connection.execute(
                f"UPDATE application_artifacts SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            return dict(connection.execute("SELECT * FROM application_artifacts WHERE id = ?", (artifact_id,)).fetchone())

    def delete_artifact(self, artifact_id: int) -> bool:
        with self.connect() as connection:
            references = connection.execute(
                "SELECT DISTINCT package_id FROM submission_package_items WHERE artifact_id = ? ORDER BY package_id ASC",
                (artifact_id,),
            ).fetchall()
            if references:
                raise ProtectedArtifactError([int(row["package_id"]) for row in references])
            return connection.execute("DELETE FROM application_artifacts WHERE id = ?", (artifact_id,)).rowcount > 0

    @staticmethod
    def _submission_from_connection(connection: sqlite3.Connection, package_id: int) -> dict[str, Any] | None:
        package = connection.execute("SELECT * FROM submission_packages WHERE id = ?", (package_id,)).fetchone()
        if package is None:
            return None
        items = connection.execute(
            "SELECT * FROM submission_package_items WHERE package_id = ? ORDER BY position ASC, artifact_id ASC",
            (package_id,),
        ).fetchall()
        result = dict(package)
        result["items"] = [dict(item) for item in items]
        return result

    def _list_submissions_in_connection(self, connection: sqlite3.Connection, application_id: int) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id FROM submission_packages WHERE application_id = ? ORDER BY submitted_at DESC, id DESC",
            (application_id,),
        ).fetchall()
        return [self._submission_from_connection(connection, int(row["id"])) for row in rows]

    def list_submissions(self, application_id: int) -> list[dict[str, Any]] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            return self._list_submissions_in_connection(connection, application_id)

    def get_submission(self, submission_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._submission_from_connection(connection, submission_id)

    def create_submission(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            artifact_ids = data["artifact_ids"]
            if not artifact_ids:
                raise ValueError("Choose at least one material.")
            placeholders = ", ".join("?" for _ in artifact_ids)
            rows = connection.execute(
                f"SELECT * FROM application_artifacts WHERE application_id = ? AND id IN ({placeholders})",
                [application_id, *artifact_ids],
            ).fetchall()
            by_id = {int(row["id"]): row for row in rows}
            missing = [artifact_id for artifact_id in artifact_ids if artifact_id not in by_id]
            if missing:
                raise ValueError("Every selected material must belong to this application.")
            submitted_at = data.get("submitted_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
            cursor = connection.execute(
                "INSERT INTO submission_packages (application_id, submitted_at, notes) VALUES (?, ?, ?)",
                (application_id, submitted_at, data.get("notes", "")),
            )
            package_id = int(cursor.lastrowid)
            for position, artifact_id in enumerate(artifact_ids):
                artifact = by_id[artifact_id]
                connection.execute(
                    "INSERT INTO submission_package_items "
                    "(package_id, artifact_id, position, snapshot_kind, snapshot_label, snapshot_uri, snapshot_version_label, snapshot_notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        package_id, artifact_id, position, artifact["kind"], artifact["label"], artifact["uri"],
                        artifact["version_label"], artifact["notes"],
                    ),
                )
            return self._submission_from_connection(connection, package_id)

    @staticmethod
    def summarize_requirements(requirements: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate transparent requirement coverage from visible rows."""
        counts = {assessment: 0 for assessment in REQUIREMENT_ASSESSMENTS}
        known_weight = 0
        covered_weight = 0.0
        missing_evidence_met = 0
        for requirement in requirements:
            assessment = requirement.get("assessment", "unknown")
            if assessment not in counts:
                assessment = "unknown"
            counts[assessment] += 1
            weight = int(requirement.get("weight") or 1)
            if assessment == "unknown":
                continue
            known_weight += weight
            if assessment == "met":
                covered_weight += weight
                if not str(requirement.get("evidence") or "").strip():
                    missing_evidence_met += 1
            elif assessment == "partial":
                covered_weight += weight * 0.5
        coverage = round((covered_weight / known_weight) * 100, 1) if known_weight else None
        return {
            "total": len(requirements),
            "counts": counts,
            "known_count": sum(count for assessment, count in counts.items() if assessment != "unknown"),
            "known_weight": known_weight,
            "covered_weight": covered_weight,
            "coverage": coverage,
            "known_weight_coverage": coverage,
            "missing_evidence_met": missing_evidence_met,
        }

    def list_requirements(self, application_id: int) -> list[dict[str, Any]] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            rows = connection.execute(
                "SELECT * FROM application_requirements WHERE application_id = ? ORDER BY position ASC, id ASC",
                (application_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def reorder_requirements(self, application_id: int, ordered_ids: list[int]) -> list[dict[str, Any]] | None:
        """Persist a complete, validated display order in one transaction."""
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            rows = connection.execute(
                "SELECT id FROM application_requirements WHERE application_id = ? ORDER BY position ASC, id ASC",
                (application_id,),
            ).fetchall()
            current_ids = [int(row["id"]) for row in rows]
            if len(ordered_ids) != len(current_ids) or set(ordered_ids) != set(current_ids):
                raise ValueError("ordered_ids must contain each application requirement exactly once.")
            for position, requirement_id in enumerate(ordered_ids):
                connection.execute(
                    "UPDATE application_requirements SET position = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND application_id = ?",
                    (position, requirement_id, application_id),
                )
            reordered = connection.execute(
                "SELECT * FROM application_requirements WHERE application_id = ? ORDER BY position ASC, id ASC",
                (application_id,),
            ).fetchall()
            return [dict(row) for row in reordered]

    def create_requirement(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            cursor = connection.execute(
                "INSERT INTO application_requirements "
                "(application_id, criterion, category, assessment, evidence, weight, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    application_id, data["criterion"], data["category"], data["assessment"],
                    data.get("evidence", ""), data.get("weight", 1), data.get("position", 0),
                ),
            )
            return dict(connection.execute("SELECT * FROM application_requirements WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def update_requirement(self, requirement_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM application_requirements WHERE id = ?", (requirement_id,)).fetchone()
            if row is None:
                return None
            changes = {field: data[field] for field in ("criterion", "category", "assessment", "evidence", "weight", "position") if field in data}
            if not changes:
                return dict(row)
            assignments = ", ".join(f"{field} = ?" for field in changes)
            values = [changes[field] for field in changes] + [requirement_id]
            connection.execute(
                f"UPDATE application_requirements SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            return dict(connection.execute("SELECT * FROM application_requirements WHERE id = ?", (requirement_id,)).fetchone())

    def delete_requirement(self, requirement_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute("DELETE FROM application_requirements WHERE id = ?", (requirement_id,)).rowcount > 0

    @staticmethod
    def _canonicalize(data: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fill v4 lifecycle fields while accepting a v3-shaped record."""
        merged = {
            "location": "", "source": "", "url": "", "notes": "",
            "salary_min": None, "salary_max": None, "salary_period": "Annual", "currency": "USD",
            "applied_date": None, "next_action_date": None, "waiting_until": None,
            **(existing or {}), **data,
        }
        status = merged.get("status")
        stage = merged.get("stage")
        if "stage" in data:
            stage = data.get("stage")
        elif "status" in data and data.get("status") in LEGACY_STATUS_TO_STAGE:
            stage = LEGACY_STATUS_TO_STAGE[data["status"]]
        if not stage and status in LEGACY_STATUS_TO_STAGE:
            stage = LEGACY_STATUS_TO_STAGE[status]
        if stage not in STAGE_TO_LEGACY_STATUS:
            stage = "Wishlist"
        # The deprecated status column is always a projection of stage. This
        # prevents direct database callers from creating contradictory pairs.
        status = STAGE_TO_LEGACY_STATUS[stage]
        outcome = merged.get("outcome")
        closed_at = merged.get("closed_at")
        if stage == "Closed":
            outcome = outcome or ("Rejected" if status == "Rejected" else None)
            closed_at = closed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
            merged["next_action_date"] = None
            merged["waiting_until"] = None
        else:
            outcome = None
            closed_at = None
        version = merged.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            version = 1
        canonical = dict(merged)
        canonical.update({"status": status, "stage": stage, "outcome": outcome, "version": version, "closed_at": closed_at})
        return canonical

    def create_application(self, data: dict[str, Any]) -> dict[str, Any]:
        data = self._canonicalize(data)
        values = [data.get(field) for field in FIELDS]
        with self.connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO applications ({', '.join(FIELDS)}) VALUES ({', '.join('?' for _ in FIELDS)})",
                values,
            )
            event_type = "applied" if data.get("status") != "Wishlist" else "custom"
            self._insert_event(
                connection,
                int(cursor.lastrowid),
                event_type,
                "Application added",
                "Application entered the JobFlow workspace.",
                origin="system",
                to_stage=data.get("stage"),
                payload={"to_stage": data.get("stage"), "outcome": data.get("outcome")},
            )
            if data.get("next_action_date") and data.get("stage") != "Closed":
                self._create_task_in_connection(
                    connection,
                    int(cursor.lastrowid),
                    kind="follow_up",
                    title="Follow up",
                    due_date=data["next_action_date"],
                )
            row = connection.execute("SELECT * FROM applications WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def update_application(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        if not data:
            return self.get_application(application_id)
        with self.connect() as connection:
            existing = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
            if existing is None:
                return None
            old = dict(existing)
            next_action_requested = "next_action_date" in data
            data = self._canonicalize(data, old)
            target_stage = data.get("stage")
            is_closed = target_stage == "Closed" or old.get("stage") == "Closed"
            lifecycle_changed = (
                data.get("stage") != old.get("stage")
                or data.get("outcome") != old.get("outcome")
                or data.get("closed_at") != old.get("closed_at")
            )
            if lifecycle_changed and data.get("stage") != old.get("stage"):
                self._transition_in_connection(
                    connection,
                    application_id,
                    to_stage=data["stage"],
                    outcome=data.get("outcome"),
                    occurred_at=data.get("closed_at") if data.get("stage") == "Closed" else None,
                    expected_version=None,
                    request_id=None,
                    origin="system",
                )
                # The transition service owns lifecycle columns and the one
                # version increment. Update only the ordinary record fields
                # here so legacy PATCH remains atomic and auditable.
                data = {field: value for field, value in data.items() if field in FIELDS and field not in {"status", "stage", "outcome", "version", "closed_at"}}
                if target_stage == "Closed":
                    data.pop("next_action_date", None)
                    data.pop("waiting_until", None)
                    next_action_requested = False
            elif lifecycle_changed:
                # A PATCH that changes a closed outcome without changing its
                # stage is still a lifecycle mutation and must be visible in
                # the immutable timeline.
                data["version"] = max(1, int(old.get("version") or 1)) + 1
                assignments = ", ".join(f"{field} = ?" for field in ("outcome", "closed_at", "version"))
                values = [data.get("outcome"), data.get("closed_at"), data["version"], application_id]
                connection.execute(
                    f"UPDATE applications SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    values,
                )
                self._insert_event(
                    connection,
                    application_id,
                    "status_changed",
                    "Application outcome updated",
                    f"Previous outcome: {old.get('outcome') or 'None'}",
                    from_stage=old.get("stage"),
                    to_stage=old.get("stage"),
                    origin="user",
                    payload={"previous_outcome": old.get("outcome"), "outcome": data.get("outcome")},
                )
                data = {field: value for field, value in data.items() if field in FIELDS and field not in {"status", "stage", "outcome", "version", "closed_at"}}
            else:
                data["version"] = max(1, int(old.get("version") or 1)) + 1

            if data:
                assignments = ", ".join(f"{field} = ?" for field in data if field in FIELDS)
                values = [data[field] for field in data if field in FIELDS] + [application_id]
                cursor = connection.execute(
                    f"UPDATE applications SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    values,
                )
                if cursor.rowcount == 0:
                    return None
            if next_action_requested and not is_closed and data.get("next_action_date") != old["next_action_date"]:
                requested_date = data.get("next_action_date")
                follow_up = connection.execute(
                    "SELECT * FROM application_tasks WHERE application_id = ? AND completed_at IS NULL "
                    "AND kind = 'follow_up' ORDER BY due_date ASC, id ASC LIMIT 1",
                    (application_id,),
                ).fetchone()
                if requested_date:
                    if follow_up:
                        connection.execute(
                            "UPDATE application_tasks SET due_date = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (requested_date, follow_up["id"]),
                        )
                    else:
                        self._create_task_in_connection(
                            connection,
                            application_id,
                            kind="follow_up",
                            title="Follow up",
                            due_date=requested_date,
                        )
                else:
                    self._complete_open_tasks(connection, application_id)
                self._sync_next_action_date(connection, application_id)
                next_action = requested_date or "No date"
                self._insert_event(
                    connection,
                    application_id,
                    "follow_up",
                    "Follow-up date updated",
                    f"Next action: {next_action}",
                    origin="user",
                )
            if "notes" in data and data["notes"] != old["notes"]:
                self._insert_event(
                    connection,
                    application_id,
                    "note",
                    "Notes updated",
                    data["notes"] or "Notes cleared.",
                    origin="user",
                )
            row = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
            return dict(row)

    def delete_application(self, application_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute("DELETE FROM applications WHERE id = ?", (application_id,)).rowcount > 0

    @staticmethod
    def _sync_next_action_date(connection: sqlite3.Connection, application_id: int) -> None:
        row = connection.execute(
            "SELECT MIN(due_date) AS due_date FROM application_tasks WHERE application_id = ? AND completed_at IS NULL",
            (application_id,),
        ).fetchone()
        connection.execute(
            "UPDATE applications SET next_action_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (row["due_date"] if row else None, application_id),
        )

    @staticmethod
    def _complete_open_tasks(connection: sqlite3.Connection, application_id: int, *, completed_at: str | None = None) -> None:
        timestamp = completed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "UPDATE application_tasks SET completed_at = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE application_id = ? AND completed_at IS NULL",
            (timestamp, application_id),
        )
        Database._sync_next_action_date(connection, application_id)

    @staticmethod
    def _create_task_in_connection(
        connection: sqlite3.Connection,
        application_id: int,
        *,
        kind: str,
        title: str,
        due_date: str,
        completed_at: str | None = None,
        version: int = 1,
    ) -> dict[str, Any]:
        cursor = connection.execute(
            "INSERT INTO application_tasks (application_id, kind, title, due_date, completed_at, version) VALUES (?, ?, ?, ?, ?, ?)",
            (application_id, kind, title, due_date, completed_at, version),
        )
        Database._sync_next_action_date(connection, application_id)
        row = connection.execute("SELECT * FROM application_tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def list_tasks(self, application_id: int, *, include_completed: bool = True) -> list[dict[str, Any]] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            where = "" if include_completed else " AND completed_at IS NULL"
            rows = connection.execute(
                f"SELECT * FROM application_tasks WHERE application_id = ?{where} ORDER BY CASE WHEN completed_at IS NULL THEN 0 ELSE 1 END, due_date ASC, id ASC",
                (application_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_task(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            application = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
            if application is None:
                return None
            if application["stage"] == "Closed":
                raise TransitionError("Closed applications cannot receive new tasks.", "application_id")
            return self._create_task_in_connection(
                connection,
                application_id,
                kind=data["kind"],
                title=data["title"],
                due_date=data["due_date"],
                completed_at=data.get("completed_at"),
                version=data.get("version", 1),
            )

    def update_task(self, task_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM application_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            current = dict(row)
            expected = data.get("expected_version")
            if expected is not None and int(current["version"]) != expected:
                raise VersionConflict(current)
            changes = {field: data[field] for field in ("kind", "title", "due_date", "completed_at") if field in data}
            if not changes:
                return current
            application = connection.execute(
                "SELECT stage FROM applications WHERE id = ?", (current["application_id"],)
            ).fetchone()
            if (
                application and application["stage"] == "Closed"
                and changes.get("completed_at") is None
                and current.get("completed_at")
            ):
                raise TransitionError("Closed applications cannot reopen completed tasks.", "completed_at")
            next_version = int(current["version"]) + 1
            assignments = ", ".join(f"{field} = ?" for field in changes)
            values = [changes[field] for field in changes] + [next_version, task_id]
            connection.execute(
                f"UPDATE application_tasks SET {assignments}, version = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            self._sync_next_action_date(connection, int(current["application_id"]))
            updated = connection.execute("SELECT * FROM application_tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(updated)

    def complete_task(self, task_id: int, *, expected_version: int | None = None) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM application_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            current = dict(row)
            if current["completed_at"]:
                return current
            if expected_version is not None and int(current["version"]) != expected_version:
                raise VersionConflict(current)
            connection.execute(
                "UPDATE application_tasks SET completed_at = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND completed_at IS NULL",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), task_id),
            )
            self._sync_next_action_date(connection, int(current["application_id"]))
            return dict(connection.execute("SELECT * FROM application_tasks WHERE id = ?", (task_id,)).fetchone())

    def snooze_task(self, task_id: int, *, due_date: str, expected_version: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM application_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            current = dict(row)
            if int(current["version"]) != expected_version:
                raise VersionConflict(current)
            if current["completed_at"]:
                raise TransitionError("Completed tasks cannot be snoozed.", "task_id")
            connection.execute(
                "UPDATE application_tasks SET due_date = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (due_date, task_id),
            )
            self._sync_next_action_date(connection, int(current["application_id"]))
            return dict(connection.execute("SELECT * FROM application_tasks WHERE id = ?", (task_id,)).fetchone())

    def today(self, as_of: str) -> dict[str, Any]:
        try:
            date.fromisoformat(as_of)
        except (TypeError, ValueError) as error:
            raise ValueError("as_of must use ISO format YYYY-MM-DD") from error
        with self.connect() as connection:
            tasks = connection.execute(
                """
                SELECT t.*, a.company, a.role, a.stage, a.status
                FROM application_tasks AS t JOIN applications AS a ON a.id = t.application_id
                WHERE t.completed_at IS NULL AND a.stage != 'Closed'
                ORDER BY t.due_date ASC, t.id ASC
                """
            ).fetchall()
            active = connection.execute(
                """
                SELECT a.* FROM applications AS a
                WHERE a.stage IN ('Ready', 'Applied', 'Interview', 'Offer')
                  AND a.waiting_until IS NOT NULL AND a.waiting_until > ?
                ORDER BY a.waiting_until ASC, a.id ASC
                """ ,
                (as_of,),
            ).fetchall()
            missing = connection.execute(
                """
                SELECT a.* FROM applications AS a
                WHERE a.stage IN ('Ready', 'Applied', 'Interview', 'Offer') AND a.waiting_until IS NULL
                  AND NOT EXISTS (SELECT 1 FROM application_tasks AS t WHERE t.application_id = a.id AND t.completed_at IS NULL)
                ORDER BY a.updated_at DESC, a.id DESC
                """
            ).fetchall()
        result = {"overdue": [], "due_today": [], "upcoming": [], "waiting": [dict(row) for row in active], "missing_next_step": [dict(row) for row in missing], "as_of": as_of}
        for row in tasks:
            item = dict(row)
            if item["due_date"] < as_of:
                result["overdue"].append(item)
            elif item["due_date"] == as_of:
                result["due_today"].append(item)
            else:
                result["upcoming"].append(item)
        return result

    @staticmethod
    def _transition_in_connection(
        connection: sqlite3.Connection,
        application_id: int,
        *,
        to_stage: str,
        outcome: str | None,
        occurred_at: str | None,
        expected_version: int | None,
        request_id: str | None,
        origin: str,
    ) -> dict[str, Any] | None:
        """Apply one lifecycle transition and its event in one transaction."""
        row = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
        if row is None:
            return None
        current = dict(row)
        if request_id:
            previous_event = connection.execute(
                "SELECT * FROM application_events WHERE request_id = ?", (request_id,)
            ).fetchone()
            if previous_event is not None:
                if int(previous_event["application_id"]) != application_id:
                    raise RequestIdConflict(int(previous_event["application_id"]))
                return {
                    "application": current,
                    "event": dict(previous_event),
                    "replayed": True,
                }
        if expected_version is not None and int(current.get("version") or 1) != expected_version:
            raise VersionConflict(current)
        from_stage = current.get("stage") or LEGACY_STATUS_TO_STAGE.get(current.get("status"), "Wishlist")
        if to_stage not in STAGES:
            raise TransitionError("Choose a valid target stage.", "to_stage")
        if from_stage not in ALLOWED_TRANSITIONS:
            raise TransitionError("The current application stage is invalid.")
        if to_stage == from_stage:
            raise TransitionError("The application is already in this stage.", "to_stage")
        if to_stage not in ALLOWED_TRANSITIONS[from_stage]:
            raise TransitionError(f"Cannot move an application from {from_stage} to {to_stage}.", "to_stage")
        if to_stage == "Closed" and not outcome:
            raise TransitionError("Closing an application requires an outcome.", "outcome")
        if to_stage != "Closed" and outcome:
            raise TransitionError("Only Closed applications can have an outcome.", "outcome")
        timestamp = occurred_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        closed_at = timestamp if to_stage == "Closed" else None
        next_version = max(1, int(current.get("version") or 1)) + 1
        status = STAGE_TO_LEGACY_STATUS[to_stage]
        connection.execute(
            "UPDATE applications SET stage = ?, status = ?, outcome = ?, closed_at = ?, version = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (to_stage, status, outcome if to_stage == "Closed" else None, closed_at, next_version, application_id),
        )
        if to_stage == "Closed":
            Database._complete_open_tasks(connection, application_id, completed_at=timestamp)
            connection.execute("UPDATE applications SET waiting_until = NULL WHERE id = ?", (application_id,))
        event_id = Database._insert_event(
            connection,
            application_id,
            "status_changed",
            f"Stage changed to {to_stage}",
            f"Previous stage: {from_stage}",
            timestamp,
            from_stage=from_stage,
            to_stage=to_stage,
            origin=origin,
            payload={
                "from_stage": from_stage,
                "to_stage": to_stage,
                "outcome": outcome,
                "expected_version": expected_version,
            },
            request_id=request_id,
        )
        updated = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
        event = connection.execute("SELECT * FROM application_events WHERE id = ?", (event_id,)).fetchone()
        return {"application": dict(updated), "event": dict(event), "replayed": False}

    def transition_application(self, application_id: int, data: dict[str, Any], *, origin: str = "system") -> dict[str, Any] | None:
        """Transition an application with optimistic concurrency and idempotency."""
        with self.connect() as connection:
            return self._transition_in_connection(
                connection,
                application_id,
                to_stage=data["to_stage"],
                outcome=data.get("outcome"),
                occurred_at=data.get("occurred_at"),
                expected_version=data.get("expected_version"),
                request_id=data.get("request_id"),
                origin=origin,
            )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        application_id: int,
        event_type: str,
        title: str,
        details: str = "",
        occurred_at: str | None = None,
        *,
        from_stage: str | None = None,
        to_stage: str | None = None,
        origin: str = "system",
        payload: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> int:
        timestamp = occurred_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        if origin not in EVENT_ORIGINS:
            raise ValueError(f"Unsupported event origin: {origin}")
        serialized_payload = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        cursor = connection.execute(
            "INSERT INTO application_events "
            "(application_id, event_type, title, details, occurred_at, from_stage, to_stage, origin, payload_json, request_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (application_id, event_type, title, details, timestamp, from_stage, to_stage, origin, serialized_payload, request_id),
        )
        return int(cursor.lastrowid)

    def list_events(self, application_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM application_events WHERE application_id = ? ORDER BY occurred_at DESC, id DESC",
                (application_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_event(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM applications WHERE id = ?", (application_id,)).fetchone() is None:
                return None
            self._insert_event(
                connection,
                application_id,
                data["event_type"],
                data["title"],
                data.get("details", ""),
                data.get("occurred_at"),
                from_stage=data.get("from_stage"),
                to_stage=data.get("to_stage"),
                origin="user",
                payload=data.get("payload_json") if isinstance(data.get("payload_json"), dict) else None,
                request_id=data.get("request_id"),
            )
            row = connection.execute("SELECT * FROM application_events WHERE id = last_insert_rowid()").fetchone()
            return dict(row)

    def delete_event(self, application_id: int, event_id: int) -> bool:
        with self.connect() as connection:
            event = connection.execute(
                "SELECT origin FROM application_events WHERE id = ? AND application_id = ?",
                (event_id, application_id),
            ).fetchone()
            if event is None:
                return False
            origin = event["origin"] or "legacy"
            if origin != "user":
                raise ProtectedEventError(origin)
            return connection.execute(
                "DELETE FROM application_events WHERE id = ? AND application_id = ?",
                (event_id, application_id),
            ).rowcount > 0

    def export_applications(self) -> list[dict[str, Any]]:
        """Return all application records in stable creation order for backup."""
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM applications ORDER BY id ASC").fetchall()
            applications = [dict(row) for row in rows]
            events = connection.execute(
                "SELECT * FROM application_events ORDER BY application_id ASC, occurred_at ASC, id ASC"
            ).fetchall()
            events_by_application: dict[int, list[dict[str, Any]]] = {}
            for event in events:
                events_by_application.setdefault(int(event["application_id"]), []).append(dict(event))
            tasks = connection.execute(
                "SELECT * FROM application_tasks ORDER BY application_id ASC, due_date ASC, id ASC"
            ).fetchall()
            tasks_by_application: dict[int, list[dict[str, Any]]] = {}
            for task in tasks:
                tasks_by_application.setdefault(int(task["application_id"]), []).append(dict(task))
            requirements = connection.execute(
                "SELECT * FROM application_requirements ORDER BY application_id ASC, position ASC, id ASC"
            ).fetchall()
            requirements_by_application: dict[int, list[dict[str, Any]]] = {}
            for requirement in requirements:
                requirements_by_application.setdefault(int(requirement["application_id"]), []).append(dict(requirement))
            artifacts = connection.execute(
                "SELECT * FROM application_artifacts ORDER BY application_id ASC, created_at ASC, id ASC"
            ).fetchall()
            artifacts_by_application: dict[int, list[dict[str, Any]]] = {}
            for artifact in artifacts:
                artifacts_by_application.setdefault(int(artifact["application_id"]), []).append(dict(artifact))
            submissions = connection.execute(
                "SELECT * FROM submission_packages ORDER BY application_id ASC, submitted_at ASC, id ASC"
            ).fetchall()
            submissions_by_application: dict[int, list[dict[str, Any]]] = {}
            for submission in submissions:
                package = dict(submission)
                items = connection.execute(
                    "SELECT * FROM submission_package_items WHERE package_id = ? ORDER BY position ASC, artifact_id ASC",
                    (submission["id"],),
                ).fetchall()
                package["items"] = [dict(item) for item in items]
                submissions_by_application.setdefault(int(submission["application_id"]), []).append(package)
            for application in applications:
                application["events"] = events_by_application.get(int(application["id"]), [])
                application["tasks"] = tasks_by_application.get(int(application["id"]), [])
                application["requirements"] = requirements_by_application.get(int(application["id"]), [])
                application["artifacts"] = artifacts_by_application.get(int(application["id"]), [])
                application["submissions"] = submissions_by_application.get(int(application["id"]), [])
            return applications

    @staticmethod
    def _merge_application_in_connection(
        connection: sqlite3.Connection,
        application_id: int,
        record: dict[str, Any],
        fields: list[str],
    ) -> bool:
        """Apply explicit non-empty import fields while keeping the write atomic."""
        row = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
        if row is None:
            raise ValueError("Merge target application was not found.")
        old = dict(row)
        mergeable = {
            "company", "role", "location", "work_mode", "source", "url", "salary_min",
            "salary_max", "salary_period", "currency", "applied_date", "notes",
        }
        changes = {
            field: record.get(field)
            for field in fields
            if field in mergeable and record.get(field) not in (None, "")
        }
        if not changes:
            return False
        merged = dict(old)
        merged.update(changes)
        canonical = Database._canonicalize(merged, old)
        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = [canonical[field] for field in changes] + [application_id]
        connection.execute(
            f"UPDATE applications SET {assignments}, version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )
        Database._insert_event(
            connection,
            application_id,
            "custom",
            "Application merged from import",
            "Explicit non-empty fields from an import row were applied.",
            origin="import",
            payload={"fields": list(changes)},
        )
        return True

    def import_applications(
        self,
        records: list[dict[str, Any]],
        *,
        replace: bool = False,
        merge_records: list[tuple[int, dict[str, Any], list[str]]] | None = None,
    ) -> int:
        """Insert a validated backup atomically, optionally merging explicit rows."""
        with self.connect() as connection:
            if replace:
                connection.execute("DELETE FROM applications")
            merged_count = 0
            for application_id, record, fields in merge_records or []:
                merged_count += int(self._merge_application_in_connection(connection, application_id, record, fields))
            placeholders = ", ".join("?" for _ in FIELDS)
            canonical_records = [self._canonicalize(record) for record in records]
            connection.executemany(
                f"INSERT INTO applications ({', '.join(FIELDS)}) VALUES ({placeholders})",
                [[record.get(field) for field in FIELDS] for record in canonical_records],
            )
            if canonical_records:
                imported_rows = connection.execute(
                    "SELECT id FROM applications ORDER BY id DESC LIMIT ?", (len(canonical_records),)
                ).fetchall()
                for row, record in zip(reversed(imported_rows), canonical_records):
                    application_id = int(row["id"])
                    raw_tasks = record.get("tasks") or []
                    if raw_tasks:
                        for task in raw_tasks:
                            self._create_task_in_connection(
                                connection,
                                application_id,
                                kind=task["kind"],
                                title=task["title"],
                                due_date=task["due_date"],
                                completed_at=task.get("completed_at"),
                                version=task.get("version", 1),
                            )
                    elif record.get("next_action_date") and record.get("stage") != "Closed":
                        self._create_task_in_connection(
                            connection,
                            application_id,
                            kind="follow_up",
                            title="Follow up",
                            due_date=record["next_action_date"],
                        )
                    self._sync_next_action_date(connection, application_id)
                    for requirement in record.get("requirements", []):
                        connection.execute(
                            "INSERT INTO application_requirements "
                            "(application_id, criterion, category, assessment, evidence, weight, position) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                application_id, requirement["criterion"], requirement["category"],
                                requirement["assessment"], requirement.get("evidence", ""),
                                requirement.get("weight", 1), requirement.get("position", 0),
                            ),
                        )
                    artifact_id_map: dict[int, int] = {}
                    artifact_rows_by_id: dict[int, sqlite3.Row] = {}
                    for artifact in record.get("artifacts", []):
                        cursor = connection.execute(
                            "INSERT INTO application_artifacts "
                            "(application_id, kind, label, uri, version_label, notes) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                application_id, artifact["kind"], artifact["label"], artifact.get("uri", ""),
                                artifact.get("version_label", ""), artifact.get("notes", ""),
                            ),
                        )
                        new_artifact_id = int(cursor.lastrowid)
                        raw_artifact_id = artifact.get("id")
                        if isinstance(raw_artifact_id, int) and not isinstance(raw_artifact_id, bool) and raw_artifact_id > 0:
                            artifact_id_map[raw_artifact_id] = new_artifact_id
                        artifact_rows_by_id[new_artifact_id] = connection.execute(
                            "SELECT * FROM application_artifacts WHERE id = ?", (new_artifact_id,)
                        ).fetchone()
                    for submission in record.get("submissions", []):
                        submitted_at = submission.get("submitted_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
                        cursor = connection.execute(
                            "INSERT INTO submission_packages (application_id, submitted_at, notes) VALUES (?, ?, ?)",
                            (application_id, submitted_at, submission.get("notes", "")),
                        )
                        package_id = int(cursor.lastrowid)
                        for position, item in enumerate(submission.get("items", [])):
                            mapped_artifact_id = artifact_id_map.get(item.get("artifact_id"))
                            if mapped_artifact_id is None:
                                raise ValueError("Submission package references an unknown material.")
                            artifact = artifact_rows_by_id[mapped_artifact_id]
                            connection.execute(
                                "INSERT INTO submission_package_items "
                                "(package_id, artifact_id, position, snapshot_kind, snapshot_label, snapshot_uri, snapshot_version_label, snapshot_notes) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    package_id, mapped_artifact_id, position,
                                    item.get("snapshot_kind", artifact["kind"]),
                                    item.get("snapshot_label", artifact["label"]),
                                    item.get("snapshot_uri", artifact["uri"]),
                                    item.get("snapshot_version_label", artifact["version_label"]),
                                    item.get("snapshot_notes", artifact["notes"]),
                                ),
                            )
                    for event in record.get("events", []):
                        self._insert_event(
                            connection,
                            application_id,
                            event["event_type"],
                            event["title"],
                            event.get("details", ""),
                            event.get("occurred_at"),
                            from_stage=event.get("from_stage"),
                            to_stage=event.get("to_stage"),
                            origin="import",
                            payload=event.get("payload_json") if isinstance(event.get("payload_json"), dict) else None,
                        )
        return len(records)

    def analytics(self) -> dict[str, Any]:
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            status_rows = connection.execute("SELECT status, COUNT(*) AS count FROM applications GROUP BY status").fetchall()
            mode_rows = connection.execute("SELECT work_mode, COUNT(*) AS count FROM applications GROUP BY work_mode").fetchall()
            interviews = connection.execute("SELECT COUNT(*) FROM applications WHERE status IN ('Interview', 'Offer')").fetchone()[0]
            submitted = connection.execute("SELECT COUNT(*) FROM applications WHERE status != 'Wishlist'").fetchone()[0]
            active = connection.execute("SELECT COUNT(*) FROM applications WHERE status IN ('Applied', 'Interview', 'Offer')").fetchone()[0]
            today = str(date.today())
            due_soon = str(date.today() + timedelta(days=7))
            overdue = connection.execute(
                "SELECT COUNT(*) FROM applications WHERE next_action_date < ? AND status != 'Rejected'",
                (today,),
            ).fetchone()[0]
            due_soon_count = connection.execute(
                "SELECT COUNT(*) FROM applications WHERE next_action_date BETWEEN ? AND ? AND status != 'Rejected'",
                (today, due_soon),
            ).fetchone()[0]
            upcoming = connection.execute(
                "SELECT * FROM applications WHERE next_action_date IS NOT NULL AND status != 'Rejected' ORDER BY next_action_date ASC LIMIT 5"
            ).fetchall()
            attention_total = connection.execute(
                """
                SELECT COUNT(*) FROM applications
                WHERE status != 'Rejected' AND (
                    (next_action_date IS NOT NULL AND next_action_date <= ?)
                    OR (next_action_date IS NULL AND status IN ('Applied', 'Interview', 'Offer'))
                )
                """,
                (due_soon,),
            ).fetchone()[0]
            attention = connection.execute(
                """
                SELECT *,
                    CASE
                        WHEN next_action_date < ? THEN 'overdue'
                        WHEN next_action_date = ? THEN 'today'
                        WHEN next_action_date IS NOT NULL AND next_action_date <= ? THEN 'due_soon'
                        ELSE 'missing'
                    END AS attention_type
                FROM applications
                WHERE status != 'Rejected' AND (
                    (next_action_date IS NOT NULL AND next_action_date <= ?)
                    OR (next_action_date IS NULL AND status IN ('Applied', 'Interview', 'Offer'))
                )
                ORDER BY
                    CASE
                        WHEN next_action_date < ? THEN 0
                        WHEN next_action_date = ? THEN 1
                        WHEN next_action_date IS NOT NULL THEN 2
                        ELSE 3
                    END,
                    CASE WHEN next_action_date IS NULL THEN 1 ELSE 0 END,
                    next_action_date ASC,
                    updated_at DESC,
                    id DESC
                LIMIT 8
                """,
                (today, today, due_soon, due_soon, today, today),
            ).fetchall()
        status_counts = {row["status"]: row["count"] for row in status_rows}
        return {
            "total": total,
            "active": active,
            "interviews": interviews,
            "submitted": submitted,
            "response_rate": round((interviews / submitted) * 100) if submitted else 0,
            "overdue": overdue,
            "due_soon": due_soon_count,
            "attention_total": attention_total,
            "by_status": status_counts,
            "by_work_mode": {row["work_mode"]: row["count"] for row in mode_rows},
            "upcoming": [dict(row) for row in upcoming],
            "attention": [dict(row) for row in attention],
        }

    @staticmethod
    def _event_datetime(value: Any) -> datetime | None:
        """Parse an event timestamp without inventing one for legacy rows."""
        if value in (None, ""):
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    @staticmethod
    def _median_days(values: list[float]) -> float | None:
        return round(float(median(values)), 1) if values else None

    def insights(self, window: str = "all") -> dict[str, Any]:
        """Calculate historical funnel metrics from recorded lifecycle events.

        A submitted cohort is anchored to the first recorded transition to
        ``Applied``. Current application fields are never used to reconstruct
        missing history; rows that only contain legacy/incomplete events are
        reported through ``history_quality``.
        """
        if window not in {"30", "90", "all"}:
            raise ValueError("Choose a 30, 90, or all-day insights window.")
        now = datetime.now(timezone.utc)
        cutoff = None if window == "all" else now - timedelta(days=int(window))
        with self.connect() as connection:
            applications = [dict(row) for row in connection.execute("SELECT * FROM applications ORDER BY id ASC").fetchall()]
            events = [dict(row) for row in connection.execute(
                "SELECT * FROM application_events ORDER BY application_id ASC, id ASC"
            ).fetchall()]

        events_by_application: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            events_by_application.setdefault(int(event["application_id"]), []).append(event)

        histories: list[dict[str, Any]] = []
        stage_names = ("Wishlist", "Ready", "Applied", "Interview", "Offer", "Closed")
        for application in applications:
            raw_events = events_by_application.get(int(application["id"]), [])
            staged: list[tuple[datetime, str, str | None]] = []
            limited = any((event.get("origin") or "") == "legacy" for event in raw_events)
            for event in raw_events:
                occurred = self._event_datetime(event.get("occurred_at"))
                if occurred is None:
                    limited = True
                    continue
                stage = event.get("to_stage") if event.get("to_stage") in stage_names else None
                outcome = None
                payload = event.get("payload_json")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (TypeError, ValueError):
                        payload = None
                if isinstance(payload, dict):
                    if not stage and payload.get("to_stage") in stage_names:
                        stage = payload.get("to_stage")
                    outcome = payload.get("outcome")
                if event.get("event_type") == "applied":
                    # Creation events from v3/v4 did not store to_stage, but
                    # the explicit event type is sufficient evidence of a
                    # submission transition. Older browser/demo records also
                    # used the current stage in this field, so the event type
                    # takes precedence for compatibility.
                    stage = "Applied"
                if stage is None:
                    continue
                staged.append((occurred, str(stage), outcome if isinstance(outcome, str) else None))
            staged.sort(key=lambda item: item[0])
            if not staged and application.get("stage") not in {None, "Wishlist"}:
                limited = True
            submitted_index = next((index for index, (_occurred, stage, _outcome) in enumerate(staged) if stage == "Applied"), None)
            submitted_at = staged[submitted_index][0] if submitted_index is not None else None
            if submitted_at is None:
                histories.append({"application": application, "submitted_at": None, "limited": limited})
                continue
            post_submitted = staged[submitted_index:]
            current_stage = application.get("stage")
            if current_stage not in {None, "Wishlist", "Ready", "Applied"} and not any(stage == current_stage for _occurred, stage, _outcome in post_submitted):
                limited = True
            # A repeated write to the same stage should not create a fake
            # zero-length interval, but a real return to a previous stage is
            # retained as a separate interval.
            response_at = None
            interviewed = False
            offered = False
            accepted = False
            for occurred, stage, outcome in post_submitted:
                if stage in {"Interview", "Offer", "Closed"} and response_at is None:
                    response_at = occurred
                interviewed = interviewed or stage == "Interview"
                offered = offered or stage == "Offer"
                accepted = accepted or (stage == "Closed" and outcome == "Accepted")
            stage_durations: dict[str, list[float]] = {}
            for (start_at, start_stage, _start_outcome), (end_at, end_stage, _end_outcome) in zip(post_submitted, post_submitted[1:]):
                if end_at <= start_at or start_stage == end_stage:
                    continue
                # Wishlist/Ready records can be legacy or out-of-order
                # compatibility markers after a submission. Closed is a
                # terminal stage, so none of those markers should create an
                # artificial open-ended interval.
                if start_stage in {"Wishlist", "Ready", "Closed"} or end_stage in {"Wishlist", "Ready"}:
                    continue
                stage_durations.setdefault(start_stage, []).append((end_at - start_at).total_seconds() / 86400)
            histories.append({
                "application": application,
                "submitted_at": submitted_at,
                "responded_at": response_at,
                "interviewed": interviewed,
                "offered": offered,
                "accepted": accepted,
                "stage_durations": stage_durations,
                "limited": limited,
            })

        cohort = [item for item in histories if item["submitted_at"] is not None and (cutoff is None or cutoff <= item["submitted_at"] <= now)]
        submitted = len(cohort)
        responded_items = [item for item in cohort if item.get("responded_at") is not None]
        interviewed = sum(1 for item in cohort if item.get("interviewed"))
        offered = sum(1 for item in cohort if item.get("offered"))
        accepted = sum(1 for item in cohort if item.get("accepted"))
        no_response = submitted - len(responded_items)
        response_times = [
            (item["responded_at"] - item["submitted_at"]).total_seconds() / 86400
            for item in responded_items
            if item["responded_at"] >= item["submitted_at"]
        ]
        duration_values: dict[str, list[float]] = {}
        for item in cohort:
            for stage, values in item.get("stage_durations", {}).items():
                duration_values.setdefault(stage, []).extend(values)

        def percentage(numerator: int, denominator: int) -> float | None:
            return round((numerator / denominator) * 100, 1) if denominator else None

        source_groups: dict[str, list[dict[str, Any]]] = {}
        for item in cohort:
            source = str(item["application"].get("source") or "Unknown").strip() or "Unknown"
            source_groups.setdefault(source, []).append(item)
        source_conversion = []
        for source in sorted(source_groups, key=str.casefold):
            group = source_groups[source]
            source_submitted = len(group)
            source_responded = sum(1 for item in group if item.get("responded_at") is not None)
            source_interviewed = sum(1 for item in group if item.get("interviewed"))
            source_offered = sum(1 for item in group if item.get("offered"))
            source_accepted = sum(1 for item in group if item.get("accepted"))
            source_conversion.append({
                "source": source,
                "submitted": source_submitted,
                "responded": source_responded,
                "interviewed": source_interviewed,
                "offered": source_offered,
                "accepted": source_accepted,
                "response_rate": percentage(source_responded, source_submitted),
                "interview_rate": percentage(source_interviewed, source_submitted),
                "offer_rate": percentage(source_offered, source_submitted),
                "acceptance_rate": percentage(source_accepted, source_submitted),
            })

        submitted_dates = [item["submitted_at"] for item in cohort]
        cohort_start = min(submitted_dates).isoformat() if submitted_dates else None
        cohort_end = max(submitted_dates).isoformat() if submitted_dates else None
        limited_count = sum(1 for item in cohort if item.get("limited"))
        limited_total = sum(1 for item in histories if item.get("limited"))
        return {
            "window": window,
            "cohort": {"start": cohort_start, "end": cohort_end, "submitted": submitted},
            "cohort_start": cohort_start,
            "cohort_end": cohort_end,
            "submitted": submitted,
            "responded": len(responded_items),
            "interviewed": interviewed,
            "offered": offered,
            "accepted": accepted,
            "no_response": no_response,
            "response_rate": percentage(len(responded_items), submitted),
            "interview_rate": percentage(interviewed, submitted),
            "offer_rate": percentage(offered, submitted),
            "acceptance_rate": percentage(accepted, submitted),
            "median_time_to_response": self._median_days(response_times),
            "median_time_in_stage": {stage: self._median_days(values) for stage, values in sorted(duration_values.items())},
            "source_conversion": source_conversion,
            "history_quality": {
                "complete": submitted - limited_count,
                "limited": limited_count,
                "limited_total": limited_total,
                "limited_note": "Legacy or incomplete event history is excluded from inferred transitions; missing timestamps are not invented.",
            },
            "denominators": {
                "submitted": "Applications with a first recorded Applied transition in the selected window.",
                "responded": "First transition out of Applied divided by the submitted cohort.",
                "interviewed": "Applications that ever reached Interview divided by the submitted cohort.",
                "offered": "Applications that ever reached Offer divided by the submitted cohort.",
                "accepted": "Applications that reached Closed with outcome Accepted divided by the submitted cohort.",
                "no_response": "Submitted applications with no recorded transition to Interview, Offer, or Closed.",
                "source_conversion": "Each source uses its first-submitted application count in the selected cohort as denominator.",
                "time_metrics": "Completed, timestamped intervals only; current open intervals are not estimated.",
            },
        }
