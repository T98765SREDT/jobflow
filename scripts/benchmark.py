#!/usr/bin/env python3
"""Measure JobFlow's read paths against a disposable synthetic dataset.

The benchmark intentionally uses a temporary SQLite file.  It never opens the
user's ``data/jobflow.db`` and reports raw timings instead of making a marketing
performance promise.  Run it from the repository root, for example:

    python3 scripts/benchmark.py --iterations 7

The fixture defaults to 5,000 applications, 25,000 events, and 10,000 tasks.
Smaller values are useful for a quick local smoke check.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobflow.database import Database, FIELDS  # noqa: E402


def percentile(values: list[float], fraction: float) -> float:
    """Return a nearest-rank percentile without adding a dependency."""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))
    return ordered[index]


def build_fixture(db: Database, applications: int, events_per_application: int, tasks_per_application: int) -> None:
    """Insert a deterministic fixture in one transaction."""
    db.initialize()
    today = date.today()
    application_rows: list[tuple[Any, ...]] = []
    for number in range(1, applications + 1):
        stage = ("Applied", "Interview", "Offer", "Ready", "Closed")[number % 5]
        status = "Rejected" if stage == "Closed" else stage
        closed_at = f"{today - timedelta(days=number % 30)}T12:00:00+00:00" if stage == "Closed" else None
        next_action = None if stage == "Closed" else str(today + timedelta(days=(number % 14) - 5))
        application_rows.append(
            (
                f"Benchmark Company {number:05d}", f"Role {number % 37:02d}",
                "Worldwide" if number % 2 else "Japan", "Remote" if number % 3 else "Hybrid",
                status, stage, "Rejected" if stage == "Closed" else None, 1, closed_at, None,
                "Synthetic benchmark", f"https://example.test/jobs/{number}",
                20 + (number % 10), 40 + (number % 10), "Hourly", "USD",
                str(today - timedelta(days=number % 60)) if stage != "Ready" else None,
                next_action, "Synthetic record used only for local benchmark measurements.",
            )
        )

    with db.connect() as connection:
        connection.executemany(
            f"INSERT INTO applications ({', '.join(FIELDS)}) VALUES ({', '.join('?' for _ in FIELDS)})",
            application_rows,
        )
        application_ids = [row[0] for row in connection.execute("SELECT id FROM applications ORDER BY id")]

        event_rows: list[tuple[Any, ...]] = []
        event_types = ("applied", "status_changed", "interview", "custom", "follow_up")
        for position, application_id in enumerate(application_ids, start=1):
            for event_number in range(events_per_application):
                event_stage = "Applied" if event_number == 0 else ("Interview" if event_number % 3 == 1 else "Offer")
                event_rows.append(
                    (
                        application_id, event_types[event_number % len(event_types)],
                        f"Benchmark event {event_number + 1}", "Synthetic event for a read benchmark.",
                        f"{today - timedelta(days=(position + event_number) % 90)}T12:00:00+00:00",
                        "Applied" if event_number else None, event_stage,
                        "benchmark", json.dumps({"to_stage": event_stage}), None,
                    )
                )
        connection.executemany(
            "INSERT INTO application_events "
            "(application_id, event_type, title, details, occurred_at, from_stage, to_stage, origin, payload_json, request_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            event_rows,
        )

        task_rows: list[tuple[Any, ...]] = []
        for position, application_id in enumerate(application_ids, start=1):
            for task_number in range(tasks_per_application):
                task_rows.append(
                    (
                        application_id, "follow_up" if task_number == 0 else "preparation",
                        f"Benchmark task {task_number + 1}",
                        str(today + timedelta(days=(position + task_number) % 14 - 5)),
                        None, 1,
                    )
                )
        connection.executemany(
            "INSERT INTO application_tasks (application_id, kind, title, due_date, completed_at, version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            task_rows,
        )
        connection.execute(
            "UPDATE applications SET next_action_date = ("
            "SELECT MIN(t.due_date) FROM application_tasks t WHERE t.application_id = applications.id"
            ") WHERE stage != 'Closed'"
        )


def measure(name: str, operation: Callable[[], Any], iterations: int) -> dict[str, Any]:
    """Warm up once, then return millisecond median and p95."""
    operation()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "operation": name,
        "iterations": iterations,
        "median_ms": round(float(median(samples)), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="jobflow-benchmark-") as directory:
        path = Path(directory) / "benchmark.db"
        db = Database(path)
        build_fixture(db, args.applications, args.events_per_application, args.tasks_per_application)
        application_id = 1
        results = [
            measure(
                "list/filter",
                lambda: db.list_applications({
                    "search": "Company 0042", "stage": "Applied", "work_mode": "Remote",
                    "sort": "updated_at", "direction": "desc", "page": "1", "limit": "20",
                }),
                args.iterations,
            ),
            measure("today", lambda: db.today(str(date.today())), args.iterations),
            measure("workspace", lambda: db.get_workspace(application_id), args.iterations),
            measure("insights", lambda: db.insights("90"), args.iterations),
        ]
        with sqlite3.connect(path) as connection:
            counts = {
                "applications": connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
                "events": connection.execute("SELECT COUNT(*) FROM application_events").fetchone()[0],
                "tasks": connection.execute("SELECT COUNT(*) FROM application_tasks").fetchone()[0],
            }
        return {
            "fixture": counts,
            "requested_fixture": {
                "applications": args.applications,
                "events_per_application": args.events_per_application,
                "tasks_per_application": args.tasks_per_application,
            },
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "sqlite": sqlite3.sqlite_version,
            },
            "results": results,
            "note": "Measurements are local observations on a temporary database; no performance threshold is asserted.",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--applications", type=int, default=5000)
    parser.add_argument("--events-per-application", type=int, default=5)
    parser.add_argument("--tasks-per-application", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args()
    for name in ("applications", "events_per_application", "tasks_per_application", "iterations"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    report = run(args)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print("JobFlow benchmark (temporary database)")
    print(f"Fixture: {report['fixture']['applications']:,} applications · {report['fixture']['events']:,} events · {report['fixture']['tasks']:,} tasks")
    runtime = report["runtime"]
    print(f"Runtime: Python {runtime['python']} · SQLite {runtime['sqlite']} · {runtime['platform']}")
    print("Operation                 median       p95       min       max (ms)")
    for result in report["results"]:
        print(f"{result['operation']:<24} {result['median_ms']:>8.3f} {result['p95_ms']:>9.3f} {result['min_ms']:>9.3f} {result['max_ms']:>9.3f}")
    print(report["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
