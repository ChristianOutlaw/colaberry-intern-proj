"""
execution/scans/find_completion_finalization_leads.py

Read-only scan for leads that appear ready for finalization.

Selection rule:
- course_state.completion_pct >= 100  (course completed)
- course_state.started_at IS NOT NULL (has a confirmed start)

No writes, no finalization execution, no dispatch.
No persistent finalized flag exists in the current schema —
this scan is a read-only candidate list only.
"""

from execution.db.sqlite import connect

_SQL = """
    SELECT l.id          AS lead_id,
           l.name,
           l.email,
           l.phone,
           cs.completion_pct,
           cs.started_at,
           cs.last_activity_at,
           cs.current_section,
           CASE WHEN EXISTS (
               SELECT 1 FROM course_invites ci
               WHERE ci.lead_id = l.id AND ci.sent_at IS NOT NULL
           ) THEN 1 ELSE 0 END AS invite_sent
    FROM   leads l
    JOIN   course_state cs ON cs.lead_id = l.id
    WHERE  cs.started_at IS NOT NULL
      AND  cs.completion_pct >= 100
    ORDER BY cs.last_activity_at DESC
    LIMIT  ?
"""


def find_completion_finalization_leads(
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """
    Read-only scan for leads that appear ready for finalization.

    For now:
    - uses existing schema only
    - selects completed leads (completion_pct >= 100, started_at IS NOT NULL)
    - no writes
    - no finalization execution
    - no dispatch

    Returns a list of dicts with keys:
        lead_id, name, email, phone,
        completion_pct, started_at, last_activity_at, current_section
    """
    conn = connect(db_path)
    rows = conn.execute(_SQL, (limit,)).fetchall()
    conn.close()
    # score=None: computing a reliable score requires invited_sent, quiz data,
    # and reflection data not available in this query. Deferred to a future
    # enrichment step that can safely join those fields.
    #
    # has_quiz_data=None: no quiz_scores or quiz_attempts table exists in the
    # current schema. compute_lead_temperature accepts avg_quiz_score and
    # avg_quiz_attempts as caller-supplied values but there is no DB table to
    # query. Deferred until a quiz storage table is added to the schema.
    return [
        {
            **dict(row),
            "invite_sent":   bool(row["invite_sent"]),
            "score":         None,
            "has_quiz_data": None,
        }
        for row in rows
    ]
