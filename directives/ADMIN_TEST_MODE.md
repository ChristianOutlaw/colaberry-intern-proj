# ADMIN_TEST_MODE — Dev/Test Harness Contract (MVP v1)

## Purpose / Scope

This directive defines the contract for a **dev-only Admin/Test Mode harness** — a
local operator tool that lets a developer seed lead data, reset progress state, and
run predefined deterministic scenarios against a local SQLite database without
touching production.

The harness exists solely to support local development, manual QA, and unit test
fixture setup. It calls existing `/execution` functions only — it introduces no
new business logic and performs no writes outside the designated dev database.

This directive is the single source of truth for what the harness is allowed to do,
what it must never do, and how correctness is verified.

---

## Non-Goals

The following are explicitly **out of scope** and must never be implemented inside
the harness:

- **No production writes.** The harness must never connect to any database other
  than the dev/test SQLite file (`tmp/app.db` or an explicitly supplied test path).
- **No real communications.** No SMS, email, or GHL push may be triggered — even
  indirectly — during a harness operation.
- **No new business logic.** The harness orchestrates existing `/execution`
  functions only. It does not contain rule evaluation, scoring, or routing decisions.
- **No secrets.** The harness requires no API keys, credentials, or environment
  variables beyond `db_path`.
- **No UI simulation in prose.** If a UI layer is built over this harness, its
  behaviour must be verified by automated tests, not narrative description.
- **No production environment detection bypass.** If a `COLABERRY_ENV` guard or
  equivalent is added in the future, the harness must respect it unconditionally.

---

## Allowed Operations

### Operation 1 — Seed Lead

Creates or updates a lead row and optionally records a course invite.

**Steps (in order, no deviation):**

1. Call `execution.leads.upsert_lead(lead_id, name, phone, email, db_path=db_path)`
2. If `mark_invite_sent` is `True`, call:
   `execution.leads.mark_course_invite_sent(invite_id, lead_id, sent_at, channel, db_path=db_path)`

Both calls are idempotent. Running Seed Lead twice on the same `lead_id` is safe
and produces the same final state.

---

### Operation 2 — Reset Progress

Deletes all `ProgressEvent` rows for a given `lead_id` (and optionally all
`CourseInvite` rows), returning the lead to a clean pre-activity state.

**Steps:**

1. DELETE from `progress_events` WHERE `lead_id = ?`
2. If `reset_invite` is `True`: DELETE from `course_invites` WHERE `lead_id = ?`
3. Optionally call `execution.leads.upsert_lead(lead_id, db_path=db_path)` to
   confirm the lead row still exists after reset.

Reset is **destructive and irreversible** within a session. A confirmation prompt
(or explicit `confirm=True` flag) is required before execution (see Safety
Constraints).

The lead row itself (`leads` table) is **never deleted** by this operation.

---

### Operation 3 — Simulate Scenario

Runs a named, predefined sequence of operations to place a lead into a known
deterministic state. Scenarios are fixed; custom sequences are not allowed via
this interface.

**Defined scenarios (exhaustive for v1):**

| Scenario ID         | Description                                                      | Operations applied |
|---------------------|------------------------------------------------------------------|--------------------|
| `COLD_NO_INVITE`    | Lead exists, no invite, no progress.                             | Seed Lead only (no invite) |
| `INVITED_NO_PROGRESS` | Lead invited but no sections completed.                        | Seed Lead + mark invite sent |
| `PARTIAL_PROGRESS`  | Lead invited, 3 of 9 sections completed (33.33 %).              | Seed Lead + invite + 3× record_progress_event |
| `HOT_READY`         | Lead invited, 3 sections completed within last 7 days (≥ 25 %). | Seed Lead + invite + 3× record_progress_event (recent timestamps) |
| `STALE_ACTIVITY`    | Lead invited, 3 sections completed but last event > 7 days ago. | Seed Lead + invite + 3× record_progress_event (old timestamps) |
| `FULL_COMPLETION`   | All 9 sections completed, invite sent.                          | Seed Lead + invite + 9× record_progress_event |

Each scenario calls only existing `/execution` functions. Timestamps for
`occurred_at` are injected by the harness caller — the harness never calls
`datetime.now()` internally for business-logic timestamps.

Running a scenario on a `lead_id` that already has data must first call
Reset Progress (with `confirm=True`) before seeding new state.

---

## Safety Constraints

