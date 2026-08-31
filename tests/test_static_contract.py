import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        cls.demo = (ROOT / "static" / "demo-data.js").read_text(encoding="utf-8")

    def test_runtime_badge_explains_local_and_demo_modes(self):
        self.assertIn('id="runtime-badge"', self.index)
        self.assertIn('id="runtime-label"', self.index)
        self.assertIn("Python API + SQLite", self.index)
        self.assertIn('id="system-error-command"', self.index)
        self.assertIn("Synthetic localStorage data", self.app)
        self.assertIn("Browser demo", self.app)

    def test_today_action_center_has_recovery_and_task_actions(self):
        self.assertIn('id="today"', self.index)
        self.assertIn('id="today-list"', self.index)
        self.assertIn('id="refresh-today"', self.index)
        self.assertIn("/api/today", self.app)
        self.assertIn("data-complete-task", self.app)
        self.assertIn("data-snooze-task", self.app)

    def test_board_view_uses_shared_filters_and_accessible_move(self):
        self.assertIn('data-display-view="table"', self.index)
        self.assertIn('data-display-view="board"', self.index)
        self.assertIn('id="board-wrap"', self.index)
        self.assertIn("function renderBoard()", self.app)
        self.assertIn("data-board-move", self.app)
        self.assertIn("/transitions", self.app)
        self.assertIn('state.displayView', self.app)

    def test_workspace_tabs_and_aggregate_snapshot_are_present(self):
        for tab in ("Overview", "Tasks", "Materials", "Requirements", "Activity"):
            self.assertIn(tab, self.app)
        self.assertIn("/workspace", self.app)
        self.assertIn("open_tasks", self.app)
        self.assertIn("completed_tasks", self.app)
        self.assertIn("requirement_summary", self.app)
        self.assertIn("/api/applications/${state.selectedId}/requirements", self.app)
        self.assertIn("data-reorder-requirement", self.app)
        self.assertIn('method: "PUT"', self.app)
        self.assertIn("/api/applications/${state.selectedId}/artifacts", self.app)
        self.assertIn("/api/applications/${state.selectedId}/submissions", self.app)
        self.assertIn("data-edit-artifact", self.app)
        self.assertIn("data-new-artifact-version", self.app)
        self.assertIn("function startArtifactVersion", self.app)
        self.assertIn("Read-only snapshot", self.app)

    def test_first_run_guidance_has_distinct_actions(self):
        for element_id in ("first-run", "first-add-application", "first-import", "learn-workflow", "workflow-guide"):
            self.assertIn(f'id="{element_id}"', self.index)
        self.assertIn("function renderFirstRun()", self.app)
        self.assertIn("state.analytics.total === 0", self.app)
        self.assertIn('id="empty-state"', self.index)

    def test_form_starts_with_essential_fields_and_has_more_details(self):
        core = self.index.split('<details class="form-more"', 1)[0]
        for field in ('name="company"', 'name="role"', 'name="status"', 'name="next_action_date"', 'name="url"'):
            self.assertIn(field, core)
        self.assertIn('<details class="form-more" id="more-details">', self.index)
        self.assertIn("Location, source, compensation, and notes", self.index)
        self.assertIn('name="waiting_until"', self.index)

    def test_form_and_loading_states_are_accessible(self):
        self.assertIn('id="form-errors" role="alert" tabindex="-1"', self.index)
        self.assertIn('id="loading-state" role="status"', self.index)
        self.assertIn('aria-live="polite"', self.index)
        self.assertIn('setAttribute("aria-invalid", "true")', self.app)

    def test_draft_recovery_and_demo_cache_recovery_are_explicit(self):
        self.assertIn('DRAFT_STORAGE_KEY = "jobflow.application-draft.v1"', self.app)
        self.assertIn("sessionStorage", self.app)
        self.assertIn("persistDraft", self.app)
        self.assertIn("clearDraft", self.app)
        self.assertIn('STORAGE_KEY = "jobflow.portfolio.v2"', self.demo)
        self.assertIn("[2, 3, 4, 5, 6, 7, SCHEMA_VERSION]", self.demo)
        for element_id in ("demo-recovery-dialog", "download-corrupt-cache", "reset-corrupt-demo", "cancel-demo-recovery"):
            self.assertIn(f'id="{element_id}"', self.index)
        self.assertIn("JobFlowDemoHasCorruptCache", self.demo)
        self.assertIn("JobFlowDemoRawCache", self.demo)

    def test_import_reconciliation_has_preview_decisions_and_recovery_report(self):
        for element_id in ("import-duplicates", "csv-duplicates", "download-csv-errors"):
            self.assertIn(f'id="{element_id}"', self.index)
        for marker in ("/api/import/preview", "duplicate_decisions", "DUPLICATES_FOUND", "renderDuplicateConflicts", "Keep as separate application"):
            self.assertIn(marker, self.app)
        self.assertIn("previewDuplicates", self.demo)

    def test_historical_insights_contract_is_shared_by_api_and_demo(self):
        for marker in ('id="historical-insights-panel"', 'id="insights-window"', 'id="historical-funnel"', 'id="source-conversion"'):
            self.assertIn(marker, self.index)
        for marker in ("/api/insights", "function refreshInsights()", "function renderInsights()", "history_quality", "source_conversion", "acceptance_rate"):
            self.assertIn(marker, self.app)
        for marker in ("function historicalInsights", "/api/insights", "median_time_in_stage", "no_response"):
            self.assertIn(marker, self.demo)

    def test_error_recovery_contract_is_explicit(self):
        for marker in ("AbortController", "MAX_GET_RETRIES", "X-Request-ID", "Retry-After", "VERSION_CONFLICT", "Review latest", "Keep my changes"):
            self.assertIn(marker, self.app)
        for marker in ("request_id", "retryable", "_error_payload", "DATABASE_BUSY", "INTERNAL_ERROR"):
            self.assertIn(marker, (ROOT / "jobflow" / "server.py").read_text(encoding="utf-8"))
        self.assertIn('data-error-action="cancel"', self.app)
        self.assertIn('id="analytics-error"', self.index)
        self.assertIn('id="insights-error"', self.index)
        self.assertIn("showRegionError", self.app)


if __name__ == "__main__":
    unittest.main()
