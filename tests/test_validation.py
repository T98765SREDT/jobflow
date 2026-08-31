import unittest

from jobflow.validation import ValidationError, validate_application, validate_as_of, validate_task, validate_transition


class ValidationTests(unittest.TestCase):
    def test_normalizes_valid_application(self):
        result = validate_application({
            "company": "  Acme  ", "role": "Backend Developer",
            "status": "Applied", "work_mode": "Remote",
            "salary_min": "20", "salary_max": 30.5, "salary_period": "Hourly", "currency": "usd",
            "url": "https://example.com/job", "applied_date": "2026-08-24",
        })
        self.assertEqual(result["company"], "Acme")
        self.assertEqual(result["salary_min"], 20)
        self.assertEqual(result["salary_max"], 30.5)
        self.assertEqual(result["salary_period"], "Hourly")
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["notes"], "")

    def test_reports_multiple_field_errors(self):
        with self.assertRaises(ValidationError) as context:
            validate_application({
                "company": "", "role": "Developer", "status": "Unknown",
                "work_mode": "Remote", "salary_min": 80, "salary_max": 20,
                "url": "example.com", "unexpected": True,
            })
        errors = context.exception.errors
        self.assertIn("company", errors)
        self.assertIn("status", errors)
        self.assertIn("salary_max", errors)
        self.assertIn("url", errors)
        self.assertIn("body", errors)

    def test_partial_update_does_not_require_unchanged_fields(self):
        result = validate_application({"status": "Interview"}, partial=True)
        self.assertEqual(result["stage"], "Interview")
        self.assertEqual(result["status"], "Interview")

    def test_accepts_new_stage_and_closed_outcome(self):
        result = validate_application({
            "company": "Acme", "role": "Developer", "stage": "Closed",
            "outcome": "Accepted", "closed_at": "2026-08-29T10:00:00Z", "work_mode": "Remote",
        })
        self.assertEqual(result["stage"], "Closed")
        self.assertEqual(result["status"], "Rejected")
        self.assertEqual(result["outcome"], "Accepted")
        self.assertEqual(result["version"], 1)

    def test_rejects_conflicting_stage_and_status(self):
        with self.assertRaises(ValidationError) as context:
            validate_application({
                "company": "Acme", "role": "Developer", "stage": "Interview",
                "status": "Applied", "work_mode": "Remote",
            })
        self.assertIn("stage", context.exception.errors)

    def test_closed_invariants_are_enforced(self):
        with self.assertRaises(ValidationError) as context:
            validate_application({
                "company": "Acme", "role": "Developer", "stage": "Closed", "work_mode": "Remote",
            })
        self.assertIn("outcome", context.exception.errors)
        self.assertIn("closed_at", context.exception.errors)
        with self.assertRaises(ValidationError) as context:
            validate_application({
                "company": "Acme", "role": "Developer", "status": "Applied", "outcome": "Rejected", "work_mode": "Remote",
            })
        self.assertIn("outcome", context.exception.errors)

    def test_rejects_non_object_body(self):
        with self.assertRaises(ValidationError):
            validate_application(["not", "an", "object"])

    def test_rejects_salary_precision_invalid_url_and_date_order(self):
        with self.assertRaises(ValidationError) as context:
            validate_application({
                "company": "Acme", "role": "Developer", "status": "Applied", "work_mode": "Remote",
                "salary_min": "20.123", "url": "https://exa mple.com/job",
                "applied_date": "2026-08-24", "next_action_date": "2026-08-23",
            })
        self.assertIn("salary_min", context.exception.errors)
        self.assertIn("url", context.exception.errors)
        self.assertIn("next_action_date", context.exception.errors)

    def test_defaults_salary_period_for_legacy_records(self):
        result = validate_application({
            "company": "Acme", "role": "Developer", "status": "Applied", "work_mode": "Remote",
        })
        self.assertEqual(result["salary_period"], "Annual")

    def test_transition_payload_defaults_timestamp_and_validates_close(self):
        result = validate_transition({"to_stage": "Interview", "request_id": "  req-1  "})
        self.assertEqual(result["request_id"], "req-1")
        self.assertTrue(result["occurred_at"])
        with self.assertRaises(ValidationError) as context:
            validate_transition({"to_stage": "Closed"})
        self.assertIn("outcome", context.exception.errors)

    def test_task_and_today_date_validation(self):
        task = validate_task({"kind": "preparation", "title": "Prepare examples", "due_date": "2026-09-01"})
        self.assertEqual(task["version"], 1)
        self.assertIsNone(task["completed_at"])
        self.assertEqual(validate_as_of("2026-09-01"), "2026-09-01")
        with self.assertRaises(ValidationError) as context:
            validate_task({"kind": "unknown", "title": "", "due_date": "tomorrow"})
        self.assertIn("kind", context.exception.errors)
        self.assertIn("title", context.exception.errors)
        self.assertIn("due_date", context.exception.errors)


if __name__ == "__main__":
    unittest.main()
