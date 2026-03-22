"""
services/worker/run_cory_sync.py

One-shot runner for process_one_cory_sync_record().

Finds the oldest pending CORY_* sync_records row, marks it SENT, prints the
result as JSON, and exits.  Processes at most one row per invocation.
No loop.  No scheduler.  No network calls.

Run:
    python services/worker/run_cory_sync.py

Environment variables:
    DB_PATH   Path to the SQLite database file.
              Default: tmp/app.db (via execution/db/sqlite.py)
    NOW       ISO-8601 UTC timestamp to inject as the processing time.
              Default: datetime.now(timezone.utc) (resolved inside the worker)

Output (stdout, always valid JSON):
    {"ok": true,  "processed": false, "reason": "NO_PENDING"}
    {"ok": true,  "processed": true,  "sync_record_id": <id>, "destination": "CORY_*"}
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


def run(db_path: str | None = None, now: str | None = None) -> dict:
    """Call the worker once, print the result as JSON, and return it.

    Args:
        db_path: Path to the SQLite file; defaults to tmp/app.db.
        now:     ISO-8601 UTC string for the processing timestamp.
                 Passed through to process_one_cory_sync_record unchanged.

    Returns:
        The dict returned by process_one_cory_sync_record().
    """
    result = process_one_cory_sync_record(db_path=db_path, now=now)
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    run(
        db_path=os.environ.get("DB_PATH") or None,
        now=os.environ.get("NOW") or None,
    )
