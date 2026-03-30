# Colaberry Cold Lead Conversion System

This repository implements the back-end execution layer for Colaberry's **Cold Lead "Free Intro to AI Class" Conversion System**. The system enables Cora (Colaberry's AI agent) to invite inactive cold leads to a free AI class, track their course progress, compute engagement signals, and surface "hot leads" ready for a booking handoff via GoHighLevel (GHL).

See [directives/PROJECT_BLUEPRINT.md](directives/PROJECT_BLUEPRINT.md) for the full problem statement, MVP outcomes, data entities, and acceptance criteria.

All business logic is **deterministic and test-first**. LLM agents (Claude) act as planners and validators — they design and review code but never execute business logic directly. Execution scripts are pure Python with no orchestration concerns baked in.

---

## Repository Architecture

```
ColaberryInternProj/
├── directives/          # Layer 1 — SOPs, rule specs, acceptance criteria (human-readable)
├── agents/              # Layer 2 — Agent persona definitions (orchestration role descriptions)
├── execution/           # Layer 3 — Deterministic scripts; one script = one responsibility
│   ├── db/                  SQLite persistence layer (schema, connection helpers)
│   ├── leads/               Lead upsert, invite recording, lifecycle, scoring, GHL intake
│   ├── ghl/                 GHL payload builder and writeback transport
│   ├── progress/            Progress event recording and course state computation
│   ├── decision/            Cold lead next-action decision engine and Cora recommendation
│   ├── scans/               Candidate discovery scans (unsent invite, stale, completion, etc.)
│   ├── events/              Event consumption and dispatch (webhook, GHL, log sink)
│   ├── course/              Course definition, course map, and quiz library loading
│   ├── reflection/          Student reflection response storage and retrieval
│   ├── ingestion/           Bulk lead import
│   ├── orchestration/       Manual entry points for scan → recommendation flows
│   ├── admin/               Dev/test harness (seed, reset, simulate scenarios)
│   └── cory/                Cory-specific GHL dispatcher
├── services/            # HTTP boundary layer — pure plumbing, no business logic
│   ├── webhook/             Inbound webhook endpoints (one per integration path)
│   └── worker/              Background job runners (one per scan/sync operation)
├── tests/               # Layer 4 — Unit tests mirroring execution and services structure
├── ui/                  # Streamlit-based portals (student, instructor, dev)
│   ├── student_portal/      Student course player
│   ├── instructor_portal/   Instructor CRM dashboard
│   └── dev_portal/          Admin test mode and sync outbox viewer
├── config/              # Environment wiring (no secrets)
└── tmp/                 # Scratch space — safe to delete, never committed
```

### The 4-Layer Model

| Layer | Role | Where |
|-------|------|--------|
| **1 — Directives** | Define intent, rules, and acceptance criteria | `/directives` |
| **2 — Orchestration** | Claude / human: plans, validates, designs tests | *(Claude Code / agents)* |
| **3 — Execution** | Deterministic scripts that do the actual work | `/execution` |
| **4 — Verification** | Automated tests proving correctness | `/tests` |

No business logic lives in directives. No orchestration logic lives in execution scripts. Tests are first-class citizens, not afterthoughts.

---

## Quickstart

**Requirements:** Python 3.12+

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies (stdlib only — no pip install required for core logic)
#    Streamlit is needed only if running the UI portals:
pip install streamlit             # optional — UI only

# 3. Run all unit tests
python -m pytest tests/ -v

# Or with the built-in runner
python -m unittest discover -s tests -v
```

Tests are fast, deterministic, and require no network or database setup. Each test creates and tears down its own isolated SQLite file under `tmp/`.

---

## Database Schema

The SQLite database lives at `tmp/app.db` (created automatically on first run). Schema is initialized by [`execution/db/sqlite.py`](execution/db/sqlite.py) — safe to call multiple times.

| Table | Purpose |
|-------|---------|
| `leads` | Core lead record (`id`, `phone`, `email`, `name`, `ghl_contact_id`, `created_at`, `updated_at`) |
| `course_enrollments` | One row per lead+course pairing |
| `course_invites` | Invite rows (`id`, `lead_id`, `course_id`, `token`, `sent_at`, `channel`, `first_used_at`) |
| `progress_events` | Individual section/phase completion events |
| `course_state` | Computed current position — `completion_pct`, `current_section`, `last_activity_at` |
| `hot_lead_signals` | Derived HOT/NOT_HOT signal with reason and score |
| `sync_records` | Outbox audit log for GHL/Cory push attempts (`NEEDS_SYNC` → `SENT`/`FAILED`) |
| `reflection_responses` | Student free-text reflection answers per section/prompt |

**`DB_PATH` environment variable** overrides the default `tmp/app.db` path. All execution functions accept a `db_path` kwarg for test isolation.

---

## Core Determinism Rule

All execution-layer functions are **pure and clock-free**. They never call `datetime.now()` internally. Instead, callers inject the current time as an ISO-8601 string via a `now` parameter.

```python
# CORRECT — caller owns the clock
result = process_ghl_lead_intake(payload, now="2026-03-27T12:00:00+00:00", ...)

