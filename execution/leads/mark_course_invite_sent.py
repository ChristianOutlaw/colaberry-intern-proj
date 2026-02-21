"""
execution/leads/mark_course_invite_sent.py

Records that a "Free Intro to AI Class" invite was sent to a lead.
Idempotent on invite_id. No business logic lives here.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def mark_course_invite_sent(
    invite_id: str,
    lead_id: str,
    sent_at: str | None = None,
    channel: str | None = None,
    metadata_json: str | None = None,
    db_path: str | None = None,
) -> None:
    """Insert a course invite record, skipping silently if it already exists.

    The foreign key constraint on lead_id requires the lead to exist in the
    leads table before this is called; IntegrityError is not caught here so
    the caller is made aware of missing leads.

    Args:
        invite_id:     Stable unique identifier for this invite (TEXT PRIMARY KEY).
        lead_id:       ID of the lead who was invited.
        sent_at:       ISO 8601 timestamp of when the invite was sent;
                       defaults to current UTC if None.
        channel:       Delivery channel (e.g. "sms", "email", "call").
        metadata_json: Optional JSON string for extra context.
        db_path:       Path to the SQLite file; defaults to tmp/app.db.
    """
    conn = connect(db_path)
    try:
        init_db(conn)

        existing = conn.execute(
            "SELECT id FROM course_invites WHERE id = ?", (invite_id,)
        ).fetchone()

        if existing is not None:
            return  # idempotent — already recorded

        if sent_at is None:
            sent_at = _utc_now()

        conn.execute(
            """
            INSERT INTO course_invites (id, lead_id, sent_at, channel, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (invite_id, lead_id, sent_at, channel, metadata_json),
        )
        conn.commit()
    finally:
        conn.close()
