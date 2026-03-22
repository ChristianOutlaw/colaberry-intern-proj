"""
execution/events/dispatch_cory_log_sink.py

Pure log-sink dispatcher for Cory sync records.

Responsibility: accept one sync_records row dict, write exactly one
timestamped JSON file to the configured log directory, and return a
result dict.  No DB reads.  No network calls.  No datetime.now().
One file per call, never append.

Output directory (default):  tmp/cory_dispatch_log/
File name pattern:            cory_dispatch_<id>_<now_safe>.json

This is the first "real" Cory dispatcher — it produces an auditable
artifact outside the database.  Future dispatchers (GHL, email, call)
will follow the same interface: accept row_data + now, return a result
dict, raise on failure.
"""

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOG_DIR = _REPO_ROOT / "tmp" / "cory_dispatch_log"

_REQUIRED_FIELDS = frozenset({"lead_id", "destination", "reason", "created_at"})


def dispatch_cory_log_sink(
    row_data: dict,
    *,
    log_dir: str | None = None,
    now: str,
) -> dict:
    """Write one dispatch JSON file for a Cory sync_records row.

    No DB reads.  No network calls.  now must be injected by the caller.

    Args:
        row_data: Dict representing one sync_records row.  Required keys:
                  lead_id, destination, reason, created_at, and either
                  'id' or 'sync_record_id'.
        log_dir:  Directory to write the JSON file into.
                  Defaults to tmp/cory_dispatch_log/ under the repo root.
                  Created automatically if it does not exist.
        now:      ISO-8601 UTC string for the dispatch timestamp.
                  Injected by the caller; never derived internally.

    Returns:
        {"dispatched": True, "mode": "log_sink", "path": "<absolute path>"}

    Raises:
        ValueError: Missing or invalid row_data fields.
        OSError:    File or directory write failure.
    """
    # ------------------------------------------------------------------
    # 1. Validate row_data fields.
    # ------------------------------------------------------------------
    missing = _REQUIRED_FIELDS - set(row_data.keys())
    if missing:
        raise ValueError(
            f"dispatch_cory_log_sink: missing required row_data fields: {sorted(missing)}"
        )

    # Accept 'id' (raw DB column) or 'sync_record_id' (aliased by caller).
    sync_record_id = row_data.get("sync_record_id") or row_data.get("id")
    if sync_record_id is None:
        raise ValueError(
            "dispatch_cory_log_sink: row_data must contain 'id' or 'sync_record_id'"
        )

    lead_id     = str(row_data["lead_id"]).strip()
    destination = str(row_data["destination"]).strip()
    reason      = str(row_data["reason"]).strip()
    queued_at   = str(row_data["created_at"]).strip()

    if not lead_id:
        raise ValueError("dispatch_cory_log_sink: lead_id must be a non-empty string")
    if not destination:
        raise ValueError("dispatch_cory_log_sink: destination must be a non-empty string")

    # ------------------------------------------------------------------
    # 2. Resolve and create log directory.
    # ------------------------------------------------------------------
    log_path = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
    log_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 3. Build the dispatch payload.
    # ------------------------------------------------------------------
    payload = {
        "sync_record_id": sync_record_id,
        "lead_id":        lead_id,
        "destination":    destination,
        "reason":         reason,
        "queued_at":      queued_at,
        "dispatched_at":  now,
        "mode":           "log_sink",
        "dispatched":     True,
    }

    # ------------------------------------------------------------------
    # 4. Write exactly one timestamped file; never append.
    #    Sanitise 'now' for use in the filename (remove :, +, ., spaces).
    # ------------------------------------------------------------------
    now_safe = re.sub(r"[:\+\.\s]", "_", now)
    filename = f"cory_dispatch_{sync_record_id}_{now_safe}.json"
    file_path = log_path / filename

    file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "dispatched": True,
        "mode":       "log_sink",
        "path":       str(file_path),
    }
