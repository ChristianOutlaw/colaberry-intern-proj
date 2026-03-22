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
    record_id: int | None = None,
) -> dict:
    """Transition the outbox row (lead_id, destination, NEEDS_SYNC) to SENT.

    Two paths depending on whether record_id is provided:

    record_id=None  (default — existing behaviour, unchanged):
        Checks are evaluated in order and stop at the first match:
        1. Lead does not exist in leads table:
               {"ok": False, "reason": "LEAD_NOT_FOUND"}
        2. A SENT row already exists for (lead_id, destination):
               {"ok": True, "changed": False, "status": "SENT"}
           Returning here prevents a UNIQUE(lead_id, destination, status)
           violation that would occur if we tried to update a NEEDS_SYNC row
           to SENT while a SENT row already exists.
        3. No NEEDS_SYNC row exists for (lead_id, destination):
               {"ok": False, "reason": "NO_NEEDS_SYNC_ROW"}
        4. Happy path — update the NEEDS_SYNC row in-place to SENT.
               {"ok": True, "changed": True, "status": "SENT"}

    record_id provided  (targeted path — bypasses the SENT-row guard):
        Used when the caller already holds the primary key of the exact
        NEEDS_SYNC row to promote (e.g. process_one_cory_sync_record).
        Checks in order:
        1. Lead does not exist:
               {"ok": False, "reason": "LEAD_NOT_FOUND"}
        2. Target row does not exist or does not match (lead_id, destination):
               {"ok": False, "reason": "RECORD_NOT_FOUND"}
        3. Delete any pre-existing SENT row for (lead_id, destination) so the
           subsequent UPDATE does not violate the UNIQUE constraint.
        4. Update the target row to SENT by primary key.
               {"ok": True, "changed": True, "status": "SENT"}

    Args:
        lead_id:       Stable unique identifier for the lead.
        now:           Current UTC datetime, injected by the caller.
                       Never calls datetime.now() internally.
        destination:   Outbox destination label.  Defaults to "GHL".
        response_json: Optional JSON string from the downstream system's
                       response.  Stored as-is; not parsed or validated here.
        db_path:       Path to the SQLite file; defaults to tmp/app.db.
        record_id:     Primary key of the specific sync_records row to promote.
                       When None (default) the legacy path is used.
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
        # Targeted path — record_id provided.
        # ------------------------------------------------------------------
        if record_id is not None:
            target = conn.execute(
                """
                SELECT id FROM sync_records
                WHERE id = ? AND lead_id = ? AND destination = ?
                """,
                (record_id, lead_id, destination),
            ).fetchone()

            if target is None:
                return {"ok": False, "reason": "RECORD_NOT_FOUND"}

            # Remove any pre-existing SENT row so the UPDATE below does not
            # violate UNIQUE(lead_id, destination, status).
            conn.execute(
                """
                DELETE FROM sync_records
                WHERE lead_id = ? AND destination = ? AND status = ?
                """,
                (lead_id, destination, _STATUS_SENT),
            )

            conn.execute(
                """
                UPDATE sync_records
                SET   status        = ?,
                      updated_at    = ?,
                      response_json = ?
                WHERE id = ?
                """,
                (_STATUS_SENT, now_str, response_json, record_id),
            )
            conn.commit()
            return {"ok": True, "changed": True, "status": _STATUS_SENT}

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
