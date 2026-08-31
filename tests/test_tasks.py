import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jobflow.database import Database, TransitionError, VersionConflict
from jobflow.server import build_server


class TaskDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "tasks.db")
        self.database.initialize()

    def tearDown(self):
        self.tempdir.cleanup()

    def application(self, **overrides):
        payload = {"company": "Acme", "role": "Engineer", "work_mode": "Remote", "status": "Applied"}
        payload.update(overrides)
        return self.database.create_application(payload)

    def test_legacy_date_becomes_task_and_derived_value_tracks_completion(self):
        application = self.application(next_action_date="2026-08-30")
        tasks = self.database.list_tasks(application["id"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["kind"], "follow_up")
        self.assertEqual(application["next_action_date"], "2026-08-30")
        completed = self.database.complete_task(tasks[0]["id"])
        self.assertIsNotNone(completed["completed_at"])
        self.assertIsNone(self.database.get_application(application["id"])["next_action_date"])

    def test_today_classifies_tasks_waiting_and_missing_deterministically(self):
        today = date.today().isoformat()
        overdue = self.application(company="Overdue", next_action_date=str(date.today() - timedelta(days=1)))
        due = self.application(company="Due", next_action_date=today)
        upcoming = self.application(company="Upcoming", next_action_date=str(date.today() + timedelta(days=2)))
        waiting = self.application(company="Waiting", waiting_until=str(date.today() + timedelta(days=3)))
        missing = self.application(company="Missing", next_action_date=None)
        result = self.database.today(today)
        self.assertEqual([item["company"] for item in result["overdue"]], ["Overdue"])
        self.assertEqual([item["company"] for item in result["due_today"]], ["Due"])
        self.assertEqual([item["company"] for item in result["upcoming"]], ["Upcoming"])
        self.assertEqual([item["company"] for item in result["waiting"]], ["Waiting"])
        self.assertEqual([item["company"] for item in result["missing_next_step"]], ["Missing"])

    def test_snooze_requires_current_version_and_close_completes_open_tasks(self):
        application = self.application(next_action_date="2026-08-30")
        task = self.database.list_tasks(application["id"])[0]
        snoozed = self.database.snooze_task(task["id"], due_date="2026-09-02", expected_version=1)
        self.assertEqual(snoozed["version"], 2)
        with self.assertRaises(VersionConflict):
            self.database.snooze_task(task["id"], due_date="2026-09-03", expected_version=1)
        closed = self.database.update_application(application["id"], {"status": "Rejected", "outcome": "Rejected"})
        self.assertEqual(closed["stage"], "Closed")
        self.assertIsNone(closed["next_action_date"])
        self.assertIsNotNone(self.database.list_tasks(application["id"])[0]["completed_at"])
        with self.assertRaises(TransitionError):
            self.database.create_task(application["id"], {"kind": "follow_up", "title": "Nope", "due_date": "2026-09-04"})

    def test_tasks_are_in_backup_and_cascade_with_application(self):
        application = self.application(next_action_date="2026-08-30")
        self.database.create_task(application["id"], {"kind": "preparation", "title": "Prepare", "due_date": "2026-08-31"})
        backup = self.database.export_applications()
        self.assertEqual(len(backup[0]["tasks"]), 2)
        self.database.delete_application(application["id"])
        self.database.import_applications(backup)
        restored = self.database.export_applications()[0]
        self.assertEqual([(task["kind"], task["due_date"]) for task in restored["tasks"]], [("follow_up", "2026-08-30"), ("preparation", "2026-08-31")])
        self.database.delete_application(restored["id"])
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM application_tasks").fetchone()[0], 0)


class TaskApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        cls.server = build_server("127.0.0.1", 0, database_path=Path(cls.tempdir.name) / "api.db", static_dir=root / "static")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tempdir.cleanup()

    def request(self, path, *, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base_url + path, data=body, method=method, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def error(self, path, *, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base_url + path, data=body, method=method, headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        return context.exception.code, json.loads(context.exception.read())

    def application(self):
        _, record = self.request("/api/applications", method="POST", payload={"company": "Task API", "role": "Engineer", "status": "Applied", "work_mode": "Remote"})
        return record

    def test_task_endpoints_and_today_validation(self):
        application = self.application()
        status, task = self.request(f"/api/applications/{application['id']}/tasks", method="POST", payload={"kind": "follow_up", "title": "Email recruiter", "due_date": "2026-09-01"})
        self.assertEqual(status, 201)
        status, tasks = self.request(f"/api/applications/{application['id']}/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(tasks[0]["title"], "Email recruiter")
        status, updated = self.request(f"/api/tasks/{task['id']}", method="PATCH", payload={"title": "Send email", "expected_version": 1})
        self.assertEqual(status, 200)
        self.assertEqual(updated["version"], 2)
        status, completed = self.request(f"/api/tasks/{task['id']}/complete", method="POST", payload={"expected_version": 2})
        self.assertEqual(status, 200)
        self.assertIsNotNone(completed["completed_at"])
        status, replay = self.request(f"/api/tasks/{task['id']}/complete", method="POST", payload={"expected_version": 1})
        self.assertEqual(status, 200)
        self.assertEqual(replay["id"], task["id"])
        status, error = self.error("/api/today?as_of=not-a-date")
        self.assertEqual(status, 400)
        self.assertIn("as_of", error["error"]["fields"])


if __name__ == "__main__":
    unittest.main()