# WRONG — execution functions must never do this
import datetime
now = datetime.now()   # not allowed inside execution/
```

This makes every execution function fully deterministic and straightforward to test.

---

## Key Workflows

### 1. GHL Handshake — Full Intake Flow

The primary integration path. GHL sends a lead; our app matches/creates the lead, generates a unique course link, and writes the full canonical field schema back to GHL.

```
GHL → POST /ghl-lead → ghl_lead_intake_endpoint
  → process_ghl_lead_intake
      → match_or_create_lead_from_ghl_payload   (Step 3a: identity resolution)
      → create_student_invite_from_payload       (Step 3b: course link generation)
      → write_ghl_contact_fields                 (Step 4:  GHL writeback)
```

See [directives/GHL_INTEGRATION.md](directives/GHL_INTEGRATION.md) for the full 5-step handshake, identity matching hierarchy, and canonical field schema.

```python
from execution.leads.process_ghl_lead_intake import process_ghl_lead_intake

result = process_ghl_lead_intake(
    {"phone": "5550001111", "ghl_contact_id": "GHL_ABC"},
    now="2026-03-27T12:00:00+00:00",
    ghl_api_url="https://your-ghl-update-endpoint",
    db_path="tmp/app.db",
)
# result["ok"]             → True
# result["app_lead_id"]    → "GHL_<uuid>"
# result["writeback_ok"]   → True / False
```

### 2. Upsert a Lead

[execution/leads/upsert_lead.py](execution/leads/upsert_lead.py)

Creates or updates a lead record. Idempotent — safe to re-run with the same `lead_id`.

```python
from execution.leads.upsert_lead import upsert_lead
upsert_lead("lead-123", name="Jane Doe", phone="555-0100")
```

### 3. Generate a Student Invite Link

[execution/leads/create_student_invite_from_payload.py](execution/leads/create_student_invite_from_payload.py)

Creates a unique, token-secured course access URL. Idempotent when a stable `invite_id` is supplied.

```python
from execution.leads.create_student_invite_from_payload import create_student_invite_from_payload

result = create_student_invite_from_payload(
    lead_id="lead-123",
    invite_id="INV_lead-123_stable",
    base_url="https://your-student-portal.com",
)
# result["invite_link"]  → "https://your-student-portal.com/?token=<token>"
```

### 4. Record a Progress Event

[execution/progress/record_progress_event.py](execution/progress/record_progress_event.py)

Persists a section completion event. Idempotent per `event_id`.

```python
from execution.progress.record_progress_event import record_progress_event
record_progress_event("evt-001", "lead-123", "section_2", occurred_at="2026-02-20T10:00:00+00:00")
```

### 5. Compute Course State

[execution/progress/compute_course_state.py](execution/progress/compute_course_state.py)

Derives and persists the lead's current section, completion percentage, and last activity timestamp from all recorded progress events.

```python
from execution.progress.compute_course_state import compute_course_state
compute_course_state("lead-123", total_sections=10)
```

### 6. Get Lead Status (includes Hot Lead Signal)

[execution/leads/get_lead_status.py](execution/leads/get_lead_status.py)

Assembles a full status dict — invite state, course state, and a computed `HotLeadSignal` — without writing to the database.

Hot-lead signal evaluates three gates: invite sent, completion ≥ 25%, last activity within 7 days. Rule spec: [directives/HOT_LEAD_SIGNAL.md](directives/HOT_LEAD_SIGNAL.md).

```python
from execution.leads.get_lead_status import get_lead_status

