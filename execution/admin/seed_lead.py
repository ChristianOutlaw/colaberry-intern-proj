"""
execution/admin/seed_lead.py

Dev/test harness — Operation 1: Seed Lead.

Creates or updates a lead row and, when requested, records a course invite.
Returns a structured result dict indicating whether the lead was created or
updated and whether an invite was recorded.

THIS MODULE IS DEV-ONLY.  It must never be imported or called in production.
See directives/ADMIN_TEST_MODE.md for the full contract.

No business logic lives here.  No direct SQL.  All persistence is delegated
to existing execution functions:
  - execution.leads.get_lead_status   (existence check only)
  - execution.leads.upsert_lead
  - execution.leads.mark_course_invite_sent
"""

from execution.leads.get_lead_status import get_lead_status
from execution.leads.mark_course_invite_sent import mark_course_invite_sent
from execution.leads.upsert_lead import upsert_lead


def seed_lead(
    *,
    lead_id: str,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    mark_invite_sent: bool = False,
    invite_id: str | None = None,
    sent_at: str | None = None,
    channel: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Create or update a lead and optionally record a course invite.

    Directive reference: ADMIN_TEST_MODE.md § Operation 1 — Seed Lead.

    Both upsert_lead and mark_course_invite_sent are idempotent, so calling
    seed_lead twice with the same arguments produces the same final DB state.

    Args:
        lead_id:          Stable unique identifier for the lead.
                          Whitespace is trimmed before use.
        name:             Optional display name.
        phone:            Optional phone number.
        email:            Optional email address.
        mark_invite_sent: If True, also call mark_course_invite_sent.
                          Default False.
        invite_id:        Required when mark_invite_sent=True.
                          Raises ValueError if omitted in that case.
        sent_at:          ISO 8601 UTC timestamp for the invite.
                          Defaults to current UTC inside mark_course_invite_sent
                          when None.
        channel:          Delivery channel (e.g. "sms", "email").
        db_path:          Path to the SQLite file.  Defaults to tmp/app.db.
                          Tests must always supply an explicit isolated path.

    Returns:
        dict with keys:
            ok       (bool)  Always True on success.
            message  (str)   Human-readable outcome, e.g.:
                             "Lead L1 created."
                             "Lead L1 updated."
                             "Lead L1 created. Invite recorded."
                             "Lead ID is required."  (ok=False)

    Raises:
        ValueError: When mark_invite_sent=True but invite_id is not provided.
    """
    lead_id = lead_id.strip()
    if not lead_id:
        return {"ok": False, "message": "Lead ID is required."}

    if mark_invite_sent and not invite_id:
        raise ValueError(
            "invite_id is required when mark_invite_sent=True."
        )

    # Determine whether this is a create or an update *before* the write so
    # the message accurately reflects the operation that occurred.
    status_before = get_lead_status(lead_id, db_path=db_path)
    is_new = not status_before["lead_exists"]

    upsert_lead(lead_id, name=name, phone=phone, email=email, db_path=db_path)

    invite_recorded = False
    if mark_invite_sent:
        mark_course_invite_sent(
            invite_id,
            lead_id,
            sent_at=sent_at,
            channel=channel,
            db_path=db_path,
        )
        invite_recorded = True

    action = "created" if is_new else "updated"
    message = f"Lead {lead_id} {action}."
    if invite_recorded:
        message += " Invite recorded."

    return {"ok": True, "message": message}
