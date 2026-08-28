import unittest

from jobflow.validation import ValidationError, validate_application


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
        self.assertEqual(validate_application({"status": "Interview"}, partial=True), {"status": "Interview"})

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


if __name__ == "__main__":
    unittest.main()
