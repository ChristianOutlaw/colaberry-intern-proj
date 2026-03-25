# SCAN_JOBS.md
**Directive v1 — Read-Only Scan Workers and Requeue Boundary**

---

## Purpose

This directive specifies the read-only scan layer and the requeue boundary for
failed dispatch records. Scans identify leads or sync records in actionable
states. They do not dispatch outreach, mutate lead state, or enqueue actions.

---

## Implemented Scans (v1)

### UNSENT_INVITE_SCAN
- **Source:** `execution/scans/find_unsent_invite_leads.py`
- **Worker:** `services/worker/run_unsent_invite_scan.py`
- **Selection rule:** leads with no `course_invites` row where `sent_at IS NOT NULL`
- **Returns:** `lead_id, name, email, phone, created_at`

### NO_START_SCAN
- **Source:** `execution/scans/find_no_start_leads.py`
- **Worker:** `services/worker/run_no_start_scan.py`
- **Selection rule:** invite sent + no `course_state.started_at` + no `progress_events`
- **Enrichment:** each row carries `no_start_threshold` (see Threshold Classifiers below)
- **Summary field:** `threshold_counts` — counts by `NO_START_24H / NO_START_72H / NO_START_7D / NONE`

### FAILED_DISPATCH_RETRY_SCAN
- **Source:** `execution/scans/find_failed_dispatch_records.py`
- **Worker:** `services/worker/run_failed_dispatch_scan.py`
- **Selection rule:** `sync_records WHERE status = 'FAILED'`
- **Returns:** `id, lead_id, destination, status, reason, error, created_at, updated_at`

### STALE_PROGRESS_SCAN
- **Source:** `execution/scans/find_stale_progress_leads.py`
- **Worker:** `services/worker/run_stale_progress_scan.py`
- **Selection rule:** `course_state.started_at IS NOT NULL` + `completion_pct < 100` + `last_activity_at IS NOT NULL`
- **Enrichment:** each row carries `stale_progress_threshold` (see Threshold Classifiers below)
- **Summary field:** `threshold_counts` — counts by `INACTIVE_48H / INACTIVE_4D / INACTIVE_7D / NONE`

### COMPLETION_FINALIZATION_SCAN
- **Source:** `execution/scans/find_completion_finalization_leads.py`
- **Worker:** `services/worker/run_completion_finalization_scan.py`
- **Selection rule:** `course_state.started_at IS NOT NULL` + `course_state.completion_pct >= 100`
- **Returns:** `lead_id, name, email, phone, completion_pct, started_at, last_activity_at, current_section, score`
- **Intended action:** `FINALIZE_LEAD_SCORE` — read-only metadata only; does not execute finalization
- **Worker summary includes:**
  - `scan_name`, `count`, `lead_ids`, `limit_used`
  - `score_summary`:
    ```python
    {
        "HAS_SCORE":     int,   # rows where score is numeric
        "MISSING_SCORE": int,   # rows where score is None
    }
    ```
  - `score_summary` is derived from each returned row's `score` field
  - With current implementation, rows return `score=None` until safe score enrichment (requiring `invited_sent`, quiz data, and reflection data) is added in a future step
  - This metadata is read-only and does not execute finalization
  - `fallback_final_label_summary`:
    ```python
    {
        "FINAL_COLD": int,
        "FINAL_WARM": int,
        "FINAL_HOT":  int,
    }
    ```
  - Derived from the same fallback logic used by `finalize_lead_score` when `score` is missing:
    `hot_signal == "HOT"` → `FINAL_HOT`, otherwise → `FINAL_WARM`
  - With the current scan row shape, candidates fall into `FINAL_WARM` because scan rows carry no `hot_signal` field and `score` is currently `None`; `FINAL_HOT` or `FINAL_COLD` become reachable once score or hot_signal enrichment is added
  - Read-only metadata only — does not execute finalization or assign persistent final labels
- **Notes:**
  - Read-only candidate scan only
  - No persistent finalized flag exists in the current schema
  - No finalization execution happens in the scan or worker

---

## Aggregator Worker

### run_all_scans
- **Worker:** `services/worker/run_all_scans.py`
- Calls all five scan workers in fixed order and returns one combined summary.
- Fixed result order:
  1. `UNSENT_INVITE_SCAN`
  2. `NO_START_SCAN`
  3. `FAILED_DISPATCH_RETRY_SCAN`
  4. `STALE_PROGRESS_SCAN`
  5. `COMPLETION_FINALIZATION_SCAN`
