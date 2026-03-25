"""
execution/scans/find_stale_progress_leads.py

Read-only scan: returns started-but-incomplete leads with inactivity classification.

Selection rule (all three must hold):
  - course_state.started_at IS NOT NULL        (has started)
  - course_state.completion_pct < 100          (not yet completed)
  - course_state.last_activity_at IS NOT NULL  (has activity evidence to classify)

No side effects — does not send nudges, enqueue actions, or write any state.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db
from execution.scans.classify_stale_progress_threshold import classify_stale_progress_threshold

_SQL = """
    SELECT l.id AS lead_id, l.name, l.email, l.phone,
           cs.completion_pct, cs.current_section,
           cs.last_activity_at, cs.started_at
    FROM leads l
    JOIN course_state cs ON cs.lead_id = l.id
    WHERE cs.started_at IS NOT NULL
      AND (cs.completion_pct IS NULL OR cs.completion_pct < 100)
      AND cs.last_activity_at IS NOT NULL
    ORDER BY cs.last_activity_at ASC
    LIMIT ?
"""


def find_stale_progress_leads(limit: int = 100, db_path: str | None = None) -> list[dict]:
    """
    Read-only scan for started-but-incomplete leads with inactivity classification.

    For now:
    - use existing schema only
    - identify leads who have started
    - exclude completed leads
    - include last_activity_at (or equivalent current activity marker)
    - attach stale_progress_threshold using classify_stale_progress_threshold(...)
    - no writes
    - no dispatch
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(_SQL, (limit,)).fetchall()
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        r = dict(row)
        r["stale_progress_threshold"] = classify_stale_progress_threshold(
            r.get("last_activity_at"),
            now,
        )
        result.append(r)
    return result
