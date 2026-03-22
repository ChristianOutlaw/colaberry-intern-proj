"""
execution/events/consume_cory_recommendation.py

Consumes a parsed cory_recommendation payload and writes at most one
deterministic outbox record to sync_records.

Responsibility: apply the Cory decision table, then write one idempotent
sync_records row for actionable outcomes.  No network calls.  No CRM
integration.  Pure local SQLite write reusing the existing outbox pattern.

Consumer Decision Table
-----------------------
event_type              priority gate    action
----------------------  ---------------  --------------------------------
HOT_LEAD_BOOKING        HIGH / MEDIUM    write NEEDS_SYNC → CORY_BOOKING
SEND_INVITE             HIGH / MEDIUM    write NEEDS_SYNC → CORY_INVITE
NUDGE_PROGRESS          HIGH / MEDIUM    write NEEDS_SYNC → CORY_NUDGE
REENGAGE_STALLED_LEAD   HIGH / MEDIUM    write NEEDS_SYNC → CORY_REENGAGE
NUDGE_START_CLASS       any              log-only, no write
NO_ACTION               any              ignore, no write
(any queued type)       LOW              no write (LOW_PRIORITY gate)
(any queued type)       channel=NONE     no write (NO_CHANNEL gate)

Idempotency note
----------------
sync_records has UNIQUE (lead_id, destination, status).  Because each
event_type maps to exactly one destination, this acts as a dedup key of
(lead_id, event_type, NEEDS_SYNC).  A second call for the same event_type
while a NEEDS_SYNC record already exists updates updated_at without
inserting a duplicate.  Section-level dedup (one pending record per section,
not just per event_type) would require a schema change — out of scope for v1.
"""

import logging

from execution.db.sqlite import connect, init_db

logger = logging.getLogger(__name__)

_STATUS_NEEDS_SYNC = "NEEDS_SYNC"

# Queued event types and their outbox destinations.
_QUEUED_DESTINATIONS: dict[str, str] = {
    "HOT_LEAD_BOOKING": "CORY_BOOKING",
    "SEND_INVITE": "CORY_INVITE",
    "NUDGE_PROGRESS": "CORY_NUDGE",
    "REENGAGE_STALLED_LEAD": "CORY_REENGAGE",
}

# Valid event types that produce no DB write.
_NO_WRITE_TYPES: frozenset[str] = frozenset({"NUDGE_START_CLASS", "NO_ACTION"})

# All known event types (used for validation).
_ALL_EVENT_TYPES: frozenset[str] = frozenset(_QUEUED_DESTINATIONS) | _NO_WRITE_TYPES

# Only these priorities allow a write.
_ACTIONABLE_PRIORITIES: frozenset[str] = frozenset({"HIGH", "MEDIUM"})

_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "lead_id", "section", "event_type",
    "priority", "recommended_channel", "reason_codes", "built_at",
})


def consume_cory_recommendation(
    data: dict,
    *,
    db_path: str | None = None,
) -> dict:
    """Consume a parsed cory_recommendation payload; write at most one outbox record.

    Args:
        data:    Parsed payload dict from the ``cory_recommendation`` event.
                 Required keys: lead_id, section, event_type, priority,
                 recommended_channel, reason_codes, built_at.
        db_path: Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        One of three shapes:

        Validation failure (invalid input):
            raises ValueError with a clear message

        Decided not to write (type, priority, or channel gate):
            {"ok": True, "wrote": False, "reason": "<GATE>"}
            where GATE is one of: NO_ACTION, NUDGE_START_CLASS,
            LOW_PRIORITY, NO_CHANNEL.

        Wrote or refreshed one sync_records row:
            {"ok": True, "wrote": True, "destination": "CORY_*"}

        Lead does not exist in the database (FK constraint):
            {"ok": False, "reason": "LEAD_NOT_FOUND"}
    """
    # ------------------------------------------------------------------
    # 1. Validate required fields.
    # ------------------------------------------------------------------
    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Missing required fields: {sorted(missing)}")

    lead_id = str(data["lead_id"]).strip()
    if not lead_id:
        raise ValueError("lead_id must be a non-empty string")

    event_type = data["event_type"]
    if event_type not in _ALL_EVENT_TYPES:
        raise ValueError(
            f"Unknown event_type {event_type!r}. "
            f"Expected one of: {sorted(_ALL_EVENT_TYPES)}"
        )

    priority = data["priority"]
    recommended_channel = (data.get("recommended_channel") or "").strip().upper()
    built_at = data["built_at"]  # ISO-8601 UTC; used as the sync record timestamp.

    # ------------------------------------------------------------------
    # 2. Apply decision table — no-write gates.
    # ------------------------------------------------------------------
    if event_type in _NO_WRITE_TYPES:
        logger.debug("consume_cory_recommendation: no-write event_type=%s", event_type)
        return {"ok": True, "wrote": False, "reason": event_type}

    if priority not in _ACTIONABLE_PRIORITIES:
        # LOW (or any unknown priority value) — skip.
        return {"ok": True, "wrote": False, "reason": "LOW_PRIORITY"}

    if not recommended_channel or recommended_channel == "NONE":
        return {"ok": True, "wrote": False, "reason": "NO_CHANNEL"}

    # ------------------------------------------------------------------
    # 3. Determine outbox destination.
    # ------------------------------------------------------------------
    destination = _QUEUED_DESTINATIONS[event_type]

    # ------------------------------------------------------------------
    # 4. Upsert sync_records — idempotent by (lead_id, destination, status).
    #    Mirrors the pattern in execution/leads/write_hot_lead_sync_record.py.
    # ------------------------------------------------------------------
    conn = connect(db_path)
    try:
        init_db(conn)

        existing = conn.execute(
            """
            SELECT id FROM sync_records
            WHERE lead_id = ? AND destination = ? AND status = ?
            """,
            (lead_id, destination, _STATUS_NEEDS_SYNC),
        ).fetchone()

        if existing is not None:
            # Refresh timestamp on the existing pending record; no new row.
            conn.execute(
                """
                UPDATE sync_records
                SET updated_at = ?
                WHERE lead_id = ? AND destination = ? AND status = ?
                """,
                (built_at, lead_id, destination, _STATUS_NEEDS_SYNC),
            )
            conn.commit()
            return {"ok": True, "wrote": True, "destination": destination}

        try:
            conn.execute(
                """
                INSERT INTO sync_records
                    (lead_id, destination, status, reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (lead_id, destination, _STATUS_NEEDS_SYNC, event_type, built_at, built_at),
            )
            conn.commit()
        except Exception as exc:
            if "FOREIGN KEY" in str(exc).upper():
                return {"ok": False, "reason": "LEAD_NOT_FOUND"}
            raise

        return {"ok": True, "wrote": True, "destination": destination}

    finally:
        conn.close()
