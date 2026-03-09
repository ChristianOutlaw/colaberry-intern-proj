"""
execution/leads/compute_lead_temperature.py

Multi-signal weighted lead temperature scoring engine (v1).

Rule specification: directives/LEAD_TEMPERATURE_SCORING.md

This engine coexists alongside the binary HotLeadSignal (v1).  It
produces a numeric score 0–100 and a three-tier classification
(HOT / WARM / COLD) from up to six independent engagement signals.

Design goals:
  - Fully deterministic: same inputs always produce the same output.
  - Resilient to missing data: every component has a safe neutral value.
  - Explainable: every score contribution is traceable to a named reason code.
  - Pure function: no database access, no network, no datetime.now() calls.
"""

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Locked constants — v1 (see directives/LEAD_TEMPERATURE_SCORING.md)
# ---------------------------------------------------------------------------

# Component weights — positive contributions (sum = 110 at perfect signals, clamped to 100)
W_COMPLETION: int = 40   # max pts for course completion
W_RECENCY: int    = 25   # max pts for recent activity
W_QUIZ: int       = 20   # max pts for avg quiz score
W_REFLECTION: int = 15   # max pts for reflection confidence
W_VELOCITY: int   = 10   # max pts for learning velocity

# Penalty cap
MAX_RETRY_PENALTY: int = 15  # maximum retry friction deduction

# Invite cap — not-invited leads cannot score above this
INVITE_CAP: int = 15

# Score → signal thresholds
SCORE_HOT: int  = 70   # score >= SCORE_HOT  → "HOT"
SCORE_WARM: int = 35   # score >= SCORE_WARM → "WARM"; below → "COLD"

# Recency breakpoints (days inactive)
_RECENCY_HOT       = 7
_RECENCY_MODERATE  = 14
_RECENCY_COOL      = 21
_RECENCY_COLD      = 30

# Retry friction breakpoints (avg attempts per quiz)
_RETRY_LOW      = 1.5
_RETRY_MODERATE = 2.5
_RETRY_HIGH     = 3.5

# Neutral half-credit values used when optional signal data is absent
_QUIZ_UNKNOWN_PTS:       int = 10   # half of W_QUIZ
_REFLECTION_UNKNOWN_PTS: int = 7    # near half of W_REFLECTION
_VELOCITY_UNKNOWN_PTS:   int = 5    # half of W_VELOCITY

# Velocity breakpoints (completion-pct-points per day)
_VELOCITY_FAST     = 5.0
_VELOCITY_MODERATE = 1.5


# ---------------------------------------------------------------------------
# Internal helpers — one per scoring component
# ---------------------------------------------------------------------------

