"""
services/worker/run_failed_dispatch_scan.py

Worker entry point for the failed-dispatch retry scan.

Calls find_failed_dispatch_records and returns a summary dict.
No side effects — does not retry, requeue, or write to DB.
"""

from execution.scans.find_failed_dispatch_records import find_failed_dispatch_records
from execution.scans.scan_registry import FAILED_DISPATCH_RETRY_SCAN


def run_failed_dispatch_scan(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Run the failed-dispatch scan and return a summary.

    Returns:
        {
            "scan_name":  "FAILED_DISPATCH_RETRY_SCAN",
            "count":      <number of FAILED sync_records>,
            "record_ids": [<id>, ...],
        }
    """
    rows = find_failed_dispatch_records(limit=limit, db_path=db_path)
    return {
        "scan_name":  FAILED_DISPATCH_RETRY_SCAN,
        "count":      len(rows),
        "record_ids": [row["id"] for row in rows],
        "limit_used": limit,
    }
