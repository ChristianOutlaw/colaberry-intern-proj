"""
execution/progress/rescore_on_section_restart.py

Minimal restart-detection hook: compares previous and current completion_pct
and triggers a fresh temperature rescore when a drop is detected.

Restart detection rule:
    current_completion_pct < previous_completion_pct
    (i.e. the stored completion dropped, indicating a section was reset)

When no restart is detected, returns None — the caller's existing score
remains valid and no unnecessary recomputation occurs.

When a restart is detected, delegates directly to compute_lead_temperature()
with the caller-supplied signals.  No DB access, no side effects.
`now` must be provided by the caller; this function never calls datetime.now().
"""

from datetime import datetime

from execution.leads.compute_lead_temperature import compute_lead_temperature


def rescore_on_section_restart(
    *,
    now: datetime,
    previous_completion_pct: float | None,
    current_completion_pct: float | None,
    invite_sent: bool,
    last_activity_at: str | None,
    started_at: str | None = None,
    avg_quiz_score: float | None = None,
    avg_quiz_attempts: float | None = None,
    reflection_confidence: str | None = None,
    current_section: str | None = None,
) -> dict | None:
    """Return a fresh temperature score when a section restart is detected.

    Restart is detected when current_completion_pct is strictly less than
    previous_completion_pct.  Both values must be non-None for detection to
    fire; unknown/missing state is treated as no-restart.

    Args:
        now:                     Reference UTC datetime (injected by caller).
        previous_completion_pct: Stored completion before the latest state update.
        current_completion_pct:  Newly computed completion after the update.
        invite_sent:             True if a confirmed course invite exists.
        last_activity_at:        ISO-8601 string of most recent activity, or None.
        started_at:              ISO-8601 string of first activity, or None.
        avg_quiz_score:          Mean quiz score 0-100, or None.
        avg_quiz_attempts:       Mean attempts per quiz, or None.
        reflection_confidence:   "HIGH" | "MEDIUM" | "LOW" | None.
        current_section:         Current section label, or None.

    Returns:
        dict  — compute_lead_temperature result, when restart is detected.
        None  — when no restart is detected (forward progress or unknown state).

    Raises:
        ValueError: if now is None (propagated from compute_lead_temperature).
    """
    # Restart requires both values to be known and for completion to have dropped.
    if (
        previous_completion_pct is None
        or current_completion_pct is None
        or current_completion_pct >= previous_completion_pct
    ):
        return None  # no restart detected

    return compute_lead_temperature(
        now=now,
        invited_sent=invite_sent,
        completion_percent=current_completion_pct,
        last_activity_at=last_activity_at,
        started_at=started_at,
        avg_quiz_score=avg_quiz_score,
        avg_quiz_attempts=avg_quiz_attempts,
        reflection_confidence=reflection_confidence,
        current_section=current_section,
    )
