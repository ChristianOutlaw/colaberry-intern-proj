"""
services/worker/run_completion_finalization_scan.py

Worker entry point for the completion finalization scan.

Calls find_completion_finalization_leads and returns a summary dict.
No side effects — does not finalize leads, dispatch actions, or write to DB.
"""

from execution.scans.find_completion_finalization_leads import find_completion_finalization_leads


def run_completion_finalization_scan(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Run the completion finalization scan and return a summary.

    Returns:
        {
            "scan_name": "COMPLETION_FINALIZATION_SCAN",
            "count":     <number of qualifying leads>,
            "lead_ids":  [<lead_id>, ...],
            "limit_used": <int>,
        }
    """
    rows = find_completion_finalization_leads(limit=limit, db_path=db_path)
    return {
        "scan_name":  "COMPLETION_FINALIZATION_SCAN",
        "count":      len(rows),
        "lead_ids":   [row["lead_id"] for row in rows],
        "limit_used": limit,
    }
