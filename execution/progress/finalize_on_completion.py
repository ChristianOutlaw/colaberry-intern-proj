"""
execution/progress/finalize_on_completion.py

Orchestration helper that wraps compute_course_state and fires lead
finalization exactly once — when completion_pct first reaches 100%.

No business logic or scoring lives here.  This file only:
  1. Detects the first-completion transition.
  2. Delegates scoring to build_ghl_full_field_payload.
  3. Delegates label assignment to finalize_lead_score.
  4. Delegates persistence to persist_final_score.
"""

from execution.db.sqlite import connect, init_db
from execution.ghl.build_ghl_full_field_payload import build_ghl_full_field_payload
from execution.leads.finalize_lead_score import finalize_lead_score
from execution.leads.persist_final_score import persist_final_score
from execution.progress.compute_course_state import compute_course_state

_COURSE_ID = "FREE_INTRO_AI_V0"


def _read_completion_pct(lead_id: str, db_path: str | None) -> float:
    """Return the stored completion_pct for lead+course, or 0.0 if absent.

    Matches the default used inside compute_course_state so the transition
    guard is evaluated on the same baseline.
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT completion_pct FROM course_state WHERE lead_id = ? AND course_id = ?",
            (lead_id, _COURSE_ID),
        ).fetchone()
    finally:
        conn.close()

    if row is None or row["completion_pct"] is None:
        return 0.0
    return float(row["completion_pct"])


def finalize_on_completion(
    lead_id: str,
    *,
    total_sections: int,
    now: str,
    db_path: str | None = None,
    webhook_url: str | None = None,
    base_url: str = "http://localhost:8501",
) -> None:
    """Wrap compute_course_state and trigger finalization on first completion.

    Reads the stored completion_pct before and after compute_course_state.
    When the value transitions from below 100% to 100% or above for the
    first time, builds the full scored payload, assigns a final label, and
    persists the result to lead_final_scores.

    Repeated calls after the transition are safe: the pre-call completion_pct
    will already be >= 100 so the guard is false and nothing runs beyond
    compute_course_state.

    Args:
        lead_id:        ID of the lead whose progress was just updated.
        total_sections: Section count denominator — forwarded to
                        compute_course_state unchanged.
        now:            ISO-8601 UTC string injected by the caller.  Used for
                        all time-dependent computations and the finalized_at
                        timestamp.  This function never calls datetime.now().
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
        webhook_url:    Optional outbound webhook URL forwarded to
                        compute_course_state for event emission.
        base_url:       Base URL for the student portal; forwarded to
                        build_ghl_full_field_payload for course_link assembly.
    """
    # Step 1 — capture the stored completion_pct before recomputation.
    prev_completion_pct = _read_completion_pct(lead_id, db_path)

    # Step 2 — recompute and persist course_state (always runs).
    compute_course_state(
        lead_id,
        total_sections=total_sections,
        course_id=_COURSE_ID,
        db_path=db_path,
        webhook_url=webhook_url,
    )

    # Step 3 — read the updated completion_pct.
    new_completion_pct = _read_completion_pct(lead_id, db_path)

    # Step 4 — fire finalization only on the first-completion transition.
    if new_completion_pct >= 100.0 and prev_completion_pct < 100.0:

        # Build the full scored payload using the same injected now/db_path.
        result = build_ghl_full_field_payload(
            lead_id,
            now=now,
            base_url=base_url,
            db_path=db_path,
        )

        if not result["ok"]:
            # Lead disappeared between steps — nothing to finalize.
            return

        ghl_payload = result["payload"]

        # Assemble the minimal shape required by finalize_lead_score.
        # hot_signal is the fallback path used when score is None.
        fin_payload = {
            "requires_finalization": True,
            "score": ghl_payload.get("final_confidence_score"),
            "hot_signal": (
                "HOT" if ghl_payload.get("final_label") == "FINAL_HOT" else "NOT_HOT"
            ),
        }

        # Assign final_label via the existing finalization boundary function.
        finalized = finalize_lead_score(lead_id, fin_payload)

        # Persist the locked result.
        persist_final_score(
            lead_id,
            final_label=finalized["final_label"],
            final_score=finalized.get("score"),
            finalized_at=now,
            db_path=db_path,
        )
