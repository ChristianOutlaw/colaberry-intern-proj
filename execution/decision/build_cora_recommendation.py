"""
execution/decision/build_cora_recommendation.py

Builds a structured recommendation event payload for Cora integration (v1).

Rule specification: directives/CORA_RECOMMENDATION_EVENTS.md

Pure function — no database access, no network calls, no datetime.now() calls.
Converts current lead state into a deterministic, explainable outreach payload
that a future Cora worker can consume to trigger the appropriate action.
"""

from datetime import datetime, timezone

from execution.leads.finalize_lead_score import finalize_lead_score
from execution.scans.classify_stale_progress_threshold import classify_stale_progress_threshold

# ---------------------------------------------------------------------------
# Locked constants — v1 (see directives/CORA_RECOMMENDATION_EVENTS.md)
# ---------------------------------------------------------------------------

# Event type labels
EVENT_SEND_INVITE    = "SEND_INVITE"
EVENT_HOT_BOOKING    = "READY_FOR_BOOKING"
EVENT_REENGAGE            = "REENGAGE_STALLED_LEAD"
EVENT_REENGAGE_COMPLETED  = "REENGAGE_COMPLETED"
EVENT_NUDGE_PROGRESS      = "NUDGE_PROGRESS"
EVENT_NO_ACTION           = "NO_ACTION"

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

    elif completion_percent is not None and completion_percent >= 100.0:
        # Rule 2 — course complete → booking call. final_label carries AI-fit segmentation.
        event_type            = EVENT_HOT_BOOKING   # value is "READY_FOR_BOOKING"
        priority              = PRIORITY_HIGH
        channel               = CHANNEL_CALL
        evt_codes             = ["COURSE_COMPLETE"]
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

    else:
        # Rule 5 — NUDGE_PROGRESS: catch-all for all invited leads not matched
        # above.  Covers sub-states distinguished by reason_codes:
        #   INVITED_NO_START  — completion_percent is None or 0.0 (not started)
        #   INACTIVE_48H/4D/7D — started, inactive past threshold (spec subtypes)
        #   ACTIVE_LEARNER    — started, active within 48 h (below stale threshold)
        event_type            = EVENT_NUDGE_PROGRESS
        priority              = PRIORITY_MEDIUM
        channel               = CHANNEL_EMAIL
        if completion_percent is None or completion_percent == 0.0:
            evt_codes = ["INVITED_NO_START"]
        else:
            stale_subtype = classify_stale_progress_threshold(last_activity_at, now_utc)
            evt_codes = [stale_subtype] if stale_subtype is not None else ["ACTIVE_LEARNER"]
        requires_finalization = False

    result_payload = {
        "completion_percent":    completion_percent,
        "current_section":       current_section,
        "days_inactive":         days_inactive,
        "hot_signal":            hot_signal,
        "temperature_signal":    temperature_signal,
        "temperature_score":     temperature_score,
        "upstream_reason_codes": list(reason_codes),
        "requires_finalization": requires_finalization,
        "final_label":           None,  # assigned by finalize_lead_score when requires_finalization is True
    }

    if requires_finalization:
        result_payload = finalize_lead_score(lead_id, result_payload)

    return {
        "lead_id":             lead_id,
        "event_type":          event_type,
        "priority":            priority,
        "reason_codes":        evt_codes,
        "recommended_channel": channel,
        "payload":             result_payload,
        "status":              "READY",
        "built_at":            built_at,
    }
