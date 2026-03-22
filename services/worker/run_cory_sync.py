"""
services/worker/run_cory_sync.py

One-shot runner for process_one_cory_sync_record().

Finds the oldest pending CORY_* sync_records row, dispatches it, prints the
result as JSON, and exits.  Processes at most one row per invocation.
No loop.  No scheduler.  No network calls.

Run:
    python services/worker/run_cory_sync.py

Environment variables:
    DB_PATH             Path to the SQLite database file.
                        Default: tmp/app.db (via execution/db/sqlite.py)
    NOW                 ISO-8601 UTC timestamp to inject as the processing time.
                        Default: datetime.now(timezone.utc) (resolved inside the worker)
    CORY_DISPATCH_MODE  Dispatch mode: "dry_run" (default) or "log_sink".
    CORY_LOG_DIR        Directory for log_sink output files.
                        Default: tmp/cory_dispatch_log/ (resolved inside the dispatcher)

Output (stdout, always valid JSON):
    {"ok": true,  "processed": false, "reason": "NO_PENDING"}
    {"ok": true,  "processed": true,  "sync_record_id": <id>, "destination": "CORY_*"}
    {"ok": false, "sync_record_id": <id>, "destination": "CORY_*", "error": "..."}
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.events.process_one_cory_sync_record import (  # noqa: E402
    process_one_cory_sync_record,
)


def run(
    db_path:       str | None = None,
    now:           str | None = None,
    dispatch_mode: str        = "dry_run",
    log_dir:       str | None = None,
) -> dict:
    """Call the worker once, print the result as JSON, and return it.

    Args:
        db_path:       Path to the SQLite file; defaults to tmp/app.db.
        now:           ISO-8601 UTC string for the processing timestamp.
                       Passed through to process_one_cory_sync_record unchanged.
        dispatch_mode: "dry_run" (default) or "log_sink".
                       Passed through to process_one_cory_sync_record unchanged.
        log_dir:       Directory for log_sink output files.
                       Ignored in dry_run mode.

    Returns:
        The dict returned by process_one_cory_sync_record().
    """
    result = process_one_cory_sync_record(
        db_path=db_path,
        now=now,
        dispatch_mode=dispatch_mode,
        log_dir=log_dir,
    )
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    run(
        db_path=os.environ.get("DB_PATH") or None,
        now=os.environ.get("NOW") or None,
        dispatch_mode=os.environ.get("CORY_DISPATCH_MODE") or "dry_run",
        log_dir=os.environ.get("CORY_LOG_DIR") or None,
    )