- **Summary shape:**
  ```python
  {
      "scan_count":   5,
      "limit_used":   int,
      "generated_at": str,   # UTC ISO-8601 timestamp, e.g. "2026-03-25T12:00:00Z"
      "action_summary": {
          "SEND_INVITE":           int,
          "NUDGE_PROGRESS":        int,
          "REQUEUE_FAILED_ACTION": int,
          "FINALIZE_LEAD_SCORE":   int,
          "UNKNOWN":               int,
      },
      "results": [ ... ],   # one entry per scan, in fixed order
  }
  ```
- **`action_summary` notes:**
  - Derived from each nested result's `intended_action` field
  - Counts scan result categories (one per scan), not leads or records
  - Read-only metadata only — does not dispatch, enqueue, or retry anything
- **Each nested result entry includes at minimum:**
  - `scan_name` — canonical constant from scan registry
  - `count` — number of qualifying rows returned
  - `limit_used` — the limit argument actually used
  - `intended_action` — read-only metadata derived from `map_scan_to_intended_action`;
    does not dispatch or enqueue actions

### export_scan_snapshot
- **Worker:** `services/worker/export_scan_snapshot.py`
- Calls `run_all_scans` once and returns results shaped for external consumption.
- **Signature:**
  ```python
  def export_scan_snapshot(
      limit: int = 100,
      db_path: str | None = None,
      scan_name: str | None = None,
      intended_action: str | None = None,
  ) -> dict
  ```
- **Optional filters:**
  - `scan_name` — if provided, only entries where `entry["scan_name"] == scan_name` are kept
  - `intended_action` — if provided, only entries where `entry["intended_action"] == intended_action` are kept
  - Both filters apply when both are provided
  - `scan_count` reflects the filtered number of scan entries
  - `scans` reflects the filtered list
  - `action_summary` remains unchanged (reflects the full unfiltered run)
  - Filtering is read-only and does not dispatch, enqueue, or retry anything
- **Snapshot shape:**
  ```python
  {
      "type":           "SCAN_SNAPSHOT",
      "generated_at":   str,   # UTC ISO-8601 timestamp
      "scan_count":     int,   # filtered count
      "action_summary": {      # unfiltered — reflects full run_all_scans output
          "SEND_INVITE":           int,
          "NUDGE_PROGRESS":        int,
          "REQUEUE_FAILED_ACTION": int,
          "UNKNOWN":               int,
      },
      "scans": [ ... ],        # filtered list of scan result entries
  }
  ```

---

## Worker Summary Shape (all individual workers)

Every scan worker returns at minimum:

```python
{
    "scan_name":  str,       # canonical constant from scan_registry
    "count":      int,       # number of qualifying rows returned
    "lead_ids":   [str],     # present on lead-oriented scans
    "record_ids": [int],     # present on FAILED_DISPATCH_RETRY_SCAN
    "limit_used": int,       # the limit argument actually used
}
```

`threshold_counts` is additionally present on `NO_START_SCAN` and
`STALE_PROGRESS_SCAN`.

---

## Threshold Classifiers

Threshold classification enriches scan output with a time-bucket label.
These are **pure functions** — no DB access, no dispatch.

### classify_no_start_threshold
- **Source:** `execution/scans/classify_no_start_threshold.py`
- **Input:** `invite_sent_at` (ISO-8601 string), `now` (datetime)
- **Output:** `"NO_START_24H"` | `"NO_START_72H"` | `"NO_START_7D"` | `None`

| Bucket | Condition |
|--------|-----------|
| `NO_START_24H` | invite sent ≥ 24 h ago |
| `NO_START_72H` | invite sent ≥ 72 h ago |
| `NO_START_7D`  | invite sent ≥ 168 h (7 days) ago |
| `None` | invite sent < 24 h ago or timestamp missing |

### classify_stale_progress_threshold
- **Source:** `execution/scans/classify_stale_progress_threshold.py`
- **Input:** `last_activity_at` (ISO-8601 string), `now` (datetime)
- **Output:** `"INACTIVE_48H"` | `"INACTIVE_4D"` | `"INACTIVE_7D"` | `None`

