# CORA_RECOMMENDATION_EVENTS.md
**Directive v1 — Cora-Ready Recommendation Event Builder**

---

## Purpose

This directive specifies how to convert a lead's current state into a structured,
explainable recommendation event payload for future Cora integration.

This is **preparation only** — no outreach is sent, no GHL API is called, no database
is written. The output is a plain dict that a future worker or integration layer can
consume to trigger the appropriate Cora action.

---

## When to Use

Call `build_cora_recommendation(...)` when you have a fully-resolved lead state and
want to determine what Cora should do next. Inputs can come from `get_lead_status`,
`compute_hot_lead_signal`, and `compute_lead_temperature`.

---

## Output Shape

| Field | Type | Description |
|-------|------|-------------|
| `lead_id` | `str` | Unique lead identifier (passed through) |
| `event_type` | `str` | One of the five v1 event types (see below) |
| `priority` | `str` | `"HIGH"` \| `"MEDIUM"` \| `"LOW"` |
| `reason_codes` | `list[str]` | Event-driving codes for this recommendation |
| `recommended_channel` | `str \| None` | `"EMAIL"` \| `"CALL"` \| `None` |
| `payload` | `dict` | Structured context for Cora (see Payload section) |
| `status` | `str` | Always `"READY"` in v1 |
| `built_at` | `str` | ISO-8601 UTC timestamp with trailing `Z` |

---

## v1 Event Types

| Event Type | Trigger Condition | Priority | Channel |
|------------|------------------|----------|---------|
| `SEND_INVITE` | No course invite sent | `LOW` | `EMAIL` |
| `READY_FOR_BOOKING` | Hot signal active AND course complete (`hot_signal == "HOT"` AND `completion_percent >= 100`) | `HIGH` | `CALL` |
| `WARM_REVIEW` | Course complete (`completion_percent == 100`), not hot, recently active (≤ `STALL_DAYS`) | `LOW` | `None` |
| `REENGAGE_COMPLETED` | Course complete, not hot, gone stale (> `STALL_DAYS` or no activity) | `MEDIUM` | `EMAIL` |
| `REENGAGE_STALLED_LEAD` | Started (`completion_percent > 0 and < 100`), inactive > `STALL_DAYS` | `HIGH` | `CALL` |
| `NUDGE_PROGRESS` | Invited; course not yet started **or** actively in progress, not hot | `MEDIUM` | `EMAIL` |

> **Note — `NUDGE_START_CLASS` removed (v1 revision):** The no-start state
> (invited, `completion_percent` is `None` or `0.0`) is no longer a separate
> top-level event.  It is handled by `NUDGE_PROGRESS` with reason code
> `INVITED_NO_START`.  Use `reason_codes` to distinguish not-started leads
> from actively-progressing ones inside the same action family.

---

## Decision Rules

Rules are evaluated in this exact order. First match wins.

1. **`SEND_INVITE`** — `invite_sent == False`
   - priority: `LOW`, channel: `EMAIL`
   - reason_codes: `["NOT_INVITED"]`

2. **`READY_FOR_BOOKING`** — `hot_signal == "HOT"`
   - priority: `HIGH`, channel: `CALL`
   - reason_codes: `["HOT_SIGNAL_ACTIVE"]`
   - Note: takes precedence over all completion states. Hot signal already encodes
     the 7-day activity window. `requires_finalization` is `True` only when
     `completion_percent >= 100`.

3. **`WARM_REVIEW`** — `completion_percent == 100` AND `days_inactive <= STALL_DAYS`
   - priority: `LOW`, channel: `None`
   - reason_codes: `["COURSE_COMPLETE"]`
   - `requires_finalization = True`

4. **`REENGAGE_COMPLETED`** — `completion_percent == 100` AND (`last_activity_at` is `None`
   OR `days_inactive > STALL_DAYS`)
   - priority: `MEDIUM`, channel: `EMAIL`
   - reason_codes: `["COMPLETED_GONE_STALE"]`
   - `requires_finalization = True`

5. **`REENGAGE_STALLED_LEAD`** — `completion_percent > 0` AND `completion_percent < 100`
   AND (`last_activity_at` is `None` OR days since last activity > `STALL_DAYS`)
   - priority: `HIGH`, channel: `CALL`
   - reason_codes: `["ACTIVITY_STALLED"]`
   - **Guard:** this rule requires `completion_percent > 0`.  Leads whose
     `completion_percent` is `None` or `0.0` fall through to `NUDGE_PROGRESS`.

