# SCAN_SCHEDULER_DESIGN.md
**Design Directive — Future Scheduled Scan Runner**

> **Status: Design only. No scheduler is currently implemented.**

---

## Purpose

This directive defines the intended future scheduled execution model for scan
workers. It documents design intent, safety rules, and verification criteria so
that when a scheduler is built, it is built consistently with the existing
deterministic-execution architecture.

No scheduler exists today. This document does not represent implemented
behavior — it represents agreed design direction.

---

## Current Implemented Building Blocks

The following pieces are already built and tested. A future scheduler will
compose them, not replace them.

| Component | Location |
|-----------|----------|
| `run_unsent_invite_scan` | `services/worker/run_unsent_invite_scan.py` |
| `run_no_start_scan` | `services/worker/run_no_start_scan.py` |
| `run_failed_dispatch_scan` | `services/worker/run_failed_dispatch_scan.py` |
| `run_stale_progress_scan` | `services/worker/run_stale_progress_scan.py` |
| `run_all_scans` | `services/worker/run_all_scans.py` |
| `export_scan_snapshot` | `services/worker/export_scan_snapshot.py` |
| `classify_no_start_threshold` | `execution/scans/classify_no_start_threshold.py` |
| `classify_stale_progress_threshold` | `execution/scans/classify_stale_progress_threshold.py` |
| `requeue_failed_action` | `execution/events/requeue_failed_action.py` |

All components above are read-only unless explicitly noted (e.g.,
`requeue_failed_action` writes a status transition, nothing else).

---

## Future Scheduler Responsibilities

When a scheduler is implemented, it should be responsible for:

- Invoking scan workers on a defined schedule (e.g., periodic or triggered)
- Collecting read-only scan summaries from each worker
- Optionally persisting snapshots for audit or downstream consumption
- Optionally triggering a downstream action-selection phase (to be designed separately)
- Never bypassing validation, directives, or test coverage requirements

The scheduler is an orchestration layer only. It does not contain business
logic — it calls existing, independently testable workers.

---

## Explicit Non-Goals (Current State)

The following are **not implemented** and must not be implied by any code or
documentation until explicitly designed and approved:

- No cron or scheduled runner exists
- No automatic dispatch of outreach (email, SMS, calls)
- No automatic enqueue of sync records
- No retry loop execution
- No bulk campaign sending

Any future work that crosses one of these boundaries requires a new directive
and explicit approval before implementation.

---

## Proposed Execution Order

When a scheduled run fires, scans should execute in this fixed order:

1. `UNSENT_INVITE_SCAN` — leads never invited
2. `NO_START_SCAN` — leads invited but never started
3. `FAILED_DISPATCH_RETRY_SCAN` — sync records in FAILED state
4. `STALE_PROGRESS_SCAN` — leads who started but have gone inactive

This order matches the current fixed order in `run_all_scans` and
`export_scan_snapshot`. Any deviation requires a directive update.

---

## Safety Rules

A scheduler implementation must follow these rules without exception:

1. **Observable** — every scheduled run must emit a log boundary (start, end,
   outcome) that can be reviewed without running the code again.
2. **Logged** — run start time, scan results summary, and any errors must be
   recorded before the run is considered complete.
3. **Non-mutating in read-only mode** — the scan phase must not write to the
   database. Only an explicitly approved execution phase (not yet designed) may
   write.
4. **Failure-isolated** — a failure in one scan must not prevent remaining
   scans from running. Each scan result is independent.
5. **Deterministic and testable** — the scheduler must be structured so that
   each scan it invokes can be tested in isolation with a fixed DB, without
   triggering real outreach or side effects.

---

## How Success Will Later Be Verified

These are future verification criteria, not current implementation targets.
When a scheduler is built, it is not complete until:

- The scheduled runner invokes all scans in the fixed order documented above
- The emitted snapshot matches the shape produced by `export_scan_snapshot`
- Failures in one scan are isolated and do not corrupt results from others
- No hidden writes occur during the read-only scan phase
- All new scheduler code has corresponding unit or integration tests
- The relevant directive (this file or a successor) is updated to reflect
  implemented behavior

---

## Related Directives

- `directives/SCAN_JOBS.md` — current scan implementation and requeue boundary
- `directives/CORA_RECOMMENDATION_EVENTS.md` — downstream action event types