def _to_utc(dt: datetime) -> datetime:
    """Return a UTC-aware datetime.  Naive inputs are assumed to be UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _days_inactive(raw: str | None, now_utc: datetime) -> int | None:
    """Parse a stored ISO-8601 timestamp and return elapsed full days.

    Returns None when raw is None or unparseable.
    Returns 0 when the timestamp is in the future relative to now_utc.
    """
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = now_utc - ts.astimezone(timezone.utc)
        return max(0, delta.days)
    except (ValueError, TypeError):
        return None


def _completion_points(pct: float | None) -> tuple[int, str]:
    """Score course completion.

    Returns (points, reason_code).
    """
    if pct is None or pct <= 0:
        return 0, "COMPLETION_NONE"
    pts = min(W_COMPLETION, int(pct * W_COMPLETION / 100))
    if pct >= 75:
        return pts, "COMPLETION_STRONG"
    if pct >= 25:
        return pts, "COMPLETION_MODERATE"
    return pts, "COMPLETION_LOW"


def _recency_points(days: int | None) -> tuple[int, str]:
    """Score recency of last activity.

    Returns (points, reason_code).
    """
    if days is None:
        return 0, "NO_ACTIVITY"
    if days <= _RECENCY_HOT:
        return W_RECENCY, "RECENTLY_ACTIVE"
    if days <= _RECENCY_MODERATE:
        return 18, "ACTIVITY_MODERATE"
    if days <= _RECENCY_COOL:
        return 10, "ACTIVITY_STALE"
    if days <= _RECENCY_COLD:
        return 4, "ACTIVITY_VERY_STALE"
    return 0, "ACTIVITY_DORMANT"


def _quiz_points(avg_score: float | None) -> tuple[int, str]:
    """Score average quiz performance.

    Returns (points, reason_code).
    Missing data earns a neutral half-credit to avoid penalising leads
    who simply have not yet reached any quizzes.
    """
    if avg_score is None:
        return _QUIZ_UNKNOWN_PTS, "QUIZ_UNKNOWN"
    pts = min(W_QUIZ, int(avg_score * W_QUIZ / 100))
    if avg_score >= 80:
        return pts, "QUIZ_STRONG"
    if avg_score >= 50:
        return pts, "QUIZ_MODERATE"
    return pts, "QUIZ_WEAK"


def _reflection_points(confidence: str | None) -> tuple[int, str]:
    """Score reflection engagement depth.

    Returns (points, reason_code).
    Missing data earns a neutral near-half-credit.
    """
    key = (confidence or "").strip().upper()
    if key == "HIGH":
        return W_REFLECTION, "REFLECTION_HIGH"
    if key == "MEDIUM":
        return 9, "REFLECTION_MEDIUM"
    if key == "LOW":
        return 3, "REFLECTION_LOW"
    return _REFLECTION_UNKNOWN_PTS, "REFLECTION_UNKNOWN"


def _retry_penalty(avg_attempts: float | None) -> tuple[int, str | None]:
    """Compute retry friction penalty.

    Returns (penalty_as_negative_int_or_zero, reason_code_or_None).
    No penalty and no code emitted when attempts are low or absent.
    """
    if avg_attempts is None or avg_attempts <= _RETRY_LOW:
        return 0, None
    if avg_attempts <= _RETRY_MODERATE:
        return -5, "RETRY_MILD"
    if avg_attempts <= _RETRY_HIGH:
        return -10, "RETRY_MODERATE"
    return -MAX_RETRY_PENALTY, "RETRY_HIGH"


def _velocity_points(
    started_at: str | None,
    completion_percent: float | None,
    now_utc: "datetime",
) -> tuple[int, str]:
    """Score learning velocity (completion rate since enrolment start).

    velocity = completion_percent / max(1, elapsed_days)
    where elapsed_days = days from started_at to now_utc.

    Returns (points, reason_code).
    Missing data earns a neutral half-credit so uninvited/unenrolled
    leads are not unfairly penalised.
    """
    if started_at is None or completion_percent is None:
        return _VELOCITY_UNKNOWN_PTS, "VELOCITY_UNKNOWN"

    elapsed_days = _days_inactive(started_at, now_utc)
    if elapsed_days is None:
        return _VELOCITY_UNKNOWN_PTS, "VELOCITY_UNKNOWN"

    elapsed_days = max(1, elapsed_days)
    velocity = completion_percent / elapsed_days

    if velocity > _VELOCITY_FAST:
        return W_VELOCITY, "VELOCITY_FAST"
    if velocity > _VELOCITY_MODERATE:
        return 6, "VELOCITY_MODERATE"
    if velocity > 0.0:
        return 3, "VELOCITY_SLOW"
    return 0, "VELOCITY_NONE"


def _build_summary(signal: str, score: int, codes: list[str]) -> str:
    """Assemble a one-line human-readable explanation from reason codes."""
    positives: list[str] = []
    negatives: list[str] = []

    # Completion
    if "COMPLETION_STRONG" in codes:
        positives.append("strong course completion")
    elif "COMPLETION_MODERATE" in codes:
        positives.append("partial course completion")
    # COMPLETION_LOW / COMPLETION_NONE → no positive contribution phrased

    # Recency
    if "RECENTLY_ACTIVE" in codes:
        positives.append("recently active")
    elif "ACTIVITY_MODERATE" in codes:
        positives.append("active within 2 weeks")
    elif "ACTIVITY_DORMANT" in codes or "ACTIVITY_VERY_STALE" in codes:
        negatives.append("inactive for over 3 weeks")
    elif "ACTIVITY_STALE" in codes:
        negatives.append("limited recent activity")
    elif "NO_ACTIVITY" in codes:
        negatives.append("no activity recorded")

    # Quiz
    if "QUIZ_STRONG" in codes:
        positives.append("strong quiz performance")
    elif "QUIZ_WEAK" in codes:
        negatives.append("low quiz scores")

    # Reflection
    if "REFLECTION_HIGH" in codes:
        positives.append("high reflection confidence")
    elif "REFLECTION_LOW" in codes:
        negatives.append("low reflection confidence")

    # Retry friction
    if "RETRY_HIGH" in codes:
        negatives.append("high quiz retry friction")
    elif "RETRY_MODERATE" in codes:
        negatives.append("moderate retry friction")
    elif "RETRY_MILD" in codes:
        negatives.append("mild retry friction")

    # Velocity
    if "VELOCITY_FAST" in codes:
        positives.append("fast learning pace")
    elif "VELOCITY_MODERATE" in codes:
        positives.append("steady learning pace")
    elif "VELOCITY_SLOW" in codes or "VELOCITY_NONE" in codes:
        negatives.append("slow learning pace")

    # Invite gate
    if "NOT_INVITED" in codes:
        negatives.append("no course invite sent")

    parts = positives + negatives
    detail = "; ".join(parts) if parts else "insufficient engagement data"
    return f"{signal} (score {score}): {detail}."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_lead_temperature(
    *,
    now: datetime,
    invited_sent: bool,
    completion_percent: float | None,
    last_activity_at: str | None,
    started_at: str | None = None,
    avg_quiz_score: float | None,
    avg_quiz_attempts: float | None,
    reflection_confidence: str | None,
    current_section: str | None,
) -> dict:
    """Compute the LeadTemperatureScore v1 for a single lead.

    All inputs are plain values — no database access or network calls occur.
    `now` must be provided by the caller; this function never calls
    datetime.now() internally.

    Args:
        now:                  Current UTC time (injected by caller).
        invited_sent:         True if a CourseInvite record exists.
        completion_percent:   0.0–100.0, or None if no ProgressEvents exist.
        last_activity_at:     ISO-8601 string of most recent activity, or None.
        started_at:           ISO-8601 string of first activity (from
                              course_state.started_at), or None.
        avg_quiz_score:       Mean quiz score 0–100, or None if no quiz data.
        avg_quiz_attempts:    Mean attempts per quiz, or None if no quiz data.
        reflection_confidence: "HIGH" | "MEDIUM" | "LOW" | None.
        current_section:      Current course section label (accepted but not
                              scored in v1; reserved for future use).

    Returns:
        dict with keys:
            signal         (str)       "HOT" | "WARM" | "COLD"
            score          (int)       0–100, clamped
            reason_codes   (list[str]) One code per scored component plus any
                                       applicable penalty / gate codes.
            reason_summary (str)       Human-readable one-line explanation.
            evaluated_at   (str)       ISO-8601 UTC with trailing "Z".

    Raises:
        ValueError: if now is None.

    See directives/LEAD_TEMPERATURE_SCORING.md for the full specification.
    """
    if now is None:
        raise ValueError(
            "now must be provided explicitly; "
            "do not call datetime.now() inside execution functions."
        )

    now_utc = _to_utc(now)
    evaluated_at = now_utc.isoformat().replace("+00:00", "Z")

    # ------------------------------------------------------------------
    # Score each component independently
    # ------------------------------------------------------------------
    days = _days_inactive(last_activity_at, now_utc)

    comp_pts,  comp_code  = _completion_points(completion_percent)
    rec_pts,   rec_code   = _recency_points(days)
    quiz_pts,  quiz_code  = _quiz_points(avg_quiz_score)
    refl_pts,  refl_code  = _reflection_points(reflection_confidence)
    retry_pen, retry_code = _retry_penalty(avg_quiz_attempts)
    vel_pts,   vel_code   = _velocity_points(started_at, completion_percent, now_utc)

    # ------------------------------------------------------------------
    # Sum and clamp to [0, 100]
    # ------------------------------------------------------------------
    raw = comp_pts + rec_pts + quiz_pts + refl_pts + retry_pen + vel_pts
    raw_clamped = max(0, min(100, int(raw)))

    # ------------------------------------------------------------------
    # Invite gate — cap score for leads who have never been invited
    # ------------------------------------------------------------------
    if not invited_sent:
        final_score = min(raw_clamped, INVITE_CAP)
    else:
        final_score = raw_clamped

    # ------------------------------------------------------------------
    # Classify
    # ------------------------------------------------------------------
    if final_score >= SCORE_HOT:
        signal = "HOT"
    elif final_score >= SCORE_WARM:
        signal = "WARM"
    else:
        signal = "COLD"

    # ------------------------------------------------------------------
    # Assemble reason codes (order: component codes, then penalty/gate)
    # ------------------------------------------------------------------
    reason_codes: list[str] = [comp_code, rec_code, quiz_code, refl_code, vel_code]
    if retry_code:
        reason_codes.append(retry_code)
    if not invited_sent:
        reason_codes.append("NOT_INVITED")

    reason_summary = _build_summary(signal, final_score, reason_codes)

    return {
        "signal":         signal,
        "score":          final_score,
        "reason_codes":   reason_codes,
        "reason_summary": reason_summary,
        "evaluated_at":   evaluated_at,
    }
