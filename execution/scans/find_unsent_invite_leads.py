"""
execution/scans/find_unsent_invite_leads.py

Read-only scan: returns leads that have never received a confirmed course invite.

Selection rule: leads with no course_invites row where sent_at IS NOT NULL.
No side effects — does not send invites, enqueue actions, or write any state.
"""

from execution.db.sqlite import connect, init_db

_SQL = """
    SELECT l.id AS lead_id, l.name, l.email, l.phone, l.created_at
    FROM leads l
    WHERE NOT EXISTS (
        SELECT 1 FROM course_invites ci
        WHERE ci.lead_id = l.id AND ci.sent_at IS NOT NULL
    )
    ORDER BY l.created_at ASC
    LIMIT ?
"""


def find_unsent_invite_leads(limit: int = 100, db_path: str | None = None) -> list[dict]:
    """
    Placeholder scan for leads that need SEND_INVITE.

    For now:
    - query existing storage using current schema
    - return rows for leads that exist but do not have invite_sent confirmed
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
