"""SQLite persistence and query layer for JobFlow."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    work_mode TEXT NOT NULL,
    status TEXT NOT NULL,
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_application_events_application ON application_events(application_id, occurred_at DESC, id DESC);
"""

SCHEMA_VERSION = 3

FIELDS = (
    "company", "role", "location", "work_mode", "status", "source", "url",
    "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes",
)


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
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
        connection.executemany(
            f"INSERT INTO applications ({', '.join(FIELDS)}) VALUES ({', '.join('?' for _ in FIELDS)})",
            examples,
        )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def list_applications(self, filters: dict[str, str]) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        for field in ("status", "work_mode"):
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

        allowed_sort = {"updated_at", "applied_date", "next_action_date", "company", "status"}
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

    def create_application(self, data: dict[str, Any]) -> dict[str, Any]:
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
            )
            row = connection.execute("SELECT * FROM applications WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def update_application(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        if not data:
            return self.get_application(application_id)
        assignments = ", ".join(f"{field} = ?" for field in data)
        values = list(data.values()) + [application_id]
        with self.connect() as connection:
            existing = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
            if existing is None:
                return None
            cursor = connection.execute(
                f"UPDATE applications SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                return None
            old = dict(existing)
            if "status" in data and data["status"] != old["status"]:
                self._insert_event(
                    connection,
                    application_id,
                    "status_changed",
                    f"Status changed to {data['status']}",
                    f"Previous status: {old['status']}",
                )
            if "next_action_date" in data and data["next_action_date"] != old["next_action_date"]:
                next_action = data["next_action_date"] or "No date"
                self._insert_event(
                    connection,
                    application_id,
                    "follow_up",
                    "Follow-up date updated",
                    f"Next action: {next_action}",
                )
            if "notes" in data and data["notes"] != old["notes"]:
                self._insert_event(
                    connection,
                    application_id,
                    "note",
                    "Notes updated",
                    data["notes"] or "Notes cleared.",
                )
            row = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
            return dict(row)

    def delete_application(self, application_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute("DELETE FROM applications WHERE id = ?", (application_id,)).rowcount > 0

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        application_id: int,
        event_type: str,
        title: str,
        details: str = "",
        occurred_at: str | None = None,
    ) -> None:
        timestamp = occurred_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO application_events (application_id, event_type, title, details, occurred_at) VALUES (?, ?, ?, ?, ?)",
            (application_id, event_type, title, details, timestamp),
        )

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
            )
            row = connection.execute("SELECT * FROM application_events WHERE id = last_insert_rowid()").fetchone()
            return dict(row)

    def delete_event(self, application_id: int, event_id: int) -> bool:
        with self.connect() as connection:
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
            for application in applications:
                application["events"] = events_by_application.get(int(application["id"]), [])
            return applications

    def import_applications(self, records: list[dict[str, Any]], *, replace: bool = False) -> int:
        """Insert a validated backup atomically, optionally replacing current data."""
        with self.connect() as connection:
            if replace:
                connection.execute("DELETE FROM applications")
            placeholders = ", ".join("?" for _ in FIELDS)
            connection.executemany(
                f"INSERT INTO applications ({', '.join(FIELDS)}) VALUES ({placeholders})",
                [[record.get(field) for field in FIELDS] for record in records],
            )
            if records:
                imported_rows = connection.execute(
                    "SELECT id FROM applications ORDER BY id DESC LIMIT ?", (len(records),)
                ).fetchall()
                for row, record in zip(reversed(imported_rows), records):
                    for event in record.get("events", []):
                        self._insert_event(
                            connection,
                            int(row["id"]),
                            event["event_type"],
                            event["title"],
                            event.get("details", ""),
                            event.get("occurred_at"),
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
