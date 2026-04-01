"""
execution/scans/find_all_completed_leads.py

Read-only scan: returns ALL leads eligible for booking under the
post-completion booking rule.

Selection rules (all must hold):
  - course_state.completion_pct >= 100   (course fully completed)
  - a course_invites row with sent_at IS NOT NULL exists (invite confirmed sent)

No recency filter. No hot-signal filter. No scoring logic.
Any lead who finishes the course with a confirmed invite qualifies.

No writes, no dispatch, no side effects.
"""

from execution.db.sqlite import connect, init_db

_SQL = """
    SELECT l.id              AS lead_id,
           l.name,
           l.email,
           l.phone,
           cs.completion_pct,
           cs.last_activity_at,
           cs.started_at,
           cs.current_section
    FROM   leads l
    JOIN   course_state cs ON cs.lead_id = l.id
    WHERE  cs.completion_pct >= 100
      AND  EXISTS (
               SELECT 1 FROM course_invites ci
               WHERE  ci.lead_id = l.id
               AND    ci.sent_at IS NOT NULL
           )
    ORDER BY cs.last_activity_at DESC NULLS LAST
    LIMIT  ?
"""


def find_all_completed_leads(
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """Return all leads who have completed the course and received a confirmed invite.

    No time-window filtering — completion alone qualifies a lead regardless
    of how recently they were active.  AI-fit segmentation is available via
    the final_label field in the GHL payload; it is not applied here.

    Args:
        limit:   Maximum rows to return. Defaults to 100.
        db_path: Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        List of dicts with keys:
            lead_id, name, email, phone,
            completion_pct, last_activity_at, started_at, current_section
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(_SQL, (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
