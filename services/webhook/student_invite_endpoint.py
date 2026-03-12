"""
services/webhook/student_invite_endpoint.py

Minimal HTTP webhook server exposing one endpoint:

    POST /invite

Accepts a JSON body, calls create_student_invite_from_payload(), and returns
JSON.  No business logic lives here — this file is pure HTTP plumbing.

Run:
    python services/webhook/student_invite_endpoint.py          # default port 8520
    python services/webhook/student_invite_endpoint.py 9000     # custom port

Environment variables:
    STUDENT_PORTAL_BASE_URL   Base URL of the student portal used when the
                               request body does not supply one.
                               Default: http://localhost:8501
    COURSE_EVENT_WEBHOOK_URL  Outbound webhook URL for course lifecycle events.
                               Default: (none — outbound events are skipped)

Request shape:
    {
        "lead_id":   "...",          # required
        "name":      "...",          # optional
        "email":     "...",          # optional
        "phone":     "...",          # optional
        "course_id": "...",          # optional, default FREE_INTRO_AI_V0
        "invite_id": "...",          # optional, auto-generated when absent
        "base_url":  "..."           # optional, default http://localhost:8501
    }

Response shape (200 OK):
    {
        "lead_id":       "...",
        "course_id":     "...",
        "enrollment_id": "...",
        "invite_id":     "...",
        "token":         "...",
        "invite_link":   "..."
    }

Error response (400 / 404 / 405):
    { "error": "..." }
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.create_student_invite_from_payload import (  # noqa: E402
    create_student_invite_from_payload,
)

INVITE_PATH = "/invite"
DEFAULT_PORT = 8520

# ---------------------------------------------------------------------------
# Runtime configuration — read once at import time from environment variables.
# Both can be overridden per-process without code changes.
# ---------------------------------------------------------------------------
STUDENT_PORTAL_BASE_URL: str = os.environ.get(
    "STUDENT_PORTAL_BASE_URL", "http://localhost:8501"
).rstrip("/")

COURSE_EVENT_WEBHOOK_URL: str | None = os.environ.get("COURSE_EVENT_WEBHOOK_URL")


# ---------------------------------------------------------------------------
# Pure handler logic — importable and testable without an HTTP server
# ---------------------------------------------------------------------------

def _handle_invite_request(body: dict, db_path: str | None = None) -> tuple[int, dict]:
    """Parse a decoded request body, run the intake helper, return (status, response).

    Separating this from the HTTP layer allows it to be unit-tested directly
    without spinning up a real server.

    Args:
        body:    Decoded JSON body as a plain dict.
        db_path: Optional path to the SQLite database; forwarded to the helper.

    Returns:
        (200, result_dict)  on success.
        (400, {"error": ...}) when lead_id is missing or validation fails.
    """
    lead_id = body.get("lead_id")
    if not lead_id or not str(lead_id).strip():
        return 400, {"error": "lead_id is required and must be a non-empty string"}

    try:
        result = create_student_invite_from_payload(
            lead_id=lead_id,
            name=body.get("name"),
            email=body.get("email"),
            phone=body.get("phone"),
            course_id=body.get("course_id", "FREE_INTRO_AI_V0"),
            invite_id=body.get("invite_id"),
            base_url=body.get("base_url") or STUDENT_PORTAL_BASE_URL,
            db_path=db_path,
        )
        return 200, result
    except ValueError as exc:
        return 400, {"error": str(exc)}


# ---------------------------------------------------------------------------
# HTTP handler — thin wrapper around _handle_invite_request
# ---------------------------------------------------------------------------

class _InviteHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the /invite endpoint.

    Class-level db_path is injected by run() (and by tests) so each handler
    instance picks it up without needing constructor arguments.
    """

    db_path: str | None = None  # overridden per-process by run() or by tests

    def do_POST(self) -> None:  # noqa: N802
        if self.path != INVITE_PATH:
            self._send(404, {"error": f"Not found: {self.path!r}"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send(400, {"error": "Request body must be valid JSON"})
            return

        status, response = _handle_invite_request(body, db_path=self.__class__.db_path)
        self._send(status, response)

    def do_GET(self) -> None:  # noqa: N802
        self._send(405, {"error": "Method not allowed — use POST /invite"})

    def _send(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        pass  # suppress per-request stdout noise; use structured logging if needed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(port: int = DEFAULT_PORT, db_path: str | None = None) -> None:
    """Start the HTTP server (blocks until Ctrl-C)."""
    _InviteHandler.db_path = db_path
    server = HTTPServer(("", port), _InviteHandler)
    print(f"Student invite webhook listening on :{port}  →  POST {INVITE_PATH}")
    print(f"  STUDENT_PORTAL_BASE_URL  = {STUDENT_PORTAL_BASE_URL}")
    print(f"  COURSE_EVENT_WEBHOOK_URL = {COURSE_EVENT_WEBHOOK_URL or '(not set)'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run(port=_port)
