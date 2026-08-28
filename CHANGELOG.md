# Changelog

This file records user-visible changes to JobFlow.

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
