import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobflow.database import Database
from jobflow.validation import validate_transition


class HistoricalInsightsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "insights.db")
        self.database.initialize(seed=False)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self):
        self.tempdir.cleanup()

    def application(self, *, source="Test", status="Wishlist"):
        return self.database.create_application({
            "company": f"Company {source} {status}",
            "role": "Software Engineer",
            "work_mode": "Remote",
            "status": status,
            "source": source,
        })

    def transition(self, application, stage, days_after, *, outcome=None):
        occurred_at = self.now - timedelta(days=20 - days_after)
        return self.database.transition_application(
            application["id"],
            validate_transition({"to_stage": stage, "outcome": outcome, "occurred_at": occurred_at.isoformat()}),
        )

    def test_funnel_keeps_responded_history_after_rejection(self):
        direct = self.application(source="Board")
        self.transition(direct, "Applied", 1)
        self.transition(direct, "Closed", 3, outcome="Rejected")

        interview = self.application(source="Referral")
        self.transition(interview, "Applied", 1)
        self.transition(interview, "Interview", 4)
        self.transition(interview, "Closed", 6, outcome="Rejected")

        result = self.database.insights("all")
        self.assertEqual(result["submitted"], 2)
        self.assertEqual(result["responded"], 2)
        self.assertEqual(result["interviewed"], 1)
        self.assertEqual(result["offered"], 0)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["no_response"], 0)
        self.assertEqual(result["source_conversion"][0]["source"], "Board")
        self.assertEqual(result["source_conversion"][1]["interviewed"], 1)

    def test_offer_and_acceptance_are_ever_reached(self):
        application = self.application(source="Company site")
        self.transition(application, "Applied", 1)
        self.transition(application, "Interview", 2)
        self.transition(application, "Offer", 4)
        self.transition(application, "Closed", 6, outcome="Accepted")

        result = self.database.insights("all")
        self.assertEqual((result["submitted"], result["responded"], result["interviewed"], result["offered"], result["accepted"]), (1, 1, 1, 1, 1))
        self.assertEqual(result["median_time_to_response"], 1.0)
        self.assertEqual(result["median_time_in_stage"], {"Applied": 1.0, "Interview": 2.0, "Offer": 2.0})

    def test_wishlist_and_no_response_are_not_false_submissions(self):
        self.application(source="Shortlist", status="Wishlist")
        waiting = self.application(source="Board")
        self.transition(waiting, "Applied", 2)

        result = self.database.insights("all")
        self.assertEqual(result["submitted"], 1)
        self.assertEqual(result["no_response"], 1)
        self.assertEqual(result["response_rate"], 0.0)
        self.assertEqual(result["source_conversion"][0]["submitted"], 1)

    def test_repeated_stage_events_do_not_create_zero_length_intervals(self):
        application = self.application(source="Repeat")
        self.transition(application, "Applied", 1)
        self.transition(application, "Interview", 3)
        self.transition(application, "Applied", 5)
        self.transition(application, "Interview", 7)

        result = self.database.insights("all")
        self.assertEqual(result["interviewed"], 1)
        self.assertEqual(result["median_time_in_stage"]["Applied"], 2.0)
        self.assertEqual(result["median_time_in_stage"]["Interview"], 2.0)

    def test_window_and_empty_denominators_are_explicit(self):
        old = self.application(source="Old")
        old_applied = self.now - timedelta(days=120)
        self.database.transition_application(old["id"], validate_transition({"to_stage": "Applied", "occurred_at": old_applied.isoformat()}))
        recent = self.application(source="Recent")
        self.transition(recent, "Applied", 2)

        recent_result = self.database.insights("30")
        self.assertEqual(recent_result["submitted"], 1)
        self.assertEqual(recent_result["source_conversion"][0]["source"], "Recent")
        self.assertEqual(self.database.insights("90")["submitted"], 1)
        self.assertEqual(self.database.insights("all")["submitted"], 2)

        empty_database = Database(Path(self.tempdir.name) / "empty.db")
        empty_database.initialize(seed=False)
        empty = empty_database.insights("30")
        self.assertEqual(empty["submitted"], 0)
        self.assertIsNone(empty["response_rate"])
        self.assertEqual(empty["source_conversion"], [])
        self.assertIn("selected window", empty["denominators"]["submitted"])

    def test_legacy_history_is_labeled_without_inventing_a_transition(self):
        application = self.application(source="Legacy", status="Applied")
        with self.database.connect() as connection:
            connection.execute("DELETE FROM application_events WHERE application_id = ?", (application["id"],))
            connection.execute(
                "INSERT INTO application_events (application_id, event_type, title, occurred_at, origin) VALUES (?, ?, ?, ?, ?)",
                (application["id"], "custom", "Imported legacy row", "", "legacy"),
            )
        result = self.database.insights("all")
        self.assertEqual(result["submitted"], 0)
        self.assertEqual(result["history_quality"]["limited"], 0)
        self.assertEqual(result["history_quality"]["limited_total"], 1)
        self.assertIn("not invented", result["history_quality"]["limited_note"])

    def test_older_applied_event_shape_is_counted_but_marked_incomplete(self):
        application = self.application(source="Old demo", status="Interview")
        result = self.database.insights("all")
        self.assertEqual(result["submitted"], 1)
        self.assertEqual(result["interviewed"], 0)
        self.assertEqual(result["history_quality"]["limited"], 1)

    def test_seeded_workspace_has_demo_history(self):
        seeded = Database(Path(self.tempdir.name) / "seeded.db")
        seeded.initialize(seed=True)
        result = seeded.insights("all")
        self.assertEqual(result["submitted"], 5)
        self.assertEqual(result["interviewed"], 3)
        self.assertEqual(result["offered"], 2)
        self.assertEqual(result["history_quality"]["limited"], 0)


if __name__ == "__main__":
    unittest.main()
