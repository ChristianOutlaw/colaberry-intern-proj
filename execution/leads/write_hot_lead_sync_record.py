"""
execution/leads/write_hot_lead_sync_record.py

Writes a NEEDS_SYNC outbox record for a lead that is currently HOT.

Responsibility: one idempotent write to sync_records when the lead's
HotLeadSignal evaluates to True.  No network calls.  No GHL integration.
Pure local SQLite write.

Rule specification: directives/HOT_LEAD_SIGNAL.md
Outbox schema:      execution/db/sqlite.py (sync_records table)
"""

from datetime import datetime

from execution.db.sqlite import connect, init_db
from execution.leads.compute_hot_lead_signal import compute_hot_lead_signal

_DESTINATION = "GHL"
_STATUS_NEEDS_SYNC = "NEEDS_SYNC"


def write_hot_lead_sync_record(
    lead_id: str,
    now: datetime,
    db_path: str | None = None,
) -> dict:
    """Ensure a NEEDS_SYNC outbox row exists for a HOT lead.

    Evaluates the lead's HotLeadSignal using the injected *now* (never calls
    datetime.now() internally).  Writes to sync_records only when the signal
    is True.  The write is idempotent: a second call for the same
    (lead_id, "GHL", "NEEDS_SYNC") updates updated_at but does not insert a
    duplicate row.

    Args:
        lead_id: Stable unique identifier for the lead.
        now:     Current UTC datetime, injected by the caller.  Used both for
                 HotLeadSignal evaluation and for sync_records timestamps.
        db_path: Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        One of three result shapes:

        Lead does not exist in the database:
            {"ok": False, "reason": "LEAD_NOT_FOUND"}

        Lead exists but is not currently HOT:
            {"ok": True, "wrote": False, "reason": "<REASON_CODE>"}
            where reason is the HotLeadSignal gate that failed
            (e.g. "NOT_INVITED", "COMPLETION_BELOW_THRESHOLD", "ACTIVITY_STALE").

        Lead is HOT — row written (inserted or updated):
            {"ok": True, "wrote": True, "sync_status": "NEEDS_SYNC"}
    """
    now_str = now.isoformat()

    conn = connect(db_path)
    try:
        init_db(conn)

        # ------------------------------------------------------------------
        # 1. Verify lead exists.
        # ------------------------------------------------------------------
        lead = conn.execute(
            "SELECT id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()

        if lead is None:
            return {"ok": False, "reason": "LEAD_NOT_FOUND"}

        # ------------------------------------------------------------------
        # 2. Gather inputs for HotLeadSignal evaluation.
        # ------------------------------------------------------------------
        invite_count = conn.execute(
            "SELECT COUNT(*) FROM course_invites WHERE lead_id = ?", (lead_id,)
        ).fetchone()[0]

        cs = conn.execute(
            "SELECT completion_pct, last_activity_at FROM course_state WHERE lead_id = ?",
            (lead_id,),
        ).fetchone()

        last_activity_time = None
        if cs is not None and cs["last_activity_at"] is not None:
            raw = cs["last_activity_at"].replace("Z", "+00:00")
            last_activity_time = datetime.fromisoformat(raw)

        # ------------------------------------------------------------------
        # 3. Evaluate HotLeadSignal with injected now.
        #    compute_hot_lead_signal is a pure function (no I/O, no datetime.now()).
        # ------------------------------------------------------------------
        hot_result = compute_hot_lead_signal(
            invite_sent=invite_count > 0,
            completion_percent=cs["completion_pct"] if cs is not None else None,
            last_activity_time=last_activity_time,
            now=now,
        )

        if not hot_result["hot"]:
            return {
                "ok": True,
                "wrote": False,
                "reason": hot_result["reasons"][0],
            }

        # ------------------------------------------------------------------
        # 4. Upsert sync_records row — idempotent by (lead_id, destination, status).
        # ------------------------------------------------------------------
        reason = hot_result["reasons"][0]  # "HOT_ENGAGED"

        existing = conn.execute(
            """
            SELECT id FROM sync_records
            WHERE lead_id = ? AND destination = ? AND status = ?
            """,
            (lead_id, _DESTINATION, _STATUS_NEEDS_SYNC),
        ).fetchone()

        if existing is not None:
            conn.execute(
                """
                UPDATE sync_records
                SET updated_at = ?
                WHERE lead_id = ? AND destination = ? AND status = ?
                """,
                (now_str, lead_id, _DESTINATION, _STATUS_NEEDS_SYNC),
            )
        else:
            conn.execute(
                """
                INSERT INTO sync_records
                    (lead_id, destination, status, reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (lead_id, _DESTINATION, _STATUS_NEEDS_SYNC, reason, now_str, now_str),
            )

        conn.commit()
        return {"ok": True, "wrote": True, "sync_status": _STATUS_NEEDS_SYNC}

    finally:
        conn.close()
