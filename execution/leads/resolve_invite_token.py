"""
execution/leads/resolve_invite_token.py

Resolves a stored invite token to its associated invite and lead context.
Records first_used_at the first time a valid token is resolved.
No business logic lives here — only a read and a conditional write.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db


def resolve_invite_token(
    token: str | None,
    db_path: str | None = None,
) -> dict | None:
    """Look up a course invite by its access token.

    On the first successful resolve, sets first_used_at to the current UTC
    timestamp.  Subsequent resolves leave first_used_at unchanged because the
    UPDATE condition requires first_used_at IS NULL.

    Args:
        token:   The opaque token string from a student invite link.
                 Returns None immediately when token is None or empty.
        db_path: Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        dict with keys:
            invite_id  (str)       The course_invites primary key.
            lead_id    (str)       The associated lead.
            sent_at    (str|None)  ISO-8601 timestamp the invite was sent.
            channel    (str|None)  Delivery channel (e.g. "email", "sms").
            token      (str)       The resolved token (echoed from input).
        None when token is blank or not found in the database.
    """
    if not token:
        return None

    conn = connect(db_path)
    try:
        init_db(conn)

        row = conn.execute(
            """
            SELECT id, lead_id, sent_at, channel, token
            FROM course_invites
            WHERE token = ?
            """,
            (token,),
        ).fetchone()

        if row is None:
            return None

        # Record the timestamp of first use.  The WHERE clause ensures this
        # is a no-op on every resolve after the first one.
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE course_invites
            SET first_used_at = ?
            WHERE token = ? AND first_used_at IS NULL
            """,
            (now_iso, token),
        )
        conn.commit()

    finally:
        conn.close()

    return {
        "invite_id": row["id"],
        "lead_id":   row["lead_id"],
        "sent_at":   row["sent_at"],
        "channel":   row["channel"],
        "token":     row["token"],
    }
