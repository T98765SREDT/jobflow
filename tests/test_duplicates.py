import tempfile
import unittest
import sqlite3
from pathlib import Path

from jobflow.database import Database
from jobflow.validation import (
    application_fingerprint,
    canonical_url,
    duplicate_reason,
    find_duplicate_matches,
)


class DuplicateIdentityTests(unittest.TestCase):
    def test_canonical_url_removes_tracking_and_fragment_but_keeps_identity_query(self):
        self.assertEqual(
            canonical_url("HTTPS://Example.COM:443/jobs//42/?utm_source=mail&job=42#description"),
            "https://example.com/jobs/42?job=42",
        )
        self.assertEqual(canonical_url("https://user:secret@example.com/jobs/42"), "https://example.com/jobs/42")

    def test_fallback_identity_normalizes_unicode_and_whitespace(self):
        left = {"company": "Ａｃｍｅ  Labs", "role": " Python  Developer ", "location": "Tokyo"}
        right = {"company": "Acme Labs", "role": "Python Developer", "location": " Tokyo "}
        self.assertEqual(application_fingerprint(left), application_fingerprint(right))
        self.assertEqual(duplicate_reason(left, right), "company_role_location")
        self.assertNotEqual(
            application_fingerprint({**right, "location": "Osaka"}),
            application_fingerprint(left),
        )

    def test_url_identity_is_preferred_and_missing_urls_use_fallback(self):
        incoming = {"company": "Different", "role": "Different", "location": "", "url": "https://example.com/job?id=7&utm_medium=x"}
        existing = {"company": "Acme", "role": "Developer", "location": "Tokyo", "url": "https://example.com/job?id=7"}
        self.assertEqual(duplicate_reason(incoming, existing), "canonical_url")
        self.assertEqual(find_duplicate_matches([incoming], [dict(existing, id=9)])[0]["existing_application_id"], 9)
        self.assertIsNone(duplicate_reason({**incoming, "url": ""}, existing))

    def test_merge_updates_only_explicit_non_empty_fields_and_records_event(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "jobflow.db")
            database.initialize(seed=False)
            current = database.create_application({
                "company": "Acme", "role": "Developer", "work_mode": "Remote", "status": "Applied",
                "location": "Tokyo", "source": "Referral", "notes": "Keep this note",
            })
            database.import_applications(
                [],
                merge_records=[(current["id"], {"location": "Osaka", "source": "", "notes": "New note"}, ["location", "source", "notes"])],
            )
            updated = database.get_application(current["id"])
            self.assertEqual(updated["location"], "Osaka")
            self.assertEqual(updated["source"], "Referral")
            self.assertEqual(updated["notes"], "New note")
            self.assertEqual(updated["version"], 2)
            self.assertTrue(any(event["origin"] == "import" for event in database.list_events(current["id"])))

    def test_merge_and_insert_batch_roll_back_together(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "jobflow.db")
            database.initialize(seed=False)
            current = database.create_application({
                "company": "Atomic Import", "role": "Engineer", "work_mode": "Remote", "status": "Applied",
                "location": "Tokyo",
            })
            with self.assertRaises(sqlite3.IntegrityError):
                database.import_applications(
                    [{"company": "Valid row", "role": "QA", "work_mode": "Remote", "status": "Wishlist"}, {}],
                    merge_records=[(current["id"], {"location": "Osaka"}, ["location"])],
                )
            self.assertEqual(database.get_application(current["id"])["location"], "Tokyo")
            self.assertEqual(len(database.export_applications()), 1)


if __name__ == "__main__":
    unittest.main()
