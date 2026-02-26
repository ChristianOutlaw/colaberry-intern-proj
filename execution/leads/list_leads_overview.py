"""
execution/leads/list_leads_overview.py

Read-only query returning an overview row for every lead.
No business logic, no writes, no datetime.now usage.

Schema tables used:
    leads           — base table  (id, name, email, phone)
    course_invites  — left-joined; MAX(sent_at) per lead when multiple invites exist
    course_state    — left-joined; stored computed state (completion_pct,
                      current_section, last_activity_at)

HOT signal rules (mirroring directives/HOT_LEAD_SIGNAL.md):
    - Invite sent (ci.sent_at IS NOT NULL)
    - completion_pct >= HOT_COMPLETION_THRESHOLD
    - last_activity_at within HOT_RECENCY_DAYS of the injected `now`

`now` MUST be provided by the caller; this function never calls datetime.now().
Pass an explicit datetime so tests and workers remain fully deterministic.
"""

from datetime import datetime, timedelta

from execution.db.sqlite import connect, init_db

MAX_LIMIT = 1000
HOT_RECENCY_DAYS = 7
HOT_COMPLETION_THRESHOLD = 25.0

# `?` for cutoff_iso is first; LIMIT and OFFSET follow.
_SQL = """
    SELECT
        l.id                AS lead_id,
        l.name,
        l.email,
        l.phone,
        ci.sent_at          AS invited_sent_at,
        cs.completion_pct,
        cs.current_section,
        cs.last_activity_at,
        CASE WHEN
            ci.sent_at IS NOT NULL
            AND cs.completion_pct >= ?
            AND cs.last_activity_at >= ?
        THEN 1 ELSE 0 END   AS is_hot
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
    now: datetime | None = None,
) -> list[dict]:
    """Return overview rows for all leads, ordered most-recently-active first.

    Joins leads with the latest course invite (if any) and stored course_state
    (if any).  Neither join is required — leads with no invite or no progress
    appear with NULL values for those fields.

    The `is_hot` column is 1 when all three HOT conditions are met:
        - invite sent
        - completion_pct >= HOT_COMPLETION_THRESHOLD (25 %)
        - last_activity_at within HOT_RECENCY_DAYS (7) days of `now`

    Args:
        db_path:  Path to the SQLite file.
        limit:    Maximum rows to return.  Hard-capped at MAX_LIMIT (1000).
        offset:   Row offset for pagination.  Defaults to 0.
        now:      Reference datetime for the HOT recency window.  Must be
                  provided explicitly — this function never calls datetime.now().
                  Raises ValueError when None.

    Returns:
        List of dicts with keys:
            lead_id, name, email, phone,
            invited_sent_at, completion_pct, current_section, last_activity_at,
            is_hot  (int: 1 = HOT, 0 = not HOT)
        Ordered by last_activity_at DESC NULLS LAST, then lead_id ASC.
        Returns an empty list when no leads exist.

    Raises:
        ValueError: if now is None.
    """
    if now is None:
        raise ValueError(
            "now must be provided explicitly; "
            "do not call datetime.now() inside execution functions."
        )

    safe_limit = min(limit, MAX_LIMIT)
    cutoff_iso = (now - timedelta(days=HOT_RECENCY_DAYS)).isoformat()

    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(
            _SQL, (HOT_COMPLETION_THRESHOLD, cutoff_iso, safe_limit, offset)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
