# UI_LEAD_STATUS_VIEW — Lead Status Viewer (MVP v1)

## Purpose

Provides a minimal local UI that lets an operator or intern look up a lead's
current status by ID. The page calls existing execution functions directly —
no new business logic is introduced at the UI layer. This satisfies the
interactive verification need raised in `PROJECT_BLUEPRINT.md §9` ("Is storage +
API sufficient first, or is a dashboard view required for MVP?").

---

## Inputs

| Field            | Type    | Required | Default | Notes |
|------------------|---------|----------|---------|-------|
| `lead_id`        | string  | Yes      | —       | Non-empty; no whitespace trimming assumed — caller must supply a clean ID |
| `total_sections` | integer | No       | `10`    | Used by `compute_course_state`; must be ≥ 1 |

---

## Action: "Fetch Status"

On submit, the UI layer must call these execution functions **in order**, passing
the local SQLite path (default `tmp/app.db`):

1. `execution.leads.upsert_lead` — ensure lead row exists (read-safe; idempotent).
2. `execution.leads.get_lead_status(lead_id, db_path)` — returns the full
   `LeadStatus` dict including computed `HotLeadSignal`.

No other network calls, external APIs, or database writes are made on fetch.
`compute_course_state` is **not** called on fetch — the UI reads what is already
stored; it does not recompute state on demand.

---

## Display Requirements

All fields map directly from the `LeadStatus` dict returned by `get_lead_status`.

| UI Label              | Source field                            | Fallback when `None` |
|-----------------------|-----------------------------------------|----------------------|
| Invite Sent           | `status["invite_sent"]`                 | `No`                 |
| Course Completion     | `status["course_state"]["completion_pct"]` | `—`               |
| Last Activity         | `status["course_state"]["last_activity_at"]` | `—`             |
| Current Section       | `status["course_state"]["current_section"]` | `—`              |
| Hot Lead Signal       | `status["hot_lead"]["signal"]`          | `—`                  |
| Reason                | `status["hot_lead"]["reason"]`          | `—`                  |

Display `None` values as `—` (em dash). Do not hide rows — always render all
six labels so the layout is stable regardless of data state.

---

## Error Handling

| Condition | Behaviour |
|-----------|-----------|
| `lead_id` is empty or whitespace-only | Show inline validation message before calling any function: `"Lead ID is required."` |
| `total_sections` < 1 | Show inline validation: `"Total sections must be at least 1."` |
| `get_lead_status` returns `lead_exists: False` | Display a clearly visible notice: `"Lead not found."` Render all display fields as `—`. |
| SQLite file missing or unreadable | Catch the exception; display: `"Database unavailable. Check that tmp/app.db exists."` Do not expose the raw exception to the UI. |
| Any other unexpected exception | Log to console; display: `"An unexpected error occurred. See console for details."` |

---

## Acceptance Criteria

- A junior developer can run the UI locally with a single command and no
  configuration beyond a Python 3.12+ environment.
- No secrets, API keys, or environment variables are required.
- The page makes no outbound network calls.
- All data displayed is read from the local SQLite file (`tmp/app.db` by default).
- Results are deterministic: the same database state always produces the same output.
- The UI must not write to the database (fetch is read-only from the user's perspective).
- Error states are shown in the UI, not only in the console.

---

## Out of Scope (v1)

- Authentication or access control.
- Editing lead data from the UI.
- Triggering invite sends or progress recordings from the UI.
- Pagination or multi-lead search.
- Any styling beyond functional readability.
