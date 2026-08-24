"""SQLite persistence and query layer for JobFlow."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
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
    salary_min INTEGER,
    salary_max INTEGER,
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
"""

FIELDS = (
    "company", "role", "location", "work_mode", "status", "source", "url",
    "salary_min", "salary_max", "currency", "applied_date", "next_action_date", "notes",
)


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, *, seed: bool = True) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            if seed and connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0:
                self._seed(connection)

    def _seed(self, connection: sqlite3.Connection) -> None:
        today = date.today()
        examples = [
            ("Northstar Labs", "Python Backend Developer", "Worldwide", "Remote", "Interview", "LinkedIn", "https://example.com/jobs/northstar", 32, 48, "USD", str(today - timedelta(days=8)), str(today + timedelta(days=1)), "Prepare API design examples and questions for the engineering team."),
            ("Lumen AI", "AI Code Evaluator — Mandarin", "Japan", "Remote", "Applied", "Company site", "https://example.com/jobs/lumen", 28, 40, "USD", str(today - timedelta(days=3)), str(today + timedelta(days=4)), "Submitted coding assessment. Follow up if there is no response."),
            ("Sora Systems", "Junior Full-Stack Engineer", "Tokyo, Japan", "Hybrid", "Wishlist", "Referral", "https://example.com/jobs/sora", 4200000, 5500000, "JPY", None, str(today + timedelta(days=2)), "Tailor portfolio summary to the product dashboard requirements."),
            ("Orbit QA", "Freelance Software Tester", "Worldwide", "Remote", "Offer", "Remote board", "https://example.com/jobs/orbit", 22, 28, "USD", str(today - timedelta(days=14)), str(today + timedelta(days=2)), "Review contractor agreement and weekly availability."),
            ("Maple Cloud", "Web Developer", "Singapore", "Remote", "Rejected", "LinkedIn", "https://example.com/jobs/maple", 3000, 4500, "USD", str(today - timedelta(days=25)), None, "Good practice interview; strengthen system-design examples."),
            ("Kite Data", "Technical Data Analyst", "Japan", "Remote", "Applied", "Company site", "https://example.com/jobs/kite", 250000, 350000, "JPY", str(today - timedelta(days=1)), str(today + timedelta(days=6)), "Highlight SQL validation and structured-data experience."),
        ]
        connection.executemany(
            f"INSERT INTO applications ({', '.join(FIELDS)}) VALUES ({', '.join('?' for _ in FIELDS)})",
            examples,
        )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def list_applications(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for field in ("status", "work_mode"):
            if filters.get(field):
                clauses.append(f"{field} = ?")
                values.append(filters[field])
        if filters.get("search"):
            clauses.append("(company LIKE ? OR role LIKE ? OR location LIKE ? OR notes LIKE ?)")
            search = f"%{filters['search']}%"
            values.extend([search] * 4)

        allowed_sort = {"updated_at", "applied_date", "next_action_date", "company", "status"}
        sort = filters.get("sort", "updated_at")
        if sort not in allowed_sort:
            sort = "updated_at"
        direction = "ASC" if filters.get("direction", "desc").lower() == "asc" else "DESC"
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        nulls_last = f"CASE WHEN {sort} IS NULL THEN 1 ELSE 0 END, " if sort in {"applied_date", "next_action_date"} else ""
        query = f"SELECT * FROM applications{where} ORDER BY {nulls_last}{sort} {direction}, id DESC"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

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
            row = connection.execute("SELECT * FROM applications WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def update_application(self, application_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        if not data:
            return self.get_application(application_id)
        assignments = ", ".join(f"{field} = ?" for field in data)
        values = list(data.values()) + [application_id]
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE applications SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
            return dict(row)

    def delete_application(self, application_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute("DELETE FROM applications WHERE id = ?", (application_id,)).rowcount > 0

    def analytics(self) -> dict[str, Any]:
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            status_rows = connection.execute("SELECT status, COUNT(*) AS count FROM applications GROUP BY status").fetchall()
            mode_rows = connection.execute("SELECT work_mode, COUNT(*) AS count FROM applications GROUP BY work_mode").fetchall()
            interviews = connection.execute("SELECT COUNT(*) FROM applications WHERE status IN ('Interview', 'Offer')").fetchone()[0]
            active = connection.execute("SELECT COUNT(*) FROM applications WHERE status IN ('Applied', 'Interview', 'Offer')").fetchone()[0]
            upcoming = connection.execute(
                "SELECT * FROM applications WHERE next_action_date IS NOT NULL AND status != 'Rejected' ORDER BY next_action_date ASC LIMIT 5"
            ).fetchall()
        status_counts = {row["status"]: row["count"] for row in status_rows}
        return {
            "total": total,
            "active": active,
            "interviews": interviews,
            "response_rate": round((interviews / total) * 100) if total else 0,
            "by_status": status_counts,
            "by_work_mode": {row["work_mode"]: row["count"] for row in mode_rows},
            "upcoming": [dict(row) for row in upcoming],
        }
