import tempfile
import unittest
import sqlite3
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
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_failed_replace_import_rolls_back(self):
        self.database.create_application(self.payload)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.import_applications([self.payload, {}], replace=True)
        self.assertEqual(len(self.database.export_applications()), 1)


if __name__ == "__main__":
    unittest.main()
