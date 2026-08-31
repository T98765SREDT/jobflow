# Changelog

This file records user-visible changes to JobFlow.

## Unreleased

- Added first-run guidance with separate add, import, and workflow-help actions for an empty local workspace.
- Added an explicit runtime badge for the Python API + SQLite app versus the synthetic browser demo.
- Simplified the application form around Company, Role, Stage, Next action, and Job URL, with secondary fields under More details.
- Added session-only draft recovery and a retry/start-command state that keeps input when the local API is unavailable.
- Added a non-destructive browser-demo cache recovery flow with raw-cache download, reset, and cancel choices.
- Added static contract checks for onboarding, accessibility, legacy demo storage, and recovery behavior.
- Upgraded the lifecycle model to schema v5 with separate stages, terminal outcomes, closed timestamps, and optimistic version values while retaining the deprecated status alias.
- Added atomic v3-to-v5 migration, stage/status conflict validation, and lifecycle options to the metadata endpoint.
- Added an audited transition API with transactional version increments, idempotent request IDs, stale-write responses, structured stage history, and protected system/import/legacy events.
- Added transition controls and explicit conflict-review actions to the application details view; the browser demo mirrors the same lifecycle contract.
- Upgraded the domain to schema v6 with first-class application tasks and a `waiting_until` pause date.
- Added task APIs for listing, creating, editing, completing, and snoozing work with version-aware conflict responses.
- Added the deterministic `/api/today` action feed for overdue, due-today, upcoming, waiting, and missing-next-step work.
- Migrated legacy `next_action_date` values into follow-up tasks, kept the field as the earliest-open-task compatibility projection, and included tasks in backup/restore and the browser demo.
- Added a Today action center with overdue, due-today, missing-next-step, waiting, and upcoming groups, inline complete/snooze actions, row-level busy states, and a refresh path that preserves the last visible data.
- Added an aggregate application workspace endpoint so Overview, Tasks, and Activity render from one consistent snapshot.
- Added an accessible Table/Board display switch with shared URL-synchronised filters, stage columns, collapsed Closed records, card age/next-action context, and a keyboard-friendly Move to… transition control.
- Added task creation and completion inside the application workspace while keeping closed-record history read-only.
- Added schema v7 application requirements with categories, met/partial/gap/unknown assessments, supporting evidence, weighted coverage, stable ordering, CRUD APIs, cascade deletion, backup/restore, and a Requirements workspace tab.
- Added schema v8 application materials with http(s)-only metadata links, version labels, immutable submission snapshots, protected referenced materials, backup/restore support, and a Materials workspace tab mirrored by the browser demo.
- Added a no-write import preview with canonical URL and company/role/location duplicate fingerprints, deterministic in-file conflict detection, and explicit Skip, Keep separate, or field-by-field Merge decisions.
- Added atomic import reconciliation with import-origin audit events, version increments for merges, invalid-row CSV reports, and matching browser-demo behavior.
- Added event-based historical funnel and source insights with 30/90/all windows, explicit denominators, response timing, completed stage-duration medians, limited-history labels, and matching browser-demo rendering.
- Added a versioned error envelope with stable error codes, field maps, retryability, request ids, and safe route-specific conflict details.
- Added bounded GET/HEAD retry and abort handling in the browser client; mutating requests are never retried implicitly.
- Added explicit recovery actions for stale writes, preserved drafts until save/cancel, request-id display for page/form failures, and database-busy handling without leaking paths or stack traces.
- Added a disposable benchmark for filtered lists, Today, workspace snapshots, and historical insights with median/p95 reporting and no fixed marketing threshold.
- Added cross-layer Playwright workflows for the primary record lifecycle, draft recovery, keyboard navigation, and 375px layout checks; CI runs them in Chromium, Firefox, and WebKit.
- Added browser evidence and benchmark documentation, and made the Pages deployment wait for a repository verification job.

## [1.1.0] - 2026-08-28

- Added saved pipeline views, literal search filtering, pagination, and URL-synchronised filters.
- Added a responsive details drawer with follow-up urgency states and salary-period support.
- Added JSON backup/restore and spreadsheet-safe CSV export with atomic server-side import validation.
- Added SQLite schema migration, WAL/busy-timeout settings, merged-record PATCH validation, and safer static/API boundaries.
- Added persistent browser-demo storage, reset controls, and recovery messaging for failed refreshes.
- Expanded the test suite to 27 checks and refreshed the interface for keyboard, focus, reduced-motion, and 320px layouts.
- Replaced native destructive-action prompts with accessible confirmation dialogs, added `Cmd/Ctrl+K` search focus, and exposed live result updates to assistive technology.
- Added iCalendar export for applications with a next-action date, including status, job URL, and notes in each event.
- Added an application activity timeline with automatic lifecycle events, manual notes/interviews, API routes, backup/restore support, and a schema migration to version 3.
- Added a prioritized needs-attention queue for overdue, due-today, due-soon, and active applications without a next action, mirrored in the browser demo and analytics API.
- Added CSV import preview with automatic header matching, manual field mapping, duplicate-row skipping, required-field feedback, and atomic append through the existing backup boundary.
- Added dependency-free Node checks for quoted CSV parsing and import mapping behavior.

## [1.0.0] - 2026-08-27

- Added a Python JSON API with SQLite storage and server-side validation.
- Added create, edit, delete, search, filter, sort, and pipeline analytics workflows.
- Added a responsive JavaScript interface and a browser-only portfolio demo.
- Added 10 automated tests for validation, persistence, analytics, static delivery, and API behavior.
- Added CI checks, architecture notes, contribution guidance, security scope, and an MIT license.
