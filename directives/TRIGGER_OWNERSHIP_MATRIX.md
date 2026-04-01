# Trigger Ownership Matrix
**Date:** 2026-04-01  
**Status:** Active  
**Relates to:** `execution/progress/`, `execution/ghl/`, `services/worker/`, `execution/scans/`

---

## 1. Purpose

This system has two classes of triggers that can write to GHL or persist lead state:

**Event-driven triggers** fire immediately in response to a student action (section completion, course completion). They are wired directly into the student course player and run synchronously with the event. They are the primary authority for fresh progress and booking state.

**Scan-driven triggers** fire on a schedule. They identify leads whose state has drifted (no start, stalled progress, failed dispatch) and initiate recovery or nurture flows. They are the secondary authority — they handle what the event path could not, and they retry what failed.

Both trigger classes write to the same GHL destination. Without clear ownership rules they will compete — a scan can send a NUDGE_PROGRESS to a lead seconds after an event-driven path already sent READY_FOR_BOOKING. This document defines who owns what and how the cooldown contract prevents conflicts.

---

## 2. Core Ownership Rules

**Event-driven triggers own:**
- Immediate GHL progress updates on section completion
- Immediate READY_FOR_BOOKING payload and writeback on course completion
- Lead finalization (final_label, final_score) on first completion transition

**Scan-driven triggers own:**
- Delayed nudges for leads who received an invite but never started
- Stale-progress nudges for leads who started but went silent
- Retry flows for failed GHL writeback records
- Recovery booking dispatch for leads who completed but whose event-driven writeback failed

**Anti-conflict contract:**
- Scanners must check the `sync_records` table for a recent `SENT` row before dispatching
- A lead with a `SENT` GHL_WRITEBACK record within the configured cooldown window is skipped by scanners
- Scanners must never send NUDGE_PROGRESS to a lead whose `completion_pct >= 100`
- The booking-ready scanner is a recovery path, not a primary dispatch path

---

## 3. Trigger Ownership Matrix

| Trigger / Condition | Owner | Action | Notes |
|---|---|---|---|
| `SECTION_COMPLETED` | Event-driven | Progress update to GHL | Fires immediately in course player; uses `write_ghl_contact_fields` |
| `COURSE_COMPLETED` (first transition) | Event-driven | Finalize score + READY_FOR_BOOKING writeback | `finalize_on_completion` → `persist_final_score` → `write_ghl_contact_fields` |
| Invite not yet sent | Scanner | `SEND_INVITE` | `find_unsent_invite_leads` → `run_unsent_invite_scan`; scheduled |
| Invite sent, course not started | Scanner | `NUDGE_PROGRESS` | `find_no_start_leads` with 24H / 72H / 7D subtypes; scheduled |
| Started, progress stalled | Scanner | `NUDGE_PROGRESS` | `find_stale_progress_leads` with 48H / 4D / 7D subtypes; scheduled |
| GHL writeback `FAILED` | Scanner / retry worker | Retry writeback | `find_failed_dispatch_records` → `retry_failed_ghl_writeback`; recovery path |
| Completed, event-driven writeback missed or failed | Scanner / recovery | `READY_FOR_BOOKING` writeback | `find_all_completed_leads` → `run_booking_ready_dispatch`; backup only |
| Completed, GHL_WRITEBACK `SENT` within cooldown | Scanner | Skip (cooldown honored) | No action; prevents double-dispatch after successful event write |

---

## 4. Anti-Conflict Rules

### 4.1 Event-driven writes are primary for completion events

When a student completes the last section, `finalize_on_completion` runs inside the course player before the page re-renders. The GHL writeback in this path is the canonical first write of the `READY_FOR_BOOKING` state. No scanner should override or duplicate this write within the cooldown window.

### 4.2 Scanners are secondary for booking-ready leads

`run_booking_ready_dispatch` exists as a recovery path. Its purpose is to catch leads whose event-driven writeback failed (network error, missing `ghl_contact_id` at the time of completion) or leads who completed before the event-driven hook existed. It is not intended to be the primary booking dispatch.

### 4.3 Scanners must honor the cooldown window

