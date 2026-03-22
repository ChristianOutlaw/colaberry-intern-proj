"""
execution/events/process_one_cory_sync_record.py

Worker slice: pick the oldest pending CORY_* sync_records row and dispatch it.

Responsibility: one deterministic state transition per call.
No network calls.  No external API calls.  No scheduler.  No loop.

Dispatch modes
--------------
dry_run  (default)  — records a dry-run payload and marks the row SENT.
                      No file is written.  Current behaviour, preserved.
log_sink            — calls dispatch_cory_log_sink(), writes one JSON file,
                      marks the row SENT on success or FAILED on error.

Run as a one-shot function from any caller (CLI, scheduler, test).
"""

import json
import logging
from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db
from execution.events.dispatch_cory_log_sink import dispatch_cory_log_sink
from execution.leads.mark_sync_record_failed import mark_sync_record_failed
from execution.leads.mark_sync_record_sent import mark_sync_record_sent

logger = logging.getLogger(__name__)

_STATUS_NEEDS_SYNC = "NEEDS_SYNC"
_CORY_PREFIX       = "CORY_"
_VALID_MODES       = frozenset({"dry_run", "log_sink"})


def process_one_cory_sync_record(
    *,
    db_path:       str | None = None,
    now:           str | None = None,
    dispatch_mode: str        = "dry_run",
    log_dir:       str | None = None,
) -> dict:
    """Pick the oldest pending CORY_* sync_records row and dispatch it.

    Args:
        db_path:       Path to the SQLite file; defaults to tmp/app.db.
        now:           ISO-8601 UTC string for the processing timestamp.
                       Injected by the caller for determinism.  When None,
                       defaults to datetime.now(timezone.utc) — injection
                       boundary; tests always pass an explicit value.
        dispatch_mode: "dry_run" (default) or "log_sink".
                       Raises ValueError for any other value.
        log_dir:       Directory for log_sink output files.  Passed through
                       to dispatch_cory_log_sink; ignored in dry_run mode.

    Returns:
        No pending Cory row found:
            {"ok": True, "processed": False, "reason": "NO_PENDING"}

        Row dispatched successfully (either mode):
            {"ok": True, "processed": True,
             "sync_record_id": <id>, "destination": "<CORY_*>"}

        Dispatch raised an exception (log_sink only):
            {"ok": False, "sync_record_id": <id>,
             "destination": "<CORY_*>", "error": "<message>"}

    Raises:
        ValueError: dispatch_mode is not "dry_run" or "log_sink".
    """
    # ------------------------------------------------------------------
    # Guard: reject unknown dispatch modes immediately.
    # ------------------------------------------------------------------
    if dispatch_mode not in _VALID_MODES:
        raise ValueError(
            f"Unknown dispatch_mode {dispatch_mode!r}. "
            f"Expected one of: {sorted(_VALID_MODES)}"
        )

    # ------------------------------------------------------------------
    # Resolve timestamp — injection boundary (tests always pass now).
    # ------------------------------------------------------------------
    now_dt: datetime = (
        datetime.fromisoformat(now)
        if now is not None
        else datetime.now(timezone.utc)
    )
    now_iso: str = now_dt.isoformat()

    # ------------------------------------------------------------------
    # 1. Find the oldest NEEDS_SYNC Cory row.
    # ------------------------------------------------------------------
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, lead_id, destination, reason, created_at
            FROM   sync_records
            WHERE  status = ? AND destination LIKE ?
            ORDER  BY created_at ASC
            LIMIT  1
            """,
            (_STATUS_NEEDS_SYNC, f"{_CORY_PREFIX}%"),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"ok": True, "processed": False, "reason": "NO_PENDING"}

    record_id   = row["id"]
    lead_id     = row["lead_id"]
    destination = row["destination"]
    reason      = row["reason"]
    created_at  = row["created_at"]

    # ------------------------------------------------------------------
    # 2. Dispatch — behaviour varies by mode.
    # ------------------------------------------------------------------
    if dispatch_mode == "dry_run":
        response_json_str = json.dumps({
            "dispatched": False,
            "mode":        "dry_run",
            "destination": destination,
            "reason":      reason,
        })
        mark_result = mark_sync_record_sent(
            lead_id=lead_id,
            now=now_dt,
            destination=destination,
            response_json=response_json_str,
            db_path=db_path,
            record_id=record_id,
        )
        if not mark_result.get("ok") or not mark_result.get("changed"):
            logger.error(
                "process_one_cory_sync_record: mark_sent failed id=%s result=%s",
                record_id,
                mark_result,
            )
            return {
                "ok":             False,
                "sync_record_id": record_id,
                "destination":    destination,
                "error":          f"mark_sync_record_sent failed: {mark_result}",
            }

    else:  # log_sink
        row_data = {
            "id":          record_id,
            "lead_id":     lead_id,
            "destination": destination,
            "reason":      reason,
            "created_at":  created_at,
        }
        try:
            dispatch_result = dispatch_cory_log_sink(
                row_data, log_dir=log_dir, now=now_iso
            )
            response_json_str = json.dumps(dispatch_result)
            mark_result = mark_sync_record_sent(
                lead_id=lead_id,
                now=now_dt,
                destination=destination,
                response_json=response_json_str,
                db_path=db_path,
                record_id=record_id,
            )
            if not mark_result.get("ok") or not mark_result.get("changed"):
                logger.error(
                    "process_one_cory_sync_record: mark_sent failed id=%s result=%s",
                    record_id,
                    mark_result,
                )
                return {
                    "ok":             False,
                    "sync_record_id": record_id,
                    "destination":    destination,
                    "error":          f"mark_sync_record_sent failed: {mark_result}",
                }
        except Exception as exc:
            logger.exception(
                "process_one_cory_sync_record: dispatch failed id=%s destination=%s",
                record_id,
                destination,
            )
            mark_sync_record_failed(
                lead_id=lead_id,
                now=now_dt,
                destination=destination,
                error=str(exc),
                db_path=db_path,
            )
            return {
                "ok":             False,
                "sync_record_id": record_id,
                "destination":    destination,
                "error":          str(exc),
            }

    logger.debug(
        "process_one_cory_sync_record: id=%s destination=%s mode=%s marked SENT",
        record_id,
        destination,
        dispatch_mode,
    )

    return {
        "ok":             True,
        "processed":      True,
        "sync_record_id": record_id,
        "destination":    destination,
    }