status = get_lead_status("lead-123")
# status["hot_lead"]["signal"]  →  "HOT" or "NOT_HOT"
# status["hot_lead"]["reason"]  →  e.g. "HOT_ENGAGED", "COMPLETION_BELOW_THRESHOLD"
```

### 7. Decide Next Cold Lead Action

[execution/decision/decide_next_cold_lead_action.py](execution/decision/decide_next_cold_lead_action.py)

Returns the recommended next action for a lead based on their current state.

```python
from execution.decision.decide_next_cold_lead_action import decide_next_cold_lead_action

action = decide_next_cold_lead_action("lead-123")
# → "SEND_INVITE" | "NUDGE_PROGRESS" | "READY_FOR_BOOKING" | "NO_LEAD"
```

### 8. Build the Full GHL Canonical Payload

[execution/ghl/build_ghl_full_field_payload.py](execution/ghl/build_ghl_full_field_payload.py)

Constructs the complete 5-group GHL custom-field schema for a lead. Read-only — no DB writes, no network calls.

```python
from execution.ghl.build_ghl_full_field_payload import build_ghl_full_field_payload

result = build_ghl_full_field_payload(
    "lead-123",
    now="2026-03-27T12:00:00+00:00",
    base_url="https://your-student-portal.com",
)
# result["ok"]       → True
# result["payload"]  → {app_lead_id, course_link, invite_ready, completion_pct, ...}
```

---

## Webhook Services

Three lightweight HTTP servers live under `services/webhook/`. Each is pure HTTP plumbing — no business logic. All execution work is delegated to `execution/`.

| Endpoint | Port | File | Delegates To |
|----------|------|------|-------------|
| `POST /invite` | 8520 | [student_invite_endpoint.py](services/webhook/student_invite_endpoint.py) | `create_student_invite_from_payload` |
| `POST /cory-recommendation` | 8521 | [cory_recommendation_endpoint.py](services/webhook/cory_recommendation_endpoint.py) | `consume_cory_recommendation` |
| `POST /ghl-lead` | 8522 | [ghl_lead_intake_endpoint.py](services/webhook/ghl_lead_intake_endpoint.py) | `process_ghl_lead_intake` |

**Running a webhook endpoint:**

```bash
# GHL intake webhook (default port 8522)
python services/webhook/ghl_lead_intake_endpoint.py

# Custom port
python services/webhook/ghl_lead_intake_endpoint.py 9002
```

**HTTP contract for all endpoints:**
- `200` — valid JSON body (business failure surfaced in body with `"ok": false`)
- `400` — request body is not valid JSON
- `405` — non-POST method used

---

## Scan Jobs and Workers

Scans discover leads that need action. Workers pick up scan results and dispatch them.

### Scan Registry

| Scan | File | What it finds |
|------|------|--------------|
| `UNSENT_INVITE_SCAN` | [find_unsent_invite_leads.py](execution/scans/find_unsent_invite_leads.py) | Leads that never received an invite |
| `NO_START_SCAN` | [find_no_start_leads.py](execution/scans/find_no_start_leads.py) | Invited leads that never opened the course |
| `STALE_PROGRESS_SCAN` | [find_stale_progress_leads.py](execution/scans/find_stale_progress_leads.py) | Started but inactive leads |
| `COMPLETION_FINALIZATION_SCAN` | [find_completion_finalization_leads.py](execution/scans/find_completion_finalization_leads.py) | Leads at 100% ready for final scoring |
| `FAILED_DISPATCH_RETRY_SCAN` | [find_failed_dispatch_records.py](execution/scans/find_failed_dispatch_records.py) | Failed sync_records eligible for retry |

See [directives/SCAN_JOBS.md](directives/SCAN_JOBS.md) for full scan specifications.

### Running Workers

```bash
# Run all scans
python services/worker/run_all_scans.py

# Run a specific scan
python services/worker/run_no_start_scan.py
python services/worker/run_stale_progress_scan.py
python services/worker/run_unsent_invite_scan.py

