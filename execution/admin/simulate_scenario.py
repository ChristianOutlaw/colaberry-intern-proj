"""
execution/admin/simulate_scenario.py

Dev/test harness — Operation 3: Simulate Scenario.

Places a lead into one of six named, deterministic states by orchestrating
existing execution functions.  No business logic.  No direct SQL.

THIS MODULE IS DEV-ONLY.  It must never be imported or called in production.
See directives/ADMIN_TEST_MODE.md § Operation 3 — Simulate Scenario.

Delegation chain (all existing /execution functions):
  - execution.admin.reset_progress     (clear prior state)
  - execution.admin.seed_lead          (create/update lead + optional invite)
  - execution.progress.record_progress_event   (record individual events)
  - execution.progress.compute_course_state    (derive completion_pct)
  - execution.leads.get_lead_status    (existence check only)
"""

from datetime import datetime, timedelta, timezone

from execution.admin.reset_progress import OperationNotConfirmedError, reset_progress
from execution.admin.seed_lead import seed_lead
from execution.course.course_registry import TOTAL_SECTIONS
from execution.leads.get_lead_status import get_lead_status
from execution.progress.compute_course_state import compute_course_state
from execution.progress.record_progress_event import record_progress_event

# ---------------------------------------------------------------------------
# Scenario registry — exhaustive for v1; matches directives/ADMIN_TEST_MODE.md
# ---------------------------------------------------------------------------
_KNOWN_SCENARIOS: frozenset[str] = frozenset({
    "COLD_NO_INVITE",
    "INVITED_NO_PROGRESS",
    "PARTIAL_PROGRESS",
    "HOT_READY",
    "STALE_ACTIVITY",
    "FULL_COMPLETION",
})

# Sections used for partial scenarios (first 3 of 9).
_PARTIAL_SECTIONS: tuple[str, ...] = ("P1_S1", "P1_S2", "P1_S3")

# All 9 canonical sections in order.
_ALL_SECTIONS: tuple[str, ...] = (
    "P1_S1", "P1_S2", "P1_S3",
    "P2_S1", "P2_S2", "P2_S3",
    "P3_S1", "P3_S2", "P3_S3",
)

# Must exceed ACTIVITY_WINDOW_DAYS (7) from directives/HOT_LEAD_SIGNAL.md.
_STALE_OFFSET_DAYS: int = 8


# ---------------------------------------------------------------------------
# Deterministic ID helpers — stable for the same (lead_id, section) pair.
# ---------------------------------------------------------------------------

def _mk_invite_id(lead_id: str) -> str:
    return f"SIM:{lead_id}:invite"


def _mk_event_id(lead_id: str, section: str) -> str:
    return f"SIM:{lead_id}:{section}"


# ---------------------------------------------------------------------------
# Internal: apply a single named scenario after the reset guard has run.
# ---------------------------------------------------------------------------

