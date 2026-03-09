"""
execution/leads/get_latest_invite_token.py

Returns the access token for a lead's most recent course invite.
No business logic lives here — only a single read query.
"""

from execution.db.sqlite import connect, init_db


def get_latest_invite_token(
    lead_id: str | None,
    db_path: str | None = None,
) -> str | None:
    """Return the token from the most recent course invite for a lead.

    "Most recent" is determined by sent_at DESC; when two invites share
    the same sent_at the one with the lexicographically greater id wins
    (stable, deterministic tie-break).

    Args:
        lead_id: The lead whose invite token is requested.
                 Returns None immediately when lead_id is None or empty.
        db_path: Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        str   — the token value when a matching invite with a non-NULL token exists.
        None  — when lead_id is blank, no invite exists, or the token column is NULL.
    """
    if not lead_id:
        return None

    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            """
            SELECT token
            FROM course_invites
            WHERE lead_id = ?
            ORDER BY sent_at DESC, id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return row["token"]  # None when the column is NULL
