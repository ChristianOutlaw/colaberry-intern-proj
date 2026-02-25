# UI_STUDENT_COURSE_PLAYER — Student Course Player (MVP v0)

## Purpose

A local, student-facing UI that allows a learner to identify themselves,
browse the nine canonical sections of `FREE_INTRO_AI_V0`, read section content,
mark sections complete, and see their progress update in real time.
All writes use existing execution functions only — no new business logic is
introduced at the UI layer.

---

## Inputs

| Field       | Type   | Required | Notes |
|-------------|--------|----------|-------|
| `lead_id`   | string | Yes      | Whitespace is trimmed before use. Show `"Lead ID is required."` if empty after trim. |
| `db_path`   | —      | Fixed    | Hard-coded to `tmp/app.db`. Not exposed to the learner. |
| `course_id` | —      | Fixed    | Hard-coded to `FREE_INTRO_AI_V0`. Not exposed to the learner. |

A confirmed `lead_id` persists for the session (no re-entry required between sections).

---

## Course Navigation

Sections must be listed in canonical order, as a clickable sidebar or selection list:

| Order | `section_id` | Title |
|-------|-------------|-------|
| 1 | `P1_S1` | What Is AI? |
| 2 | `P1_S2` | How Machines Learn |
| 3 | `P1_S3` | AI in the Real World |
| 4 | `P2_S1` | Understanding Data |
| 5 | `P2_S2` | Exploring Data |
| 6 | `P2_S3` | Preparing Data for AI |
| 7 | `P3_S1` | Building Your First Model |
| 8 | `P3_S2` | Evaluating Results |
| 9 | `P3_S3` | Next Steps in AI |

Selecting a section loads its content and shows the **Mark Complete** button.
The canonical order and titles are the source of truth for
`execution/course/course_registry.py` and `directives/COURSE_STRUCTURE.md`.

---

## Section Rendering

- The active section's content is rendered from the corresponding markdown file:
  `course_content/FREE_INTRO_AI_V0/{section_id}.md`
- Rendered read-only. The learner cannot edit content.
- If the file cannot be read, display: `"Section content unavailable."`
  Do not expose the file path or raw exception.

---

## Completion: "Mark Complete" Action

When the learner clicks **Mark Complete** for the active section, the UI must call
these execution functions **in order**, with no deviation:

1. `execution.leads.upsert_lead(lead_id, db_path=db_path)`
2. `execution.progress.record_progress_event(event_id, lead_id, section_id, occurred_at=now_utc, db_path=db_path)`
3. `execution.progress.compute_course_state(lead_id, total_sections=9, db_path=db_path)`
4. `execution.leads.get_lead_status(lead_id, db_path=db_path)` → refresh progress display

### Deterministic event_id

```
event_id = f"{lead_id}:{section_id}"
```

This format makes completion idempotent per lead per section:
a second click writes the same `event_id`, which `record_progress_event` silently
skips. `completion_pct` does not increase on a duplicate click.

### occurred_at

`occurred_at` is the current UTC time formatted as ISO 8601, injected by the UI
at click time:

```python
from datetime import datetime, timezone
occurred_at = datetime.now(timezone.utc).isoformat()
```

No execution function may call `datetime.now()` internally for this value.

---

## Progress Display

The progress panel must always be visible when a `lead_id` is active.
Refresh it after every successful **Mark Complete** call.

| UI Label          | Source field                                  | Fallback |
|-------------------|-----------------------------------------------|----------|
| Completion        | `status["course_state"]["completion_pct"]`    | `0.0 %`  |
| Progress bar      | `completion_pct / 100.0`                      | empty    |
| Current Section   | `status["course_state"]["current_section"]`   | `—`      |
| Last Activity     | `status["course_state"]["last_activity_at"]`  | `—`      |

`completion_pct` must be displayed to two decimal places (e.g., `11.11 %`,
`33.33 %`, `100.00 %`).

Completed sections must be visually distinguished in the navigation list
(e.g., a checkmark or strikethrough). The UI derives this by comparing
`current_section` and `completion_pct` from `get_lead_status`; it must
**not** query the `progress_events` table directly.

---

## Constraints

- No invite sending. The **Mark Complete** button is the only write action.
- No HotLeadSignal display; this UI is student-facing, not operator-facing.
- No outbound network calls. All reads are from local markdown files and SQLite.
- No secrets or environment variables required.
- `total_sections` is always `9` for `FREE_INTRO_AI_V0`. Hard-coded, not a UI input.

---

## Error Handling

| Condition | Behaviour |
|-----------|-----------|
| Empty or whitespace-only `lead_id` | Inline: `"Lead ID is required."` Block all actions. |
| Section markdown file unreadable | Section area shows: `"Section content unavailable."` Mark Complete still available. |
| `record_progress_event` raises `ValueError` (invalid section_id) | Show: `"Cannot record completion: unrecognised section."` Log to console. |
| SQLite error during any write step | Show: `"Could not save progress. Check that tmp/app.db is accessible."` |
| Any other unexpected exception | Show: `"An unexpected error occurred. See console for details."` Log with `logging.exception`. |

Errors must appear in the UI, not only in the console.
Raw exception messages and tracebacks must never be shown to the learner.

---

## Acceptance Criteria

| # | Scenario | Expected result |
|---|----------|-----------------|
| AC1 | Learner marks `P1_S1` complete | `completion_pct` = `11.11 %`; progress bar at ~1/9 |
| AC2 | Learner marks `P1_S1`, `P1_S2`, `P1_S3` complete | `completion_pct` = `33.33 %` |
| AC3 | Learner clicks **Mark Complete** on an already-completed section | `completion_pct` unchanged; no second `ProgressEvent` row created |
| AC4 | Learner marks all 9 sections complete | `completion_pct` = `100.00 %` |
| AC5 | `lead_id` field submitted with surrounding whitespace | Trimmed value used in all function calls; no error raised |
| AC6 | Section content file is missing | `"Section content unavailable."` shown; rest of UI remains functional |
| AC7 | Single-command local launch with no secrets or environment variables | `streamlit run ui/course_player.py` (or equivalent) works out of the box |
