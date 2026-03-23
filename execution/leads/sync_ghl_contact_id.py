"""
execution/leads/sync_ghl_contact_id.py

Resolves and stores ghl_contact_id for one lead by querying a configured
lookup endpoint with the lead's local identifiers (email first, phone second).

Responsibility: one targeted DB write — ghl_contact_id only.  No other lead
fields are touched.  No retries.  No bulk sync.  No GHL dispatcher.

Lookup endpoint contract
------------------------
GET <ghl_lookup_url>?email=<email>   (preferred)
GET <ghl_lookup_url>?phone=<phone>   (fallback when email is absent)

Expected response (JSON):
    Match found:    {"ghl_contact_id": "<non-empty string>"}
    No match:       {"ghl_contact_id": null}  or  {"ghl_contact_id": ""}

Non-2xx responses are raised (urllib.error.HTTPError / URLError).

Environment variable (for future runner wiring — not read here):
    GHL_LOOKUP_URL   URL of the GHL contact-lookup endpoint.

Return shapes
-------------
Lead not found in local DB:
    {"ok": False, "reason": "LEAD_NOT_FOUND"}

No lookup fields available (email and phone are both absent):
    {"ok": True, "updated": False, "reason": "NO_LOOKUP_FIELDS"}

Lookup URL not configured:
    {"ok": True, "updated": False, "reason": "NO_LOOKUP_URL"}

Endpoint returned no matching contact:
    {"ok": True, "updated": False, "reason": "NO_MATCH"}

Match found — ghl_contact_id written to leads:
    {"ok": True, "updated": True, "ghl_contact_id": "<id>"}

Raises
------
urllib.error.HTTPError:  Non-2xx response from the lookup endpoint.
urllib.error.URLError:   Network-level failure (timeout, DNS, refused).
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from execution.db.sqlite import connect, init_db

_CONTENT_TYPE = "application/json"


def sync_ghl_contact_id(
    lead_id: str,
    *,
    db_path: str | None = None,
    ghl_lookup_url: str | None = None,
    timeout: int = 5,
) -> dict:
    """Resolve and store ghl_contact_id for one lead.

    Reads the lead's email and phone from the local DB, queries the configured
    lookup endpoint, and writes ghl_contact_id back to leads when a match is
    returned.  Only ghl_contact_id is updated — no other columns are touched.

    Args:
        lead_id:        Local lead identifier (TEXT PRIMARY KEY in leads).
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
        ghl_lookup_url: URL of the GHL contact-lookup endpoint.
                        When None or blank the function returns a safe no-op
                        without making any network call.
                        Future env var: GHL_LOOKUP_URL.
        timeout:        Socket timeout in seconds.  Defaults to 5.

    Returns:
        See module docstring for all return shapes.

    Raises:
        urllib.error.HTTPError: Non-2xx HTTP response from the lookup endpoint.
        urllib.error.URLError:  Network failure (timeout, DNS, refused).
    """
    # ------------------------------------------------------------------
    # 1. Read the lead from the local DB.
    # ------------------------------------------------------------------
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT id, email, phone FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"ok": False, "reason": "LEAD_NOT_FOUND"}

    email = (row["email"] or "").strip() or None
    phone = (row["phone"] or "").strip() or None

    # ------------------------------------------------------------------
    # 2. Guard: require at least one lookup field.
    # ------------------------------------------------------------------
    if email is None and phone is None:
        return {"ok": True, "updated": False, "reason": "NO_LOOKUP_FIELDS"}

    # ------------------------------------------------------------------
    # 3. Guard: require a configured lookup URL.
    # ------------------------------------------------------------------
    if not ghl_lookup_url or not str(ghl_lookup_url).strip():
        return {"ok": True, "updated": False, "reason": "NO_LOOKUP_URL"}

    # ------------------------------------------------------------------
    # 4. Build the GET request — email first, phone as fallback.
    # ------------------------------------------------------------------
    lookup_field, lookup_value = ("email", email) if email else ("phone", phone)
    query_string = urllib.parse.urlencode({lookup_field: lookup_value})
    url = f"{str(ghl_lookup_url).rstrip('/')}?{query_string}"

    req = urllib.request.Request(url, method="GET")

    # ------------------------------------------------------------------
    # 5. Call the endpoint — non-2xx raises, network errors propagate.
    # ------------------------------------------------------------------
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()

    response = json.loads(body)
    ghl_contact_id = (response.get("ghl_contact_id") or "").strip() or None

    # ------------------------------------------------------------------
    # 6. No match returned.
    # ------------------------------------------------------------------
    if ghl_contact_id is None:
        return {"ok": True, "updated": False, "reason": "NO_MATCH"}

    # ------------------------------------------------------------------
    # 7. Match — write ghl_contact_id back to leads (one column only).
    # ------------------------------------------------------------------
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE leads SET ghl_contact_id = ? WHERE id = ?",
            (ghl_contact_id, lead_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "updated": True, "ghl_contact_id": ghl_contact_id}
