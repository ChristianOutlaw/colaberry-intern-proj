"""
execution/decision/get_cora_recommendation.py

Read-only wrapper that assembles currently available lead data and returns
a Cory recommendation for a single lead.

Directive: directives/CORA_RECOMMENDATION_EVENTS.md

Pure read-only — no database writes occur here.  Combines get_lead_status
with build_cora_recommendation using today's available signals.

Three temperature inputs are not yet instrumented and are passed as None;
compute_lead_temperature handles absence gracefully (neutral half-credit):
    avg_quiz_score        — quiz scores not yet persisted to DB
    avg_quiz_attempts     — quiz attempts not yet persisted to DB
    reflection_confidence — not yet derived from stored reflection text
"""

from datetime import datetime, timezone

from execution.decision.build_cora_recommendation import build_cora_recommendation
from execution.leads.compute_lead_temperature import compute_lead_temperature
from execution.leads.get_lead_status import get_lead_status


def get_cora_recommendation(
    lead_id: str,
    *,
    now: datetime | None = None,
    db_path: str | None = None,
) -> dict:
    """Return a Cory recommendation dict for a single lead.

    Reads the lead's current status from the database and assembles the
    inputs available today into a build_cora_recommendation call.

    Args:
        lead_id:  ID of the lead to evaluate.  Must be a non-empty string
                  and must exist in the database.
        now:      Reference UTC datetime injected by the caller.
                  Defaults to the current UTC time when None.
                  Tests must always supply a fixed value — never call
                  datetime.now() inside test assertions.
        db_path:  Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        dict — the full build_cora_recommendation output shape:
            lead_id, event_type, priority, reason_codes,
            recommended_channel, payload, status, built_at.

    Raises:
        ValueError: if lead_id is empty or the lead does not exist in
                    the database.
    """
    if not lead_id or not str(lead_id).strip():
        raise ValueError("lead_id must be a non-empty string.")

    if now is None:
        now = datetime.now(timezone.utc)

    status = get_lead_status(lead_id, db_path=db_path, now_utc=now)

    if not status["lead_exists"]:
        raise ValueError(f"Lead not found: {lead_id!r}")

    cs       = status["course_state"]
    hot_lead = status["hot_lead"]

    # Compute temperature score from signals available today.
    # avg_quiz_score, avg_quiz_attempts, and reflection_confidence are not yet
    # persisted to the DB; each receives a neutral half-credit in the engine.
    temp_result = compute_lead_temperature(
        now=now,
        invited_sent=status["invite_sent"],
        completion_percent=cs["completion_pct"],
        last_activity_at=cs["last_activity_at"],
        started_at=cs["started_at"],
        avg_quiz_score=None,         # not yet instrumented
        avg_quiz_attempts=None,      # not yet instrumented
        reflection_confidence=None,  # not yet instrumented
        current_section=cs["current_section"],
    )

    # upstream_reason_codes: hot-signal reason first (if present), then the
    # full set of temperature component codes so Cora workers can see why
    # the score is what it is without needing to re-run the scoring engine.
    reason_codes: list[str] = (
        [hot_lead["reason"]] if hot_lead.get("reason") else []
    )
    reason_codes = reason_codes + temp_result["reason_codes"]

    return build_cora_recommendation(
        now=now,
        lead_id=lead_id,
        invite_sent=status["invite_sent"],
        completion_percent=cs["completion_pct"],
        current_section=cs["current_section"],
        last_activity_at=cs["last_activity_at"],
        hot_signal=hot_lead["signal"] or "NOT_HOT",
        temperature_signal=temp_result["signal"],
        temperature_score=temp_result["score"],
        reason_codes=reason_codes,
    )
