"""
execution/scans/classify_no_start_threshold.py

Pure helper: classifies a no-start lead into a threshold bucket based on
elapsed time since invite was sent.

No DB access, no dispatch, no side effects.
"""

from datetime import datetime, timezone


def classify_no_start_threshold(invite_sent_at: str | None, now: datetime) -> str | None:
    """
    Classify a no-start lead into the current threshold bucket.

    Returns one of:
    - "NO_START_24H"  — invite sent >= 24 hours ago
    - "NO_START_72H"  — invite sent >= 72 hours ago
    - "NO_START_7D"   — invite sent >= 7 days ago
    - None            — invite_sent_at is None or elapsed < 24 hours

    Rules (evaluated in descending severity order; first match wins):
    - if invite_sent_at is None -> None
    - if elapsed >= 7 days  -> "NO_START_7D"
    - elif elapsed >= 72 hours -> "NO_START_72H"
    - elif elapsed >= 24 hours -> "NO_START_24H"
    - else -> None

    Args:
        invite_sent_at: ISO-8601 timestamp string, or None.
        now:            Reference UTC datetime (injected by caller).
    """
    if invite_sent_at is None:
        return None

    ts = datetime.fromisoformat(invite_sent_at.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    elapsed = now_utc - ts.astimezone(timezone.utc)
    elapsed_hours = elapsed.total_seconds() / 3600

    if elapsed_hours >= 168:   # 7 days = 7 * 24
        return "NO_START_7D"
    if elapsed_hours >= 72:
        return "NO_START_72H"
    if elapsed_hours >= 24:
        return "NO_START_24H"
    return None
