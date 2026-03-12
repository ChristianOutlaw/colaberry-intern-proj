"""
execution/events/send_course_event.py

Minimal outbound webhook helper.  Posts a generic course event to an
external URL and returns a structured result dict.  No business logic lives
here — callers decide when and why to fire an event.

Return shape:
    {
        "status":      "no_op" | "success" | "error",
        "http_status": int | None,   # HTTP response code, or None when N/A
        "error":       str | None    # error message, or None on success/no-op
    }
"""

import json
import urllib.error
import urllib.request

_CONTENT_TYPE = "application/json"


def send_course_event(
    event_name: str,
    payload: dict,
    webhook_url: str | None = None,
    timeout_seconds: int = 5,
) -> dict:
    """POST a course event to webhook_url, or return a no-op result if absent.

    Args:
        event_name:       Non-empty string identifying the event type
                          (e.g. "section_completed", "invite_sent").
        payload:          Arbitrary dict of event data; must be JSON-serialisable.
        webhook_url:      URL to POST to.  When None or blank the function
                          returns immediately without making a network call.
        timeout_seconds:  Socket timeout for the outbound request.  Defaults to 5.

    Returns:
        dict with keys:
            status       — "no_op", "success", or "error"
            http_status  — integer HTTP response code, or None
            error        — error message string, or None

    Raises:
        ValueError: If event_name is not a non-empty string, or if payload is
                    not a dict, or if timeout_seconds is not a positive int.
    """
    _validate(event_name, payload, timeout_seconds)

    if not webhook_url or not str(webhook_url).strip():
        return {"status": "no_op", "http_status": None, "error": None}

    body = json.dumps({"event": event_name, "data": payload}).encode()
    req = urllib.request.Request(
        url=webhook_url,
        data=body,
        headers={"Content-Type": _CONTENT_TYPE, "Content-Length": str(len(body))},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            return {"status": "success", "http_status": resp.status, "error": None}
    except urllib.error.HTTPError as exc:
        return {
            "status": "error",
            "http_status": exc.code,
            "error": f"HTTP {exc.code}: {exc.reason}",
        }
    except Exception as exc:  # noqa: BLE001  (network errors, timeouts, etc.)
        return {"status": "error", "http_status": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# Internal validation
# ---------------------------------------------------------------------------

def _validate(event_name: object, payload: object, timeout_seconds: object) -> None:
    """Raise ValueError if any argument fails type or content rules."""
    if not isinstance(event_name, str) or not event_name.strip():
        raise ValueError(
            f"send_course_event: 'event_name' must be a non-empty string, "
            f"got {event_name!r}"
        )
    if not isinstance(payload, dict):
        raise ValueError(
            f"send_course_event: 'payload' must be a dict, "
            f"got {type(payload).__name__}"
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError(
            f"send_course_event: 'timeout_seconds' must be a positive int, "
            f"got {timeout_seconds!r}"
        )