# Process one pending Cory sync record
python services/worker/run_cory_sync.py
```

---

## UI Portals

The UI is built with Streamlit and lives under `ui/`. Three portals serve different audiences.

| Portal | Entry Point | Audience |
|--------|------------|---------|
| Student Course Player | `ui/student_portal/student_app.py` | Students accessing the free AI course |
| Instructor Dashboard | `ui/instructor_portal/instructor_app.py` | Staff managing leads and course progress |
| Dev / Admin | `ui/dev_portal/dev_app.py` | Developers testing and inspecting system state |

**Running a portal:**

```bash
streamlit run ui/student_portal/student_app.py
streamlit run ui/instructor_portal/instructor_app.py
streamlit run ui/dev_portal/dev_app.py
```

The Dev portal includes:
- **Admin Test Mode** — seed leads, reset progress, simulate lifecycle scenarios
- **Sync Outbox Viewer** — inspect pending, sent, and failed `sync_records`

---

## Directives Index

Directives are the source of truth for business rules and acceptance criteria. Read the relevant directive before modifying any execution function.

| Directive | What it covers |
|-----------|---------------|
| [PROJECT_BLUEPRINT.md](directives/PROJECT_BLUEPRINT.md) | Problem statement, MVP outcomes, data entities, acceptance criteria |
| [GHL_INTEGRATION.md](directives/GHL_INTEGRATION.md) | GHL handshake flow, identity matching hierarchy, canonical field schema |
| [HOT_LEAD_SIGNAL.md](directives/HOT_LEAD_SIGNAL.md) | Binary hot-lead signal rule engine (3 gates) |
| [LEAD_TEMPERATURE_SCORING.md](directives/LEAD_TEMPERATURE_SCORING.md) | Multi-signal weighted scoring (0–100, 6 engagement signals) |
| [SCAN_JOBS.md](directives/SCAN_JOBS.md) | Scan types, selection rules, and dispatch targets |
| [SCAN_SCHEDULER_DESIGN.md](directives/SCAN_SCHEDULER_DESIGN.md) | Scheduler architecture for running scans |
| [CORA_RECOMMENDATION_EVENTS.md](directives/CORA_RECOMMENDATION_EVENTS.md) | Cora recommendation event types and priorities |
| [COURSE_STRUCTURE.md](directives/COURSE_STRUCTURE.md) | FREE_INTRO_AI_V0 course definition (9 sections) |
| [ADMIN_TEST_MODE.md](directives/ADMIN_TEST_MODE.md) | Dev/test harness operations (seed, reset, simulate) |
| [UI_STUDENT_COURSE_PLAYER.md](directives/UI_STUDENT_COURSE_PLAYER.md) | Student course player UI specification |
| [UI_LEAD_STATUS_VIEW.md](directives/UI_LEAD_STATUS_VIEW.md) | Instructor lead status view specification |

---

## How to Add a New Feature

Follow the **Directives → Execution → Tests** order. Do not write code before the rule is documented.

1. **Write or update a directive** in `/directives/`
   - Define the goal, inputs, outputs, edge cases, and how success is verified.
   - If the feature changes existing behavior, update the relevant existing directive.

2. **Write the execution script** in `/execution/`
   - One script, one responsibility.
   - No orchestration logic, no prompts, no network calls that touch production.
   - Core logic must be importable as a pure function.
   - **Never call `datetime.now()` inside an execution function** — accept `now: str` as a parameter and raise `ValueError` when it is `None`.

3. **Write unit tests** in `/tests/`
   - Mirror the execution module name: `test_<module_name>.py`.
   - Cover happy path, all failure branches, and boundary conditions.
   - Inject all time-dependent values (`now`, timestamps) — never call `datetime.now()` in test setup.
   - Tests must be fast, deterministic, and use an isolated `tmp/test_<name>.db` file that is deleted in `tearDown`.

4. **Run tests and confirm green:**
   ```bash
   python -m pytest tests/ -v
   ```

5. **A change is not done until:**
   - All unit tests pass.
   - The relevant directive is updated.
   - No secrets are introduced.
   - The logic is understandable by a junior developer.

---

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `DB_PATH` | All webhook and worker scripts | Path to SQLite database. Defaults to `tmp/app.db`. |
| `GHL_API_URL` | `write_ghl_contact_fields` | GHL contact-update endpoint URL. Writeback is a safe no-op when absent. |
| `GHL_LOOKUP_URL` | `sync_ghl_contact_id` | GHL contact-lookup endpoint for resolving `ghl_contact_id` when not stored. |

No secrets are stored in this repository. All credentials and URLs are injected at runtime via environment variables or function arguments.
