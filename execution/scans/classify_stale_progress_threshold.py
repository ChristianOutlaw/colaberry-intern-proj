"""
execution/scans/classify_stale_progress_threshold.py

Pure helper: classifies an in-progress lead into an inactivity threshold bucket
based on elapsed time since last course activity.

No DB access, no dispatch, no side effects.
"""

from datetime import datetime, timezone


def classify_stale_progress_threshold(last_activity_at: str | None, now: datetime) -> str | None:
    """
    Classify an in-progress lead into the current inactivity threshold bucket.

    Returns one of:
    - "INACTIVE_48H"  — last activity >= 48 hours ago
    - "INACTIVE_4D"   — last activity >= 4 days ago
    - "INACTIVE_7D"   — last activity >= 7 days ago
    - None            — last_activity_at is None or elapsed < 48 hours

    Rules (evaluated in descending severity order; first match wins):
    - if last_activity_at is None -> None
    - if elapsed >= 7 days  -> "INACTIVE_7D"
    - elif elapsed >= 4 days -> "INACTIVE_4D"
    - elif elapsed >= 48 hours -> "INACTIVE_48H"
    - else -> None

    Args:
        last_activity_at: ISO-8601 timestamp string, or None.
        now:              Reference UTC datetime (injected by caller).
    """
    if last_activity_at is None:
        return None

    ts = datetime.fromisoformat(last_activity_at.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    elapsed_hours = (now_utc - ts.astimezone(timezone.utc)).total_seconds() / 3600

    if elapsed_hours >= 168:   # 7 days = 7 * 24
        return "INACTIVE_7D"
    if elapsed_hours >= 96:    # 4 days = 4 * 24
        return "INACTIVE_4D"
    if elapsed_hours >= 48:
        return "INACTIVE_48H"
    return None
