"""
execution/leads/upsert_enrollment.py

Inserts a new course enrollment or returns the existing one without
overwriting any fields. Idempotent on (lead_id, course_id).
No business logic lives here.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _enrollment_id(lead_id: str, course_id: str) -> str:
    """Derive a stable, human-readable enrollment ID."""
    return f"ENR_{lead_id}_{course_id}"


def upsert_enrollment(
    lead_id: str,
    course_id: str = "FREE_INTRO_AI_V0",
    enrolled_at: str | None = None,
    status: str = "active",
    db_path: str | None = None,
) -> dict:
    """Insert a new enrollment or return the existing one unchanged.

    - On insert: all supplied fields are written; created_at and updated_at
      are set to the current UTC timestamp.
    - On duplicate (same lead_id + course_id): the existing row is returned
      as-is; no fields are overwritten.

    Args:
        lead_id:     Stable unique identifier for the lead (must exist in leads).
        course_id:   Course being enrolled in. Defaults to 'FREE_INTRO_AI_V0'.
        enrolled_at: Optional ISO 8601 timestamp of when the lead enrolled.
                     Stored as NULL when omitted.
        status:      Enrollment lifecycle status. Defaults to 'active'.
        db_path:     Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        dict with keys: id, lead_id, course_id, enrolled_at, status,
                        created_at, updated_at.

    Raises:
        sqlite3.IntegrityError: If lead_id does not exist in the leads table.
    """
    conn = connect(db_path)
    try:
        init_db(conn)

        existing = conn.execute(
            "SELECT * FROM course_enrollments WHERE lead_id = ? AND course_id = ?",
            (lead_id, course_id),
        ).fetchone()

        if existing is not None:
            return dict(existing)

        now = _utc_now()
        enrollment_id = _enrollment_id(lead_id, course_id)

        conn.execute(
            """
            INSERT INTO course_enrollments
                (id, lead_id, course_id, enrolled_at, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (enrollment_id, lead_id, course_id, enrolled_at, status, now, now),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM course_enrollments WHERE id = ?",
            (enrollment_id,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()
