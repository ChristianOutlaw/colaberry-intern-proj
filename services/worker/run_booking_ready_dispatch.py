"""
services/worker/run_booking_ready_dispatch.py

MVP scan → dispatch worker for READY_FOR_BOOKING leads.

Finds leads that have completed the course and hold a HOT signal, then
pushes their canonical GHL payload via write_ghl_contact_fields.

A per-lead cooldown window (default 24 h) prevents re-dispatching leads
that were already successfully written within the current window.

No business logic, no scoring, no schema changes — pure orchestration.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from execution.db.sqlite import connect, init_db
from execution.ghl.write_ghl_contact_fields import write_ghl_contact_fields
from execution.scans.find_all_completed_leads import find_all_completed_leads

_DESTINATION = "GHL_WRITEBACK"
_STATUS_SENT = "SENT"

logger = logging.getLogger(__name__)


def _within_cooldown(
    lead_id: str,
    now: datetime,
    cooldown_hours: int,
    db_path: str | None,
) -> bool:
    """Return True when the lead has a SENT sync record within cooldown_hours of now.

    Queries sync_records for the most recent GHL_WRITEBACK row.  Only a SENT
    row within the cooldown window triggers a skip — FAILED and NEEDS_SYNC rows
    are always eligible for a fresh dispatch attempt.

    Args:
        lead_id:        Lead to check.
        now:            Reference UTC datetime for the cooldown calculation.
        cooldown_hours: Hours that must elapse after a SENT record before
                        re-dispatching.
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            """
            SELECT status, updated_at
            FROM sync_records
            WHERE lead_id = ? AND destination = ?
            """,
            (lead_id, _DESTINATION),
        ).fetchone()
    finally:
        conn.close()

    if row is None or row["status"] != _STATUS_SENT:
        return False

    try:
        sent_at = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False

    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (now_utc - sent_at) < timedelta(hours=cooldown_hours)


def run_booking_ready_dispatch(
    *,
    now: datetime,
    limit: int = 50,
    cooldown_hours: int = 24,
    ghl_api_url: str | None = None,
    ghl_lookup_url: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Scan for READY_FOR_BOOKING leads and dispatch GHL writebacks.

    Steps:
      1. ENV GUARD — abort immediately when ghl_api_url is absent.
      2. SCAN — find booking-ready leads via find_ready_for_booking_leads.
      3. COOLDOWN CHECK — skip leads dispatched successfully within cooldown_hours.
      4. DISPATCH — call write_ghl_contact_fields for each eligible lead.
      5. RETURN — summary dict with counts for dispatched / skipped / failed / cooldown_skipped.

    Args:
        now:            Reference UTC datetime for the scan and all time
                        calculations.  Must be provided by the caller — this
                        function never calls datetime.now() internally.
        limit:          Maximum leads to process per run. Defaults to 50.
        cooldown_hours: Hours after a SENT record before re-dispatching the
                        same lead. Defaults to 24.
        ghl_api_url:    GHL contact-update endpoint URL.  When None the
                        function returns immediately with dispatched=0.
        ghl_lookup_url: Optional GHL contact-lookup URL forwarded to
                        write_ghl_contact_fields for contact-ID resolution.
        db_path:        Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        dict with keys:
            ok, scan_name, dispatched, skipped, failed, cooldown_skipped,
            total_scanned, and optionally message.
    """
    # A. ENV GUARD — safe no-op when GHL is not configured.
    if not ghl_api_url:
        logger.info("run_booking_ready_dispatch: GHL_API_URL not set — no dispatches sent.")
        return {
            "ok":               True,
            "scan_name":        "booking_ready",
            "dispatched":       0,
            "skipped":          0,
            "failed":           0,
            "cooldown_skipped": 0,
            "message":          "GHL_API_URL not set",
        }

    # B. SCAN
    leads = find_all_completed_leads(limit=limit, db_path=db_path)
    logger.info("run_booking_ready_dispatch: found %d booking-ready lead(s).", len(leads))

    dispatched       = 0
    skipped          = 0
    failed           = 0
    cooldown_skipped = 0

    # C. LOOP LEADS
    for lead in leads:
        lead_id = lead["lead_id"]

        # 1. COOLDOWN CHECK
        if _within_cooldown(lead_id, now, cooldown_hours, db_path):
            logger.debug("Skipping %s — within %dh cooldown window.", lead_id, cooldown_hours)
            cooldown_skipped += 1
            continue

        # 2. DISPATCH
        result = write_ghl_contact_fields(
            lead_id,
            now=now.isoformat(),
            ghl_api_url=ghl_api_url,
            ghl_lookup_url=ghl_lookup_url,
            db_path=db_path,
        )

        # 3. HANDLE RESULT
        if result["ok"] and result["sent"]:
            logger.info("Dispatched %s → GHL (status %s).", lead_id, result.get("status_code"))
            dispatched += 1
        elif result["ok"]:
            logger.debug("Skipped %s — %s", lead_id, result.get("message"))
            skipped += 1
        else:
            logger.warning("Failed %s — %s", lead_id, result.get("message"))
            failed += 1

    # D. RETURN SUMMARY
    logger.info(
        "run_booking_ready_dispatch complete: dispatched=%d skipped=%d failed=%d cooldown_skipped=%d",
        dispatched, skipped, failed, cooldown_skipped,
    )
    return {
        "ok":               True,
        "scan_name":        "booking_ready",
        "dispatched":       dispatched,
        "skipped":          skipped,
        "failed":           failed,
        "cooldown_skipped": cooldown_skipped,
        "total_scanned":    len(leads),
    }
