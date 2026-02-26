"""
execution/leads/list_leads_overview.py

Read-only query returning an overview row for every lead.
No business logic, no writes, no datetime.now usage.

Schema tables used:
    leads           — base table  (id, name, email, phone)
    course_invites  — left-joined; MAX(sent_at) per lead when multiple invites exist
    course_state    — left-joined; stored computed state (completion_pct,
                      current_section, last_activity_at)
"""

from execution.db.sqlite import connect, init_db

MAX_LIMIT = 1000

_SQL = """
    SELECT
        l.id                AS lead_id,
        l.name,
        l.email,
        l.phone,
        ci.sent_at          AS invited_sent_at,
        cs.completion_pct,
        cs.current_section,
        cs.last_activity_at
    FROM leads l
    LEFT JOIN (
        SELECT lead_id, MAX(sent_at) AS sent_at
        FROM course_invites
        GROUP BY lead_id
    ) ci ON ci.lead_id = l.id
    LEFT JOIN course_state cs ON cs.lead_id = l.id
    ORDER BY cs.last_activity_at DESC NULLS LAST, l.id ASC
    LIMIT ? OFFSET ?
"""


def list_leads_overview(
    db_path: str,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """Return overview rows for all leads, ordered most-recently-active first.

    Joins leads with the latest course invite (if any) and stored course_state
    (if any).  Neither join is required — leads with no invite or no progress
    appear with NULL values for those fields.

    Args:
        db_path:  Path to the SQLite file.
        limit:    Maximum rows to return.  Hard-capped at MAX_LIMIT (1000).
        offset:   Row offset for pagination.  Defaults to 0.

    Returns:
        List of dicts with keys:
            lead_id, name, email, phone,
            invited_sent_at, completion_pct, current_section, last_activity_at
        Ordered by last_activity_at DESC NULLS LAST, then lead_id ASC.
        Returns an empty list when no leads exist.
    """
    safe_limit = min(limit, MAX_LIMIT)

    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(_SQL, (safe_limit, offset)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
