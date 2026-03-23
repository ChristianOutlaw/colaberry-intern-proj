"""
services/worker/run_sync_ghl_contact_id.py

One-shot runner for sync_ghl_contact_id().

Resolves and stores the GHL contact ID for exactly one lead, prints the
result as JSON, and exits.  Processes at most one lead per invocation.
No loop.  No scheduler.

Run:
    python services/worker/run_sync_ghl_contact_id.py

Environment variables:
    LEAD_ID          (required) Local lead identifier to sync.
    DB_PATH          Path to the SQLite database file.
                     Default: tmp/app.db (via execution/db/sqlite.py)
    GHL_LOOKUP_URL   URL of the GHL contact-lookup endpoint.
                     When absent or blank, the function returns a safe no-op.

Output (stdout, always valid JSON):
    {"ok": false, "reason": "LEAD_NOT_FOUND"}
    {"ok": true,  "updated": false, "reason": "NO_LOOKUP_FIELDS"}
    {"ok": true,  "updated": false, "reason": "NO_LOOKUP_URL"}
    {"ok": true,  "updated": false, "reason": "NO_MATCH"}
    {"ok": true,  "updated": true,  "ghl_contact_id": "<id>"}
    {"ok": false, "error": "<message>"}          (on unexpected exception)
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.sync_ghl_contact_id import sync_ghl_contact_id  # noqa: E402


def run(
    lead_id: str,
    *,
    db_path: str | None = None,
    ghl_lookup_url: str | None = None,
) -> dict:
    """Call sync_ghl_contact_id once, print the result as JSON, and return it.

    Args:
        lead_id:        Local lead identifier (TEXT PRIMARY KEY in leads).
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
        ghl_lookup_url: URL of the GHL contact-lookup endpoint.
                        When None or blank, returns a safe no-op without a
                        network call.

    Returns:
        The dict returned by sync_ghl_contact_id().
    """
    try:
        result = sync_ghl_contact_id(
            lead_id,
            db_path=db_path,
            ghl_lookup_url=ghl_lookup_url,
        )
    except Exception as exc:  # network errors, malformed JSON, etc.
        result = {"ok": False, "error": str(exc)}

    print(json.dumps(result))
    return result


if __name__ == "__main__":
    _lead_id = os.environ.get("LEAD_ID", "").strip()
    if not _lead_id:
        print(json.dumps({"ok": False, "error": "LEAD_ID env var is required"}))
        sys.exit(1)

    run(
        _lead_id,
        db_path=os.environ.get("DB_PATH") or None,
        ghl_lookup_url=os.environ.get("GHL_LOOKUP_URL") or None,
    )