### Dev-Only Gating

The harness must confirm it is operating on a dev/test database before any write.
The check is:

- The `db_path` argument must be explicitly supplied and must **not** match any
  path pattern that designates a production database (e.g., must not be a remote
  URL, must not be a path outside the project `tmp/` directory or an explicitly
  provided test-fixture path).
- If `db_path` is `None` and no default override is active, the harness defaults
  to `tmp/app.db` and proceeds.
- If a `COLABERRY_ENV` environment variable is present and set to `"production"`,
  the harness must abort immediately with the message:
  `"Admin/Test Mode harness is disabled in production. Set COLABERRY_ENV to dev or test."`

### Required Confirmations for Destructive Actions

| Operation       | Destructive? | Required confirmation |
|-----------------|--------------|-----------------------|
| Seed Lead       | No           | None required |
| Reset Progress  | Yes          | Caller must pass `confirm=True` or acknowledge a prompt. Without it, raise `OperationNotConfirmedError`. |
| Simulate Scenario (which internally resets) | Yes | `confirm=True` is forwarded to the internal Reset Progress call. |

### Production Write Protection

- The harness must never import or call any module responsible for GHL sync,
  outbound SMS, outbound email, or any external API write.
- All writes are SQLite-only, scoped to `db_path`.
- No `SyncRecord` rows may be written by the harness (sync records imply a
  production handoff intent).

---

## Inputs

### Seed Lead

| Field            | Type          | Required | Notes |
|------------------|---------------|----------|-------|
| `lead_id`        | `str`         | Yes      | Stable unique identifier. Whitespace trimmed before use. |
| `name`           | `str \| None` | No       | Display name. |
| `phone`          | `str \| None` | No       | Phone number. |
| `email`          | `str \| None` | No       | Email address. |
| `mark_invite_sent` | `bool`      | No       | Default `False`. If `True`, also calls `mark_course_invite_sent`. |
| `invite_id`      | `str`         | If `mark_invite_sent=True` | Stable unique ID for the invite record. |
| `sent_at`        | `str \| None` | No       | ISO 8601 UTC. Defaults to current UTC if omitted. |
| `channel`        | `str \| None` | No       | e.g. `"sms"`, `"email"`. |
| `db_path`        | `str \| None` | No       | Defaults to `tmp/app.db`. |

### Reset Progress

| Field           | Type   | Required | Notes |
|-----------------|--------|----------|-------|
| `lead_id`       | `str`  | Yes      | Lead whose progress rows are deleted. |
| `reset_invite`  | `bool` | No       | Default `False`. If `True`, also deletes `course_invites` rows. |
| `confirm`       | `bool` | Yes      | Must be `True` or operation is aborted. |
| `db_path`       | `str \| None` | No | Defaults to `tmp/app.db`. |

### Simulate Scenario

| Field         | Type   | Required | Notes |
|---------------|--------|----------|-------|
| `scenario_id` | `str`  | Yes      | One of the defined scenario IDs (see table above). Unknown IDs raise `ValueError`. |
| `lead_id`     | `str`  | Yes      | Lead to place into the scenario state. |
| `confirm`     | `bool` | Yes      | Forwarded to internal Reset Progress call. |
| `now`         | `datetime \| None` | No | UTC datetime injected for timestamp generation. Defaults to current UTC if `None`. The harness must not call `datetime.now()` for business timestamps when `now` is supplied. |
| `db_path`     | `str \| None` | No | Defaults to `tmp/app.db`. |

---

## Outputs

### Seed Lead

| Condition | UI / return message |
|-----------|---------------------|
| Success (new lead) | `"Lead {lead_id} created."` |
| Success (updated lead) | `"Lead {lead_id} updated."` |
| Success + invite recorded | append `" Invite recorded."` |
| Lead ID empty after trim | `"Lead ID is required."` — abort. |

### Reset Progress

| Condition | UI / return message |
|-----------|---------------------|
| Success | `"Progress reset for lead {lead_id}. {N} event(s) deleted."` |
| `reset_invite=True` success | append `" Invite record(s) cleared."` |
| `confirm` not `True` | raise `OperationNotConfirmedError`: `"Reset requires confirm=True."` |
| Lead does not exist | `"Lead {lead_id} not found. Nothing deleted."` |

### Simulate Scenario

