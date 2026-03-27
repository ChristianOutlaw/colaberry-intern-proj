"""
execution/orchestration/run_booking_ready_scan.py

Manual orchestration entry point: READY_FOR_BOOKING scan → recommendations.

Flow:
  1. find_ready_for_booking_leads(now, ...) — returns leads that are
     completed, invite-confirmed, and active within the HOT recency window.
  2. For each lead: build_cora_recommendation(...) — builds the structured
     recommendation payload.
  3. Returns a list of slim result dicts (lead_id, event_type, priority,
     reason_codes) — enough for a caller to act or log without the full payload.

No scheduling, no queues, no async, no side effects beyond what the called
functions already do.  `now` must be provided by the caller.
"""

from datetime import datetime

from execution.decision.build_cora_recommendation import build_cora_recommendation
from execution.scans.find_ready_for_booking_leads import find_ready_for_booking_leads


def run_booking_ready_scan(
    now: datetime,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """Run the READY_FOR_BOOKING scan and build a recommendation for each lead.

    Args:
        now:      Reference UTC datetime (injected by caller — never datetime.now()).
        limit:    Maximum leads to scan. Defaults to 100.
        db_path:  Path to the SQLite file. Uses default dev DB when None.

    Returns:
        List of dicts, one per eligible lead, in scan order:
            {
                "lead_id":     str,
                "event_type":  str,
                "priority":    str,
                "reason_codes": list[str],
            }
        Returns [] when no eligible leads exist.

    Raises:
        ValueError: if now is None (propagated from find_ready_for_booking_leads).
    """
    leads = find_ready_for_booking_leads(now=now, limit=limit, db_path=db_path)

    results: list[dict] = []
    for lead in leads:
        rec = build_cora_recommendation(
            now=now,
            lead_id=lead["lead_id"],
            invite_sent=True,                        # guaranteed by scan filter
            completion_percent=lead["completion_pct"],
            current_section=lead.get("current_section"),
            last_activity_at=lead.get("last_activity_at"),
            hot_signal="HOT",                        # guaranteed by scan filter
            temperature_signal=None,
            temperature_score=None,
            reason_codes=[],
        )
        results.append({
            "lead_id":     rec["lead_id"],
            "event_type":  rec["event_type"],
            "priority":    rec["priority"],
            "reason_codes": rec["reason_codes"],
        })

    return results
