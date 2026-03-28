"""
execution/leads/match_or_create_lead_from_ghl_payload.py

Accepts a GHL-style inbound contact payload and resolves it to an internal
lead record, creating one if no match exists.

Matching follows the identity hierarchy defined in
directives/GHL_INTEGRATION.md:
    1. phone  — primary matcher
    2. email  — fallback
    3. name   — weak fallback; used ONLY when exactly one lead row in the
                local DB carries that name.  If zero or multiple rows share
                the name the match is skipped and a new lead is created.

No business logic lives here beyond the matching rules.
No network calls.  No datetime.now().  DB path is injected for testability.

Return shape
-----------
ok=True  (match or create succeeded):
    {
        "ok":          True,
        "app_lead_id": str,           # the resolved internal lead ID
        "matched_by":  str,           # "phone" | "email" | "name" | "created"
        "message":     str,
    }

ok=False  (validation rejected the payload before any DB mutation):
    {
        "ok":          False,
        "app_lead_id": None,
        "matched_by":  None,
        "message":     str,           # human-readable reason
    }

Idempotency note
----------------
Calling this function twice with the same payload containing a unique phone or
email will return the same app_lead_id both times (match wins on the second
call).  Name-only payloads are not idempotent: two calls with the same name on
an empty DB will create two leads because the second call sees one existing row
(the one just created) and matches it — unless the first call left more than
one row with that name, in which case a second lead is created.  For this reason
callers should always supply phone or email when available.
"""

import uuid

from execution.db.sqlite import connect, init_db
from execution.leads.upsert_lead import upsert_lead

# ---------------------------------------------------------------------------
# Normalization helpers — pure functions, no I/O
# ---------------------------------------------------------------------------

def _norm_phone(raw: str | None) -> str | None:
    """Strip surrounding whitespace only.

    Phone normalization is kept conservative because GHL may format numbers
    differently across regions (e.g. "+1 555 123 4567" vs "5551234567").
    Stripping only avoids silent mismatches caused by aggressive cleaning.
    A future version may apply E.164 normalization once a canonical format
    is confirmed for this deployment.
    """
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


def _norm_email(raw: str | None) -> str | None:
    """Strip and lowercase."""
    if raw is None:
        return None
    cleaned = str(raw).strip().lower()
    return cleaned or None


def _norm_name(raw: str | None) -> str | None:
    """Strip surrounding whitespace only."""
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


# ---------------------------------------------------------------------------
# DB query helpers — each returns a lead_id string or None
# ---------------------------------------------------------------------------

def _find_by_phone(conn, phone: str) -> str | None:
    """Return the lead_id of a matching lead, or None."""
    row = conn.execute(
        "SELECT id FROM leads WHERE phone = ? LIMIT 1",
        (phone,),
    ).fetchone()
    return row["id"] if row else None


def _find_by_email(conn, email: str) -> str | None:
    """Return the lead_id of a matching lead, or None."""
    row = conn.execute(
        "SELECT id FROM leads WHERE email = ? LIMIT 1",
        (email,),
    ).fetchone()
    return row["id"] if row else None


def _find_by_name_unique(conn, name: str) -> str | None:
    """Return the lead_id ONLY when exactly one lead carries this name.

    When zero or two-or-more leads share the name, returns None so the
    caller creates a new lead rather than risk a false match.
    """
    rows = conn.execute(
        "SELECT id FROM leads WHERE name = ?",
        (name,),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["id"]
    return None


# ---------------------------------------------------------------------------
# ghl_contact_id writer — targeted single-column update
# ---------------------------------------------------------------------------

def _write_ghl_contact_id(lead_id: str, ghl_contact_id: str, db_path: str | None) -> None:
    """Overwrite ghl_contact_id on an existing lead row.

    Uses a separate connection so it can be called after upsert_lead()
    closes its own connection.  Mirrors the pattern in sync_ghl_contact_id.py.
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        conn.execute(
            "UPDATE leads SET ghl_contact_id = ? WHERE id = ?",
            (ghl_contact_id, lead_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_or_create_lead_from_ghl_payload(
    payload: dict,
    db_path: str | None = None,
) -> dict:
    """Match an inbound GHL contact payload to an internal lead, or create one.

    Args:
        payload: Dict with any of:
                   ghl_contact_id (str | None)
                   phone          (str | None)
                   email          (str | None)
                   name           (str | None)
                 All keys are optional; missing keys behave the same as None.
                 At least one of phone, email, or name must be present and
                 non-empty, otherwise the function returns ok=False without
                 touching the database.
        db_path: Path to the SQLite file.  Defaults to tmp/app.db.

    Returns:
        See module docstring for the two possible return shapes.
    """
    # ------------------------------------------------------------------
    # 1. Extract and normalize identity fields.
    # ------------------------------------------------------------------
    if not isinstance(payload, dict):
        return {
            "ok":          False,
            "app_lead_id": None,
            "matched_by":  None,
            "message":     "payload must be a dict",
        }

    ghl_contact_id = _norm_phone(payload.get("ghl_contact_id"))  # treat as opaque string
    phone          = _norm_phone(payload.get("phone"))
    email          = _norm_email(payload.get("email"))
    name           = _norm_name(payload.get("name"))

    # ------------------------------------------------------------------
    # 2. Validate: require at least one identity field.
    # ------------------------------------------------------------------
    if phone is None and email is None and name is None:
        return {
            "ok":          False,
            "app_lead_id": None,
            "matched_by":  None,
            "message":     (
                "payload must include at least one of: phone, email, name. "
                "No database mutation performed."
            ),
        }

    # ------------------------------------------------------------------
    # 3. Match against existing leads (phone → email → name).
    #    A single DB connection is opened for all three queries so they
    #    run against a consistent snapshot.
    # ------------------------------------------------------------------
    conn = connect(db_path)
    try:
        init_db(conn)

        lead_id    = None
        matched_by = None

        if phone is not None:
            lead_id = _find_by_phone(conn, phone)
            if lead_id:
                matched_by = "phone"

        if lead_id is None and email is not None:
            lead_id = _find_by_email(conn, email)
            if lead_id:
                matched_by = "email"

        if lead_id is None and name is not None:
            lead_id = _find_by_name_unique(conn, name)
            if lead_id:
                matched_by = "name"

    finally:
        conn.close()

    # ------------------------------------------------------------------
    # 4. Create a new lead when no match was found.
    # ------------------------------------------------------------------
    if lead_id is None:
        lead_id    = f"GHL_{uuid.uuid4().hex}"
        matched_by = "created"

    # ------------------------------------------------------------------
    # 5. Upsert the lead (creates on first call; updates supplied fields
    #    on subsequent calls without touching un-supplied columns).
    # ------------------------------------------------------------------
    upsert_lead(
        lead_id,
        phone=phone,
        email=email,
        name=name,
        db_path=db_path,
    )

    # ------------------------------------------------------------------
    # 6. Store ghl_contact_id when the payload includes one.
    # ------------------------------------------------------------------
    if ghl_contact_id:
        _write_ghl_contact_id(lead_id, ghl_contact_id, db_path)

    # ------------------------------------------------------------------
    # 7. Return structured result.
    # ------------------------------------------------------------------
    action_word = "Matched" if matched_by != "created" else "Created"
    return {
        "ok":          True,
        "app_lead_id": lead_id,
        "matched_by":  matched_by,
        "message":     f"{action_word} lead via {matched_by}.",
    }
