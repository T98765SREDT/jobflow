import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobflow.database import Database, ProtectedArtifactError
from jobflow.validation import ValidationError, validate_artifact, validate_submission


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "jobflow.db")
        self.database.initialize(seed=False)
        self.application = self.database.create_application({
            "company": "Materials Co",
            "role": "Python Engineer",
            "location": "Worldwide",
            "work_mode": "Remote",
            "status": "Applied",
            "source": "Test",
            "url": "",
            "salary_min": None,
            "salary_max": None,
            "currency": "USD",
            "salary_period": "Annual",
            "applied_date": "2026-08-20",
            "next_action_date": None,
            "notes": "",
        })

    def tearDown(self):
        self.tempdir.cleanup()

    def test_material_validators_allow_http_metadata_and_reject_local_files(self):
        artifact = validate_artifact({
            "kind": "resume",
            "label": "Resume v1",
            "uri": "https://example.com/resume",
            "version_label": "v1",
            "notes": "Backend-focused version",
        })
        self.assertEqual(artifact["kind"], "resume")
        with self.assertRaises(ValidationError) as context:
            validate_artifact({"kind": "resume", "label": "Local file", "uri": "file:///tmp/resume.pdf"})
        self.assertIn("uri", context.exception.errors)
        with self.assertRaises(ValidationError) as context:
            validate_submission({"artifact_ids": [1, 1]})
        self.assertIn("artifact_ids", context.exception.errors)

    def test_snapshot_is_immutable_and_referenced_material_is_protected(self):
        first = self.database.create_artifact(self.application["id"], validate_artifact({"kind": "resume", "label": "Resume v1", "version_label": "v1"}))
        second = self.database.create_artifact(self.application["id"], validate_artifact({"kind": "portfolio", "label": "JobFlow demo", "version_label": "v2"}))
        submission = self.database.create_submission(self.application["id"], validate_submission({"artifact_ids": [first["id"], second["id"],], "notes": "Submitted"}))
        self.database.update_artifact(first["id"], validate_artifact({"label": "Resume revised"}, partial=True))
        restored = self.database.get_submission(submission["id"])
        self.assertEqual(restored["items"][0]["snapshot_label"], "Resume v1")
        with self.assertRaises(ProtectedArtifactError) as context:
            self.database.delete_artifact(first["id"])
        self.assertEqual(context.exception.package_ids, [submission["id"]])
        self.assertTrue(self.database.delete_application(self.application["id"]))
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM application_artifacts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM submission_packages").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM submission_package_items").fetchone()[0], 0)

    def test_export_import_round_trip_preserves_material_versions_and_snapshots(self):
        artifact = self.database.create_artifact(self.application["id"], validate_artifact({"kind": "cover_letter", "label": "Cover letter", "version_label": "v3", "notes": "Tailored"}))
        self.database.create_submission(self.application["id"], validate_submission({"artifact_ids": [artifact["id"]], "notes": "Portal submission"}))
        exported = self.database.export_applications()
        restored_database = Database(Path(self.tempdir.name) / "restored.db")
        restored_database.initialize(seed=False)
        self.assertEqual(restored_database.import_applications(exported), 1)
        restored = restored_database.export_applications()[0]
        self.assertEqual(restored["artifacts"][0]["version_label"], "v3")
        self.assertEqual(restored["submissions"][0]["items"][0]["snapshot_label"], "Cover letter")
        self.assertEqual(restored["submissions"][0]["items"][0]["snapshot_version_label"], "v3")

    def test_unknown_snapshot_reference_rolls_back_the_import(self):
        exported = self.database.export_applications()[0]
        exported["artifacts"] = [{"id": 10, "kind": "resume", "label": "Resume", "uri": "", "version_label": "v1", "notes": ""}]
        exported["submissions"] = [{"submitted_at": "2026-08-29T00:00:00+00:00", "notes": "bad backup", "items": [{"artifact_id": 999}]}]
        target = Database(Path(self.tempdir.name) / "target.db")
        target.initialize(seed=False)
        with self.assertRaises(ValueError):
            target.import_applications([exported], replace=True)
        self.assertEqual(target.export_applications(), [])


if __name__ == "__main__":
    unittest.main()
