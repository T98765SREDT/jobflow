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

    @staticmethod
    def error_info(payload):
        return payload["error"] if isinstance(payload.get("error"), dict) else payload

    def test_health_and_static_page(self):
        status, data = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["service"], "jobflow")
        self.assertEqual(data["schema_version"], 8)
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

    def test_error_envelope_contains_request_id_and_recovery_metadata(self):
        request = Request(
            self.base_url + "/api/applications?limit=500",
            method="GET",
            headers={"X-Request-ID": "api-contract-123"},
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        payload = json.loads(context.exception.read())
        info = self.error_info(payload)
        self.assertEqual(info["code"], "BAD_REQUEST")
        self.assertFalse(info["retryable"])
        self.assertEqual(info["request_id"], "api-contract-123")
        self.assertEqual(context.exception.headers.get("X-Request-ID"), "api-contract-123")
        self.assertIn("limit", info["fields"])

    def test_historical_insights_endpoint_and_window_validation(self):
        status, insights = self.request("/api/insights?window=all")
        self.assertEqual(status, 200)
        for key in ("submitted", "responded", "interviewed", "offered", "accepted", "no_response", "source_conversion", "history_quality", "denominators"):
            self.assertIn(key, insights)
        self.assertEqual(insights["window"], "all")
        status, error = self.error_request("/api/insights?window=14")
        self.assertEqual(status, 400)
        self.assertIn("window", self.error_info(error)["fields"])
        status, error = self.error_request("/api/insights?window=30&extra=1")
        self.assertEqual(status, 400)
        self.assertIn("query", self.error_info(error)["fields"])

    def test_create_update_and_delete(self):
        payload = {"company": "API Test", "role": "Developer", "status": "Applied", "work_mode": "Remote"}
        status, created = self.request("/api/applications", method="POST", payload=payload)
        self.assertEqual(status, 201)
        self.assertEqual(created["stage"], "Applied")
        self.assertIsNone(created["outcome"])
        self.assertEqual(created["version"], 1)
        _, updated = self.request(f"/api/applications/{created['id']}", method="PATCH", payload={"status": "Interview"})
        self.assertEqual(updated["status"], "Interview")
        self.assertEqual(updated["stage"], "Interview")
        self.assertEqual(updated["version"], 2)
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

    def test_application_workspace_returns_consistent_snapshot(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={
                "company": "Workspace API", "role": "Python Engineer", "status": "Applied",
                "work_mode": "Remote", "next_action_date": "2026-09-03",
            },
        )
        application_id = created["id"]
        status, task = self.request(
            f"/api/applications/{application_id}/tasks", method="POST",
            payload={"kind": "preparation", "title": "Prepare examples", "due_date": "2026-09-02"},
        )
        self.assertEqual(status, 201)
        status, workspace = self.request(f"/api/applications/{application_id}/workspace")
        self.assertEqual(status, 200)
        self.assertEqual(workspace["application"]["id"], application_id)
        self.assertEqual(workspace["open_tasks"][0]["id"], task["id"])
        self.assertEqual(workspace["open_tasks"][0]["due_date"], "2026-09-02")
        self.assertEqual(workspace["summary"]["open_tasks"], 2)
        self.assertEqual(workspace["summary"]["activity_count"], 1)
        status, error = self.error_request(f"/api/applications/999999/workspace")
        self.assertEqual(status, 404)
        self.assertIn("deleted", self.error_info(error)["message"])

    def test_requirements_api_crud_and_workspace_summary(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Requirements API", "role": "Python Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        application_id = created["id"]
        rows = [
            {"criterion": "Python", "category": "skill", "assessment": "met", "evidence": "JobFlow project", "weight": 5, "position": 0},
            {"criterion": "SQL", "category": "skill", "assessment": "partial", "evidence": "Coursework", "weight": 3, "position": 1},
            {"criterion": "Work authorization", "category": "work_authorization", "assessment": "unknown", "weight": 5, "position": 2},
        ]
        created_rows = []
        for row in rows:
            status, requirement = self.request(f"/api/applications/{application_id}/requirements", method="POST", payload=row)
            self.assertEqual(status, 201)
            created_rows.append(requirement)
        status, requirements = self.request(f"/api/applications/{application_id}/requirements")
        self.assertEqual(status, 200)
        self.assertEqual([item["criterion"] for item in requirements], ["Python", "SQL", "Work authorization"])
        status, workspace = self.request(f"/api/applications/{application_id}/workspace")
        self.assertEqual(status, 200)
        self.assertEqual(workspace["requirement_summary"]["counts"]["unknown"], 1)
        self.assertEqual(workspace["requirement_summary"]["known_weight"], 8)
        self.assertEqual(workspace["requirement_summary"]["coverage"], 81.2)
        status, updated = self.request(
            f"/api/requirements/{created_rows[1]['id']}", method="PATCH", payload={"assessment": "met", "evidence": "SQL validation"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["assessment"], "met")
        status, reordered = self.request(
            f"/api/applications/{application_id}/requirements", method="PUT",
            payload={"ordered_ids": [created_rows[2]["id"], created_rows[0]["id"], created_rows[1]["id"]]},
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in reordered], [created_rows[2]["id"], created_rows[0]["id"], created_rows[1]["id"]])
        status, _ = self.request(f"/api/requirements/{created_rows[2]['id']}", method="DELETE")
        self.assertEqual(status, 204)
        status, error = self.error_request(
            f"/api/applications/{application_id}/requirements", method="POST",
            payload={"criterion": "Bad", "category": "skill", "assessment": "invalid", "weight": 9},
        )
        self.assertEqual(status, 422)
        self.assertIn("assessment", self.error_info(error)["fields"])
        self.assertIn("weight", self.error_info(error)["fields"])

    def test_artifacts_and_submission_snapshots_api(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Materials API", "role": "Python Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        application_id = created["id"]
        status, resume = self.request(
            f"/api/applications/{application_id}/artifacts", method="POST",
            payload={"kind": "resume", "label": "Resume — Python focus", "version_label": "v1", "uri": "https://example.com/resume", "notes": "Initial version"},
        )
        self.assertEqual(status, 201)
        status, portfolio = self.request(
            f"/api/applications/{application_id}/artifacts", method="POST",
            payload={"kind": "portfolio", "label": "JobFlow demo", "version_label": "v2"},
        )
        self.assertEqual(status, 201)
        status, snapshot = self.request(
            f"/api/applications/{application_id}/submissions", method="POST",
            payload={"artifact_ids": [resume["id"], portfolio["id"]], "notes": "Submitted through the company portal."},
        )
        self.assertEqual(status, 201)
        self.assertEqual([item["snapshot_label"] for item in snapshot["items"]], ["Resume — Python focus", "JobFlow demo"])

        status, _ = self.request(f"/api/artifacts/{resume['id']}", method="PATCH", payload={"label": "Resume — revised"})
        self.assertEqual(status, 200)
        status, restored = self.request(f"/api/submissions/{snapshot['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(restored["items"][0]["snapshot_label"], "Resume — Python focus")
        status, error = self.error_request(f"/api/artifacts/{resume['id']}", method="DELETE")
        self.assertEqual(status, 409)
        self.assertEqual(self.error_info(error)["code"], "ARTIFACT_IN_USE")
        self.assertEqual(error["package_ids"], [snapshot["id"]])
        status, workspace = self.request(f"/api/applications/{application_id}/workspace")
        self.assertEqual(status, 200)
        self.assertEqual(len(workspace["artifacts"]), 2)
        self.assertEqual(len(workspace["submissions"]), 1)
        status, backup = self.request("/api/export")
        self.assertEqual(status, 200)
        exported = next(item for item in backup["applications"] if item["id"] == application_id)
        self.request(f"/api/applications/{application_id}", method="DELETE")
        status, result = self.request(
            "/api/import?mode=append", method="POST",
            payload={"schema_version": 8, "applications": [exported]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["imported"], 1)

    def test_artifact_and_submission_validation_api(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Materials Validation", "role": "QA Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        application_id = created["id"]
        status, error = self.error_request(
            f"/api/applications/{application_id}/artifacts", method="POST",
            payload={"kind": "resume", "label": "Bad link", "uri": "file:///tmp/resume.pdf"},
        )
        self.assertEqual(status, 422)
        self.assertIn("uri", self.error_info(error)["fields"])
        status, error = self.error_request(
            f"/api/applications/{application_id}/submissions", method="POST",
            payload={"artifact_ids": []},
        )
        self.assertEqual(status, 422)
        self.assertIn("artifact_ids", self.error_info(error)["fields"])

    def test_import_preview_requires_duplicate_decision_and_supports_merge(self):
        _, existing = self.request(
            "/api/applications", method="POST",
            payload={
                "company": "Duplicate Preview API", "role": "Python Engineer", "location": "Tokyo",
                "work_mode": "Remote", "status": "Applied", "url": "https://example.com/jobs/duplicate-preview",
                "source": "Original",
            },
        )
        incoming = {
            "company": "Duplicate Preview API", "role": "Python Engineer", "location": "Osaka",
            "work_mode": "Remote", "status": "Applied", "url": "https://example.com/jobs/duplicate-preview?utm_source=newsletter#role",
            "source": "", "notes": "Updated note",
        }
        status, preview = self.request("/api/import/preview", method="POST", payload={"schema_version": 8, "applications": [incoming]})
        self.assertEqual(status, 200)
        self.assertEqual(preview["valid_count"], 1)
        self.assertEqual(preview["conflicts"][0]["existing_application_id"], existing["id"])
        status, error = self.error_request("/api/import?mode=append", method="POST", payload={"schema_version": 8, "applications": [incoming]})
        self.assertEqual(status, 409)
        self.assertEqual(self.error_info(error)["code"], "DUPLICATES_FOUND")
        status, result = self.request(
            "/api/import?mode=append", method="POST",
            payload={
                "schema_version": 8,
                "applications": [incoming],
                "duplicate_decisions": [{"incoming_index": 0, "action": "merge", "existing_application_id": existing["id"], "fields": ["location", "notes", "source"]}],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["merged"], 1)
        _, updated = self.request(f"/api/applications/{existing['id']}")
        self.assertEqual(updated["location"], "Osaka")
        self.assertEqual(updated["source"], "Original")
        self.assertEqual(updated["notes"], "Updated note")

    def test_import_preview_reports_in_file_duplicate_without_mutation(self):
        rows = [
            {"company": "In-file Duplicate API", "role": "QA", "work_mode": "Remote", "status": "Wishlist"},
            {"company": "In-file Duplicate API", "role": "QA", "work_mode": "Remote", "status": "Wishlist"},
        ]
        status, preview = self.request("/api/import/preview", method="POST", payload={"schema_version": 8, "applications": rows})
        self.assertEqual(status, 200)
        self.assertEqual(preview["conflicts"][0]["matched_incoming_index"], 0)
        status, error = self.error_request("/api/import?mode=append", method="POST", payload={"schema_version": 8, "applications": rows})
        self.assertEqual(status, 409)
        self.assertEqual(self.error_info(error)["code"], "DUPLICATES_FOUND")
        _, listing = self.request("/api/applications?search=In-file%20Duplicate%20API")
        self.assertEqual(listing["total"], 0)

    def test_requirement_backup_import_keeps_nested_rows(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Requirement Backup", "role": "QA Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        application_id = created["id"]
        self.request(
            f"/api/applications/{application_id}/requirements", method="POST",
            payload={"criterion": "API testing", "category": "skill", "assessment": "met", "evidence": "QA Sentinel", "weight": 4, "position": 0},
        )
        _, backup = self.request("/api/export")
        exported = next(item for item in backup["applications"] if item["id"] == application_id)
        self.request(f"/api/applications/{application_id}", method="DELETE")
        status, result = self.request(
            "/api/import?mode=append", method="POST",
            payload={"schema_version": 7, "applications": [exported]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["imported"], 1)
        _, records = self.request("/api/applications?search=Requirement%20Backup")
        self.assertEqual(len(records["items"]), 1)
        restored_id = records["items"][0]["id"]
        _, requirements = self.request(f"/api/applications/{restored_id}/requirements")
        self.assertEqual(requirements[0]["criterion"], "API testing")

    def test_validation_error_response(self):
        request = Request(
            self.base_url + "/api/applications", data=b'{"company":""}', method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        self.assertEqual(context.exception.code, 422)
        payload = json.loads(context.exception.read())
        self.assertIn("company", self.error_info(payload)["fields"])

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
        self.assertIn("salary_max", self.error_info(error)["fields"])
        _, unchanged = self.request(f"/api/applications/{created['id']}")
        self.assertEqual(unchanged["salary_max"], 40)

    def test_query_validation_and_missing_static_asset(self):
        status, error = self.error_request("/api/applications?limit=500")
        self.assertEqual(status, 400)
        self.assertIn("limit", self.error_info(error)["fields"])
        status, error = self.error_request("/missing.js")
        self.assertEqual(status, 404)
        self.assertIn("File not found", self.error_info(error)["message"])

    def test_request_body_content_type_and_encoding_errors(self):
        status, _ = self.error_request(
            "/api/applications", method="POST", body=b"{}", headers={"Content-Type": "text/plain"}
        )
        self.assertEqual(status, 415)
        status, error = self.error_request(
            "/api/applications", method="POST", body=b"\xff", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)
        self.assertIn("UTF-8", self.error_info(error)["message"])

    def test_malformed_backup_json_does_not_change_database(self):
        _, before = self.request("/api/export")
        status, error = self.error_request(
            "/api/import?mode=replace",
            method="POST",
            body=b'{"schema_version":3,"applications":[',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertIn("valid JSON", self.error_info(error)["message"])
        _, after = self.request("/api/export")
        self.assertEqual(after["applications"], before["applications"])

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
        self.assertIn("applications.1.company", self.error_info(error)["fields"])
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
        self.assertIn("schema_version", self.error_info(error)["fields"])
        status, error = self.error_request(
            "/api/import?mode=append", method="POST",
            payload={"applications": [], "unexpected": True},
        )
        self.assertEqual(status, 422)
        self.assertIn("body", self.error_info(error)["fields"])

    def test_meta_exposes_v4_lifecycle_options_and_legacy_statuses(self):
        status, data = self.request("/api/meta/options")
        self.assertEqual(status, 200)
        self.assertEqual(data["stages"], ["Wishlist", "Ready", "Applied", "Interview", "Offer", "Closed"])
        self.assertIn("Accepted", data["outcomes"])
        self.assertEqual(data["statuses"], ["Wishlist", "Applied", "Interview", "Offer", "Rejected"])
        self.assertEqual(data["artifact_kinds"], ["job_description", "resume", "cover_letter", "portfolio", "assessment", "other"])

    def test_transition_api_is_idempotent_and_structured(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Transition API", "role": "Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        transition = {"to_stage": "Interview", "expected_version": 1, "request_id": "transition-api-1"}
        status, first = self.request(f"/api/applications/{created['id']}/transitions", method="POST", payload=transition)
        self.assertEqual(status, 200)
        self.assertFalse(first["replayed"])
        self.assertEqual(first["application"]["stage"], "Interview")
        self.assertEqual(first["application"]["version"], 2)
        self.assertEqual(first["event"]["from_stage"], "Applied")
        self.assertEqual(first["event"]["to_stage"], "Interview")
        self.assertEqual(first["event"]["origin"], "system")
        status, replay = self.request(f"/api/applications/{created['id']}/transitions", method="POST", payload=transition)
        self.assertEqual(status, 200)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["event"]["id"], first["event"]["id"])
        _, events = self.request(f"/api/applications/{created['id']}/events")
        self.assertEqual(len(events), 2)

    def test_transition_api_returns_current_record_for_stale_version(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Stale API", "role": "Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        self.request(
            f"/api/applications/{created['id']}/transitions", method="POST",
            payload={"to_stage": "Interview", "expected_version": 1, "request_id": "stale-first"},
        )
        status, error = self.error_request(
            f"/api/applications/{created['id']}/transitions", method="POST",
            payload={"to_stage": "Offer", "expected_version": 1, "request_id": "stale-second"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.error_info(error)["code"], "VERSION_CONFLICT")
        self.assertEqual(error["current"]["stage"], "Interview")

    def test_transition_api_rejects_incomplete_close_and_allows_backtrack(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Invalid API", "role": "Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        status, error = self.error_request(
            f"/api/applications/{created['id']}/transitions", method="POST",
            payload={"to_stage": "Closed", "request_id": "missing-outcome"},
        )
        self.assertEqual(status, 422)
        self.assertIn("outcome", self.error_info(error)["fields"])
        status, result = self.request(
            f"/api/applications/{created['id']}/transitions", method="POST",
            payload={"to_stage": "Wishlist", "request_id": "legal-backtrack"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["application"]["stage"], "Wishlist")

    def test_system_event_cannot_be_deleted_but_user_event_can(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Protected API", "role": "Engineer", "status": "Applied", "work_mode": "Remote"},
        )
        status, events = self.request(f"/api/applications/{created['id']}/events")
        self.assertEqual(status, 200)
        status, error = self.error_request(f"/api/applications/{created['id']}/events/{events[0]['id']}", method="DELETE")
        self.assertEqual(status, 403)
        self.assertEqual(self.error_info(error)["code"], "PROTECTED_EVENT")
        _, user_event = self.request(
            f"/api/applications/{created['id']}/events", method="POST",
            payload={"event_type": "note", "title": "Personal note"},
        )
        status, _ = self.request(f"/api/applications/{created['id']}/events/{user_event['id']}", method="DELETE")
        self.assertEqual(status, 204)

    def test_stage_input_and_conflicting_status_are_handled(self):
        payload = {"company": "Lifecycle API", "role": "Engineer", "stage": "Ready", "work_mode": "Remote"}
        status, created = self.request("/api/applications", method="POST", payload=payload)
        self.assertEqual(status, 201)
        self.assertEqual(created["stage"], "Ready")
        self.assertEqual(created["status"], "Applied")
        status, error = self.error_request(
            "/api/applications", method="POST",
            payload={"company": "Conflict", "role": "Engineer", "stage": "Interview", "status": "Applied", "work_mode": "Remote"},
        )
        self.assertEqual(status, 422)
        self.assertIn("stage", self.error_info(error)["fields"])

    def test_closed_application_requires_outcome_and_timestamp(self):
        status, error = self.error_request(
            "/api/applications", method="POST",
            payload={"company": "Closed", "role": "Engineer", "stage": "Closed", "work_mode": "Remote"},
        )
        self.assertEqual(status, 422)
        self.assertIn("outcome", self.error_info(error)["fields"])
        self.assertIn("closed_at", self.error_info(error)["fields"])
        status, created = self.request(
            "/api/applications", method="POST",
            payload={"company": "Accepted", "role": "Engineer", "stage": "Closed", "outcome": "Accepted", "closed_at": "2026-08-29T10:00:00Z", "work_mode": "Remote"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["stage"], "Closed")
        self.assertEqual(created["outcome"], "Accepted")
        self.assertEqual(created["status"], "Rejected")

    def test_v3_backup_import_is_upgraded_to_v4_lifecycle_fields(self):
        status, created = self.request(
            "/api/import?mode=append", method="POST",
            payload={
                "schema_version": 3,
                "applications": [{
                    "company": "Legacy API", "role": "QA Tester", "status": "Rejected", "work_mode": "Remote",
                }],
            },
        )
        self.assertEqual(status, 201)
        _, records = self.request("/api/applications?search=Legacy%20API")
        self.assertEqual(records["items"][0]["stage"], "Closed")
        self.assertEqual(records["items"][0]["outcome"], "Rejected")

    def test_partial_note_update_does_not_clear_closed_outcome(self):
        _, created = self.request(
            "/api/applications", method="POST",
            payload={
                "company": "Outcome Safe", "role": "Engineer", "stage": "Closed",
                "outcome": "Accepted", "closed_at": "2026-08-29T10:00:00Z", "work_mode": "Remote",
            },
        )
        _, updated = self.request(
            f"/api/applications/{created['id']}", method="PATCH",
            payload={"notes": "Keep the accepted outcome."},
        )
        self.assertEqual(updated["stage"], "Closed")
        self.assertEqual(updated["outcome"], "Accepted")


if __name__ == "__main__":
    unittest.main()
