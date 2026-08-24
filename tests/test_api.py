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

    def test_health_and_static_page(self):
        status, data = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["service"], "jobflow")
        with urlopen(self.base_url + "/", timeout=2) as response:
            self.assertIn(b"JobFlow", response.read())

    def test_create_update_and_delete(self):
        payload = {"company": "API Test", "role": "Developer", "status": "Applied", "work_mode": "Remote"}
        status, created = self.request("/api/applications", method="POST", payload=payload)
        self.assertEqual(status, 201)
        _, updated = self.request(f"/api/applications/{created['id']}", method="PATCH", payload={"status": "Interview"})
        self.assertEqual(updated["status"], "Interview")
        status, data = self.request(f"/api/applications/{created['id']}", method="DELETE")
        self.assertEqual(status, 204)
        self.assertIsNone(data)

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


if __name__ == "__main__":
    unittest.main()
