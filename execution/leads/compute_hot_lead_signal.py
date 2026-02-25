"""
execution/leads/compute_hot_lead_signal.py

Implements the HotLeadSignal MVP v1 rule engine.

Rule specification: directives/HOT_LEAD_SIGNAL.md
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — locked for v1 (see directives/HOT_LEAD_SIGNAL.md §Locked thresholds)
# ---------------------------------------------------------------------------
COMPLETION_THRESHOLD_PCT: float = 25.0
ACTIVITY_WINDOW_DAYS: int = 7


def _ensure_utc(dt: datetime, name: str) -> datetime:
    """Return a timezone-aware UTC datetime.

    If *dt* is naive (no tzinfo), it is assumed to be UTC and a warning is
    emitted. If *dt* is already aware, it is converted to UTC.
    """
    if dt.tzinfo is None:
        logger.warning(
            "compute_hot_lead_signal: %s has no tzinfo; assuming UTC.", name
        )
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_hot_lead_signal(
    *,
    invite_sent: bool,
    completion_percent: float | None,
    last_activity_time: datetime | None,
    now: datetime,
) -> dict:
    """Evaluate the HotLeadSignal v1 rule set for a single lead.

    Implements the three-gate deterministic rule engine defined in
    directives/HOT_LEAD_SIGNAL.md. Gates are evaluated in order; evaluation
    stops at the first failure.

    Args:
        invite_sent:        True if a CourseInvite record exists for this lead.
        completion_percent: Course completion (0.0–100.0), or None when no
                            ProgressEvent rows exist.
        last_activity_time: UTC datetime of the most recent ProgressEvent, or
                            None when no events have been recorded.
        now:                Current UTC time, injected by the caller. This
                            function never calls datetime.now() internally.

    Returns:
        dict with keys:
            hot          (bool) – True only when all three gates pass.
            reasons      (list) – Exactly one reason-code string (v1).
            evaluated_at (str)  – ISO-8601 UTC string with trailing "Z".

    Reason codes (exhaustive for v1):
        NOT_INVITED               – invite_sent is False.
        COMPLETION_UNKNOWN        – completion_percent is None.
        COMPLETION_BELOW_THRESHOLD – completion_percent < 25.0.
        NO_ACTIVITY_RECORDED      – last_activity_time is None.
        ACTIVITY_STALE            – last activity more than 7 days before now.
        HOT_ENGAGED               – all gates passed.

    See directives/HOT_LEAD_SIGNAL.md for full specification.
    """
    now_utc = _ensure_utc(now, "now")
    evaluated_at = now_utc.isoformat().replace("+00:00", "Z")

    # ------------------------------------------------------------------
    # Rule 1 — Invite Gate
    # ------------------------------------------------------------------
    if not invite_sent:
        return {
            "hot": False,
            "reasons": ["NOT_INVITED"],
            "evaluated_at": evaluated_at,
        }

    # ------------------------------------------------------------------
    # Rule 2 — Completion Gate
    # ------------------------------------------------------------------
    if completion_percent is None:
        return {
            "hot": False,
            "reasons": ["COMPLETION_UNKNOWN"],
            "evaluated_at": evaluated_at,
        }

    if completion_percent < COMPLETION_THRESHOLD_PCT:
        return {
            "hot": False,
            "reasons": ["COMPLETION_BELOW_THRESHOLD"],
            "evaluated_at": evaluated_at,
        }

    # ------------------------------------------------------------------
    # Rule 3 — Recency Gate
    # ------------------------------------------------------------------
    if last_activity_time is None:
        return {
            "hot": False,
            "reasons": ["NO_ACTIVITY_RECORDED"],
            "evaluated_at": evaluated_at,
        }

    last_utc = _ensure_utc(last_activity_time, "last_activity_time")
    if (now_utc - last_utc).days > ACTIVITY_WINDOW_DAYS:
        return {
            "hot": False,
            "reasons": ["ACTIVITY_STALE"],
            "evaluated_at": evaluated_at,
        }

    # ------------------------------------------------------------------
    # All gates pass
    # ------------------------------------------------------------------
    return {
        "hot": True,
        "reasons": ["HOT_ENGAGED"],
        "evaluated_at": evaluated_at,
    }
