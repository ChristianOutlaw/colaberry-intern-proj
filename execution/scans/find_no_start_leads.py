"""
execution/scans/find_no_start_leads.py

Read-only scan: returns leads with a confirmed invite but no course start evidence.

Selection rule:
  - course_invites row with sent_at IS NOT NULL  (invite confirmed delivered)
  - AND no course_state row with started_at IS NOT NULL  (no recorded start)
  - AND no progress_events rows  (no activity at all)

No side effects — does not send nudges, enqueue actions, or write any state.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db
from execution.scans.classify_no_start_threshold import classify_no_start_threshold

_SQL = """
    SELECT l.id AS lead_id, l.name, l.email, l.phone, l.created_at,
           ci.sent_at AS invite_sent_at
    FROM leads l
    JOIN course_invites ci ON ci.lead_id = l.id AND ci.sent_at IS NOT NULL
    WHERE NOT EXISTS (
        SELECT 1 FROM course_state cs
        WHERE cs.lead_id = l.id AND cs.started_at IS NOT NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM progress_events pe
        WHERE pe.lead_id = l.id
    )
    ORDER BY l.created_at ASC
    LIMIT ?
"""


def find_no_start_leads(limit: int = 100, db_path: str | None = None) -> list[dict]:
    """
    Read-only scan for leads with invite sent but no course start yet.

    For now:
    - use existing schema only
    - identify leads with confirmed invite sent
    - exclude leads with any recorded course/progress start evidence
    - no side effects
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
        r["no_start_threshold"] = classify_no_start_threshold(
            r.get("invite_sent_at"),
            now,
        )
        result.append(r)
    return result
