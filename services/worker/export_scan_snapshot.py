"""
services/worker/export_scan_snapshot.py

Read-only snapshot wrapper around run_all_scans, shaped for external consumption.
No side effects — does not dispatch nudges, enqueue actions, or write to DB.
"""

from services.worker.run_all_scans import run_all_scans


def export_scan_snapshot(
    limit: int = 100,
    db_path: str | None = None,
    scan_name: str | None = None,
    intended_action: str | None = None,
) -> dict:
    """
    Produce a read-only snapshot of all scan results, shaped for external consumption.

    Optional filters:
    - scan_name: keep only entries where entry["scan_name"] == scan_name
    - intended_action: keep only entries where entry["intended_action"] == intended_action
    Both filters are applied when provided.

    Returns:
    {
        "type": "SCAN_SNAPSHOT",
        "generated_at": <str>,
        "scan_count": <int>,
        "action_summary": {...},
        "scans": [...],   # filtered subset of run_all_scans()["results"]
    }
    """
    result = run_all_scans(limit=limit, db_path=db_path)

    scans = result["results"]
    if scan_name is not None:
        scans = [s for s in scans if s["scan_name"] == scan_name]
    if intended_action is not None:
        scans = [s for s in scans if s["intended_action"] == intended_action]

    return {
        "type":           "SCAN_SNAPSHOT",
        "generated_at":   result["generated_at"],
        "scan_count":     len(scans),
        "action_summary": result["action_summary"],
        "scans":          scans,
    }
