import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jobflow.server import build_server


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        project_root = Path(__file__).resolve().parents[1]
        cls.server = build_server("127.0.0.1", 0, database_path=Path(cls.tempdir.name) / "api.db", static_dir=project_root / "static")
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
            data = response.read()
            return response.status, json.loads(data) if data else None

    def error_request(self, path, *, method="GET", payload=None, body=None, headers=None):
        if body is None and payload is not None:
            body = json.dumps(payload).encode()
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=headers or {"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        return context.exception.code, json.loads(context.exception.read())

    def test_health_and_static_page(self):
        status, data = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["service"], "jobflow")
        self.assertEqual(data["schema_version"], 3)
        with urlopen(self.base_url + "/", timeout=2) as response:
            page = response.read()
            self.assertIn(b"JobFlow", page)
            self.assertIn(b'id="confirm-dialog"', page)
            self.assertIn(b'aria-keyshortcuts="Control+K Meta+K"', page)
            self.assertIn(b'data-export="calendar"', page)
            self.assertIn(b'id="toast-action"', page)
            self.assertIn(b'id="attention-list"', page)
            self.assertIn(b'id="csv-dialog"', page)
            with urlopen(self.base_url + "/csv.js", timeout=2) as csv_response:
                self.assertEqual(csv_response.status, 200)
                self.assertIn(b"JobFlowCsv", csv_response.read())

    def test_create_update_and_delete(self):
        payload = {"company": "API Test", "role": "Developer", "status": "Applied", "work_mode": "Remote"}
        status, created = self.request("/api/applications", method="POST", payload=payload)
        self.assertEqual(status, 201)
        _, updated = self.request(f"/api/applications/{created['id']}", method="PATCH", payload={"status": "Interview"})
        self.assertEqual(updated["status"], "Interview")
        status, data = self.request(f"/api/applications/{created['id']}", method="DELETE")
        self.assertEqual(status, 204)
        self.assertIsNone(data)

    def test_application_events_api(self):
        payload = {"company": "Timeline API", "role": "Engineer", "status": "Applied", "work_mode": "Remote"}
        _, created = self.request("/api/applications", method="POST", payload=payload)
        application_id = created["id"]

        status, event = self.request(
            f"/api/applications/{application_id}/events",
            method="POST",
            payload={"event_type": "interview", "title": "Technical interview", "details": "Prepare API examples."},
        )
        self.assertEqual(status, 201)
        self.assertEqual(event["event_type"], "interview")

        status, events = self.request(f"/api/applications/{application_id}/events")
        self.assertEqual(status, 200)
        self.assertEqual(len(events), 2)
        status, _ = self.request(f"/api/applications/{application_id}/events/{event['id']}", method="DELETE")
        self.assertEqual(status, 204)
        status, events = self.request(f"/api/applications/{application_id}/events")
        self.assertEqual(status, 200)
        self.assertEqual(len(events), 1)

    def test_validation_error_response(self):
        request = Request(
            self.base_url + "/api/applications", data=b'{"company":""}', method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        self.assertEqual(context.exception.code, 422)
        payload = json.loads(context.exception.read())
        self.assertIn("company", payload["fields"])

    def test_patch_validates_the_merged_record(self):
        payload = {
            "company": "Patch Test", "role": "Developer", "status": "Applied", "work_mode": "Remote",
            "salary_min": 25, "salary_max": 40, "salary_period": "Hourly",
        }
        _, created = self.request("/api/applications", method="POST", payload=payload)
        status, error = self.error_request(
            f"/api/applications/{created['id']}", method="PATCH", payload={"salary_max": 20}
        )
        self.assertEqual(status, 422)
        self.assertIn("salary_max", error["fields"])
        _, unchanged = self.request(f"/api/applications/{created['id']}")
        self.assertEqual(unchanged["salary_max"], 40)

    def test_query_validation_and_missing_static_asset(self):
        status, error = self.error_request("/api/applications?limit=500")
        self.assertEqual(status, 400)
        self.assertIn("limit", error["fields"])
        status, error = self.error_request("/missing.js")
        self.assertEqual(status, 404)
        self.assertEqual(error["error"], "File not found.")

    def test_request_body_content_type_and_encoding_errors(self):
        status, _ = self.error_request(
            "/api/applications", method="POST", body=b"{}", headers={"Content-Type": "text/plain"}
        )
        self.assertEqual(status, 415)
        status, error = self.error_request(
            "/api/applications", method="POST", body=b"\xff", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)
        self.assertIn("UTF-8", error["error"])

    def test_backup_import_is_atomic(self):
        _, before = self.request("/api/export")
        valid = {
            "company": "Imported", "role": "QA Engineer", "status": "Applied", "work_mode": "Remote",
            "salary_period": "Annual",
        }
        status, error = self.error_request(
            "/api/import?mode=append", method="POST", payload={"applications": [valid, {"company": ""}]}
        )
        self.assertEqual(status, 422)
        self.assertIn("applications.1.company", error["fields"])
        _, unchanged = self.request("/api/export")
        self.assertEqual(len(unchanged["applications"]), len(before["applications"]))

        status, imported = self.request(
            "/api/import?mode=append", method="POST", payload={"schema_version": 2, "applications": [valid]}
        )
        self.assertEqual(status, 201)
        self.assertEqual(imported["imported"], 1)

    def test_backup_rejects_unknown_or_future_schema(self):
        status, error = self.error_request(
            "/api/import?mode=append", method="POST",
            payload={"schema_version": 99, "applications": []},
        )
        self.assertEqual(status, 422)
        self.assertIn("schema_version", error["fields"])
        status, error = self.error_request(
            "/api/import?mode=append", method="POST",
            payload={"applications": [], "unexpected": True},
        )
        self.assertEqual(status, 422)
        self.assertIn("body", error["fields"])


if __name__ == "__main__":
    unittest.main()
