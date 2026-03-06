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
| `event_type` | `str` | One of the six v1 event types (see below) |
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
| `NUDGE_START_CLASS` | Invited, course not started | `MEDIUM` | `EMAIL` |
| `HOT_LEAD_BOOKING` | Hot signal active | `HIGH` | `CALL` |
| `REENGAGE_STALLED_LEAD` | Started, inactive > 14 days | `HIGH` | `CALL` |
| `NUDGE_PROGRESS` | In progress, recently active, not hot | `MEDIUM` | `EMAIL` |
| `NO_ACTION` | Course complete, not hot | `LOW` | `None` |

---

## Decision Rules

Rules are evaluated in this exact order. First match wins.

1. **`SEND_INVITE`** — `invite_sent == False`
   - priority: `LOW`, channel: `EMAIL`
   - reason_codes: `["NOT_INVITED"]`

2. **`NUDGE_START_CLASS`** — `invite_sent == True` AND `completion_percent` is `None`
   or `0.0`
   - priority: `MEDIUM`, channel: `EMAIL`
   - reason_codes: `["INVITED_NO_START"]`

3. **`HOT_LEAD_BOOKING`** — `hot_signal == "HOT"`
   - priority: `HIGH`, channel: `CALL`
   - reason_codes: `["HOT_SIGNAL_ACTIVE"]`
   - Note: takes precedence over REENGAGE even if activity is stale, because the
     hot signal already encodes the 7-day window.

4. **`REENGAGE_STALLED_LEAD`** — `completion_percent > 0` AND `completion_percent < 100`
   AND (`last_activity_at` is `None` OR days since last activity > `STALL_DAYS`)
   - priority: `HIGH`, channel: `CALL`
   - reason_codes: `["ACTIVITY_STALLED"]`

5. **`NUDGE_PROGRESS`** — `completion_percent > 0` AND `completion_percent < 100`
   (activity within `STALL_DAYS` implied — rules 1–4 did not match)
   - priority: `MEDIUM`, channel: `EMAIL`
   - reason_codes: `["ACTIVE_LEARNER"]`

6. **`NO_ACTION`** — all other cases (typically `completion_percent == 100`)
   - priority: `LOW`, channel: `None`
   - reason_codes: `["COURSE_COMPLETE"]` or `["NO_QUALIFYING_STATE"]`

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
| `INVITED_NO_START` | Invite sent, no course progress |
| `HOT_SIGNAL_ACTIVE` | Binary hot signal is `"HOT"` |
| `ACTIVITY_STALLED` | Active learner with no recent activity |
| `ACTIVE_LEARNER` | In-progress lead with recent activity |
| `COURSE_COMPLETE` | Completion is 100% |
| `NO_QUALIFYING_STATE` | None of the above rules matched |

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

| # | invite_sent | completion_pct | hot_signal | days_inactive | Expected event_type |
|---|-------------|----------------|------------|---------------|---------------------|
| T1 | False | None | NOT_HOT | None | SEND_INVITE |
| T2 | True | None | NOT_HOT | None | NUDGE_START_CLASS |
| T3 | True | 0.0 | NOT_HOT | None | NUDGE_START_CLASS |
| T4 | True | 33.0 | HOT | 9 | HOT_LEAD_BOOKING |
| T5 | True | 33.0 | NOT_HOT | 20 | REENGAGE_STALLED_LEAD |
| T6 | True | 33.0 | NOT_HOT | None | REENGAGE_STALLED_LEAD |
| T7 | True | 33.0 | NOT_HOT | 5 | NUDGE_PROGRESS |
| T8 | True | 100.0 | NOT_HOT | 2 | NO_ACTION |
| T9 | True | 90.0 | HOT | 25 | HOT_LEAD_BOOKING (HOT beats stale) |

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
