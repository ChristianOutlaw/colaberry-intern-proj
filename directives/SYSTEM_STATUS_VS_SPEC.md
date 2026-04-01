# System Status vs. Spec
**Reference documents:** Cory/Cora Action, Trigger, and Scoring Blueprint + Draft v1 Spec  
**Date assessed:** 2026-04-01  
**Assessed against:** current `main` branch

---

## Completed

### Lead Lifecycle States
- All 8 spec states (A–H) are modeled in `execution/leads/derive_lead_lifecycle_state.py`
- State constants: `NOT_INVITED`, `INVITED_NOT_STARTED`, `STARTED_ACTIVE`, `STARTED_STALE`, `COMPLETED_WARM`, `COMPLETED_REENGAGE`, `BOOKING_READY`
- Rule ordering mirrors the spec priority chain

### Invite Semantics (Core Fix)
- `course_invites.sent_at` is the authoritative "invite sent" marker
- `find_no_start_leads.py` correctly filters on `ci.sent_at IS NOT NULL`
- `mark_course_invite_sent.py` exists as the explicit "mark sent" function
- `decide_next_cold_lead_action` returns `SEND_INVITE` when `invite_sent = False`

### Action Families (External)
- `SEND_INVITE` — returned when invite not sent
- `NUDGE_PROGRESS` — returned when invite sent, course not complete
- `READY_FOR_BOOKING` — returned when `completion_pct >= 100`

### Scoring Engine
- `compute_lead_temperature.py` — full weighted 0–100 scoring engine
- 6 signal categories: completion, recency, quiz, reflection, velocity, retry penalty
- Invite cap (max 15 pts if not invited)
- Reason codes + human-readable summary per lead
- `SCORE_HOT = 70`, `SCORE_WARM = 35`, `SCORE_COLD` = below 35

### Final Label Classification
- `classify_final_lead_label.py` — pure function: `FINAL_COLD / FINAL_WARM / FINAL_HOT`
- `finalize_lead_score.py` — finalization boundary function
- `FINAL_HOT` requires full course completion (`completion_percent >= 100` AND `hot_signal == "HOT"`)

### Provisional Score (Rolling)
- `build_ghl_full_field_payload.py` computes `rolling_confidence_score` using midpoint progress events
- `rolling_label` derived from the rolling score
- GHL payload includes both `rolling_confidence_score` and `final_confidence_score` separately

### Structured Reflection Scoring (Mode A)
- All 9 course sections now have `rating_prompts` in `course_map.json`
- Student course player renders selectbox (1–5 scale) for rating prompts
- `_resolve_reflection_confidence()` reads stored ratings and maps to `LOW / MEDIUM / HIGH`
- `HIGH = +15 pts`, `MEDIUM = +8 pts`, `LOW = 0 pts` wired into `compute_lead_temperature`
- Free-text responses return `None` → `REFLECTION_UNKNOWN` (7 pts near-half-credit, safe Mode B behavior)

### Restart / Rescore Detection
- `rescore_on_section_restart.py` — detects completion drop and triggers fresh rescore
- Called at the section-completion boundary when completion_pct drops

### Scan Layer (Read-Only)
All 7 scan types from the spec are implemented as pure read functions:

| Scan | File |
|---|---|
| `UNSENT_INVITE_SCAN` | `find_unsent_invite_leads.py` |
| `NO_START_SCAN` (24H / 72H / 7D subtypes) | `find_no_start_leads.py` + `classify_no_start_threshold.py` |
| `STALE_PROGRESS_SCAN` (48H / 4D / 7D) | `find_stale_progress_leads.py` + `classify_stale_progress_threshold.py` |
| `COMPLETION_FINALIZATION_SCAN` | `find_completion_finalization_leads.py` |
| `READY_FOR_BOOKING_SCAN` | `find_ready_for_booking_leads.py` |
| `WARM_REVIEW_SCAN` | `find_warm_review_leads.py` |
| `FAILED_DISPATCH_RETRY_SCAN` | `find_failed_dispatch_records.py` |

`scan_registry.py` and `map_scan_to_intended_action.py` exist as canonical registries.

### Worker Entry Points (Read-Only)
Worker shells exist for all scans: `run_unsent_invite_scan`, `run_no_start_scan`, `run_stale_progress_scan`, `run_completion_finalization_scan`, `run_failed_dispatch_scan`, `run_all_scans`, `export_scan_snapshot`.

### Bulk Ingestion (Thin)
- `bulk_ingest_leads.py` — validates and upserts a list of lead payloads
- Per-lead error capture; partial failures do not abort the batch
- Thin loop over `upsert_lead()` — no queuing, no async

### GHL Integration
- `build_ghl_full_field_payload.py` — full canonical 25-field payload
- `write_ghl_contact_fields.py` — HTTP POST with outbox pattern (NEEDS_SYNC → SENT/FAILED)
- `retry_failed_ghl_writeback.py` — manual retry for FAILED rows
- GHL writeback triggered on section completion (`1_Student_Course_Player.py`)

