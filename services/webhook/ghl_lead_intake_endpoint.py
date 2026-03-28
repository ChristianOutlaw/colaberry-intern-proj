"""
services/webhook/ghl_lead_intake_endpoint.py

Minimal HTTP webhook server exposing one endpoint:

    POST /ghl-lead

Accepts an inbound GHL contact payload, resolves it to an internal lead
using the phone-first identity matching hierarchy defined in
directives/GHL_INTEGRATION.md, and returns the resolved app_lead_id.

No business logic lives here — this file is pure HTTP plumbing.
No course link is generated here.
No GHL API calls are made here.

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

    Match or create succeeded:
        {
            "ok":          true,
            "app_lead_id": "...",
            "matched_by":  "phone" | "email" | "name" | "created",
            "message":     "..."
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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.match_or_create_lead_from_ghl_payload import (  # noqa: E402
    match_or_create_lead_from_ghl_payload,
)

logger = logging.getLogger(__name__)

INTAKE_PATH = "/ghl-lead"
DEFAULT_PORT = 8522

# ---------------------------------------------------------------------------
# Pure handler logic — importable and testable without an HTTP server
# ---------------------------------------------------------------------------

def _handle_ghl_intake_request(body: dict, db_path: str | None = None) -> tuple[int, dict]:
    """Parse a decoded request body, run the intake matcher, return (status, response).

    Separating this from the HTTP layer allows it to be unit-tested directly
    without spinning up a real server.

    HTTP status rules (per GHL_INTEGRATION.md and task spec):
        200 — valid JSON body, regardless of whether ok is True or False inside.
        400 — body was not valid JSON (handled in do_POST before this is called).

    Args:
        body:    Decoded JSON body as a plain dict.
        db_path: Optional path to the SQLite database; forwarded to the matcher.

    Returns:
        (200, result_dict) in all cases where the body was valid JSON.
    """
    result = match_or_create_lead_from_ghl_payload(
        {
            "ghl_contact_id": body.get("ghl_contact_id"),
            "phone":          body.get("phone"),
            "email":          body.get("email"),
            "name":           body.get("name"),
        },
        db_path=db_path,
    )

    if result["ok"]:
        logger.debug(
            "ghl_lead_intake: app_lead_id=%s matched_by=%s",
            result["app_lead_id"],
            result["matched_by"],
        )
    else:
        logger.warning("ghl_lead_intake: rejected — %s", result["message"])

    return 200, result


# ---------------------------------------------------------------------------
# HTTP handler — thin wrapper around _handle_ghl_intake_request
# ---------------------------------------------------------------------------

class _GhlIntakeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the /ghl-lead endpoint.

    Class-level db_path is injected by run() (and by tests) so each handler
    instance picks it up without needing constructor arguments.
    """

    db_path: str | None = None  # overridden per-process by run() or by tests

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

        status, response = _handle_ghl_intake_request(body, db_path=self.__class__.db_path)
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

def run(port: int = DEFAULT_PORT, db_path: str | None = None) -> None:
    """Start the HTTP server (blocks until Ctrl-C)."""
    _GhlIntakeHandler.db_path = db_path
    server = HTTPServer(("", port), _GhlIntakeHandler)
    db_label = db_path or os.environ.get("DB_PATH", "tmp/app.db (default)")
    print(f"GHL lead intake webhook listening on :{port}  →  POST {INTAKE_PATH}")
    print(f"  DB_PATH = {db_label}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run(port=_port)
