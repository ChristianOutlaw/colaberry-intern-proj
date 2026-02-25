"""
execution/leads/mark_sync_record_sent.py

Transitions a sync_records outbox row from NEEDS_SYNC to SENT.
No network calls.  No datetime.now() — now is always injected.
One responsibility: record that a GHL push was delivered.

Outbox schema: execution/db/sqlite.py (sync_records table)
"""

from datetime import datetime

from execution.db.sqlite import connect, init_db

_STATUS_NEEDS_SYNC = "NEEDS_SYNC"
_STATUS_SENT = "SENT"
_DESTINATION_DEFAULT = "GHL"


def mark_sync_record_sent(
    lead_id: str,
    now: datetime,
    destination: str = _DESTINATION_DEFAULT,
    response_json: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Transition the outbox row (lead_id, destination, NEEDS_SYNC) to SENT.

    Checks are evaluated in order and stop at the first match:

    1. Lead does not exist in leads table:
           {"ok": False, "reason": "LEAD_NOT_FOUND"}

    2. A SENT row already exists for (lead_id, destination):
           {"ok": True, "changed": False, "status": "SENT"}
       Returning here — before inspecting the NEEDS_SYNC row — prevents any
       attempt to UPDATE NEEDS_SYNC → SENT when SENT is already present, which
       would violate the UNIQUE(lead_id, destination, status) constraint.

    3. No NEEDS_SYNC row exists for (lead_id, destination):
           {"ok": False, "reason": "NO_NEEDS_SYNC_ROW"}

    4. Happy path — NEEDS_SYNC row found, SENT row absent:
       Update the existing NEEDS_SYNC row in-place:
           status       → "SENT"
           updated_at   → now.isoformat()
           response_json → provided value (None clears the field)
       Return:
           {"ok": True, "changed": True, "status": "SENT"}

    Args:
        lead_id:       Stable unique identifier for the lead.
        now:           Current UTC datetime, injected by the caller.
                       Never calls datetime.now() internally.
        destination:   Outbox destination label.  Defaults to "GHL".
        response_json: Optional JSON string from the downstream system's
                       response.  Stored as-is; not parsed or validated here.
        db_path:       Path to the SQLite file; defaults to tmp/app.db.
    """
    now_str = now.isoformat()

    conn = connect(db_path)
    try:
        init_db(conn)

        # ------------------------------------------------------------------
        # 1. Verify lead exists.
        # ------------------------------------------------------------------
        lead = conn.execute(
            "SELECT id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()

        if lead is None:
            return {"ok": False, "reason": "LEAD_NOT_FOUND"}

        # ------------------------------------------------------------------
        # 2. Short-circuit if SENT row already present.
        #    Must check BEFORE NEEDS_SYNC to avoid a UNIQUE violation on UPDATE.
        # ------------------------------------------------------------------
        sent_row = conn.execute(
            """
            SELECT id FROM sync_records
            WHERE lead_id = ? AND destination = ? AND status = ?
            """,
            (lead_id, destination, _STATUS_SENT),
        ).fetchone()

        if sent_row is not None:
            return {"ok": True, "changed": False, "status": _STATUS_SENT}

        # ------------------------------------------------------------------
        # 3. Confirm a NEEDS_SYNC row exists to transition.
        # ------------------------------------------------------------------
        needs_sync_row = conn.execute(
            """
            SELECT id FROM sync_records
            WHERE lead_id = ? AND destination = ? AND status = ?
            """,
            (lead_id, destination, _STATUS_NEEDS_SYNC),
        ).fetchone()

        if needs_sync_row is None:
            return {"ok": False, "reason": "NO_NEEDS_SYNC_ROW"}

        # ------------------------------------------------------------------
        # 4. Transition NEEDS_SYNC → SENT in-place.
        # ------------------------------------------------------------------
        conn.execute(
            """
            UPDATE sync_records
            SET   status        = ?,
                  updated_at    = ?,
                  response_json = ?
            WHERE lead_id = ? AND destination = ? AND status = ?
            """,
            (
                _STATUS_SENT,
                now_str,
                response_json,
                lead_id,
                destination,
                _STATUS_NEEDS_SYNC,
            ),
        )
        conn.commit()
        return {"ok": True, "changed": True, "status": _STATUS_SENT}

    finally:
        conn.close()