### Event-Driven Triggers (Partial)
- `LEAD_RECEIVED` → `process_ghl_lead_intake.py` (upsert + invite + writeback)
- `SECTION_COMPLETED` → `record_progress_event` + `compute_course_state` + GHL writeback
- `COURSE_STARTED` → `student_started_course` webhook event emitted
- `COURSE_COMPLETED` → `course_completed` webhook event emitted
- `REFLECTION_SUBMITTED` → stored via `save_reflection_response.py`

---

## Not Completed / Gaps

### 1. READY_FOR_BOOKING gate is incomplete
**Problem:** `decide_next_cold_lead_action` returns `READY_FOR_BOOKING` when `completion_pct >= 100`, but does **not** check if `final_score = HOT`.  
**Spec rule (hard):** READY_FOR_BOOKING requires full course completion AND final score = HOT.  
A lead that completed with a FINAL_WARM or FINAL_COLD score would still receive `READY_FOR_BOOKING`.

### 2. WARM_REVIEW action not returned by decision function
**Problem:** `decide_next_cold_lead_action` has no branch for completed warm leads. The scan `find_warm_review_leads.py` finds them, but the action is never dispatched.  
**Spec:** completed + FINAL_WARM → `WARM_REVIEW`.

### 3. Scan workers do not dispatch actions
**Problem:** All worker entry points (`run_no_start_scan`, `run_stale_progress_scan`, etc.) are **read-only summaries**. They identify leads but do not dispatch `NUDGE_PROGRESS`, `SEND_INVITE`, or any other action.  
**Spec:** Scheduled scans should trigger actions. Currently: identify only.

### 4. NUDGE_PROGRESS subtypes not wired to dispatch
**Problem:** `find_no_start_leads` classifies `NO_START_24H / 72H / 7D` and `find_stale_progress_leads` classifies `INACTIVE_48H / 4D / 7D`, but these subtypes are never passed to any GHL campaign trigger.  
**Spec:** Each subtype maps to a different GHL campaign / outreach sequence.

### 5. COURSE_COMPLETED does not trigger FINALIZE_LEAD_SCORE
**Problem:** `compute_course_state` emits a `course_completed` webhook event when completion hits 100%, but no finalization is run (no score lock, no label written to DB).  
**Spec:** `COURSE_COMPLETED` → `FINALIZE_LEAD_SCORE` → determine FINAL_COLD/WARM/HOT → enqueue next action.  
Currently finalization is a function that exists but is never called automatically.

### 6. Final score and label not persisted to DB
**Problem:** `finalize_lead_score()` accepts a payload dict and returns it modified, but nothing writes the final label or score to a `leads` table column or a dedicated finalization table.  
Scores are recomputed on every `build_ghl_full_field_payload` call. No locked final state exists.

### 7. Score gate for finalization is overly restrictive
**Problem:** `can_compute_final_score` requires `invite_sent AND has_quiz_data AND has_reflection_data`. Requiring `has_reflection_data` means any lead without a structured rating stored cannot receive a final score — even if their completion and quiz signals are strong.  
**Spec:** Reflection evidence is optional; if unscored, treat as UNKNOWN and continue scoring.

### 8. Per-section attempt tracking does not exist
**Problem:** `rescore_on_section_restart.py` detects a drop in overall `completion_pct` but does not identify **which section** dropped, does not remove that section's evidence from the score, and does not track multiple attempts per section.  
**Spec:** "Each section needs a current active attempt. Only the latest valid attempt counts toward final section score."

### 9. Restart penalty not implemented
**Problem:** The spec specifies that restarting a section should apply a configurable penalty on top of rescoring. `rescore_on_section_restart` rescores from scratch using current signals but applies no penalty.

### 10. Bulk dispatch loop not implemented
**Problem:** The Cory sync worker (`run_cory_sync.py`) processes one record at a time. No `process_pending_cory_sync_records(limit=...)` batch loop with claim-and-dispatch pattern exists.  
**Spec:** Worker should claim N rows, dispatch each, summarize success/failure/retry.

### 11. No scheduler is wired
**Problem:** All scan workers exist as callable Python functions but nothing calls them on a schedule. No cron, no task queue, no background scheduler.  
**Spec:** Scheduled scans are required for no-start nudges, stale progress nudges, and booking finalization.

### 12. `invite_generated_at` column is null for all existing leads
**Problem:** `course_invites.generated_at` column exists in schema but is `NULL` for all records. The distinction between "invite generated" and "invite sent" is semantically correct in the scan logic but the `generated_at` timestamp is never written.

### 13. REENGAGE_COMPLETED action not implemented
**Status:** Acknowledged as a future action in the spec. `STATE_COMPLETED_REENGAGE` exists in `derive_lead_lifecycle_state.py` but no action is mapped or dispatched for it.

### 14. FINAL_COLD post-completion action not implemented
**Problem:** The spec defines STATE_H (COURSE_COMPLETED_FINAL_COLD) with possible low-priority nurture or no action. No branch handles this in the decision or dispatch layer.

---

## Recommended Next Steps (Priority Order)

