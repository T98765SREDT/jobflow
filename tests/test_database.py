import tempfile
import unittest
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from jobflow.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "test.db")
        self.database.initialize(seed=False)
        self.payload = {
            "company": "Acme", "role": "Python Developer", "location": "Worldwide",
            "work_mode": "Remote", "status": "Applied", "source": "Test",
            "url": "", "salary_min": 25, "salary_max": 40, "currency": "USD",
            "salary_period": "Hourly",
            "applied_date": "2026-08-20", "next_action_date": "2026-08-28", "notes": "Follow up",
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_crud_lifecycle(self):
        created = self.database.create_application(self.payload)
        self.assertEqual(created["company"], "Acme")
        self.assertEqual(self.database.get_application(created["id"])["role"], "Python Developer")

        updated = self.database.update_application(created["id"], {"status": "Interview"})
        self.assertEqual(updated["status"], "Interview")
        self.assertTrue(self.database.delete_application(created["id"]))
        self.assertIsNone(self.database.get_application(created["id"]))

    def test_activity_events_follow_application_lifecycle(self):
        created = self.database.create_application(self.payload)
        initial_events = self.database.list_events(created["id"])
        self.assertEqual(len(initial_events), 1)
        self.assertEqual(initial_events[0]["event_type"], "applied")

        self.database.update_application(created["id"], {"status": "Interview", "next_action_date": "2026-08-30", "notes": "Prepare interview notes"})
        events = self.database.list_events(created["id"])
        self.assertEqual({event["event_type"] for event in events}, {"applied", "status_changed", "follow_up", "note"})

        self.assertTrue(self.database.delete_application(created["id"]))
        self.assertEqual(self.database.list_events(created["id"]), [])

    def test_export_and_import_preserve_activity_events(self):
        created = self.database.create_application(self.payload)
        self.database.create_event(created["id"], {
            "event_type": "interview",
            "title": "Technical interview",
            "details": "Discuss API design.",
            "occurred_at": "2026-08-27T09:00:00+00:00",
        })
        exported = self.database.export_applications()
        self.assertEqual(len(exported[0]["events"]), 2)

        self.database.delete_application(created["id"])
        self.database.import_applications(exported)
        restored = self.database.export_applications()
        self.assertEqual(len(restored[0]["events"]), 2)
        self.assertTrue(all(event["origin"] == "import" for event in restored[0]["events"]))

    def test_search_and_filters(self):
        self.database.create_application(self.payload)
        second = {**self.payload, "company": "Beta", "role": "QA Engineer", "status": "Wishlist", "work_mode": "Hybrid"}
        self.database.create_application(second)
        self.assertEqual(len(self.database.list_applications({"search": "python"})["items"]), 1)
        self.assertEqual(len(self.database.list_applications({"status": "Wishlist"})["items"]), 1)
        self.assertEqual(len(self.database.list_applications({"work_mode": "Remote"})["items"]), 1)

    def test_search_treats_sql_wildcards_as_text(self):
        self.database.create_application({**self.payload, "company": "100% Remote"})
        self.database.create_application({**self.payload, "company": "Ordinary Company"})
        result = self.database.list_applications({"search": "%"})
        self.assertEqual([item["company"] for item in result["items"]], ["100% Remote"])

    def test_pagination_and_saved_views(self):
        for index in range(5):
            status = "Interview" if index < 3 else "Wishlist"
            self.database.create_application({**self.payload, "company": f"Company {index}", "status": status})
        first_page = self.database.list_applications({"view": "interview", "limit": "2", "page": "1"})
        second_page = self.database.list_applications({"view": "interview", "limit": "2", "page": "2"})
        self.assertEqual(first_page["total"], 3)
        self.assertEqual(len(first_page["items"]), 2)
        self.assertEqual(len(second_page["items"]), 1)

    def test_analytics(self):
        self.database.create_application(self.payload)
        self.database.create_application({**self.payload, "company": "Beta", "status": "Interview"})
        analytics = self.database.analytics()
        self.assertEqual(analytics["total"], 2)
        self.assertEqual(analytics["active"], 2)
        self.assertEqual(analytics["interviews"], 1)
        self.assertEqual(analytics["response_rate"], 50)

    def test_analytics_prioritizes_attention_queue(self):
        today = date.today()
        self.database.create_application({
            **self.payload,
            "company": "Overdue Co",
            "next_action_date": str(today - timedelta(days=1)),
        })
        self.database.create_application({
            **self.payload,
            "company": "Missing Step",
            "next_action_date": None,
        })
        self.database.create_application({
            **self.payload,
            "company": "Closed Co",
            "status": "Rejected",
            "next_action_date": str(today - timedelta(days=2)),
        })
        analytics = self.database.analytics()
        self.assertEqual(analytics["attention_total"], 2)
        self.assertEqual([item["company"] for item in analytics["attention"]], ["Overdue Co", "Missing Step"])
        self.assertEqual([item["attention_type"] for item in analytics["attention"]], ["overdue", "missing"])

    def test_wishlist_does_not_change_interview_share(self):
        self.database.create_application({**self.payload, "company": "Interview", "status": "Interview"})
        self.database.create_application({**self.payload, "company": "Wishlist", "status": "Wishlist"})
        analytics = self.database.analytics()
        self.assertEqual(analytics["submitted"], 1)
        self.assertEqual(analytics["response_rate"], 100)

    def test_demo_seed_runs_only_for_a_new_database(self):
        path = Path(self.tempdir.name) / "seeded.db"
        database = Database(path)
        database.initialize(seed=True)
        for item in database.export_applications():
            database.delete_application(item["id"])
        database.initialize(seed=True)
        self.assertEqual(database.export_applications(), [])

    def test_schema_migrates_existing_database(self):
        path = Path(self.tempdir.name) / "legacy.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL, "
                "location TEXT NOT NULL DEFAULT '', work_mode TEXT NOT NULL, status TEXT NOT NULL, "
                "source TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', salary_min INTEGER, "
                "salary_max INTEGER, currency TEXT NOT NULL DEFAULT 'USD', applied_date TEXT, "
                "next_action_date TEXT, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO applications (company, role, work_mode, status) VALUES ('Legacy', 'Developer', 'Remote', 'Applied')"
            )
        database = Database(path)
        database.initialize()
        record = database.export_applications()[0]
        self.assertEqual(record["salary_period"], "Annual")
        with database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 8)

    def test_transition_writes_immutable_lifecycle_event(self):
        from jobflow.validation import validate_transition

        created = self.database.create_application(self.payload)
        result = self.database.transition_application(
            created["id"], validate_transition({"to_stage": "Interview", "expected_version": 1, "request_id": "db-transition-1"})
        )
        self.assertFalse(result["replayed"])
        self.assertEqual(result["application"]["version"], 2)
        self.assertEqual(result["event"]["from_stage"], "Applied")
        self.assertEqual(result["event"]["to_stage"], "Interview")
        self.assertEqual(result["event"]["origin"], "system")
        replay = self.database.transition_application(
            created["id"], validate_transition({"to_stage": "Interview", "expected_version": 1, "request_id": "db-transition-1"})
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.database.list_events(created["id"])), 2)

    def test_stage_and_outcome_are_migrated_from_legacy_status(self):
        created = self.database.create_application({**self.payload, "status": "Rejected"})
        self.assertEqual(created["stage"], "Closed")
        self.assertEqual(created["outcome"], "Rejected")
        self.assertIsNotNone(created["closed_at"])
        self.assertEqual(created["version"], 1)

    def test_stage_input_keeps_legacy_status_alias(self):
        created = self.database.create_application({
            **self.payload,
            "status": "Applied",
            "stage": "Interview",
        })
        self.assertEqual(created["stage"], "Interview")
        self.assertEqual(created["status"], "Interview")

    def test_failed_replace_import_rolls_back(self):
        self.database.create_application(self.payload)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.import_applications([self.payload, {}], replace=True)
        self.assertEqual(len(self.database.export_applications()), 1)


if __name__ == "__main__":
    unittest.main()
