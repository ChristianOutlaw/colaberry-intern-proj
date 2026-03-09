"""
execution/leads/resolve_invite_token.py

Resolves a stored invite token to its associated invite and lead context.
No business logic lives here — only a single read query.
"""

from execution.db.sqlite import connect, init_db


def resolve_invite_token(
    token: str | None,
    db_path: str | None = None,
) -> dict | None:
    """Look up a course invite by its access token.

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
    finally:
        conn.close()

    if row is None:
        return None

    return {
        "invite_id": row["id"],
        "lead_id":   row["lead_id"],
        "sent_at":   row["sent_at"],
        "channel":   row["channel"],
        "token":     row["token"],
    }