### Priority 1 — Fix READY_FOR_BOOKING gate
**File:** `execution/decision/decide_next_cold_lead_action.py`  
Add a final score check. Only return `READY_FOR_BOOKING` when `completion_pct >= 100` AND final signal is `HOT`. Return `WARM_REVIEW` when warm, `NO_ACTION` (or a FINAL_COLD label) when cold.  
This is a business-correctness bug — the wrong people would be booked.

### Priority 2 — Wire FINALIZE_LEAD_SCORE at course completion
**Files:** `execution/progress/compute_course_state.py` or a new orchestration helper  
When `completion_pct` first reaches 100%, call `finalize_lead_score` and persist the result (final label + score) to a DB column or a `lead_final_scores` table. This is the State E → F/G/H transition.

### Priority 3 — Persist final score and label to DB
**Action:** Add `final_label` and `final_score` columns to `leads` table (or a new `lead_final_scores` table).  
Populate them when finalization runs. GHL payload can then read stored final state instead of recomputing every call.

### Priority 4 — Relax the score gate
**File:** `execution/leads/can_compute_final_score.py`  
Remove `has_reflection_data` as a hard requirement. Reflection is optional signal. A lead with invite + quiz data and no reflection should still get a final score (reflection scores as UNKNOWN = 7 pts).

### Priority 5 — Wire scan workers to dispatch actions
**Files:** `services/worker/run_no_start_scan.py`, `run_stale_progress_scan.py`, `run_unsent_invite_scan.py`  
After finding leads, call the appropriate dispatch function per lead (e.g. `write_ghl_contact_fields` with the action subtype, or push a GHL campaign tag). Start with `NUDGE_PROGRESS / NO_START` as it is the highest-volume expected path.

### Priority 6 — Add NUDGE_PROGRESS subtype to GHL payload
**File:** `execution/ghl/build_ghl_full_field_payload.py`  
Expose the current nudge subtype (e.g. `NO_START_72H`, `INACTIVE_4D`) as a GHL field so GHL automations can branch on it without re-querying the system.

### Priority 7 — Add a minimal scheduler
**Options:** Python `schedule` library run in a long-lived process, or a cron entry calling `run_all_scans.py`.  
Wire to `/services/worker/run_all_scans.py`. Start with a once-per-hour scan cycle.  
This closes the gap between "scans exist" and "nudges actually fire."

### Priority 8 — Implement bulk dispatch loop
**File:** New `services/worker/run_bulk_dispatch.py`  
Wrap `process_one_cory_sync_record` in a batch loop with a configurable limit. Claim rows atomically (status = PROCESSING), dispatch, mark SENT or FAILED, return a summary. This is the `process_pending_cory_sync_records(limit=...)` pattern from the spec.

### Priority 9 — Per-section attempt tracking
**Action:** Add an `attempts` column or a `section_attempts` table. Track which attempt is currently active per (lead, section). Modify `rescore_on_section_restart` to target the specific section rather than inferring from completion drop. Apply restart penalty during rescore.  
This is the most structurally complex change — defer until priorities 1–8 are done.

---

## Summary Table

| Spec Requirement | Status |
|---|---|
| Lead lifecycle states (A–H) | Done |
| Invite generated ≠ invite sent (semantics) | Done (sent_at used correctly) |
| invite_generated_at written to DB | Partial (column exists, always NULL) |
| SEND_INVITE action | Done |
| NUDGE_PROGRESS action (top-level) | Done |
| NUDGE_PROGRESS subtypes (NO_START, INACTIVE) | Classified, not dispatched |
| READY_FOR_BOOKING (completion only) | Done |
| READY_FOR_BOOKING (requires HOT score) | **Gap — not gated on score** |
| WARM_REVIEW action | **Gap — not returned or dispatched** |
| REENGAGE_COMPLETED | Not implemented (future) |
| Provisional score (rolling) | Done |
| Final score (locked at completion) | Computed, **not persisted** |
| FINAL_COLD / FINAL_WARM / FINAL_HOT labels | Done (compute only) |
| FINAL_HOT requires full completion | Done |
| Reflection Mode A (structured selectbox) | Done (all 9 sections) |
| Reflection Mode B (unscored fallback) | Done (free-text → UNKNOWN) |
| Restart detection | Done (completion drop) |
| Per-section attempt tracking | **Not implemented** |
| Restart penalty | **Not implemented** |
| FINALIZE_LEAD_SCORE at completion | **Not triggered automatically** |
| Scan: UNSENT_INVITE | Done (read-only) |
| Scan: NO_START (24H/72H/7D) | Done (read-only) |
| Scan: STALE_PROGRESS (48H/4D/7D) | Done (read-only) |
| Scan: COMPLETION_FINALIZATION | Done (read-only) |
| Scan: READY_FOR_BOOKING | Done (read-only) |
| Scan: WARM_REVIEW | Done (read-only) |
| Scan: FAILED_DISPATCH_RETRY | Done (read-only) |
| Scan workers dispatch actions | **Gap — all read-only** |
| Bulk ingestion | Done (thin loop) |
| Bulk dispatch loop | **Not implemented** |
| Scheduler / cron | **Not implemented** |
| GHL canonical payload | Done (25 fields) |
| GHL writeback on section completion | Done |
| GHL writeback retry | Done |
