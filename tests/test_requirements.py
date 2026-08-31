import tempfile
import unittest
from pathlib import Path

from jobflow.database import Database
from jobflow.validation import ValidationError, validate_requirement


class RequirementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "requirements.db")
        self.database.initialize()
        self.application = self.database.create_application({
            "company": "Example Labs",
            "role": "Python Engineer",
            "work_mode": "Remote",
            "status": "Applied",
        })

    def tearDown(self):
        self.tempdir.cleanup()

    def test_validation_enforces_enums_and_bounds(self):
        valid = validate_requirement({
            "criterion": "Python",
            "category": "skill",
            "assessment": "met",
            "evidence": "Built a small API.",
            "weight": 5,
            "position": 0,
        })
        self.assertEqual(valid["assessment"], "met")
        with self.assertRaises(ValidationError) as context:
            validate_requirement({"criterion": "Python", "category": "other", "assessment": "maybe", "weight": 6})
        self.assertIn("assessment", context.exception.errors)
        self.assertIn("weight", context.exception.errors)
        with self.assertRaises(ValidationError) as context:
            validate_requirement({"criterion": "Python", "category": "skill", "position": -1})
        self.assertIn("position", context.exception.errors)

    def test_summary_excludes_unknown_from_known_weight(self):
        requirements = [
            {"assessment": "met", "weight": 5, "evidence": "Project"},
            {"assessment": "partial", "weight": 3, "evidence": "Course"},
            {"assessment": "gap", "weight": 2, "evidence": "Not yet"},
            {"assessment": "unknown", "weight": 5, "evidence": "Check later"},
        ]
        summary = self.database.summarize_requirements(requirements)
        self.assertEqual(summary["counts"], {"met": 1, "partial": 1, "gap": 1, "unknown": 1})
        self.assertEqual(summary["known_weight"], 10)
        self.assertEqual(summary["covered_weight"], 6.5)
        self.assertEqual(summary["coverage"], 65.0)
        self.assertEqual(summary["known_count"], 3)
        self.assertEqual(summary["missing_evidence_met"], 0)
        missing = self.database.summarize_requirements([{"assessment": "met", "weight": 1, "evidence": ""}])
        self.assertEqual(missing["missing_evidence_met"], 1)

    def test_crud_preserves_position_order_and_cascades(self):
        application_id = self.application["id"]
        first = self.database.create_requirement(application_id, {
            "criterion": "Python", "category": "skill", "assessment": "met", "position": 2, "weight": 5,
        })
        second = self.database.create_requirement(application_id, {
            "criterion": "Mandarin", "category": "language", "assessment": "unknown", "position": 1, "weight": 2,
        })
        self.assertEqual([item["id"] for item in self.database.list_requirements(application_id)], [second["id"], first["id"]])
        updated = self.database.update_requirement(first["id"], {"position": 0, "assessment": "partial"})
        self.assertEqual(updated["assessment"], "partial")
        self.assertEqual(self.database.list_requirements(application_id)[0]["id"], first["id"])
        self.assertTrue(self.database.delete_requirement(second["id"]))
        self.assertFalse(self.database.delete_requirement(second["id"]))
        self.assertTrue(self.database.delete_application(application_id))
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM application_requirements").fetchone()[0]
        self.assertEqual(count, 0)

    def test_export_import_round_trip_preserves_requirements(self):
        application_id = self.application["id"]
        self.database.create_requirement(application_id, {
            "criterion": "SQL joins", "category": "skill", "assessment": "partial",
            "evidence": "SQLite project", "weight": 3, "position": 0,
        })
        exported = self.database.export_applications()
        self.assertEqual(len(exported[0]["requirements"]), 1)
        self.database.delete_application(application_id)
        self.database.import_applications(exported)
        restored = self.database.export_applications()
        self.assertEqual(restored[0]["requirements"][0]["criterion"], "SQL joins")
        self.assertEqual(restored[0]["requirements"][0]["assessment"], "partial")


if __name__ == "__main__":
    unittest.main()
