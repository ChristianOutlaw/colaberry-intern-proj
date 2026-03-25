"""
execution/scans/map_scan_to_intended_action.py

Maps a canonical scan name to its intended action family.
Pure function — no DB access, no dispatch.
"""

from execution.scans.scan_registry import (
    UNSENT_INVITE_SCAN,
    NO_START_SCAN,
    FAILED_DISPATCH_RETRY_SCAN,
    STALE_PROGRESS_SCAN,
    COMPLETION_FINALIZATION_SCAN,
)

_SCAN_ACTION_MAP: dict[str, str] = {
    UNSENT_INVITE_SCAN:           "SEND_INVITE",
    NO_START_SCAN:                "NUDGE_PROGRESS",
    FAILED_DISPATCH_RETRY_SCAN:   "REQUEUE_FAILED_ACTION",
    STALE_PROGRESS_SCAN:          "NUDGE_PROGRESS",
    COMPLETION_FINALIZATION_SCAN: "FINALIZE_LEAD_SCORE",
}


def map_scan_to_intended_action(scan_name: str) -> str | None:
    """
    Map a canonical scan name to its intended action family.

    Returns:
    - "SEND_INVITE" for UNSENT_INVITE_SCAN
    - "NUDGE_PROGRESS" for NO_START_SCAN
    - "REQUEUE_FAILED_ACTION" for FAILED_DISPATCH_RETRY_SCAN
    - "NUDGE_PROGRESS" for STALE_PROGRESS_SCAN
    - None for unknown scan names
    """
    return _SCAN_ACTION_MAP.get(scan_name)
