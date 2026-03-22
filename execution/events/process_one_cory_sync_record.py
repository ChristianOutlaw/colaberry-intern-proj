"""
execution/events/process_one_cory_sync_record.py

Worker slice: pick the oldest pending CORY_* sync_records row and mark it SENT.

Responsibility: one deterministic state transition per call.
No network calls.  No external API calls.  No scheduler.  No loop.
Records a dry-run response_json to document that no real dispatch was attempted.

Run as a one-shot function from any caller (CLI, scheduler, test).
"""

import json
import logging
from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db
from execution.leads.mark_sync_record_sent import mark_sync_record_sent

logger = logging.getLogger(__name__)

_STATUS_NEEDS_SYNC = "NEEDS_SYNC"
_CORY_PREFIX = "CORY_"


def process_one_cory_sync_record(
    *,
    db_path: str | None = None,
    now: str | None = None,
) -> dict:
    """Pick the oldest pending CORY_* sync_records row and mark it SENT.

    No external service is called.  A deterministic dry-run response_json is
    stored on the row to document the processing outcome.

    Args:
        db_path: Path to the SQLite file; defaults to tmp/app.db.
        now:     ISO-8601 UTC string for the processing timestamp.
                 Injected by the caller for determinism.  When None, defaults
                 to datetime.now(timezone.utc) — this is the injection boundary;
                 tests always pass an explicit value.

    Returns:
        No pending Cory row found:
            {"ok": True, "processed": False, "reason": "NO_PENDING"}

        Row found and marked SENT:
            {"ok": True, "processed": True,
             "sync_record_id": <id>, "destination": "<CORY_*>"}
    """
    # ------------------------------------------------------------------
    # Resolve timestamp — injection boundary (tests always pass now).
    # ------------------------------------------------------------------
    now_dt: datetime = (
        datetime.fromisoformat(now)
        if now is not None
        else datetime.now(timezone.utc)
    )

    # ------------------------------------------------------------------
    # 1. Find the oldest NEEDS_SYNC Cory row.
    # ------------------------------------------------------------------
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, lead_id, destination, reason
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

    # ------------------------------------------------------------------
    # 2. Build a deterministic dry-run response payload.
    # ------------------------------------------------------------------
    response_json_str = json.dumps({
        "dispatched": False,
        "mode":        "dry_run",
        "destination": destination,
        "reason":      reason,
    })

    # ------------------------------------------------------------------
    # 3. Mark SENT via the repo's existing sent-marking helper.
    #    Connection was closed above; mark_sync_record_sent opens its own.
    # ------------------------------------------------------------------
    mark_sync_record_sent(
        lead_id=lead_id,
        now=now_dt,
        destination=destination,
        response_json=response_json_str,
        db_path=db_path,
    )

    logger.debug(
        "process_one_cory_sync_record: id=%s destination=%s marked SENT",
        record_id,
        destination,
    )

    return {
        "ok":            True,
        "processed":     True,
        "sync_record_id": record_id,
        "destination":   destination,
    }
