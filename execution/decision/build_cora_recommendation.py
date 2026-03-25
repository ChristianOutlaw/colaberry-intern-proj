"""
execution/decision/build_cora_recommendation.py

Builds a structured recommendation event payload for Cora integration (v1).

Rule specification: directives/CORA_RECOMMENDATION_EVENTS.md

Pure function — no database access, no network calls, no datetime.now() calls.
Converts current lead state into a deterministic, explainable outreach payload
that a future Cora worker can consume to trigger the appropriate action.
"""

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Locked constants — v1 (see directives/CORA_RECOMMENDATION_EVENTS.md)
# ---------------------------------------------------------------------------

# Event type labels
EVENT_SEND_INVITE    = "SEND_INVITE"
EVENT_HOT_BOOKING    = "HOT_LEAD_BOOKING"
EVENT_REENGAGE       = "REENGAGE_STALLED_LEAD"
EVENT_NUDGE_PROGRESS = "NUDGE_PROGRESS"
EVENT_NO_ACTION      = "NO_ACTION"

# Priority tiers
PRIORITY_HIGH   = "HIGH"
PRIORITY_MEDIUM = "MEDIUM"
PRIORITY_LOW    = "LOW"

# Recommended outreach channels (advisory only)
CHANNEL_EMAIL = "EMAIL"
CHANNEL_CALL  = "CALL"

# Days of inactivity after which a started lead is considered stalled
STALL_DAYS: int = 14


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_utc(dt: datetime) -> datetime:
    """Return a UTC-aware datetime. Naive inputs are assumed to be UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _days_since(raw: str | None, now_utc: datetime) -> int | None:
    """Return elapsed full days since an ISO-8601 timestamp, or None.

    Returns None when raw is None or unparseable.
    Returns 0 when the timestamp is in the future relative to now_utc.
    """
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

def build_cora_recommendation(
    *,
    now: datetime,
    lead_id: str,
    invite_sent: bool,
    completion_percent: float | None,
    current_section: str | None,
    last_activity_at: str | None,
    hot_signal: str,
    temperature_signal: str | None,
    temperature_score: int | None,
    reason_codes: list[str],
) -> dict:
    """Build a Cora-ready recommendation event payload for a single lead.

    All inputs are plain values — no database access or network calls occur.
    `now` must be provided by the caller; this function never calls datetime.now().

    Args:
        now:                Reference UTC datetime (injected by caller).
        lead_id:            Unique lead identifier.
        invite_sent:        True if a CourseInvite record exists.
        completion_percent: 0.0–100.0, or None if no progress events exist.
        current_section:    Current course section label, or None.
        last_activity_at:   ISO-8601 string of most recent activity, or None.
        hot_signal:         "HOT" or "NOT_HOT" from compute_hot_lead_signal.
        temperature_signal: "HOT" | "WARM" | "COLD" | None from compute_lead_temperature.
        temperature_score:  Numeric temperature score 0–100, or None.
        reason_codes:       Upstream reason codes (passed through into payload).

    Returns:
        dict with keys:
            lead_id              (str)       Echoed from input.
            event_type           (str)       One of the five v1 event types.
            priority             (str)       "HIGH" | "MEDIUM" | "LOW"
            reason_codes         (list[str]) Event-driving codes for this recommendation.
            recommended_channel  (str|None)  "EMAIL" | "CALL" | None
            payload              (dict)      Structured context for Cora.
            status               (str)       Always "READY" in v1.
            built_at             (str)       ISO-8601 UTC with trailing "Z".

    Raises:
        ValueError: if now is None or lead_id is empty.

    See directives/CORA_RECOMMENDATION_EVENTS.md for the full specification.
    """
    if now is None:
        raise ValueError(
            "now must be provided explicitly; "
            "do not call datetime.now() inside execution functions."
        )
    if not lead_id:
        raise ValueError("lead_id must be a non-empty string.")

    now_utc      = _to_utc(now)
    built_at     = now_utc.isoformat().replace("+00:00", "Z")
    days_inactive = _days_since(last_activity_at, now_utc)

    # ------------------------------------------------------------------
    # Decision tree — evaluated in priority order; first match wins.
    # Five rules; NUDGE_START_CLASS is no longer a top-level event —
    # not-started leads fall through to NUDGE_PROGRESS (INVITED_NO_START).
    # See directives/CORA_RECOMMENDATION_EVENTS.md for rationale.
    # ------------------------------------------------------------------

    if not invite_sent:
        # Rule 1 — no invite exists yet.
        event_type            = EVENT_SEND_INVITE
        priority              = PRIORITY_LOW
        channel               = CHANNEL_EMAIL
        evt_codes             = ["NOT_INVITED"]
        requires_finalization = False

    elif hot_signal == "HOT" and completion_percent is not None and completion_percent >= 100.0:
        # Rule 2 — HOT signal AND full course completion → booking call.
        # Spec hard rule: READY_FOR_BOOKING requires 100% course completion.
        # A lead at partial completion with a hot signal is still in progress
        # and must be nudged, not booked.
        event_type            = EVENT_HOT_BOOKING
        priority              = PRIORITY_HIGH
        channel               = CHANNEL_CALL
        evt_codes             = ["HOT_SIGNAL_ACTIVE"]
        requires_finalization = True

    elif (
        completion_percent is not None
        and completion_percent > 0.0
        and completion_percent < 100.0
        and (days_inactive is None or days_inactive > STALL_DAYS)
    ):
        # Rule 3 — started but stalled.  Guard requires completion_percent > 0
        # so None / 0.0 (not-started) never reaches this branch, avoiding a
        # TypeError on None < 100.0 comparisons.
        event_type            = EVENT_REENGAGE
        priority              = PRIORITY_HIGH
        channel               = CHANNEL_CALL
        evt_codes             = ["ACTIVITY_STALLED"]
        requires_finalization = False

    elif completion_percent is not None and completion_percent >= 100.0:
        # Rule 4 — course complete, hot signal not active → WARM_REVIEW.
        # The lead finished but did not meet final-hot criteria; route to
        # human review rather than discarding. FINALIZE_LEAD_SCORE scoring
        # will gate this more precisely in the next step.
        event_type            = "WARM_REVIEW"
        priority              = PRIORITY_LOW
        channel               = None
        evt_codes             = ["COURSE_COMPLETE"]
        requires_finalization = True

    else:
        # Rule 5 — NUDGE_PROGRESS: catch-all for all invited leads not matched
        # above.  Covers two sub-states distinguished by reason_codes:
        #   INVITED_NO_START  — completion_percent is None or 0.0 (not started)
        #   ACTIVE_LEARNER    — 0 < completion_percent < 100, within STALL_DAYS
        event_type            = EVENT_NUDGE_PROGRESS
        priority              = PRIORITY_MEDIUM
        channel               = CHANNEL_EMAIL
        evt_codes             = (
            ["INVITED_NO_START"]
            if completion_percent is None or completion_percent == 0.0
            else ["ACTIVE_LEARNER"]
        )
        requires_finalization = False

    return {
        "lead_id":             lead_id,
        "event_type":          event_type,
        "priority":            priority,
        "reason_codes":        evt_codes,
        "recommended_channel": channel,
        "payload": {
            "completion_percent":    completion_percent,
            "current_section":       current_section,
            "days_inactive":         days_inactive,
            "hot_signal":            hot_signal,
            "temperature_signal":    temperature_signal,
            "temperature_score":     temperature_score,
            "upstream_reason_codes": list(reason_codes),
            "requires_finalization": requires_finalization,
        },
        "status":   "READY",
        "built_at": built_at,
    }
