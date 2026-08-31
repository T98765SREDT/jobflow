import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobflow.database import FIELDS, Database


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class MigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def _fixture_database(self) -> Database:
        path = Path(self.tempdir.name) / "fixture.db"
        with sqlite3.connect(path) as connection:
            connection.executescript((FIXTURES / "jobflow_v3.sql").read_text(encoding="utf-8"))
        database = Database(path)
        database.initialize(seed=False)
        return database

    def test_v3_fixture_opens_and_preserves_realistic_records(self):
        database = self._fixture_database()

        records = database.export_applications()

        self.assertEqual([record["status"] for record in records], ["Applied", "Wishlist", "Interview", "Offer", "Rejected"])
        self.assertEqual([record["stage"] for record in records], ["Applied", "Wishlist", "Interview", "Offer", "Closed"])
        self.assertEqual(records[-1]["outcome"], "Rejected")
        self.assertEqual([record["version"] for record in records], [1, 1, 1, 1, 1])
        self.assertEqual(records[1]["company"], "星河科技")
        self.assertEqual(records[2]["location"], "Tokyo, Japan")
        self.assertIsNone(records[1]["salary_min"])
        self.assertEqual(records[0]["source"], "CSV import")
        self.assertEqual([(task["kind"], task["due_date"]) for task in records[0]["tasks"]], [("follow_up", "2026-08-28")])
        self.assertEqual(records[-1]["tasks"], [])
        self.assertEqual(len(records[2]["events"]), 3)
        self.assertTrue(all(event["origin"] == "legacy" for record in records for event in record["events"]))

    def test_export_shape_and_equal_timestamp_order_are_stable(self):
        database = self._fixture_database()
        records = database.export_applications()

        self.assertEqual(set(records[0]), set(FIELDS) | {"id", "created_at", "updated_at", "events", "tasks", "requirements", "artifacts", "submissions"})
        self.assertEqual(
            set(records[0]["events"][0]),
            {"id", "application_id", "event_type", "title", "details", "occurred_at", "created_at", "from_stage", "to_stage", "origin", "payload_json", "request_id"},
        )
        self.assertEqual(
            [event["title"] for event in database.list_events(3)],
            ["Interview notes", "Interview scheduled", "Application submitted"],
        )
        self.assertEqual(
            [event["title"] for event in records[0]["events"]],
            ["Application submitted", "Imported from example CSV"],
        )

    def test_backup_fixture_round_trips_domain_fields_and_events(self):
        backup = json.loads((FIXTURES / "jobflow_v3_backup.json").read_text(encoding="utf-8"))
        path = Path(self.tempdir.name) / "roundtrip.db"
        database = Database(path)
        database.initialize(seed=False)

        self.assertEqual(database.import_applications(backup["applications"], replace=True), 5)
        actual = database.export_applications()
        domain_fields = list(FIELDS)
        for expected, restored in zip(backup["applications"], actual):
            self.assertEqual(
                {field: restored[field] for field in ("company", "role", "location", "work_mode", "status", "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes")},
                {field: expected[field] for field in ("company", "role", "location", "work_mode", "status", "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes")},
            )
            self.assertEqual(restored["stage"], {"Wishlist": "Wishlist", "Applied": "Applied", "Interview": "Interview", "Offer": "Offer", "Rejected": "Closed"}[expected["status"]])
            self.assertEqual(restored["outcome"], "Rejected" if expected["status"] == "Rejected" else None)
            self.assertEqual(restored["version"], 1)
            self.assertEqual(
                [
                    (event["event_type"], event["title"], event["details"], event["occurred_at"])
                    for event in restored["events"]
                ],
                [
                    (event["event_type"], event["title"], event["details"], event["occurred_at"])
                    for event in expected["events"]
                ],
            )

    def test_empty_database_analytics_have_safe_zero_values(self):
        database = Database(Path(self.tempdir.name) / "empty.db")
        database.initialize(seed=False)

        analytics = database.analytics()

        self.assertEqual(analytics["total"], 0)
        self.assertEqual(analytics["submitted"], 0)
        self.assertEqual(analytics["response_rate"], 0)
        self.assertEqual(analytics["attention"], [])

    def test_future_database_schema_is_rejected_before_opening(self):
        path = Path(self.tempdir.name) / "future.db"
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version = 9")

        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            Database(path).initialize(seed=False)


if __name__ == "__main__":
    unittest.main()
