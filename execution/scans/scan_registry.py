"""
execution/scans/scan_registry.py

Canonical registry of scan names used across worker entry points.
No logic, no DB access, no side effects.
"""

UNSENT_INVITE_SCAN        = "UNSENT_INVITE_SCAN"
NO_START_SCAN             = "NO_START_SCAN"
FAILED_DISPATCH_RETRY_SCAN = "FAILED_DISPATCH_RETRY_SCAN"

SCAN_NAMES = {
    UNSENT_INVITE_SCAN,
    NO_START_SCAN,
    FAILED_DISPATCH_RETRY_SCAN,
}


def is_known_scan_name(name: str) -> bool:
    """Return True if name is one of the registered scan names."""
    return name in SCAN_NAMES