Before calling `write_ghl_contact_fields` for any lead, scanner workers must check `sync_records` for a `GHL_WRITEBACK` row with `status = SENT` and `updated_at` within the configured cooldown window (default: 24 hours). If such a row exists, the lead is skipped for this run. This prevents a scanner running one hour after course completion from re-dispatching a booking action the event path already sent successfully.

### 4.4 Scanners must retry FAILED rows

A `GHL_WRITEBACK` row with `status = FAILED` means the event-driven or a prior scan attempt failed. Scanners treat FAILED rows as eligible for dispatch — the cooldown check does not block them. `retry_failed_ghl_writeback` is the dedicated retry path; `run_booking_ready_dispatch` also dispatches to FAILED leads because `_within_cooldown` only blocks on SENT status.

### 4.5 Scanners must never downgrade completed leads

A scan that processes all invited leads must filter out completers before sending NUDGE_PROGRESS. `find_stale_progress_leads` and `find_no_start_leads` select only leads with `completion_pct < 100`. This constraint must be enforced at the SQL layer in every scan, not only in worker logic.

### 4.6 Final labels are segmentation only — not dispatch gates

`FINAL_COLD`, `FINAL_WARM`, and `FINAL_HOT` are AI-fit segmentation signals embedded in the GHL payload. They must not be used to gate or delay a booking dispatch. Any lead with `completion_pct >= 100` and a confirmed invite is eligible for `READY_FOR_BOOKING` regardless of final label.

---

## 5. Current Implementation Status

| Component | Status |
|---|---|
| Section completion GHL writeback | Done — `write_ghl_contact_fields` called in `1_Student_Course_Player.py` after `finalize_on_completion` |
| Course completion finalization | Done — `finalize_on_completion` detects first-completion transition, calls `persist_final_score` |
| Final score persistence | Done — `lead_final_scores` table; `persist_final_score` writes on first completion |
| Booking-ready scan | Done — `find_all_completed_leads`; no HOT filter |
| Booking-ready dispatch worker | Done — `run_booking_ready_dispatch` with cooldown check |
| GHL writeback retry | Done — `retry_failed_ghl_writeback` |
| Invite scan (read-only) | Done — `find_unsent_invite_leads` / `run_unsent_invite_scan` |
| No-start nudge scan (read-only) | Done — `find_no_start_leads` / `run_no_start_scan` |
| Stale-progress nudge scan (read-only) | Done — `find_stale_progress_leads` / `run_stale_progress_scan` |
| Scan workers dispatching nudges | Not yet — all nudge workers are read-only summaries |
| Scheduler / cron | Not yet — no automated scan invocation |

---

## 6. Next Implementation Priorities

### Priority 1 — Use the booking-ready worker as a recovery path only

Schedule `run_booking_ready_dispatch` to run once daily or after a known event-driven failure. Do not run it in real-time — its purpose is to catch leads the event path missed, not to be the primary booking channel. The cooldown window (default 24H) prevents double-dispatch when both paths fire.

### Priority 2 — Wire nudge workers to dispatch actions

`run_no_start_scan` and `run_stale_progress_scan` are currently read-only summaries. Wire them to dispatch GHL campaign tags or contact field updates for the appropriate nudge subtype (NO_START_24H, INACTIVE_4D, etc.). Start with `run_no_start_scan` — it is the highest-volume expected path.

### Priority 3 — Add a scheduler

All scan workers exist as callable Python functions. Add a minimal scheduler (Python `schedule` library or a cron entry) that calls `run_all_scans` on a regular cycle. Start with hourly. This closes the gap between "scans exist" and "nudges actually fire."

### Priority 4 — Avoid redundant booking dispatch

Once the scheduler is running, confirm that the cooldown contract is holding: completed leads that received an event-driven writeback within 24H should not be re-dispatched by `run_booking_ready_dispatch`. Monitor `sync_records.cooldown_skipped` counts to verify. If any gaps appear, tighten the cooldown window or add a lead-level "finalized" flag as an additional skip gate.

---

## Summary

Event-driven triggers own the real-time path. Scanners own the delayed, scheduled, and recovery paths. The `sync_records` cooldown window is the contract that prevents both from colliding. Every new scan or worker must respect this matrix before merging.
