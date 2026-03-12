"""
execution/progress/record_progress_event.py

Records a single lead progress update (phase/section level) into the
progress_events table. Idempotent on event_id. No business logic or
state computation lives here.
"""

from datetime import datetime, timezone

from execution.course.course_registry import is_valid_section_id
from execution.db.sqlite import connect, init_db
from execution.events.send_course_event import send_course_event
from execution.leads.upsert_enrollment import upsert_enrollment


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def record_progress_event(
    event_id: str,
    lead_id: str,
    section: str,
    occurred_at: str | None = None,
    metadata_json: str | None = None,
    course_id: str = "FREE_INTRO_AI_V0",
    db_path: str | None = None,
    webhook_url: str | None = None,
) -> None:
    """Insert a progress event row, skipping silently if it already exists.

    After a new row is written, emits a "section_completed" outbound webhook
    event via send_course_event().  If webhook_url is None or blank the emit
    is a no-op.  Webhook failures are never propagated — a failed outbound
    call does not affect the progress write.

    The foreign key constraint on lead_id means the lead must exist in the
    leads table before this is called; the caller is responsible for that.

    Args:
        event_id:      Stable unique identifier for this event (TEXT PRIMARY KEY).
        lead_id:       ID of the lead this event belongs to.
        section:       Canonical section ID (e.g. "P1_S1"). Must be one of the
                       IDs defined in directives/COURSE_STRUCTURE.md and
                       execution/course/course_registry.SECTION_IDS.
        occurred_at:   ISO 8601 timestamp; defaults to current UTC if None.
        metadata_json: Optional JSON string for extra context.
        course_id:     Course this event belongs to. Defaults to
                       'FREE_INTRO_AI_V0' for backward compatibility.
        db_path:       Path to the SQLite file; defaults to tmp/app.db.
        webhook_url:   Optional URL to POST a "section_completed" event to
                       after a successful write.  Omit (or pass None) to skip.

    Raises:
        ValueError: If section is not a canonical section ID.
    """
    if not is_valid_section_id(section):
        raise ValueError(f"Invalid section_id: {section!r}")

    # Ensure an enrollment row exists before inserting the event.  Called
    # before opening the event connection to avoid concurrent write-lock
    # contention.  upsert_enrollment is idempotent — safe to call every time.
    upsert_enrollment(lead_id, course_id=course_id, db_path=db_path)

    _wrote_new_row = False
    conn = connect(db_path)
    try:
        init_db(conn)

        existing = conn.execute(
            "SELECT id FROM progress_events WHERE id = ?", (event_id,)
        ).fetchone()

        if existing is not None:
            return  # idempotent — already recorded

        if occurred_at is None:
            occurred_at = _utc_now()

        conn.execute(
            """
            INSERT INTO progress_events (id, lead_id, course_id, section, occurred_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, lead_id, course_id, section, occurred_at, metadata_json),
        )
        conn.commit()
        _wrote_new_row = True
    finally:
        conn.close()

    # Emit outbound event only after the DB connection is fully closed, only
    # when a new row was actually inserted (not on idempotent skips), and only
    # when a webhook_url was supplied.  send_course_event swallows all network
    # failures — progress write is safe.
    if _wrote_new_row and webhook_url:
        send_course_event(
            "section_completed",
            {"lead_id": lead_id, "course_id": course_id, "section": section},
            webhook_url=webhook_url,
        )
