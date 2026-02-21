"""
execution/leads/upsert_lead.py

Inserts a new lead or updates an existing one without overwriting
fields that were not supplied. No business logic lives here.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def upsert_lead(
    lead_id: str,
    phone: str | None = None,
    email: str | None = None,
    name: str | None = None,
    db_path: str | None = None,
) -> None:
    """Insert a new lead row or update an existing one.

    - On insert: all supplied fields are written; created_at and updated_at
      are set to the current UTC timestamp.
    - On update: only non-None arguments overwrite existing column values;
      created_at is never touched; updated_at is always refreshed.

    Args:
        lead_id: Stable unique identifier for the lead (TEXT PRIMARY KEY).
        phone:   Optional phone number; ignored on update when None.
        email:   Optional email address; ignored on update when None.
        name:    Optional display name; ignored on update when None.
        db_path: Path to the SQLite file; defaults to the repo tmp/app.db.
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        now = _utc_now()

        existing = conn.execute(
            "SELECT id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO leads (id, phone, email, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (lead_id, phone, email, name, now, now),
            )
        else:
            # Build SET clause dynamically; only include fields that were supplied.
            updates: list[tuple[str, object]] = [("updated_at", now)]
            if phone is not None:
                updates.append(("phone", phone))
            if email is not None:
                updates.append(("email", email))
            if name is not None:
                updates.append(("name", name))

            set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
            values = [val for _, val in updates]
            values.append(lead_id)

            conn.execute(
                f"UPDATE leads SET {set_clause} WHERE id = ?",  # noqa: S608
                values,
            )

        conn.commit()
    finally:
        conn.close()
