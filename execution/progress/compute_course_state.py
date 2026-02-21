"""
execution/progress/compute_course_state.py

Derives and persists a lead's current course state from their recorded
progress events. No business logic or hot-lead scoring lives here.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def compute_course_state(
    lead_id: str,
    total_sections: int = 10,
    db_path: str | None = None,
) -> None:
    """Derive and upsert a lead's course state from their progress events.

    Reads all progress_events for the lead, computes the current section,
    completion percentage, and last activity timestamp, then writes the
    result into course_state. If the lead has no events, nothing is written.

    Args:
        lead_id:        ID of the lead to compute state for.
        total_sections: Denominator used for completion_pct calculation.
                        Defaults to 10.
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
    """
    conn = connect(db_path)
    try:
        init_db(conn)

        rows = conn.execute(
            """
            SELECT section, occurred_at
            FROM progress_events
            WHERE lead_id = ?
            ORDER BY occurred_at ASC
            """,
            (lead_id,),
        ).fetchall()

        if not rows:
            return  # no events — nothing to compute or persist

        current_section = rows[-1]["section"]
        last_activity_at = rows[-1]["occurred_at"]

        distinct_sections = conn.execute(
            """
            SELECT COUNT(DISTINCT section)
            FROM progress_events
            WHERE lead_id = ?
            """,
            (lead_id,),
        ).fetchone()[0]

        completion_pct = (distinct_sections / total_sections) * 100.0
        now = _utc_now()

        existing = conn.execute(
            "SELECT lead_id FROM course_state WHERE lead_id = ?", (lead_id,)
        ).fetchone()

        if existing is not None:
            conn.execute(
                """
                UPDATE course_state
                SET current_section  = ?,
                    completion_pct   = ?,
                    last_activity_at = ?,
                    updated_at       = ?
                WHERE lead_id = ?
                """,
                (current_section, completion_pct, last_activity_at, now, lead_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO course_state
                    (lead_id, current_section, completion_pct, last_activity_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (lead_id, current_section, completion_pct, last_activity_at, now),
            )

        conn.commit()
    finally:
        conn.close()
