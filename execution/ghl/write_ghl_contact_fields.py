"""
execution/ghl/write_ghl_contact_fields.py

Step 4 of the GHL handshake flow (directives/GHL_INTEGRATION.md):

    … → [Step 3 complete] → [Step 4 — this function] → GHL → Step 5 …

Responsibility: build the full canonical GHL custom-field payload for one lead
(via build_ghl_full_field_payload) and POST it to the configured GHL contact-
update endpoint.  No business logic lives here — only payload construction,
contact-ID resolution, and HTTP transport.

GHL contact resolution
----------------------
1. Preferred path — stored ghl_contact_id on the leads row (already embedded in
   the built payload, so no extra DB read is needed).
2. Fallback path — if ghl_contact_id is absent AND a ghl_lookup_url is provided,
   sync_ghl_contact_id is called to resolve and store it before sending.
3. If neither path yields a ghl_contact_id, no HTTP request is made and the
   function returns ok=False.

Safe no-op
----------
When ghl_api_url is None or blank the function returns ok=True, sent=False with
reason="NO_URL".  This mirrors the pattern in dispatch_cory_ghl so callers can
wire configuration gradually without breaking tests or dev environments.

Return shape
------------
    {
        "ok":             bool,
        "app_lead_id":    str,
        "ghl_contact_id": str | None,
        "sent":           bool,
        "status_code":    int | None,
        "message":        str,
    }
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime

from execution.db.sqlite import connect, init_db
from execution.ghl.build_ghl_full_field_payload import build_ghl_full_field_payload
from execution.leads.mark_sync_record_failed import mark_sync_record_failed
from execution.leads.mark_sync_record_sent import mark_sync_record_sent
from execution.leads.sync_ghl_contact_id import sync_ghl_contact_id

_CONTENT_TYPE          = "application/json"
_DESTINATION_WRITEBACK = "GHL_WRITEBACK"
_STATUS_NEEDS_SYNC     = "NEEDS_SYNC"


def write_ghl_contact_fields(
    app_lead_id: str,
    *,
    now: str,
    ghl_api_url: str | None = None,
    ghl_lookup_url: str | None = None,
    base_url: str = "http://localhost:8501",
    db_path: str | None = None,
    timeout: int = 10,
) -> dict:
    """Build and POST the full canonical GHL field payload for one lead.

    This is Step 4 of the GHL handshake.  It must be called after Step 3
    (process_ghl_lead_intake) has completed and the course link is stored.

    Args:
        app_lead_id:    Internal lead identifier.
        now:            ISO-8601 UTC string for all time-dependent derivations.
                        Must be provided by the caller — this function never
                        calls datetime.now() internally.  Raises ValueError
                        when None.
        ghl_api_url:    URL of the GHL contact-update endpoint.  When None or
                        blank the function returns a safe no-op (sent=False,
                        ok=True) without making any network call.
        ghl_lookup_url: URL of the GHL contact-lookup endpoint used by
                        sync_ghl_contact_id when ghl_contact_id is not yet
                        stored on the lead.  Optional.
        base_url:       Base URL of the student portal, forwarded to
                        build_ghl_full_field_payload for course_link
                        construction.  Defaults to http://localhost:8501.
        db_path:        Path to the SQLite file; defaults to tmp/app.db.
        timeout:        Socket timeout in seconds for the outbound HTTP request.
                        Defaults to 10.

    Returns:
        dict — see module docstring for the return shape.
    """
    # ------------------------------------------------------------------
    # 0. Determinism guard.
    # ------------------------------------------------------------------
    if now is None:
        raise ValueError(
            "write_ghl_contact_fields: 'now' must be provided by the caller. "
            "Do not call datetime.now() inside execution functions."
        )

    # ------------------------------------------------------------------
    # 1. Build the full canonical payload.
    #    This reads all required DB rows and derives all field values.
    # ------------------------------------------------------------------
    build_result = build_ghl_full_field_payload(
        app_lead_id,
        now=now,
        base_url=base_url,
        db_path=db_path,
    )

    if not build_result["ok"]:
        return {
            "ok":             False,
            "app_lead_id":    app_lead_id,
            "ghl_contact_id": None,
            "sent":           False,
            "status_code":    None,
            "message":        build_result["message"],
        }

    field_payload = build_result["payload"]

    # ------------------------------------------------------------------
    # 2. Guard: block send when course_link has not been generated yet.
    #
    #    invite_ready is derived from course_link in build_ghl_full_field_payload
    #    (invite_ready = course_link is not None).  Sending to GHL before a
    #    course_link exists would write invite_ready=False with a null link —
    #    correct per the schema but premature: the directive requires the full
    #    handshake (link generated → writeback → GHL sends) to complete in order.
    #    Blocking here enforces that constraint at the transport boundary.
    # ------------------------------------------------------------------
    if not field_payload.get("course_link"):
        return {
            "ok":             False,
            "app_lead_id":    app_lead_id,
            "ghl_contact_id": None,
            "sent":           False,
            "status_code":    None,
            "message":        "Writeback blocked: course_link not yet generated for this lead.",
        }

    # ------------------------------------------------------------------
    # 3. Resolve ghl_contact_id.
    #
    #    Preferred: already stored on the lead (present in the built payload).
    #    Fallback:  call sync_ghl_contact_id when a lookup URL is configured.
    #               sync_ghl_contact_id writes the resolved ID back to the DB;
    #               we read it from its return value.
    # ------------------------------------------------------------------
    ghl_contact_id: str | None = field_payload.get("ghl_contact_id") or None

    if ghl_contact_id is None and ghl_lookup_url:
        sync_result = sync_ghl_contact_id(
            app_lead_id,
            db_path=db_path,
            ghl_lookup_url=ghl_lookup_url,
            timeout=timeout,
        )
        if sync_result.get("ok") and sync_result.get("updated"):
            ghl_contact_id = sync_result.get("ghl_contact_id") or None

    if ghl_contact_id is None:
        return {
            "ok":             False,
            "app_lead_id":    app_lead_id,
            "ghl_contact_id": None,
            "sent":           False,
            "status_code":    None,
            "message":        (
                "No ghl_contact_id available for this lead. "
                "Provide a ghl_lookup_url or ensure the lead has been matched "
                "via the inbound GHL webhook."
            ),
        }

    # ------------------------------------------------------------------
    # 3. Safe no-op when no API URL is configured.
    # ------------------------------------------------------------------
    if not ghl_api_url or not str(ghl_api_url).strip():
        return {
            "ok":             True,
            "app_lead_id":    app_lead_id,
            "ghl_contact_id": ghl_contact_id,
            "sent":           False,
            "status_code":    None,
            "message":        "No ghl_api_url configured — payload built but not sent.",
        }

    # ------------------------------------------------------------------
    # 4. Resolve GHL API key — fail explicitly if absent.
    #
    #    The key must be supplied at runtime via the GHL_API_KEY environment
    #    variable.  It must never be hardcoded.  If it is missing, we return
    #    an error rather than silently sending an unauthenticated request.
    # ------------------------------------------------------------------
    api_key = os.environ.get("GHL_API_KEY")
    if not api_key:
        return {
            "ok":             False,
            "app_lead_id":    app_lead_id,
            "ghl_contact_id": ghl_contact_id,
            "sent":           False,
            "status_code":    None,
            "message":        "GHL_API_KEY environment variable is not set.",
        }

    # ------------------------------------------------------------------
    # 5. Write NEEDS_SYNC outbox record before the HTTP attempt.
    #
    #    DELETE all prior rows for (app_lead_id, GHL_WRITEBACK) first so
    #    that mark_sync_record_failed — which has no record_id targeted path
    #    and returns {changed: False} when a FAILED row already exists —
    #    always finds a clean slate.  This makes retries safe.
    # ------------------------------------------------------------------
    now_dt = datetime.fromisoformat(now)
    _conn = connect(db_path)
    try:
        init_db(_conn)
        _conn.execute(
            "DELETE FROM sync_records WHERE lead_id = ? AND destination = ?",
            (app_lead_id, _DESTINATION_WRITEBACK),
        )
        _conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (app_lead_id, _DESTINATION_WRITEBACK, _STATUS_NEEDS_SYNC, now, now),
        )
        _conn.commit()
        _sync_record_id = _conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]
    finally:
        _conn.close()

    # ------------------------------------------------------------------
    # 6. POST the canonical payload to GHL.
    #
    #    The body wraps the field payload with the target ghl_contact_id so
    #    the receiving endpoint always has full context in a single request.
    # ------------------------------------------------------------------
    outbound = {
        "ghl_contact_id": ghl_contact_id,
        "fields":         field_payload,
        "sent_at":        now,
    }

    body = json.dumps(outbound).encode("utf-8")
    req = urllib.request.Request(
        url=str(ghl_api_url).strip(),
        data=body,
        headers={
            "Authorization":  f"Bearer {api_key}",
            "Content-Type":   _CONTENT_TYPE,
            "Content-Length": str(len(body)),
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            mark_sync_record_sent(
                app_lead_id,
                now_dt,
                destination=_DESTINATION_WRITEBACK,
                db_path=db_path,
                record_id=_sync_record_id,
            )
            return {
                "ok":             True,
                "app_lead_id":    app_lead_id,
                "ghl_contact_id": ghl_contact_id,
                "sent":           True,
                "status_code":    resp.status,
                "message":        "GHL contact fields updated successfully.",
            }

    except urllib.error.HTTPError as exc:
        mark_sync_record_failed(
            app_lead_id,
            now_dt,
            destination=_DESTINATION_WRITEBACK,
            error=f"HTTP {exc.code}: {exc.reason}",
            db_path=db_path,
        )
        return {
            "ok":             False,
            "app_lead_id":    app_lead_id,
            "ghl_contact_id": ghl_contact_id,
            "sent":           False,
            "status_code":    exc.code,
            "message":        f"GHL API returned HTTP {exc.code}: {exc.reason}",
        }

    except urllib.error.URLError as exc:
        mark_sync_record_failed(
            app_lead_id,
            now_dt,
            destination=_DESTINATION_WRITEBACK,
            error=f"Network error: {exc.reason}",
            db_path=db_path,
        )
        return {
            "ok":             False,
            "app_lead_id":    app_lead_id,
            "ghl_contact_id": ghl_contact_id,
            "sent":           False,
            "status_code":    None,
            "message":        f"Network error contacting GHL API: {exc.reason}",
        }