| Bucket | Condition |
|--------|-----------|
| `INACTIVE_48H` | last activity ≥ 48 h ago |
| `INACTIVE_4D`  | last activity ≥ 96 h (4 days) ago |
| `INACTIVE_7D`  | last activity ≥ 168 h (7 days) ago |
| `None` | last activity < 48 h ago or timestamp missing |

> Threshold classification enriches scan output but does **not** yet trigger
> campaigns or dispatch outreach. That boundary is not yet implemented.

---

## Requeue Boundary

### REQUEUE_FAILED_ACTION
- **Source:** `execution/events/requeue_failed_action.py`
- **Signature:** `requeue_failed_action(record_id: int, db_path: str | None = None) -> dict`
- **Transition:** `FAILED` → `NEEDS_SYNC` (by integer PK of `sync_records`)
- **Returns:**
  ```python
  {
      "record_id":       int,
      "previous_status": str,   # "FAILED"
      "new_status":      str,   # "NEEDS_SYNC"
      "updated":         bool,  # False if record not found or not in FAILED status
  }
  ```
- **Guards:**
  - Returns `updated=False` if row not found
  - Returns `updated=False` if current status is not `FAILED`
- **No retry execution** — this function only changes the status flag.
  The actual retry (re-dispatching the action) is a separate, not-yet-implemented step.

---

## Scan Registry

- **Source:** `execution/scans/scan_registry.py`
- Canonical constants: `UNSENT_INVITE_SCAN`, `NO_START_SCAN`,
  `FAILED_DISPATCH_RETRY_SCAN`, `STALE_PROGRESS_SCAN`, `COMPLETION_FINALIZATION_SCAN`
- Helper: `is_known_scan_name(name: str) -> bool`
- All worker wrappers import scan names from this registry — no hardcoded strings.

---

## Important Constraints

- **All current scan workers are read-only.** No scan enqueues or dispatches
  actions in v1.
- **No scheduler exists yet.** Scans are invoked on demand; there is no cron
  or scheduled runner.
- **No bulk dispatch.** `run_all_scans` aggregates results; it does not trigger
  any outreach.
- **No nudge sending from scans.** Threshold classification identifies urgency
  but does not send communications.
- **No retry execution loop.** `requeue_failed_action` transitions a record's
  status only; it does not re-attempt the original dispatch.

---

## How Success Is Verified

The following test files cover this layer:

| Test file | What it verifies |
|-----------|-----------------|
| `tests/test_find_unsent_invite_leads.py` | SQL selection for UNSENT_INVITE_SCAN |
| `tests/test_find_no_start_leads.py` | SQL selection + threshold enrichment for NO_START_SCAN |
| `tests/test_find_failed_dispatch_records.py` | SQL selection for FAILED_DISPATCH_RETRY_SCAN |
| `tests/test_find_stale_progress_leads.py` | SQL selection + threshold enrichment for STALE_PROGRESS_SCAN |
| `tests/test_run_unsent_invite_scan.py` | Worker wrapper summary shape + limit |
| `tests/test_run_no_start_scan.py` | Worker summary including threshold_counts |
| `tests/test_run_failed_dispatch_scan.py` | Worker summary including record_ids + limit |
| `tests/test_run_stale_progress_scan.py` | Worker summary including threshold_counts |
| `tests/test_scan_worker_smoke.py` | Cross-worker smoke: all four workers importable and callable |
| `tests/test_requeue_failed_action.py` | FAILED → NEEDS_SYNC transition, guard cases |
| `tests/test_failed_scan_requeue_integration.py` | Scan → requeue boundary end-to-end |
| `tests/test_find_completion_finalization_leads.py` | SQL selection for COMPLETION_FINALIZATION_SCAN (completed leads only, limit) |
| `tests/test_run_completion_finalization_scan.py` | Worker summary shape, exclusion of incomplete leads, limit, score_summary values, fallback_final_label_summary counts |
| `tests/test_run_all_scans.py` | Aggregator shape (5 scans), limit propagation, fixed scan order, intended_action presence, generated_at parseability, action_summary including FINALIZE_LEAD_SCORE |
| `tests/test_export_scan_snapshot.py` | Snapshot shape, filter by scan_name, filter by intended_action, filtered scan_count behavior |

A change to scan selection logic, worker summary shape, threshold buckets, or
requeue behavior must be accompanied by passing tests from the relevant files
above before the change is considered complete.
