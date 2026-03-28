"""
services/webhook/ghl_lead_intake_endpoint.py

Minimal HTTP webhook server exposing one endpoint:

    POST /ghl-lead

Accepts an inbound GHL contact payload, runs Steps 3 and 4 of the GHL
handshake (via process_ghl_lead_intake), and returns a structured result.

No business logic lives here — this file is pure HTTP plumbing.
All execution logic lives in process_ghl_lead_intake and its dependencies.

Run:
    python services/webhook/ghl_lead_intake_endpoint.py          # default port 8522
    python services/webhook/ghl_lead_intake_endpoint.py 9002     # custom port

Environment variables:
    DB_PATH   Path to the SQLite database file.
              Default: tmp/app.db (via execution/db/sqlite.py)

Request shape (all fields optional, but at least one identity field required):
    {
        "ghl_contact_id": "...",   # optional — stored when present
        "phone":          "...",   # optional — primary identity matcher
        "email":          "...",   # optional — fallback identity matcher
        "name":           "..."    # optional — weak fallback (unique match only)
    }

Response shape (200 OK — valid JSON body):

    Intake and writeback succeeded (or writeback was a configured no-op):
        {
            "ok":           true,
            "app_lead_id":  "...",
            "matched_by":   "phone" | "email" | "name" | "created",
            "writeback_ok": true | false,
            "message":      "..."
        }

    No usable identity field supplied:
        {
            "ok":      false,
            "message": "..."
        }

Error response (400):
    { "error": "..." }   — only when the request body is not valid JSON

HTTP 405:
    { "error": "..." }   — when a non-POST method is used
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.process_ghl_lead_intake import (  # noqa: E402
    process_ghl_lead_intake,
)

logger = logging.getLogger(__name__)

INTAKE_PATH = "/ghl-lead"
DEFAULT_PORT = 8522

# ---------------------------------------------------------------------------
# Pure handler logic — importable and testable without an HTTP server
# ---------------------------------------------------------------------------

def _handle_ghl_intake_request(
    body: dict,
    *,
    now: str | None = None,
    ghl_api_url: str | None = None,
    ghl_lookup_url: str | None = None,
    db_path: str | None = None,
) -> tuple[int, dict]:
    """Parse a decoded request body, run full GHL intake (Steps 3+4), return (status, response).

    Separating this from the HTTP layer allows it to be unit-tested directly
    without spinning up a real server.

    The `now` timestamp is the service-layer clock boundary: when None (live
    HTTP path), it is computed here via datetime.now().  Tests always inject
    an explicit value for determinism.

    HTTP status rules (per GHL_INTEGRATION.md):
        200 — valid JSON body, regardless of whether ok is True or False inside.
        400 — body was not valid JSON (handled in do_POST before this is called).

    Args:
        body:           Decoded JSON body as a plain dict.
        now:            ISO-8601 UTC string.  Computed from wall clock when None.
        ghl_api_url:    GHL contact-update URL; forwarded to process_ghl_lead_intake.
        ghl_lookup_url: GHL contact-lookup URL; forwarded for ghl_contact_id resolution.
        db_path:        Optional path to the SQLite database.

    Returns:
        (200, response_dict) in all cases where the body was valid JSON.
    """
    if now is None:
        now = datetime.now(timezone.utc).isoformat()

    result = process_ghl_lead_intake(
        {
            "ghl_contact_id": body.get("ghl_contact_id"),
            "phone":          body.get("phone"),
            "email":          body.get("email"),
            "name":           body.get("name"),
        },
        now=now,
        ghl_api_url=ghl_api_url,
        ghl_lookup_url=ghl_lookup_url,
        db_path=db_path,
    )

    if result["ok"]:
        logger.debug(
            "ghl_lead_intake: app_lead_id=%s matched_by=%s writeback_ok=%s",
            result["app_lead_id"],
            result["matched_by"],
            result["writeback_ok"],
        )
        response = {
            "ok":           True,
            "app_lead_id":  result["app_lead_id"],
            "matched_by":   result["matched_by"],
            "writeback_ok": result["writeback_ok"],
            "message":      result["message"],
        }
    else:
        logger.warning("ghl_lead_intake: rejected — %s", result["message"])
        response = {
            "ok":      False,
            "message": result["message"],
        }

    return 200, response


# ---------------------------------------------------------------------------
# HTTP handler — thin wrapper around _handle_ghl_intake_request
# ---------------------------------------------------------------------------

class _GhlIntakeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the /ghl-lead endpoint.

    Class-level attributes are injected by run() so each handler instance
    picks them up without needing constructor arguments.
    """

    db_path: str | None        = None  # overridden per-process by run()
    ghl_api_url: str | None    = None
    ghl_lookup_url: str | None = None

    def do_POST(self) -> None:  # noqa: N802
        if self.path != INTAKE_PATH:
            self._send(404, {"error": f"Not found: {self.path!r}"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send(400, {"error": "Request body must be valid JSON"})
            return

        if not isinstance(body, dict):
            self._send(400, {"error": "Request body must be a JSON object"})
            return

        cls = self.__class__
        status, response = _handle_ghl_intake_request(
            body,
            ghl_api_url=cls.ghl_api_url,
            ghl_lookup_url=cls.ghl_lookup_url,
            db_path=cls.db_path,
        )
        self._send(status, response)

    def do_GET(self) -> None:  # noqa: N802
        self._send(405, {"error": "Method not allowed — use POST /ghl-lead"})

    def _send(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        pass  # suppress per-request stdout noise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    port: int = DEFAULT_PORT,
    db_path: str | None = None,
    ghl_api_url: str | None = None,
    ghl_lookup_url: str | None = None,
) -> None:
    """Start the HTTP server (blocks until Ctrl-C)."""
    _GhlIntakeHandler.db_path        = db_path
    _GhlIntakeHandler.ghl_api_url    = ghl_api_url
    _GhlIntakeHandler.ghl_lookup_url = ghl_lookup_url
    server = HTTPServer(("", port), _GhlIntakeHandler)
    db_label = db_path or os.environ.get("DB_PATH", "tmp/app.db (default)")
    print(f"GHL lead intake webhook listening on :{port}  →  POST {INTAKE_PATH}")
    print(f"  DB_PATH = {db_label}")
    print(f"  GHL_API_URL = {ghl_api_url or '(not configured — writeback disabled)'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run(port=_port)
