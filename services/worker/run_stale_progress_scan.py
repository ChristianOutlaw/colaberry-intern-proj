"""
services/worker/run_stale_progress_scan.py

Worker entry point for the stale-progress scan.

Calls find_stale_progress_leads and returns a summary dict.
No side effects — does not dispatch nudges, enqueue actions, or write to DB.
"""

from execution.scans.find_stale_progress_leads import find_stale_progress_leads
from execution.scans.scan_registry import STALE_PROGRESS_SCAN


def run_stale_progress_scan(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Run the stale-progress scan and return a summary.

    Returns:
        {
            "scan_name": "STALE_PROGRESS_SCAN",
            "count":     <number of qualifying leads>,
            "lead_ids":  [<lead_id>, ...],
        }
    """
    rows = find_stale_progress_leads(limit=limit, db_path=db_path)
    return {
        "scan_name": STALE_PROGRESS_SCAN,
        "count":     len(rows),
        "lead_ids":   [row["lead_id"] for row in rows],
        "limit_used": limit,
    }
