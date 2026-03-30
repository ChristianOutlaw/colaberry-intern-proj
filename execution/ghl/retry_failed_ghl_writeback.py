"""
execution/ghl/retry_failed_ghl_writeback.py

Manual retry helper for a single failed GHL full-field writeback.

Implements the three-step flow documented in directives/GHL_INTEGRATION.md
(Writeback Outcome Tracking → Retry Strategy):

    1. Validate that the sync_records row is FAILED and destination=GHL_WRITEBACK.
    2. Call requeue_failed_action(record_id) to transition it back to NEEDS_SYNC.
    3. Call write_ghl_contact_fields(app_lead_id, ...) to attempt the HTTP send.

This helper retries exactly ONE record per call.  No automatic scheduling,
no looping, no CLI/UI wiring.  The caller is responsible for selecting which
record_id to retry.

Return shape
------------
    {
        "ok":          bool,
        "record_id":   int,
        "app_lead_id": str | None,
        "message":     str,
        "writeback":   dict | None,   # full result from write_ghl_contact_fields
    }
"""

from execution.db.sqlite import connect, init_db
from execution.events.requeue_failed_action import requeue_failed_action
from execution.ghl.write_ghl_contact_fields import write_ghl_contact_fields

_DESTINATION_WRITEBACK = "GHL_WRITEBACK"
_STATUS_FAILED         = "FAILED"


def retry_failed_ghl_writeback(
    record_id: int,
    *,
    now: str,
    ghl_api_url: str | None = None,
    ghl_lookup_url: str | None = None,
    base_url: str = "http://localhost:8501",
    db_path: str | None = None,
    timeout: int = 10,
) -> dict:
    """Retry exactly one FAILED GHL writeback sync_record.

    Args:
        record_id:      Primary key of the sync_records row to retry.
                        Must have destination=GHL_WRITEBACK and status=FAILED.
        now:            ISO-8601 UTC string forwarded to write_ghl_contact_fields.
                        Must be provided by the caller — never calls datetime.now().
        ghl_api_url:    URL of the GHL contact-update endpoint.
        ghl_lookup_url: Optional URL for ghl_contact_id resolution fallback.
        base_url:       Student portal base URL for course_link construction.
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
        timeout:        Socket timeout in seconds for the outbound HTTP request.

    Returns:
        dict — see module docstring for the return shape.
    """
    if now is None:
        raise ValueError(
            "retry_failed_ghl_writeback: 'now' must be provided by the caller."
        )

    # ------------------------------------------------------------------
    # 1. Validate the sync_records row.
    #    Must exist, must be destination=GHL_WRITEBACK, must be FAILED.
    # ------------------------------------------------------------------
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT lead_id, destination, status FROM sync_records WHERE id = ?",
            (record_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {
            "ok":          False,
            "record_id":   record_id,
            "app_lead_id": None,
            "message":     f"sync_records row {record_id} not found.",
            "writeback":   None,
        }

    if row["destination"] != _DESTINATION_WRITEBACK:
        return {
            "ok":          False,
            "record_id":   record_id,
            "app_lead_id": row["lead_id"],
            "message":     (
                f"Row {record_id} has destination={row['destination']!r}; "
                f"expected {_DESTINATION_WRITEBACK!r}."
            ),
            "writeback":   None,
        }

    if row["status"] != _STATUS_FAILED:
        return {
            "ok":          False,
            "record_id":   record_id,
            "app_lead_id": row["lead_id"],
            "message":     (
                f"Row {record_id} has status={row['status']!r}; "
                f"expected {_STATUS_FAILED!r}."
            ),
            "writeback":   None,
        }

    app_lead_id = row["lead_id"]

    # ------------------------------------------------------------------
    # 2. Requeue: FAILED → NEEDS_SYNC.
    # ------------------------------------------------------------------
    requeue_result = requeue_failed_action(record_id, db_path=db_path)

    if not requeue_result.get("updated"):
        return {
            "ok":          False,
            "record_id":   record_id,
            "app_lead_id": app_lead_id,
            "message":     f"requeue_failed_action did not update row: {requeue_result}",
            "writeback":   None,
        }

    # ------------------------------------------------------------------
    # 3. Retry: attempt the full GHL writeback.
    #    write_ghl_contact_fields will DELETE the NEEDS_SYNC row, insert a
    #    fresh one, attempt the HTTP POST, and persist the new outcome.
    # ------------------------------------------------------------------
    writeback_result = write_ghl_contact_fields(
        app_lead_id,
        now=now,
        ghl_api_url=ghl_api_url,
        ghl_lookup_url=ghl_lookup_url,
        base_url=base_url,
        db_path=db_path,
        timeout=timeout,
    )

    return {
        "ok":          writeback_result["ok"],
        "record_id":   record_id,
        "app_lead_id": app_lead_id,
        "message":     writeback_result["message"],
        "writeback":   writeback_result,
    }
