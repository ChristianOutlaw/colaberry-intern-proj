"""
execution/leads/persist_final_score.py

Persists a lead's finalized score and label into the lead_final_scores table.
No business logic or scoring lives here — pure DB write.
"""

from execution.db.sqlite import connect, init_db

_COURSE_ID = "FREE_INTRO_AI_V0"


def persist_final_score(
    lead_id: str,
    *,
    final_label: str,
    final_score: int | None,
    finalized_at: str,
    db_path: str | None = None,
) -> None:
    """Write the final score and label for a lead into lead_final_scores.

    Uses INSERT OR REPLACE so repeated calls are safe — the row is overwritten
    with the latest finalized values.  The composite primary key (lead_id,
    course_id) ensures one locked result per lead per course.

    Args:
        lead_id:      ID of the lead being finalized.
        final_label:  One of FINAL_COLD, FINAL_WARM, FINAL_HOT.
        final_score:  Numeric score 0–100 from compute_lead_temperature, or
                      None when the fallback (hot_signal-only) path was used.
        finalized_at: ISO-8601 UTC timestamp — must be injected by the caller;
                      this function never calls datetime.now().
        db_path:      Path to the SQLite file; defaults to tmp/app.db.
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO lead_final_scores
                (lead_id, course_id, final_label, final_score, finalized_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lead_id, _COURSE_ID, final_label, final_score, finalized_at),
        )
        conn.commit()
    finally:
        conn.close()
