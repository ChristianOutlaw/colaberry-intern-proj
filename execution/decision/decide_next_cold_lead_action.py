"""
execution/decision/decide_next_cold_lead_action.py

Determines the next recommended action for a cold lead based on their
current status. Read-only: no database writes occur here.
"""

from execution.leads.get_lead_status import get_lead_status


def decide_next_cold_lead_action(
    lead_id: str,
    db_path: str | None = None,
) -> str:
    """Return the recommended next action for a cold lead.

    Reads the lead's current status and applies a simple priority-ordered
    decision tree to select the appropriate action label.

    Return values:
        "NO_LEAD"            — lead_id does not exist in the database.
        "SEND_INVITE"        — lead exists but no course invite has been sent.
        "NUDGE_START_CLASS"  — invite sent but lead has not started the course.
        "NUDGE_PROGRESS"     — lead has started but has not completed the course.
        "READY_FOR_BOOKING"  — lead has completed the course (completion_pct >= 100).

    Args:
        lead_id: ID of the lead to evaluate.
        db_path: Path to the SQLite file; defaults to tmp/app.db.
    """
    status = get_lead_status(lead_id, db_path)

    if not status["lead_exists"]:
        return "NO_LEAD"

    if not status["invite_sent"]:
        return "SEND_INVITE"

    completion_pct = status["course_state"]["completion_pct"]
    current_section = status["course_state"]["current_section"]

    if current_section is None:
        return "NUDGE_START_CLASS"

    if completion_pct is not None and completion_pct < 100:
        return "NUDGE_PROGRESS"

    return "READY_FOR_BOOKING"
