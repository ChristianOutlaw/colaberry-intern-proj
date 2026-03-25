"""
execution/scans/find_no_start_leads.py

Read-only scan: returns leads with a confirmed invite but no course start evidence.

Selection rule:
  - course_invites row with sent_at IS NOT NULL  (invite confirmed delivered)
  - AND no course_state row with started_at IS NOT NULL  (no recorded start)
  - AND no progress_events rows  (no activity at all)

No side effects — does not send nudges, enqueue actions, or write any state.
"""

from execution.db.sqlite import connect, init_db

_SQL = """
    SELECT l.id AS lead_id, l.name, l.email, l.phone, l.created_at
    FROM leads l
    WHERE EXISTS (
        SELECT 1 FROM course_invites ci
        WHERE ci.lead_id = l.id AND ci.sent_at IS NOT NULL
    )
    AND NOT EXISTS (
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
        return [dict(row) for row in rows]
    finally:
        conn.close()