| Condition | UI / return message |
|-----------|---------------------|
| Success | `"Scenario {scenario_id} applied to lead {lead_id}."` |
| Unknown `scenario_id` | raise `ValueError`: `"Unknown scenario: {scenario_id}."` |
| Production env guard triggered | abort with message (see Safety Constraints). |
| Underlying execution function raises | propagate exception with original message; do not swallow. |

All output messages must appear in the operator UI (if one exists), not only in
the console. Raw tracebacks must never be shown to the operator; log them with
`logging.exception` and show the plain message only.

---

## Verification / Definition of Done

### Unit Tests Required

Tests live in `/tests/` and must use an in-memory or temporary SQLite database
(never `tmp/app.db`). No test may write to `tmp/app.db` or any shared fixture
file. All timestamps must be injected — no test may rely on `datetime.now()`.

| # | Function under test | Scenario | Expected outcome |
|---|---------------------|----------|------------------|
| U1 | `seed_lead` | New lead, no invite | Lead row created; no `course_invites` row |
| U2 | `seed_lead` | New lead + `mark_invite_sent=True` | Lead row + `course_invites` row created |
| U3 | `seed_lead` | Called twice with same `lead_id` | Idempotent — no duplicate rows; `updated_at` refreshed |
| U4 | `seed_lead` | `lead_id` empty string | Raises or returns `"Lead ID is required."` without writing |
| U5 | `reset_progress` | Lead with 3 progress events | All 3 `progress_events` rows deleted; lead row intact |
| U6 | `reset_progress` | `reset_invite=True` | `course_invites` rows also deleted |
| U7 | `reset_progress` | `confirm=False` | Raises `OperationNotConfirmedError`; no rows deleted |
| U8 | `reset_progress` | Lead does not exist | Returns `"Lead {lead_id} not found."` without error |
| U9 | `simulate_scenario` | `COLD_NO_INVITE` | Lead row exists; no invite; no progress events |
| U10 | `simulate_scenario` | `INVITED_NO_PROGRESS` | Lead + invite row; no progress events |
| U11 | `simulate_scenario` | `PARTIAL_PROGRESS` | Lead + invite + exactly 3 `progress_events` rows |
| U12 | `simulate_scenario` | `HOT_READY` | `get_lead_status` reports `hot=True` for injected `now` |
| U13 | `simulate_scenario` | `STALE_ACTIVITY` | `get_lead_status` reports `hot=False`, reason `ACTIVITY_STALE` |
| U14 | `simulate_scenario` | `FULL_COMPLETION` | `completion_pct = 100.0`; 9 `progress_events` rows |
| U15 | `simulate_scenario` | Unknown `scenario_id` | Raises `ValueError` before any DB write |
| U16 | Production env guard | `COLABERRY_ENV=production` | All three operations abort with guard message; 0 rows written |

All tests must be:
- Fast (no network, no real file I/O beyond a temp SQLite path)
- Deterministic (injected timestamps, fixed `lead_id` values)
- Runnable locally with a single command: `pytest tests/`

### Integration / E2E Expectations

Kept minimal for MVP v1:

- If an operator UI panel is built over this harness, at least one Playwright
  smoke test must confirm: selecting `HOT_READY`, entering a `lead_id`, clicking
  **Apply**, and verifying the Lead Status panel updates to `hot = True`.
- No integration test may run against a shared or production database.
- Integration tests require the `COLABERRY_ENV=test` flag to be set explicitly.

### All Tests Pass Locally

A change to the harness is not done until:
- `pytest tests/` exits with 0 failures, including the new harness unit tests.
- No new test touches `tmp/app.db` or any production-adjacent path.
- The `OperationNotConfirmedError` guard is verified by at least one test (U7).
- The production env guard is verified by at least one test (U16).

---

## References

- [COURSE_STRUCTURE.md](COURSE_STRUCTURE.md) — canonical section IDs used in Simulate Scenario progress events
- [HOT_LEAD_SIGNAL.md](HOT_LEAD_SIGNAL.md) — rule spec verified by `HOT_READY` and `STALE_ACTIVITY` scenarios
- [UI_STUDENT_COURSE_PLAYER.md](UI_STUDENT_COURSE_PLAYER.md) — student-facing execution call order this harness mirrors
- [UI_LEAD_STATUS_VIEW.md](UI_LEAD_STATUS_VIEW.md) — operator lead status display this harness is designed to exercise
- [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md) — system-wide architecture and layer boundaries
