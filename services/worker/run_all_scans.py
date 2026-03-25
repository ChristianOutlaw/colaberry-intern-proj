"""
services/worker/run_all_scans.py

Aggregator: runs all read-only scan workers and returns one combined summary.
No side effects — does not dispatch nudges, enqueue actions, or write to DB.
"""

from services.worker.run_unsent_invite_scan import run_unsent_invite_scan
from services.worker.run_no_start_scan import run_no_start_scan
from services.worker.run_failed_dispatch_scan import run_failed_dispatch_scan
from services.worker.run_stale_progress_scan import run_stale_progress_scan


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
    return {
        "scan_count": 4,
        "limit_used": limit,
        "results": [
            run_unsent_invite_scan(limit=limit, db_path=db_path),
            run_no_start_scan(limit=limit, db_path=db_path),
            run_failed_dispatch_scan(limit=limit, db_path=db_path),
            run_stale_progress_scan(limit=limit, db_path=db_path),
        ],
    }
