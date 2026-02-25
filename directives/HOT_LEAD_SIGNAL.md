# HotLeadSignal (MVP v1) — Rule Spec

## Context

This directive resolves the open questions stated in `PROJECT_BLUEPRINT.md §9`
("What defines 'hot lead' for MVP — e.g., completion threshold, recent activity
window?") and formally specifies **Outcome O4**: "The system can identify and
surface 'hot leads' based on engagement signals (progress activity), preparing
them to be pushed to GHL." The `HotLeadSignal` is a deterministic, rule-based
boolean computed at query time from a lead's `CourseState` and `CourseInvite`
records. This file is the single source of truth for that computation in MVP v1.
All `/execution` scripts and `/tests` unit tests must reference these rules as
their canonical specification; no rule logic may live outside this directive and
the corresponding execution module.

---

## Inputs

| Field                | Type              | Description |
|----------------------|-------------------|-------------|
| `completion_percent` | `float \| None`   | Percentage of course content completed (0.0–100.0). `None` when no `ProgressEvent` rows exist for this lead. |
| `last_activity_time` | `datetime \| None`| UTC timestamp of the most recent `ProgressEvent` for this lead. `None` when no events have been recorded. |
| `invite_sent`        | `bool`            | `True` if a "Free Intro to AI Class" `CourseInvite` record exists for this lead; `False` otherwise. |
| `invite_sent_at`     | `datetime \| None`| UTC timestamp of invite delivery. Present only when `invite_sent` is `True`; unused by v1 rules but carried for auditability. |
| `current_phase`      | `str \| None`     | Lead's current phase label (e.g., `"phase_2"`). Optional; carried in output for context but not evaluated by v1 rules. |
| `now`                | `datetime`        | Current time in UTC, **injected by the caller**. The rule engine must never call `datetime.now()` internally. Must be timezone-aware (`tzinfo=UTC`). |

### Timezone assumption

All timestamps persisted in the system are UTC. If a caller supplies a
naive `datetime` (no `tzinfo`), the rule engine must treat it as UTC and
emit a warning. No local-timezone conversion is performed at any layer.

### What counts as "activity"

Only persisted `ProgressEvent` rows (phase/section completions or explicit
check-in events) count as activity. The following do **not** count:

- Invite delivery alone
- Link opens or passive page views (not tracked)
- Any event not written to the `ProgressEvent` table

---

## MVP v1 Rule Set

`HotLeadSignal` is **`True` only when all three gates below pass**, evaluated in
order. Evaluation stops at the first failing gate. A `reasons` list of short
string codes accompanies every result to explain the outcome.

### Locked thresholds (v1)

| Constant                  | Value          |
|---------------------------|----------------|
| `COMPLETION_THRESHOLD_PCT` | `25.0` percent |
| `ACTIVITY_WINDOW_DAYS`     | `7` calendar days |

Both constants must be defined as named module-level variables in the execution
module; no magic numbers in rule logic.

---

### Rule 1 — Invite Gate

**Condition:** `invite_sent is True`

- If `False` → `hot = False`, `reasons = ["NOT_INVITED"]`. **Stop.**

Rationale: A lead who was never offered the free class cannot be considered
engaged with it. This gate prevents flagging leads who progressed through
content via other channels from being misrouted as invite-driven hot leads.

---

### Rule 2 — Completion Gate

**Condition:** `completion_percent is not None` AND `completion_percent >= 25.0`

- If `completion_percent is None` → `hot = False`, `reasons = ["COMPLETION_UNKNOWN"]`. **Stop.**
- If `completion_percent < 25.0` → `hot = False`, `reasons = ["COMPLETION_BELOW_THRESHOLD"]`. **Stop.**

Rationale: 25 % is the minimum bar indicating meaningful engagement with course
content (at least one substantive section completed). Leads below this threshold
have not demonstrated sufficient commitment to warrant a booking push.

---

### Rule 3 — Recency Gate

**Condition:** `last_activity_time is not None` AND
`(now - last_activity_time).days <= 7`

- If `last_activity_time is None` → `hot = False`, `reasons = ["NO_ACTIVITY_RECORDED"]`. **Stop.**
- If delta exceeds window → `hot = False`, `reasons = ["ACTIVITY_STALE"]`. **Stop.**

Note: `(now - last_activity_time).days` uses Python's `timedelta.days`
(integer floor of full days). A delta of exactly 7 days (e.g., 168 h) is
**within** the window. A delta of 8 days is outside it.

---

### All gates pass → Hot

`hot = True`, `reasons = ["HOT_ENGAGED"]`

---

### Reason codes (exhaustive for v1)

| Code                        | Meaning |
|-----------------------------|---------|
| `"NOT_INVITED"`             | Lead has no `CourseInvite` record — prerequisite unmet |
| `"COMPLETION_UNKNOWN"`      | `completion_percent` is `None`; no progress events exist |
| `"COMPLETION_BELOW_THRESHOLD"` | `completion_percent` is set but `< 25.0` |
| `"NO_ACTIVITY_RECORDED"`    | `last_activity_time` is `None` despite invite being sent |
| `"ACTIVITY_STALE"`          | Most recent activity is more than 7 days before `now` |
| `"HOT_ENGAGED"`             | All gates passed; lead is flagged as hot |

---

### Output shape

```python
{
    "hot": bool,           # True or False
    "reasons": [str],      # Exactly one reason code per evaluation
    "evaluated_at": str,   # ISO-8601 UTC string derived from injected "now"
}
```

`reasons` always contains exactly one code in v1 (multi-reason is reserved for
future versions).

---

### Persistence

`HotLeadSignal` is **computed at query time** from stored `CourseState` and
`CourseInvite` records. It is not written to its own database row. Script D
(`get_lead_status`) carries the computed result inside the `LeadStatus` summary
object. Callers who require a persistent snapshot (e.g., for GHL push audit)
must store the returned summary themselves via a `SyncRecord`.

---

## Examples

All examples use `now = 2026-02-24T12:00:00Z`.

---

### Example 1 — HOT: all gates pass

| Field                | Value |
|----------------------|-------|
| `invite_sent`        | `True` |
| `invite_sent_at`     | `2026-02-10T09:00:00Z` |
| `completion_percent` | `30.0` |
| `last_activity_time` | `2026-02-21T14:00:00Z` (3 days ago) |
| `current_phase`      | `"phase_2"` |

**Output:** `hot = True`, `reasons = ["HOT_ENGAGED"]`

Rule 1 passes (invited). Rule 2 passes (30 ≥ 25). Rule 3 passes (3 ≤ 7).

---

### Example 2 — NOT HOT: completion below threshold

| Field                | Value |
|----------------------|-------|
| `invite_sent`        | `True` |
| `completion_percent` | `10.0` |
| `last_activity_time` | `2026-02-22T08:00:00Z` (2 days ago) |

**Output:** `hot = False`, `reasons = ["COMPLETION_BELOW_THRESHOLD"]`

Rule 1 passes. Rule 2 fails (10 < 25). Evaluation stops.

---

### Example 3 — NOT HOT: activity stale

| Field                | Value |
|----------------------|-------|
| `invite_sent`        | `True` |
| `completion_percent` | `40.0` |
| `last_activity_time` | `2026-02-14T10:00:00Z` (10 days ago) |

**Output:** `hot = False`, `reasons = ["ACTIVITY_STALE"]`

Rule 1 passes. Rule 2 passes (40 ≥ 25). Rule 3 fails (10 > 7). Evaluation stops.

---

### Example 4 — NOT HOT: never invited (invite gate blocks)

| Field                | Value |
|----------------------|-------|
| `invite_sent`        | `False` |
| `completion_percent` | `50.0` |
| `last_activity_time` | `2026-02-23T10:00:00Z` (1 day ago) |

**Output:** `hot = False`, `reasons = ["NOT_INVITED"]`

Rule 1 fails immediately. Evaluation stops regardless of other fields.

---

### Example 5 — NOT HOT: completion unknown (no progress events)

| Field                | Value |
|----------------------|-------|
| `invite_sent`        | `True` |
| `completion_percent` | `None` |
| `last_activity_time` | `2026-02-23T10:00:00Z` |

**Output:** `hot = False`, `reasons = ["COMPLETION_UNKNOWN"]`

Rule 1 passes. Rule 2 fails (`None` branch). Evaluation stops.

---

### Example 6 — NOT HOT: completion computable but no activity timestamp

| Field                | Value |
|----------------------|-------|
| `invite_sent`        | `True` |
| `completion_percent` | `35.0` |
| `last_activity_time` | `None` |

**Output:** `hot = False`, `reasons = ["NO_ACTIVITY_RECORDED"]`

Rule 1 passes. Rule 2 passes (35 ≥ 25). Rule 3 fails (`None` branch). This edge
case can occur if `completion_percent` is seeded from an external source while
no local `ProgressEvent` rows have been written yet.

---

## Verification: Unit Test Checklist

Tests live in `/tests/` and must import the rule engine as a pure function
(no I/O, no DB calls). `now` must always be injected, never mocked via
`datetime.now` patching.

| # | Scenario | Expected `hot` | Expected `reasons[0]` |
|---|----------|:--------------:|----------------------|
| T1 | Example 1 (all gates pass, 30 %, 3 days) | `True` | `"HOT_ENGAGED"` |
| T2 | Example 2 (completion 10 %, invited, recent) | `False` | `"COMPLETION_BELOW_THRESHOLD"` |
| T3 | Example 3 (completion 40 %, invited, 10 days) | `False` | `"ACTIVITY_STALE"` |
| T4 | Example 4 (not invited, completion 50 %, recent) | `False` | `"NOT_INVITED"` |
| T5 | Example 5 (invited, completion None, recent timestamp) | `False` | `"COMPLETION_UNKNOWN"` |
| T6 | Example 6 (invited, completion 35 %, activity None) | `False` | `"NO_ACTIVITY_RECORDED"` |
| T7 | Boundary: completion exactly 25.0 %, activity 7 days exactly | `True` | `"HOT_ENGAGED"` |
| T8 | Boundary: completion 24.9 %, activity 6 days | `False` | `"COMPLETION_BELOW_THRESHOLD"` |
| T9 | Boundary: completion 25.0 %, activity exactly 8 days | `False` | `"ACTIVITY_STALE"` |
| T10 | Naive `datetime` supplied for `last_activity_time` | `False` or `True` (per other rules) | warning logged; result still deterministic |
| T11 | `invite_sent=False` with all other fields valid | `False` | `"NOT_INVITED"` |
| T12 | Output contains `evaluated_at` matching injected `now` ISO string | — | structural assertion |

All tests must be **fast, deterministic, and require no network or database
access**. No test may call `datetime.now()` directly; all must inject a fixed
`now` value.

---

## Non-Goals (v1)

- **Numeric scoring:** No points, weights, or composite score. The output is a
  boolean signal only.
- **Tiering (HOT / WARM / COLD):** Multi-tier classification is explicitly out
  of scope for v1. `HotLeadSignal` is binary.
- **ML or probabilistic models:** All rules are hand-authored and fully
  deterministic.
- **Real-time recalculation triggers:** The signal is computed on demand (pull),
  not pushed on event arrival.
- **GHL push logic:** This directive covers signal derivation only. The GHL
  handoff SOP is a separate concern and must be integration-gated.
