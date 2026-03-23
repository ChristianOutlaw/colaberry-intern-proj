"""
execution/cory/dispatch_cory_ghl.py

Pure GHL dispatcher for one already-decided Cory action.

Responsibility: accept one action payload and a resolved GHL contact ID,
POST to the configured GHL API URL, and return a result dict.  No DB reads.
No datetime.now().  Safe no-op when the URL is absent or blank.

Interface mirrors dispatch_cory_webhook — explicit injected inputs, raises on
failure, returns a compact dict on success.  The worker layer (not this module)
owns DB state transitions (mark SENT / FAILED) and timestamp resolution.

Return shapes
-------------
No URL configured (ghl_api_url is None or blank):
    {"dispatched": False, "mode": "ghl", "reason": "NO_URL"}

HTTP 2xx success:
    {"dispatched": True, "mode": "ghl", "http_status": <code>,
     "ghl_contact_id": "<id>"}

Raises
------
ValueError:               Missing or blank required argument.
urllib.error.HTTPError:   Non-2xx response from the GHL API endpoint.
urllib.error.URLError:    Network-level failure (DNS, timeout, refused).
"""

import json
import urllib.error
import urllib.request

_CONTENT_TYPE = "application/json"


def dispatch_cory_ghl(
    ghl_contact_id: str,
    action: dict,
    *,
    ghl_api_url: str | None = None,
    now: str,
    timeout: int = 5,
) -> dict:
    """POST one already-decided Cory action to the GHL API for one contact.

    No DB reads.  No datetime.now().  now must be injected by the caller.
    Non-2xx responses are raised, never silently swallowed.

    Args:
        ghl_contact_id: GHL contact identifier for the lead.  Must be a
                        non-empty string.
        action:         Already-decided Cory action payload (dict).  At minimum
                        must contain a non-empty "type" key identifying the
                        action (e.g. "CORY_BOOKING", "CORY_NUDGE").
        ghl_api_url:    URL to POST to.  When None or blank the function
                        returns a safe no-op result without any network call.
        now:            ISO-8601 UTC string for the dispatch timestamp.
                        Injected by the caller; never derived internally.
        timeout:        Socket timeout in seconds.  Defaults to 5.

    Returns:
        No URL:   {"dispatched": False, "mode": "ghl", "reason": "NO_URL"}
        Success:  {"dispatched": True,  "mode": "ghl",
                   "http_status": <code>, "ghl_contact_id": "<id>"}

    Raises:
        ValueError:             ghl_contact_id is blank, action is missing/
                                empty, or action["type"] is missing/blank.
        urllib.error.HTTPError: GHL API returned a non-2xx status code.
        urllib.error.URLError:  Network failure (timeout, DNS, refused).
    """
    # ------------------------------------------------------------------
    # 1. Validate inputs.
    # ------------------------------------------------------------------
    if not ghl_contact_id or not str(ghl_contact_id).strip():
        raise ValueError("dispatch_cory_ghl: ghl_contact_id must be a non-empty string")

    if not action or not isinstance(action, dict):
        raise ValueError("dispatch_cory_ghl: action must be a non-empty dict")

    action_type = str(action.get("type", "")).strip()
    if not action_type:
        raise ValueError('dispatch_cory_ghl: action["type"] must be a non-empty string')

    # ------------------------------------------------------------------
    # 2. No-op when URL is absent or blank.
    # ------------------------------------------------------------------
    if not ghl_api_url or not str(ghl_api_url).strip():
        return {"dispatched": False, "mode": "ghl", "reason": "NO_URL"}

    # ------------------------------------------------------------------
    # 3. Build the outbound payload.
    # ------------------------------------------------------------------
    payload = {
        "ghl_contact_id": str(ghl_contact_id).strip(),
        "action":         action,
        "dispatched_at":  now,
        "mode":           "ghl",
    }

    # ------------------------------------------------------------------
    # 4. POST — non-2xx raises via urlopen; network errors propagate.
    # ------------------------------------------------------------------
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=str(ghl_api_url).strip(),
        data=body,
        headers={"Content-Type": _CONTENT_TYPE, "Content-Length": str(len(body))},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {
            "dispatched":     True,
            "mode":           "ghl",
            "http_status":    resp.status,
            "ghl_contact_id": str(ghl_contact_id).strip(),
        }
