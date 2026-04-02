# Directives Index

One-line reference for each directive in this folder. Read the linked file for full rules and acceptance criteria.

- **[ADMIN_TEST_MODE.md](ADMIN_TEST_MODE.md)** — Contract for the dev-only Admin/Test Mode harness: seed leads, reset progress, and run deterministic scenarios against local SQLite without touching production.
- **[CORA_RECOMMENDATION_EVENTS.md](CORA_RECOMMENDATION_EVENTS.md)** — Spec for converting a lead's current state into a structured Cora-ready recommendation event payload.
- **[COURSE_STRUCTURE.md](COURSE_STRUCTURE.md)** — Defines the course identity, phase/section layout, and content structure for `FREE_INTRO_AI_V0`.
- **[GHL_GAP_ANALYSIS.md](GHL_GAP_ANALYSIS.md)** — Point-in-time snapshot comparing the repo against the Kes GHL meeting decisions; identifies built, partial, and missing integration pieces.
- **[GHL_INTEGRATION.md](GHL_INTEGRATION.md)** — Authoritative contract for how the app integrates with GoHighLevel: field mapping, payload rules, writeback transport, and error handling.
- **[HOT_LEAD_SIGNAL.md](HOT_LEAD_SIGNAL.md)** — Rule spec for the binary HOT lead signal: thresholds for course completion, invite status, and recent activity window.
- **[LEAD_TEMPERATURE_SCORING.md](LEAD_TEMPERATURE_SCORING.md)** — Spec for the multi-signal weighted scoring engine (0–100) that classifies leads as HOT / WARM / COLD.
- **[PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md)** — Top-level problem statement, MVP outcomes, data entities, acceptance criteria, and open questions for the Cold Lead Conversion System.
- **[SCAN_JOBS.md](SCAN_JOBS.md)** — Defines the read-only scan workers, scan naming conventions, requeue boundary, and expected outputs.
- **[SCAN_SCHEDULER_DESIGN.md](SCAN_SCHEDULER_DESIGN.md)** — Design-only directive (not yet implemented) for a future scheduled scan runner and cron orchestration layer.
- **[SYSTEM_STATUS_VS_SPEC.md](SYSTEM_STATUS_VS_SPEC.md)** — Dated build-vs-spec status report (2026-04-01) mapping each spec requirement to its implementation state.
- **[TRIGGER_OWNERSHIP_MATRIX.md](TRIGGER_OWNERSHIP_MATRIX.md)** — Defines which layer owns each trigger event (progress, GHL writeback, scan dispatch) and the boundary rules between them.
- **[UI_LEAD_STATUS_VIEW.md](UI_LEAD_STATUS_VIEW.md)** — Directive for the operator-facing Lead Status Viewer UI page: lookup contract, display fields, and no-new-logic constraint.
- **[UI_STUDENT_COURSE_PLAYER.md](UI_STUDENT_COURSE_PLAYER.md)** — Directive for the student-facing Course Player UI: section navigation, progress writes, and content rendering rules.
- **[UI_STUDENT_PORTAL_UX_UPGRADES.md](UI_STUDENT_PORTAL_UX_UPGRADES.md)** — Scope and goals for student portal UX improvements: LMS-style progress, navigation polish, and quiz/reflection rendering.
- **[course_player_regression_checklist.md](course_player_regression_checklist.md)** — Manual regression checklist for verifying Course Player navigation stability after session state or routing changes.
