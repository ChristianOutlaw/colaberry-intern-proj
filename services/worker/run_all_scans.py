"""
services/worker/run_all_scans.py

Aggregator: runs all read-only scan workers and returns one combined summary.
No side effects — does not dispatch nudges, enqueue actions, or write to DB.
"""

from datetime import datetime, timezone

from execution.scans.map_scan_to_intended_action import map_scan_to_intended_action
from services.worker.run_unsent_invite_scan import run_unsent_invite_scan
from services.worker.run_no_start_scan import run_no_start_scan
from services.worker.run_failed_dispatch_scan import run_failed_dispatch_scan
from services.worker.run_stale_progress_scan import run_stale_progress_scan
from services.worker.run_completion_finalization_scan import run_completion_finalization_scan


def _with_intended_action(result: dict) -> dict:
    result["intended_action"] = map_scan_to_intended_action(result["scan_name"])
    return result


def run_all_scans(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Run all current read-only scan workers and return one combined summary.

    Returns:
    {
        "scan_count": 4,
        "limit_used": <int>,
        "results": [
            <run_unsent_invite_scan result>,
            <run_no_start_scan result>,
            <run_failed_dispatch_scan result>,
            <run_stale_progress_scan result>,
        ],
    }
    """
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    results = [
        _with_intended_action(run_unsent_invite_scan(limit=limit, db_path=db_path)),
        _with_intended_action(run_no_start_scan(limit=limit, db_path=db_path)),
        _with_intended_action(run_failed_dispatch_scan(limit=limit, db_path=db_path)),
        _with_intended_action(run_stale_progress_scan(limit=limit, db_path=db_path)),
        _with_intended_action(run_completion_finalization_scan(limit=limit, db_path=db_path)),
    ]
    action_summary = {"SEND_INVITE": 0, "NUDGE_PROGRESS": 0, "REQUEUE_FAILED_ACTION": 0, "UNKNOWN": 0}
    for r in results:
        key = r["intended_action"] or "UNKNOWN"
        action_summary[key] = action_summary.get(key, 0) + 1
    return {
        "scan_count":     5,
        "limit_used":     limit,
        "generated_at":   generated_at,
        "action_summary": action_summary,
        "results":        results,
    }