def _apply_scenario(
    scenario_id: str,
    lead_id: str,
    now: datetime,
    db_path: str | None,
) -> None:
    """Execute the ordered operation sequence for *scenario_id*.

    All timestamps are derived from the injected *now*; datetime.now() is
    never called here.  compute_course_state is called after any progress
    events so that get_lead_status returns an accurate completion_pct.
    """
    now_str = now.isoformat()
    stale_str = (now - timedelta(days=_STALE_OFFSET_DAYS)).isoformat()
    invite_id = _mk_invite_id(lead_id)

    if scenario_id == "COLD_NO_INVITE":
        # Lead exists; no invite; no progress.
        seed_lead(lead_id=lead_id, db_path=db_path)

    elif scenario_id == "INVITED_NO_PROGRESS":
        # Lead invited but no sections completed.
        seed_lead(
            lead_id=lead_id,
            mark_invite_sent=True,
            invite_id=invite_id,
            sent_at=now_str,
            db_path=db_path,
        )

    elif scenario_id in ("PARTIAL_PROGRESS", "HOT_READY"):
        # Lead invited; 3 of 9 sections completed with recent timestamps.
        # HOT_READY is structurally identical: same 3 sections, same recent
        # timestamps.  "Hot" status is verified by the caller via get_lead_status.
        seed_lead(
            lead_id=lead_id,
            mark_invite_sent=True,
            invite_id=invite_id,
            sent_at=now_str,
            db_path=db_path,
        )
        for section in _PARTIAL_SECTIONS:
            record_progress_event(
                _mk_event_id(lead_id, section),
                lead_id,
                section,
                occurred_at=now_str,
                db_path=db_path,
            )
        compute_course_state(
            lead_id,
            total_sections=TOTAL_SECTIONS,
            db_path=db_path,
        )

    elif scenario_id == "STALE_ACTIVITY":
        # Lead invited; 3 sections completed but all events older than 7 days.
        seed_lead(
            lead_id=lead_id,
            mark_invite_sent=True,
            invite_id=invite_id,
            sent_at=stale_str,
            db_path=db_path,
        )
        for section in _PARTIAL_SECTIONS:
            record_progress_event(
                _mk_event_id(lead_id, section),
                lead_id,
                section,
                occurred_at=stale_str,
                db_path=db_path,
            )
        compute_course_state(
            lead_id,
            total_sections=TOTAL_SECTIONS,
            db_path=db_path,
        )

    elif scenario_id == "FULL_COMPLETION":
        # All 9 sections completed; invite sent.
        seed_lead(
            lead_id=lead_id,
            mark_invite_sent=True,
            invite_id=invite_id,
            sent_at=now_str,
            db_path=db_path,
        )
        for section in _ALL_SECTIONS:
            record_progress_event(
                _mk_event_id(lead_id, section),
                lead_id,
                section,
                occurred_at=now_str,
                db_path=db_path,
            )
        compute_course_state(
            lead_id,
            total_sections=TOTAL_SECTIONS,
            db_path=db_path,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simulate_scenario(
    *,
    scenario_id: str,
    lead_id: str,
    confirm: bool,
    now: datetime | None = None,
    db_path: str | None = None,
) -> dict:
    """Place a lead into a named deterministic state.

    Directive reference: ADMIN_TEST_MODE.md § Operation 3 — Simulate Scenario.

    Validation order (each check fires before any DB access):
        1. lead_id blank  → ok=False soft return
        2. scenario_id unknown → ValueError
        3. confirm not True → OperationNotConfirmedError

    If the lead already has data, reset_progress(reset_invite=True) is called
    first so the scenario starts from a clean slate.

    Args:
        scenario_id: One of the six scenario IDs defined in ADMIN_TEST_MODE.md.
        lead_id:     Stable unique identifier for the lead; whitespace trimmed.
        confirm:     Must be True or OperationNotConfirmedError is raised.
        now:         UTC datetime injected by the caller for all timestamps.
                     Defaults to real current UTC when None.  Tests must always
                     supply this so behaviour is fully deterministic.
        db_path:     Path to the SQLite file.  Defaults to tmp/app.db.
                     Tests must always supply an explicit isolated path.

    Returns:
        {"ok": True, "message": f"Scenario {scenario_id} applied to lead {lead_id}."}

    Raises:
        ValueError: When scenario_id is not one of the six known IDs.
        OperationNotConfirmedError: When confirm is not True.
    """
    lead_id = lead_id.strip()
    if not lead_id:
        return {"ok": False, "message": "Lead ID is required."}

    if scenario_id not in _KNOWN_SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_id}.")

    if not confirm:
        raise OperationNotConfirmedError("Reset requires confirm=True.")

    if now is None:
        now = datetime.now(timezone.utc)

    # If the lead already exists, wipe its state before applying the scenario.
    status = get_lead_status(lead_id, db_path=db_path)
    if status["lead_exists"]:
        reset_progress(
            lead_id=lead_id,
            reset_invite=True,
            confirm=True,
            db_path=db_path,
        )

    _apply_scenario(scenario_id, lead_id, now, db_path)

    return {
        "ok": True,
        "message": f"Scenario {scenario_id} applied to lead {lead_id}.",
    }
