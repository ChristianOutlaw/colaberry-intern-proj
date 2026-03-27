"""
execution/scans/find_warm_review_leads.py

Read-only scan: returns leads eligible for WARM_REVIEW outreach.

Selection rules (all must hold):
  - course_invites: a row with sent_at IS NOT NULL exists   (invite confirmed sent)
  - course_state.completion_pct >= 100                      (course fully completed)
  - NOT HOT: last_activity_at IS NULL OR < hot_cutoff       (outside 7-day HOT window)
  - NOT stale: last_activity_at IS NULL OR >= stale_cutoff  (within 14-day stale window)

The NOT-HOT and NOT-stale gates carve out the WARM_REVIEW band from the
completed-lead population, mirroring Rule 4b of build_cora_recommendation.py:
  - leads with recent activity (< 7 days) → BOOKING_READY, not here
  - leads inactive > 14 days             → REENGAGE_COMPLETED, not here
  - completed leads with NULL activity or 7–14 days inactive → WARM_REVIEW (this scan)

No writes, no dispatch, no side effects.
`now` must be provided by the caller; this function never calls datetime.now().
"""

from datetime import datetime, timedelta, timezone

from execution.db.sqlite import connect, init_db
from execution.decision.build_cora_recommendation import STALL_DAYS
from execution.leads.compute_hot_lead_signal import ACTIVITY_WINDOW_DAYS

_SQL = """
    SELECT l.id              AS lead_id,
           l.name,
           l.email,
           l.phone,
           cs.completion_pct,
           cs.started_at,
           cs.last_activity_at,
           cs.current_section
    FROM   leads l
    JOIN   course_state cs ON cs.lead_id = l.id
    WHERE  cs.completion_pct >= 100
      AND  EXISTS (
               SELECT 1 FROM course_invites ci
               WHERE  ci.lead_id = l.id
               AND    ci.sent_at IS NOT NULL
           )
      AND  (cs.last_activity_at IS NULL OR cs.last_activity_at < ?)
      AND  (cs.last_activity_at IS NULL OR cs.last_activity_at >= ?)
    ORDER BY cs.last_activity_at DESC NULLS LAST
    LIMIT  ?
"""


def find_warm_review_leads(
    now: datetime,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """
    Read-only scan for leads eligible for WARM_REVIEW outreach.

    Returns completed leads that sit between the HOT recency window and the
    stale re-engagement threshold — plus completed leads with no activity
    timestamp at all.

    Args:
        now:      Reference UTC datetime (injected by caller — never call datetime.now()).
        limit:    Maximum rows to return. Defaults to 100.
        db_path:  Path to the SQLite file. Uses default dev DB when None.

    Returns:
        List of dicts with keys:
            lead_id, name, email, phone,
            completion_pct, started_at, last_activity_at, current_section

    Raises:
        ValueError: if now is None.
    """
    if now is None:
        raise ValueError(
            "now must be provided explicitly; "
            "do not call datetime.now() inside execution functions."
        )

    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    hot_cutoff_iso   = (now_utc - timedelta(days=ACTIVITY_WINDOW_DAYS)).isoformat()
    stale_cutoff_iso = (now_utc - timedelta(days=STALL_DAYS)).isoformat()

    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(_SQL, (hot_cutoff_iso, stale_cutoff_iso, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
