"""
execution/scans/find_ready_for_booking_leads.py

Read-only scan: returns leads eligible for READY_FOR_BOOKING outreach.

Selection rules (all three must hold):
  - course_invites: a row with sent_at IS NOT NULL exists   (invite confirmed sent)
  - course_state.completion_pct >= 100                      (course fully completed)
  - course_state.last_activity_at >= activity_cutoff        (within HOT recency window)

These mirror the BOOKING_READY lifecycle state and the HOT signal gates in
execution/leads/compute_hot_lead_signal.py (ACTIVITY_WINDOW_DAYS).

No writes, no dispatch, no side effects.
`now` must be provided by the caller; this function never calls datetime.now().
"""

from datetime import datetime, timedelta, timezone

from execution.db.sqlite import connect, init_db
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
      AND  cs.last_activity_at IS NOT NULL
      AND  cs.last_activity_at >= ?
      AND  EXISTS (
               SELECT 1 FROM course_invites ci
               WHERE  ci.lead_id = l.id
               AND    ci.sent_at IS NOT NULL
           )
    ORDER BY cs.last_activity_at DESC
    LIMIT  ?
"""


def find_ready_for_booking_leads(
    now: datetime,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """
    Read-only scan for leads eligible for READY_FOR_BOOKING outreach.

    Returns leads where all HOT + completion gates are satisfied:
      - invite confirmed sent
      - course fully completed (completion_pct >= 100)
      - last activity within ACTIVITY_WINDOW_DAYS (7 days) of now

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
    activity_cutoff_iso = (now_utc - timedelta(days=ACTIVITY_WINDOW_DAYS)).isoformat()

    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(_SQL, (activity_cutoff_iso, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
