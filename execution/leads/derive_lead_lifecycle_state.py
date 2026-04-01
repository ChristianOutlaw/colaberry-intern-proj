"""
execution/leads/derive_lead_lifecycle_state.py

Pure helper: maps a lead's current signals into an explicit lifecycle state label.

Aligns with the spec's A–H lifecycle concept using only repo-available fields.
No database access, no network calls, no datetime.now().

Rule ordering mirrors build_cora_recommendation so lifecycle state and
recommendation event always agree on lead classification.
"""

from datetime import datetime, timezone

from execution.decision.build_cora_recommendation import STALL_DAYS

# ---------------------------------------------------------------------------
# Explicit lifecycle state constants
# ---------------------------------------------------------------------------
STATE_NOT_INVITED         = "NOT_INVITED"           # spec A — no invite sent yet
STATE_INVITED_NOT_STARTED = "INVITED_NOT_STARTED"   # spec B — invite sent, no progress
STATE_STARTED_ACTIVE      = "STARTED_ACTIVE"        # spec C — in progress, within stall window
STATE_STARTED_STALE       = "STARTED_STALE"         # spec D — in progress, no activity > STALL_DAYS
STATE_COMPLETED_WARM      = "COMPLETED_WARM"        # spec E — finished, not hot, not yet stale
STATE_COMPLETED_REENGAGE  = "COMPLETED_REENGAGE"    # spec F — finished, stale, re-engage
STATE_BOOKING_READY       = "BOOKING_READY"         # spec G/H — finished + HOT signal active


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _days_since_iso(raw: str | None, now_utc: datetime) -> int | None:
    """Return elapsed full days since an ISO-8601 timestamp, or None."""
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, (now_utc - ts.astimezone(timezone.utc)).days)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def derive_lead_lifecycle_state(
    *,
    invite_sent: bool,
    completion_percent: float | None,
    last_activity_at: str | None,
    hot_signal: str,
    now: datetime,
) -> str:
    """Derive an explicit lifecycle state label for a lead from current signals.

    Rules are evaluated in priority order; first match wins.

    Args:
        invite_sent:        True if a CourseInvite row with sent_at IS NOT NULL exists.
        completion_percent: 0.0–100.0, or None if no progress events exist.
        last_activity_at:   ISO-8601 string of most recent activity, or None.
        hot_signal:         "HOT" or "NOT_HOT" from compute_hot_lead_signal.
        now:                Reference UTC datetime (injected by caller).

    Returns:
        One of the STATE_* string constants defined in this module.
    """
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    days_inactive = _days_since_iso(last_activity_at, now_utc)

    # Rule 1 — no invite sent
    if not invite_sent:
        return STATE_NOT_INVITED

    # Rule 2 — 100 % complete → booking candidate (hot signal not required)
    if completion_percent is not None and completion_percent >= 100.0:
        return STATE_BOOKING_READY

    # Rule 3 — started but stalled mid-course
    if (
        completion_percent is not None
        and completion_percent > 0.0
        and completion_percent < 100.0
        and (days_inactive is None or days_inactive > STALL_DAYS)
    ):
        return STATE_STARTED_STALE

    # Rule 4 — course complete, not hot
    if completion_percent is not None and completion_percent >= 100.0:
        if days_inactive is not None and days_inactive > STALL_DAYS:
            return STATE_COMPLETED_REENGAGE
        return STATE_COMPLETED_WARM

    # Rule 5 — in progress and active within the stall window
    if completion_percent is not None and completion_percent > 0.0:
        return STATE_STARTED_ACTIVE

    # Catch-all — invited but not yet started (completion is None or 0.0)
    return STATE_INVITED_NOT_STARTED