6. **`NUDGE_PROGRESS`** — catch-all for all invited leads not yet matched above
   - priority: `MEDIUM`, channel: `EMAIL`
   - Covers two sub-states, distinguished by `reason_codes`:
     - **Not started** (`completion_percent` is `None` or `0.0`):
       reason_codes: `["INVITED_NO_START"]`
     - **Actively progressing** (`0 < completion_percent < 100`, within `STALL_DAYS`):
       reason_codes: `["ACTIVE_LEARNER"]`

---

## Staleness Threshold

`STALL_DAYS = 14`

A lead who started the course but has not been active within 14 days is considered
stalled. This is deliberately wider than the 7-day HOT activity window, giving a
week of grace after the HOT window closes before escalating to a re-engagement call.

---

## Reason Codes

| Code | Emitted When |
|------|-------------|
| `NOT_INVITED` | No course invite exists |
| `HOT_SIGNAL_ACTIVE` | Binary hot signal is `"HOT"` |
| `COURSE_COMPLETE` | Completion is 100%, recently active (→ `WARM_REVIEW`) |
| `COMPLETED_GONE_STALE` | Completion is 100%, gone stale (→ `REENGAGE_COMPLETED`) |
| `ACTIVITY_STALLED` | Started lead with no recent activity (> `STALL_DAYS`) |
| `INVITED_NO_START` | Invite sent, `completion_percent` is `None` or `0.0` — sub-reason within `NUDGE_PROGRESS` |
| `ACTIVE_LEARNER` | In-progress lead with recent activity — sub-reason within `NUDGE_PROGRESS` |

---

## Payload Structure

The `payload` dict provides structured context that a Cora worker can use to
personalise or validate the outreach. It is read-only and never written to the DB.

```json
{
  "completion_percent":     0.0–100.0 | null,
  "current_section":        "section-N" | null,
  "days_inactive":          int | null,
  "hot_signal":             "HOT" | "NOT_HOT",
  "temperature_signal":     "HOT" | "WARM" | "COLD" | null,
  "temperature_score":      0–100 | null,
  "upstream_reason_codes":  ["CODE", ...]
}
```

---

## Test Matrix

| # | invite_sent | completion_pct | hot_signal | days_inactive | Expected event_type | reason_code |
|---|-------------|----------------|------------|---------------|---------------------|-------------|
| T1 | False | None | NOT_HOT | None | SEND_INVITE | NOT_INVITED |
| T2 | True | None | NOT_HOT | None | NUDGE_PROGRESS | INVITED_NO_START |
| T3 | True | 0.0 | NOT_HOT | None | NUDGE_PROGRESS | INVITED_NO_START |
| T4 | True | 33.0 | HOT | 9 | HOT_LEAD_BOOKING | HOT_SIGNAL_ACTIVE |
| T5 | True | 33.0 | NOT_HOT | 20 | REENGAGE_STALLED_LEAD | ACTIVITY_STALLED |
| T6 | True | 33.0 | NOT_HOT | None | REENGAGE_STALLED_LEAD | ACTIVITY_STALLED |
| T7 | True | 33.0 | NOT_HOT | 5 | NUDGE_PROGRESS | ACTIVE_LEARNER |
| T8 | True | 100.0 | NOT_HOT | 2 | NO_ACTION | COURSE_COMPLETE |
| T9 | True | 90.0 | HOT | 25 | HOT_LEAD_BOOKING | HOT_SIGNAL_ACTIVE (HOT beats stale) |

---

## Integration Notes

- This function is **stateless and pure**. It reads no database and makes no
  network calls.
- The `status` field is always `"READY"` in v1. Future versions may introduce
  `"DRAFT"` or `"QUEUED"` states.
- `recommended_channel` is advisory only. A Cora worker should validate channel
  preference before sending.
- `built_at` uses the injected `now` — never `datetime.now()` internally.
- The function must remain importable and independently testable at all times.

---

## Verification

A change to this system is considered correct when:

1. All unit tests in `tests/test_build_cora_recommendation.py` pass.
2. Every event type in the test matrix maps to the correct `event_type`,
   `priority`, and `recommended_channel`.
3. The output shape is complete and type-correct for every input combination.
4. No database, network, or filesystem access occurs during test execution.
