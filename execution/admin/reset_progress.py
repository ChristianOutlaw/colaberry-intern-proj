"""
execution/admin/reset_progress.py

Dev/test harness — Operation 2: Reset Progress.

Deletes all progress_events rows for a given lead_id and, when requested,
all course_invites rows as well.  The lead row in the leads table is never
deleted by this operation.

THIS MODULE IS DEV-ONLY.  It must never be imported or called in production.
See directives/ADMIN_TEST_MODE.md for the full contract.

No business logic lives here; all persistence uses execution/db/sqlite helpers.
"""

from execution.db.sqlite import connect, init_db


class OperationNotConfirmedError(Exception):
    """Raised when a destructive harness operation is called without confirm=True."""


def reset_progress(
    *,
    lead_id: str,
    reset_invite: bool = False,
    confirm: bool = False,
    db_path: str | None = None,
) -> dict:
    """Delete progress events (and optionally course invites) for a lead.

    Directive reference: ADMIN_TEST_MODE.md § Operation 2 — Reset Progress.

    Args:
        lead_id:      ID of the lead whose progress rows are to be deleted.
                      Whitespace is trimmed before use.
        reset_invite: If True, also deletes all course_invites rows for this
                      lead.  Default False.
        confirm:      Must be True or OperationNotConfirmedError is raised.
                      This gate is mandatory for any destructive operation.
        db_path:      Path to the SQLite file.  Defaults to tmp/app.db.
                      Tests must always supply an explicit isolated path.

    Returns:
        dict with the following keys:
            ok              (bool)  True on success, False on a soft failure.
            message         (str)   Human-readable outcome description.
            events_deleted  (int)   Number of progress_events rows deleted.
                                    Present only when ok=True.
            invites_cleared (bool)  True when course_invites were also deleted.
                                    Present only when ok=True.

    Raises:
        OperationNotConfirmedError: When confirm is not True (checked before
                                    any database access).
    """
    lead_id = lead_id.strip()
    if not lead_id:
        return {"ok": False, "message": "Lead ID is required."}

    if not confirm:
        raise OperationNotConfirmedError("Reset requires confirm=True.")

    conn = connect(db_path)
    try:
        init_db(conn)

        # Guard: lead must exist before attempting any delete.
        existing = conn.execute(
            "SELECT id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()

        if existing is None:
            return {
                "ok": False,
                "message": f"Lead {lead_id} not found. Nothing deleted.",
            }

        # Delete progress events and capture the row count.
        cursor = conn.execute(
            "DELETE FROM progress_events WHERE lead_id = ?", (lead_id,)
        )
        events_deleted: int = cursor.rowcount

        # Optionally delete course invites.
        invites_cleared = False
        if reset_invite:
            conn.execute(
                "DELETE FROM course_invites WHERE lead_id = ?", (lead_id,)
            )
            invites_cleared = True

        conn.commit()
    finally:
        conn.close()

    message = (
        f"Progress reset for lead {lead_id}. {events_deleted} event(s) deleted."
    )
    if invites_cleared:
        message += " Invite record(s) cleared."

    return {
        "ok": True,
        "message": message,
        "events_deleted": events_deleted,
        "invites_cleared": invites_cleared,
    }
