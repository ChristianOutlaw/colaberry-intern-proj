"""
execution/leads/process_ghl_lead_intake.py

Steps 3 and 4 of the GHL handshake flow (directives/GHL_INTEGRATION.md):

    GHL → webhook → [Step 2 received] → [Step 3+4 — this function] → GHL Step 5

Responsibility: accept an inbound GHL contact payload, resolve it to an
internal lead, generate the unique course access link, and write the full
canonical GHL field payload back to GHL.

Composed from existing functions:
    1. match_or_create_lead_from_ghl_payload  — identity resolution (Step 3a)
    2. create_student_invite_from_payload     — course link generation (Step 3b)
    3. write_ghl_contact_fields               — full schema writeback (Step 4)

Idempotency
-----------
A stable invite_id of the form  GHL_INTAKE_{app_lead_id}  is passed to
create_student_invite_from_payload.  Calling this function twice with a
payload that resolves to the same lead will return the same invite token
on both calls (INSERT OR IGNORE behaviour in the invite table).

invite_generated_at
-------------------
The course_invites table does not currently store a generated-at timestamp
separate from sent_at.  This function captures the generation time at call
time (injected via the `now` parameter for deterministic testing) and
returns it in the response.

Writeback
---------
After the invite is generated, write_ghl_contact_fields is called with the
same now/base_url/db_path values.  If ghl_api_url is not configured, the
writeback is a safe no-op (writeback_ok=True, writeback_sent=False).  A
writeback failure is surfaced in the return dict — it is never hidden.

Return shapes
-------------
ok=True:
    {
        "ok":                    True,
        "app_lead_id":           str,
        "matched_by":            str,      # "phone"|"email"|"name"|"created"
        "course_link_generated": True,
        "invite_id":             str,
        "invite_generated_at":   str,      # ISO-8601 UTC string
        "writeback_attempted":   bool,
        "writeback_ok":          bool,
        "writeback_status_code": int|None,
        "writeback_message":     str,
        "message":               str,
    }

ok=False (identity validation failed before any DB mutation):
    {
        "ok":                    False,
        "app_lead_id":           None,
        "matched_by":            None,
        "course_link_generated": False,
        "writeback_attempted":   False,
        "writeback_ok":          False,
        "writeback_status_code": None,
        "writeback_message":     str,
        "message":               str,
    }
"""

from execution.ghl.write_ghl_contact_fields import write_ghl_contact_fields
from execution.leads.create_student_invite_from_payload import (
    create_student_invite_from_payload,
)
from execution.leads.match_or_create_lead_from_ghl_payload import (
    match_or_create_lead_from_ghl_payload,
)

# Stable invite_id prefix that scopes an invite to the GHL intake path.
# Using the lead ID makes the invite deterministic across repeated calls.
_INTAKE_INVITE_PREFIX = "GHL_INTAKE"


def process_ghl_lead_intake(
    payload: dict,
    *,
    now: str | None = None,
    base_url: str = "http://localhost:8501",
    ghl_api_url: str | None = None,
    ghl_lookup_url: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Resolve a GHL inbound payload, generate its course link, and write back to GHL.

    This covers Steps 3 and 4 of the GHL handshake.  It must be called after
    GHL has delivered the contact payload (Step 2).

    Args:
        payload:        GHL inbound contact dict.  Expected keys (all optional):
                          ghl_contact_id, phone, email, name.
                        At least one of phone, email, or name must be non-empty;
                        otherwise the function returns ok=False without mutating
                        the database.
        now:            ISO-8601 UTC string for the invite_generated_at timestamp
                        and all downstream time-dependent computations.
                        Must be provided by the caller — this function never calls
                        datetime.now() internally.  Raises ValueError when None.
        base_url:       Base URL of the student portal used when building the
                        invite link.  Defaults to http://localhost:8501.
        ghl_api_url:    URL of the GHL contact-update endpoint.  When None or
                        blank, the writeback is a safe no-op (writeback_ok=True,
                        no network call made).
        ghl_lookup_url: URL of the GHL contact-lookup endpoint, forwarded to
                        write_ghl_contact_fields for ghl_contact_id resolution
                        when not already stored on the lead.
        db_path:        Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        See module docstring for the two possible return shapes.
    """
    # ------------------------------------------------------------------
    # 1. Resolve invite_generated_at (injection boundary for tests).
    # ------------------------------------------------------------------
    if now is None:
        raise ValueError(
            "process_ghl_lead_intake: 'now' must be provided by the caller. "
            "Do not call datetime.now() inside execution functions."
        )
    invite_generated_at: str = now

    # ------------------------------------------------------------------
    # 2. Match or create the lead from the inbound payload.
    # ------------------------------------------------------------------
    match_result = match_or_create_lead_from_ghl_payload(payload, db_path=db_path)

    if not match_result["ok"]:
        return {
            "ok":                    False,
            "app_lead_id":           None,
            "matched_by":            None,
            "course_link_generated": False,
            "writeback_attempted":   False,
            "writeback_ok":          False,
            "writeback_status_code": None,
            "writeback_message":     "Writeback not attempted — lead matching failed.",
            "message":               match_result["message"],
        }

    app_lead_id = match_result["app_lead_id"]

    # ------------------------------------------------------------------
    # 3. Generate the unique course link using a stable invite_id.
    #
    #    A stable invite_id scoped to this lead makes the operation
    #    idempotent: calling process_ghl_lead_intake twice with a payload
    #    that resolves to the same lead returns the same token both times.
    #
    #    Identity fields (phone, email, name) are intentionally not passed
    #    here — the matcher in Step 2 already normalised and stored them.
    #    Re-passing raw payload values risks overwriting normalised data.
    # ------------------------------------------------------------------
    invite_id = f"{_INTAKE_INVITE_PREFIX}_{app_lead_id}"

    create_student_invite_from_payload(
        lead_id=app_lead_id,
        invite_id=invite_id,
        base_url=base_url,
        db_path=db_path,
    )

    # ------------------------------------------------------------------
    # 4. Write the full canonical GHL field payload back to GHL (Step 4).
    #
    #    writeback_attempted is True from this point — we have a valid lead
    #    and a generated invite, so writeback is always tried.
    #    A writeback failure is surfaced in the return dict; it is never
    #    silently swallowed.
    # ------------------------------------------------------------------
    writeback = write_ghl_contact_fields(
        app_lead_id,
        now=now,
        ghl_api_url=ghl_api_url,
        ghl_lookup_url=ghl_lookup_url,
        base_url=base_url,
        db_path=db_path,
    )

    # ------------------------------------------------------------------
    # 5. Return structured result.
    # ------------------------------------------------------------------
    return {
        "ok":                    True,
        "app_lead_id":           app_lead_id,
        "matched_by":            match_result["matched_by"],
        "course_link_generated": True,
        "invite_id":             invite_id,
        "invite_generated_at":   invite_generated_at,
        "writeback_attempted":   True,
        "writeback_ok":          writeback["ok"],
        "writeback_status_code": writeback["status_code"],
        "writeback_message":     writeback["message"],
        "message":               (
            f"Lead {match_result['matched_by']}. Course link generated."
        ),
    }
