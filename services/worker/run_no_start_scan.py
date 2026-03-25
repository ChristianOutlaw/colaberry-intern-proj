"""
services/worker/run_no_start_scan.py

Worker entry point for the no-start scan.

Calls find_no_start_leads and returns a summary dict.
No side effects — does not dispatch nudges, enqueue actions, or write to DB.
"""

from execution.scans.find_no_start_leads import find_no_start_leads
from execution.scans.scan_registry import NO_START_SCAN


def run_no_start_scan(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Run the no-start scan and return a summary.

    Returns:
        {
            "scan_name": "NO_START_SCAN",
            "count":     <number of qualifying leads>,
            "lead_ids":  [<lead_id>, ...],
        }
    """
    rows = find_no_start_leads(limit=limit, db_path=db_path)
    return {
        "scan_name": NO_START_SCAN,
        "count":     len(rows),
        "lead_ids":  [row["lead_id"] for row in rows],
    }
