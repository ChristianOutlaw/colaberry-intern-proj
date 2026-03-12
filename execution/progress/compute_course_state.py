"""
execution/progress/compute_course_state.py

Derives and persists a lead's current course state from their recorded
progress events. No business logic or hot-lead scoring lives here.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db
from execution.events.send_course_event import send_course_event


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def compute_course_state(
    lead_id: str,
    total_sections: int = 10,
    course_id: str = "FREE_INTRO_AI_V0",
    db_path: str | None = None,
    webhook_url: str | None = None,
) -> None:
    """Derive and upsert a lead's course state from their progress events.

    Reads all progress_events for the lead and course, computes the current
    section, completion percentage, and last activity timestamp, then writes
    the result into course_state. If the lead has no events, nothing is written.

    After a successful write, emits up to two outbound webhook events:

    - "student_started_course" — fires once, on the first INSERT into
      course_state (transition guard: existing row was absent).  Payload
      includes lead_id, course_id, started_at.

    - "course_completed" — fires once, when completion_pct first reaches
      100 % (transition guard: previous stored value was < 100 %).
      Payload includes lead_id, course_id, completion_pct.

    Repeated recomputation never re-fires either event.  Webhook failures
    are never propagated — the course_state write is always authoritative.

    Args:
        lead_id:        ID of the lead to compute state for.
        total_sections: Denominator used for completion_pct calculation.
                        Defaults to 10.
        course_id:      Course whose events are used. Defaults to
                        'FREE_INTRO_AI_V0' for backward compatibility.
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
        webhook_url:    Optional URL to POST outbound events to.  Omit (or
                        pass None) to skip all outbound emission.
    """
    conn = connect(db_path)
    try:
        init_db(conn)

        rows = conn.execute(
            """
            SELECT section, occurred_at
            FROM progress_events
            WHERE lead_id = ? AND course_id = ?
            ORDER BY occurred_at ASC
            """,
            (lead_id, course_id),
        ).fetchall()

        if not rows:
            return  # no events — nothing to compute or persist

        current_section   = rows[-1]["section"]
        last_activity_at  = rows[-1]["occurred_at"]
        first_activity_at = rows[0]["occurred_at"]

        distinct_sections = conn.execute(
            """
            SELECT COUNT(DISTINCT section)
            FROM progress_events
            WHERE lead_id = ? AND course_id = ?
            """,
            (lead_id, course_id),
        ).fetchone()[0]

        completion_pct = (distinct_sections / total_sections) * 100.0
        now = _utc_now()

        # Read the existing row for two transition guards used after the write:
        #   _is_first_start    — True only when inserting the first-ever row.
        #   prev_completion_pct — previous value for the completion guard.
        # A missing row is treated as 0.0 for completion (never completed).
        existing = conn.execute(
            "SELECT completion_pct FROM course_state WHERE lead_id = ? AND course_id = ?",
            (lead_id, course_id),
        ).fetchone()
        prev_completion_pct: float = existing["completion_pct"] if existing is not None else 0.0
        _is_first_start: bool = existing is None

        if existing is not None:
            conn.execute(
                """
                UPDATE course_state
                SET current_section  = ?,
                    completion_pct   = ?,
                    last_activity_at = ?,
                    started_at       = COALESCE(started_at, ?),
                    updated_at       = ?
                WHERE lead_id = ? AND course_id = ?
                """,
                (current_section, completion_pct, last_activity_at, first_activity_at, now,
                 lead_id, course_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO course_state
                    (lead_id, course_id, current_section, completion_pct,
                     last_activity_at, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (lead_id, course_id, current_section, completion_pct,
                 last_activity_at, first_activity_at, now),
            )

        conn.commit()
    finally:
        conn.close()

    # Emit "student_started_course" only when this is the first-ever course_state
    # row for this lead+course (INSERT path).  Subsequent recomputations take the
    # UPDATE path and never reach here.  send_course_event swallows all failures.
    if webhook_url and _is_first_start:
        send_course_event(
            "student_started_course",
            {"lead_id": lead_id, "course_id": course_id, "started_at": first_activity_at},
            webhook_url=webhook_url,
        )

    # Emit "course_completed" only when:
    #   1. A webhook URL was supplied.
    #   2. The newly computed completion_pct is exactly 100 %.
    #   3. The previous stored value was below 100 % (transition guard —
    #      prevents re-firing on repeated recomputation of a completed course).
    # send_course_event swallows all network failures — state write is safe.
    if webhook_url and completion_pct >= 100.0 and prev_completion_pct < 100.0:
        send_course_event(
            "course_completed",
            {"lead_id": lead_id, "course_id": course_id, "completion_pct": completion_pct},
            webhook_url=webhook_url,
        )
