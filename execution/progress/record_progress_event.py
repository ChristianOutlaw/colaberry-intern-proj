"""
execution/progress/record_progress_event.py

Records a single lead progress update (phase/section level) into the
progress_events table. Idempotent on event_id. No business logic or
state computation lives here.
"""

from datetime import datetime, timezone

from execution.course.course_registry import is_valid_section_id
from execution.db.sqlite import connect, init_db


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def record_progress_event(
    event_id: str,
    lead_id: str,
    section: str,
    occurred_at: str | None = None,
    metadata_json: str | None = None,
    db_path: str | None = None,
) -> None:
    """Insert a progress event row, skipping silently if it already exists.

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
        db_path:       Path to the SQLite file; defaults to tmp/app.db.

    Raises:
        ValueError: If section is not a canonical section ID.
    """
    if not is_valid_section_id(section):
        raise ValueError(f"Invalid section_id: {section!r}")

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
            INSERT INTO progress_events (id, lead_id, section, occurred_at, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, lead_id, section, occurred_at, metadata_json),
        )
        conn.commit()
    finally:
        conn.close()
