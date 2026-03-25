"""
execution/events/requeue_failed_action.py

Transitions a single sync_records row from FAILED back to NEEDS_SYNC.

Outbox schema: execution/db/sqlite.py (sync_records table)
Only FAILED -> NEEDS_SYNC is permitted.
No dispatch, no retry execution, no other rows touched.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db

_STATUS_FAILED     = "FAILED"
_STATUS_NEEDS_SYNC = "NEEDS_SYNC"


def requeue_failed_action(record_id: int, db_path: str | None = None) -> dict:
    """
    Requeue a FAILED sync_record by moving it back to NEEDS_SYNC.

    Returns:
        If the row was FAILED and is now NEEDS_SYNC:
            {"record_id": <int>, "previous_status": "FAILED",
             "new_status": "NEEDS_SYNC", "updated": True}

        If the row does not exist:
            {"record_id": <int>, "updated": False, "reason": "NOT_FOUND"}

        If the row exists but is not FAILED:
            {"record_id": <int>, "updated": False, "current_status": <str>}
    """
    now_str = datetime.now(timezone.utc).isoformat()

    conn = connect(db_path)
    try:
        init_db(conn)

        row = conn.execute(
            "SELECT id, status FROM sync_records WHERE id = ?",
            (record_id,),
        ).fetchone()

        if row is None:
            return {"record_id": record_id, "updated": False, "reason": "NOT_FOUND"}

        current_status = row["status"]

        if current_status != _STATUS_FAILED:
            return {"record_id": record_id, "updated": False, "current_status": current_status}

        conn.execute(
            """
            UPDATE sync_records
            SET   status     = ?,
                  updated_at = ?
            WHERE id = ?
            """,
            (_STATUS_NEEDS_SYNC, now_str, record_id),
        )
        conn.commit()

        return {
            "record_id":       record_id,
            "previous_status": _STATUS_FAILED,
            "new_status":      _STATUS_NEEDS_SYNC,
            "updated":         True,
        }

    finally:
        conn.close()
