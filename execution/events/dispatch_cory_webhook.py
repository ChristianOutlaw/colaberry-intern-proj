"""
execution/events/dispatch_cory_webhook.py

Pure webhook dispatcher for Cory sync records.

Responsibility: accept one sync_records row dict, POST a compact JSON payload
to a configured webhook URL, and return a result dict.  No DB reads.  No
datetime.now().  Safe no-op when the URL is absent or blank.

Interface mirrors dispatch_cory_log_sink — same row_data contract, injected
now, raises on failure.  Future dispatchers follow this same pattern.

Return shapes:
    No URL configured (webhook_url is None or blank):
        {"dispatched": False, "mode": "webhook", "reason": "NO_URL"}

    HTTP 2xx success:
        {"dispatched": True, "mode": "webhook", "http_status": <code>}

Raises:
    ValueError:               Missing or invalid row_data fields.
    urllib.error.HTTPError:   Non-2xx HTTP response from the webhook endpoint.
    urllib.error.URLError:    Network-level failure (DNS, timeout, refused).
"""

import json
import urllib.error
import urllib.request

_CONTENT_TYPE = "application/json"
_REQUIRED_FIELDS = frozenset({"lead_id", "destination", "reason", "created_at"})


def dispatch_cory_webhook(
    row_data: dict,
    *,
    webhook_url: str | None = None,
    now: str,
    timeout: int = 5,
) -> dict:
    """POST one Cory sync-record payload to a webhook URL.

    No DB reads.  No datetime.now().  now must be injected by the caller.
    Non-2xx responses are raised, never silently swallowed.

    Args:
        row_data:    Dict representing one sync_records row.  Required keys:
                     lead_id, destination, reason, created_at, and either
                     'id' or 'sync_record_id'.
        webhook_url: URL to POST to.  When None or blank the function returns
                     a safe no-op result without making any network call.
        now:         ISO-8601 UTC string for the dispatch timestamp.
                     Injected by the caller; never derived internally.
        timeout:     Socket timeout in seconds.  Defaults to 5.

    Returns:
        No URL:   {"dispatched": False, "mode": "webhook", "reason": "NO_URL"}
        Success:  {"dispatched": True,  "mode": "webhook", "http_status": <code>}

    Raises:
        ValueError:             row_data is missing required fields.
        urllib.error.HTTPError: Webhook returned a non-2xx status code.
        urllib.error.URLError:  Network failure (timeout, DNS, connection refused).
    """
    # ------------------------------------------------------------------
    # 1. Validate row_data.
    # ------------------------------------------------------------------
    missing = _REQUIRED_FIELDS - set(row_data.keys())
    if missing:
        raise ValueError(
            f"dispatch_cory_webhook: missing required row_data fields: {sorted(missing)}"
        )

    sync_record_id = row_data.get("sync_record_id") or row_data.get("id")
    if sync_record_id is None:
        raise ValueError(
            "dispatch_cory_webhook: row_data must contain 'id' or 'sync_record_id'"
        )

    lead_id     = str(row_data["lead_id"]).strip()
    destination = str(row_data["destination"]).strip()
    reason      = str(row_data["reason"]).strip()
    queued_at   = str(row_data["created_at"]).strip()

    if not lead_id:
        raise ValueError("dispatch_cory_webhook: lead_id must be a non-empty string")
    if not destination:
        raise ValueError("dispatch_cory_webhook: destination must be a non-empty string")

    # ------------------------------------------------------------------
    # 2. No-op when URL is absent or blank.
    # ------------------------------------------------------------------
    if not webhook_url or not str(webhook_url).strip():
        return {"dispatched": False, "mode": "webhook", "reason": "NO_URL"}

    # ------------------------------------------------------------------
    # 3. Build the outbound payload.
    # ------------------------------------------------------------------
    payload = {
        "sync_record_id": sync_record_id,
        "lead_id":        lead_id,
        "destination":    destination,
        "reason":         reason,
        "queued_at":      queued_at,
        "dispatched_at":  now,
        "mode":           "webhook",
    }

    # ------------------------------------------------------------------
    # 4. POST — raise on non-2xx; let network errors propagate naturally.
    # ------------------------------------------------------------------
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=str(webhook_url).strip(),
        data=body,
        headers={"Content-Type": _CONTENT_TYPE, "Content-Length": str(len(body))},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"dispatched": True, "mode": "webhook", "http_status": resp.status}
