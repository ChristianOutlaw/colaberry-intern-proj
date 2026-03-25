"""
execution/scans/find_failed_dispatch_records.py

Read-only scan: returns sync_records rows currently marked FAILED.

Selection rule: sync_records WHERE status = 'FAILED', ordered by created_at ASC.
No writes — does not change status, requeue, or retry any record.
"""

from execution.db.sqlite import connect, init_db

_STATUS_FAILED = "FAILED"

_SQL = """
    SELECT id, lead_id, destination, status, reason, error, created_at, updated_at
    FROM sync_records
    WHERE status = ?
    ORDER BY created_at ASC
    LIMIT ?
"""


def find_failed_dispatch_records(limit: int = 100, db_path: str | None = None) -> list[dict]:
    """
    Read-only scan for failed dispatch/sync records eligible for retry later.

    For now:
    - use the existing sync/outbox schema only
    - select records currently marked failed
    - no writes
    - no requeue
    - no retry execution
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(_SQL, (_STATUS_FAILED, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
