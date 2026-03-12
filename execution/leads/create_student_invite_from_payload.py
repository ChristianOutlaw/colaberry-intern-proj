"""
execution/leads/create_student_invite_from_payload.py

Business-level intake helper that turns a lead payload into a student invite link.

Calls upsert_lead, then mark_course_invite_sent (which upserts the enrollment
internally), then reads back the token and derives the enrollment_id.

Returns a dict ready for the caller to send as an invite link.
"""

import secrets

from execution.db.sqlite import connect, init_db
from execution.leads.mark_course_invite_sent import mark_course_invite_sent
from execution.leads.upsert_lead import upsert_lead


def create_student_invite_from_payload(
    lead_id: str,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    course_id: str = "FREE_INTRO_AI_V0",
    invite_id: str | None = None,
    base_url: str = "http://localhost:8501",
    db_path: str | None = None,
) -> dict:
    """Upsert a lead, ensure enrollment, create an invite, and return all IDs + link.

    This is the primary intake path for turning an inbound lead payload into a
    student invite link.  Every sub-operation is idempotent: re-calling with
    the same invite_id will skip the INSERT and return the existing token.

    Args:
        lead_id:   Stable unique identifier for the lead.
        name:      Optional display name; updates an existing lead when supplied.
        email:     Optional email; updates an existing lead when supplied.
        phone:     Optional phone; updates an existing lead when supplied.
        course_id: Course to enroll in.  Defaults to 'FREE_INTRO_AI_V0'.
        invite_id: Stable unique identifier for this invite.  A random ID is
                   generated when omitted; supply a stable ID for idempotency.
        base_url:  Base URL of the student portal, without trailing slash.
                   Defaults to 'http://localhost:8501'.
        db_path:   Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        dict with keys:
            lead_id       — the lead identifier (echoed from input)
            course_id     — the course identifier (echoed from input)
            enrollment_id — stable ENR_{lead_id}_{course_id} identifier
            invite_id     — the invite identifier (generated or echoed)
            token         — the URL-safe token stored on the invite row
            invite_link   — f"{base_url}/?token={token}"

    Raises:
        ValueError: If lead_id is not a non-empty string.
    """
    if not isinstance(lead_id, str) or not lead_id.strip():
        raise ValueError(
            f"create_student_invite_from_payload: 'lead_id' must be a non-empty string, "
            f"got {lead_id!r}"
        )

    if invite_id is None:
        invite_id = f"INV_{lead_id}_{secrets.token_urlsafe(8)}"

    # Step 1: Ensure the lead row exists; update any supplied optional fields.
    upsert_lead(lead_id, phone=phone, email=email, name=name, db_path=db_path)

    # Step 2: Create the invite.  mark_course_invite_sent calls upsert_enrollment
    #         internally, so enrollment is guaranteed before this returns.
    mark_course_invite_sent(
        invite_id,
        lead_id,
        course_id=course_id,
        db_path=db_path,
    )

    # Step 3: Read the token back from the invite row (generated inside mark_course_invite_sent).
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT token FROM course_invites WHERE id = ?",
            (invite_id,),
        ).fetchone()
    finally:
        conn.close()

    token = row["token"]
    enrollment_id = f"ENR_{lead_id}_{course_id}"

    return {
        "lead_id": lead_id,
        "course_id": course_id,
        "enrollment_id": enrollment_id,
        "invite_id": invite_id,
        "token": token,
        "invite_link": f"{base_url}/?token={token}",
    }
