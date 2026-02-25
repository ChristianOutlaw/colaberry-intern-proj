"""
execution/leads/get_lead_status.py

Assembles and returns a lead's current status summary from the database.
No business logic or state computation lives here — only data retrieval.
"""

from datetime import datetime, timezone

from execution.db.sqlite import connect, init_db
from execution.leads.compute_hot_lead_signal import compute_hot_lead_signal

_EMPTY_STATUS = {
    "lead_exists": False,
    "invite_sent": False,
    "course_state": {
        "current_section": None,
        "completion_pct": None,
        "last_activity_at": None,
    },
    "hot_lead": {
        "signal": None,
        "score": None,
        "reason": None,
    },
}


def get_lead_status(
    lead_id: str,
    db_path: str | None = None,
) -> dict:
    """Return a structured status summary for a lead.

    Queries leads, course_state, and course_invites, then derives the
    HotLeadSignal in-process and assembles the results into a single
    dictionary for downstream use
    (e.g. Cora personalisation, GHL push decisions).

    Args:
        lead_id: ID of the lead to look up.
        db_path: Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        dict with keys: lead_exists, invite_sent, course_state, hot_lead.
        All nested fields default to None when the corresponding row is absent.
    """
    conn = connect(db_path)
    try:
        init_db(conn)

        lead = conn.execute(
            "SELECT id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()

        if lead is None:
            return dict(_EMPTY_STATUS)  # shallow copy is safe — nested dicts are recreated below

        invite_count = conn.execute(
            "SELECT COUNT(*) FROM course_invites WHERE lead_id = ?", (lead_id,)
        ).fetchone()[0]

        cs = conn.execute(
            "SELECT current_section, completion_pct, last_activity_at FROM course_state WHERE lead_id = ?",
            (lead_id,),
        ).fetchone()

    finally:
        conn.close()

    now_utc = datetime.now(timezone.utc)

    last_activity_time = None
    if cs is not None and cs["last_activity_at"] is not None:
        raw = cs["last_activity_at"].replace("Z", "+00:00")
        last_activity_time = datetime.fromisoformat(raw)

    hot_result = compute_hot_lead_signal(
        invite_sent=invite_count > 0,
        completion_percent=cs["completion_pct"] if cs is not None else None,
        last_activity_time=last_activity_time,
        now=now_utc,
    )
    hot_signal = "HOT" if hot_result["hot"] else "NOT_HOT"
    hot_score = None
    hot_reason = hot_result["reasons"][0]

    return {
        "lead_exists": True,
        "invite_sent": invite_count > 0,
        "course_state": {
            "current_section": cs["current_section"] if cs else None,
            "completion_pct": cs["completion_pct"] if cs else None,
            "last_activity_at": cs["last_activity_at"] if cs else None,
        },
        "hot_lead": {
            "signal": hot_signal,
            "score": hot_score,
            "reason": hot_reason,
        },
    }
