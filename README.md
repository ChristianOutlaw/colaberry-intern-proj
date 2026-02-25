# Colaberry Cold Lead Conversion System

This repository implements the back-end execution layer for Colaberry's **Cold Lead "Free Intro to AI Class" Conversion System**. The system enables Cora (Colaberry's AI agent) to invite inactive cold leads to a free AI class, track their course progress, compute engagement signals, and surface "hot leads" ready for a booking handoff to GHL. See [directives/PROJECT_BLUEPRINT.md](directives/PROJECT_BLUEPRINT.md) for the full problem statement, MVP outcomes, data entities, and acceptance criteria.

All business logic is **deterministic and test-first**. LLM agents (Claude) act as planners and validators — they design and review code but never execute business logic directly. Execution scripts are pure Python with no orchestration concerns baked in.

---

## Repository Architecture

```
ColaberryInternProj/
├── directives/   # Layer 1 — SOPs, rule specs, acceptance criteria (human-readable)
├── execution/    # Layer 3 — Deterministic scripts; one script = one responsibility
│   ├── db/           SQLite persistence layer
│   ├── leads/        Lead upsert, invite recording, status aggregation, hot-lead signal
│   ├── progress/     Progress event recording and course state computation
│   └── decision/     Cold lead next-action decision engine
├── tests/        # Layer 4 — Unit tests mirroring execution structure
├── agents/       # Layer 2 — Agent persona definitions (orchestration role descriptions)
├── config/       # Environment wiring (no secrets)
└── tmp/          # Scratch space — safe to delete, never committed
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

# 2. Install dependencies (stdlib only for now — no pip install required)

# 3. Run all unit tests
python -m pytest tests/ -v

# Or with the built-in runner
python -m unittest discover -s tests -v
```

Tests are fast, deterministic, and require no network or database setup. Each test creates and tears down its own isolated SQLite file under `tmp/`.

---

## Main Workflows

All scripts live under `execution/` and are importable as pure functions. No script makes network calls or reads production credentials.

### 1. Upsert a Lead
[execution/leads/upsert_lead.py](execution/leads/upsert_lead.py)

Creates or updates a lead record. Idempotent — safe to re-run with the same `lead_id`.

```python
from execution.leads.upsert_lead import upsert_lead
upsert_lead("lead-123", name="Jane Doe", phone="555-0100")
```

### 2. Mark Course Invite Sent
[execution/leads/mark_course_invite_sent.py](execution/leads/mark_course_invite_sent.py)

Records that the "Free Intro to AI Class" invitation was sent. Idempotent per lead.

```python
from execution.leads.mark_course_invite_sent import mark_course_invite_sent
mark_course_invite_sent("invite-001", lead_id="lead-123", channel="sms")
```

### 3. Record a Progress Event
[execution/progress/record_progress_event.py](execution/progress/record_progress_event.py)

Persists a phase/section completion event for a lead. Idempotent per `event_id`.

```python
from execution.progress.record_progress_event import record_progress_event
record_progress_event("evt-001", "lead-123", "section_2", occurred_at="2026-02-20T10:00:00+00:00")
```

### 4. Compute Course State
[execution/progress/compute_course_state.py](execution/progress/compute_course_state.py)

Derives and persists the lead's current section, completion percentage, and last activity timestamp from all recorded `ProgressEvent` rows.

```python
from execution.progress.compute_course_state import compute_course_state
compute_course_state("lead-123", total_sections=10)
```

### 5. Get Lead Status (includes HotLeadSignal)
[execution/leads/get_lead_status.py](execution/leads/get_lead_status.py)

Assembles a full `LeadStatus` dict — invite state, course state, and a computed `HotLeadSignal` — without writing anything to the database. The hot-lead signal is evaluated in-process via [execution/leads/compute_hot_lead_signal.py](execution/leads/compute_hot_lead_signal.py) using three gates: invite sent, completion ≥ 25%, last activity within 7 days. Rule spec: [directives/HOT_LEAD_SIGNAL.md](directives/HOT_LEAD_SIGNAL.md).

```python
from execution.leads.get_lead_status import get_lead_status
status = get_lead_status("lead-123")
# status["hot_lead"]["signal"]  →  "HOT" or "NOT_HOT"
# status["hot_lead"]["reason"]  →  e.g. "HOT_ENGAGED", "COMPLETION_BELOW_THRESHOLD"
```

### 6. Decide Next Cold Lead Action
[execution/decision/decide_next_cold_lead_action.py](execution/decision/decide_next_cold_lead_action.py)

Returns the recommended next action for a cold lead based on their current status (e.g., send invite, follow up on progress, escalate hot lead).

```python
from execution.decision.decide_next_cold_lead_action import decide_next_cold_lead_action
action = decide_next_cold_lead_action(lead_status=status)
```

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

3. **Write unit tests** in `/tests/`
   - Mirror the execution module name: `test_<module_name>.py`.
   - Cover happy path, all failure branches, and boundary conditions.
   - Inject all time-dependent values (`now`, timestamps) — never call `datetime.now()` inside rule logic.
   - Tests must be fast, deterministic, and require no network or database beyond a local `tmp/` SQLite file.

4. **Run tests and confirm green:**
   ```bash
   python -m pytest tests/ -v
   ```

5. **A change is not done until:**
   - All unit tests pass.
   - The relevant directive is updated.
   - No secrets are introduced.
   - The logic is understandable by a junior developer.
