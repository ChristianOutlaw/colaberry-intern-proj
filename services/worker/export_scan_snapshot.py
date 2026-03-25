"""
services/worker/export_scan_snapshot.py

Read-only snapshot wrapper around run_all_scans, shaped for external consumption.
No side effects — does not dispatch nudges, enqueue actions, or write to DB.
"""

from services.worker.run_all_scans import run_all_scans


def export_scan_snapshot(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Produce a read-only snapshot of all scan results, shaped for external consumption.

    Returns:
    {
        "type": "SCAN_SNAPSHOT",
        "generated_at": <str>,
        "scan_count": <int>,
        "action_summary": {...},
        "scans": [...],   # same as run_all_scans()["results"]
    }
    """
    result = run_all_scans(limit=limit, db_path=db_path)

    return {
        "type":           "SCAN_SNAPSHOT",
        "generated_at":   result["generated_at"],
        "scan_count":     result["scan_count"],
        "action_summary": result["action_summary"],
        "scans":          result["results"],
    }
