import tempfile
import unittest
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
        self.assertEqual(len(self.database.list_applications({"search": "python"})), 1)
        self.assertEqual(len(self.database.list_applications({"status": "Wishlist"})), 1)
        self.assertEqual(len(self.database.list_applications({"work_mode": "Remote"})), 1)

    def test_analytics(self):
        self.database.create_application(self.payload)
        self.database.create_application({**self.payload, "company": "Beta", "status": "Interview"})
        analytics = self.database.analytics()
        self.assertEqual(analytics["total"], 2)
        self.assertEqual(analytics["active"], 2)
        self.assertEqual(analytics["interviews"], 1)
        self.assertEqual(analytics["response_rate"], 50)


if __name__ == "__main__":
    unittest.main()
