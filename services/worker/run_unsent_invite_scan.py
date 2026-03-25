"""
services/worker/run_unsent_invite_scan.py

Worker entry point for the unsent-invite scan.

Calls find_unsent_invite_leads and returns a summary dict.
No side effects — does not dispatch invites, enqueue actions, or write to DB.
"""

from execution.scans.find_unsent_invite_leads import find_unsent_invite_leads
from execution.scans.scan_registry import UNSENT_INVITE_SCAN


def run_unsent_invite_scan(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Run the unsent-invite scan and return a summary.

    Returns:
        {
            "scan_name": "UNSENT_INVITE_SCAN",
            "count":     <number of qualifying leads>,
            "lead_ids":  [<lead_id>, ...],
        }
    """
    rows = find_unsent_invite_leads(limit=limit, db_path=db_path)
    return {
        "scan_name": UNSENT_INVITE_SCAN,
        "count":     len(rows),
        "lead_ids":   [row["lead_id"] for row in rows],
        "limit_used": limit,
    }
