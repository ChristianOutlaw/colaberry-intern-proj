"""
services/webhook/cory_recommendation_endpoint.py

Minimal HTTP webhook server exposing one endpoint:

    POST /cory-recommendation

Accepts the cory_recommendation event envelope, validates the event name,
extracts the data payload, and forwards it to consume_cory_recommendation().
No business logic lives here — this file is pure HTTP plumbing.

Run:
    python services/webhook/cory_recommendation_endpoint.py          # default port 8521
    python services/webhook/cory_recommendation_endpoint.py 9001     # custom port

Environment variables:
    DB_PATH   Path to the SQLite database file.
              Default: tmp/app.db (via execution/db/sqlite.py)

Request shape (envelope wrapping the cory_recommendation data payload):
    {
        "event": "cory_recommendation",
        "data": {
            "lead_id":             "...",   # required
            "section":             "...",   # required
            "event_type":          "...",   # required
            "priority":            "...",   # required
            "recommended_channel": "...",   # required
            "reason_codes":        [...],   # required
            "built_at":            "..."    # required
        }
    }

Response shapes:

    200 OK — accepted (write or no-write):
        {"ok": true,  "wrote": true,  "destination": "CORY_*"}
        {"ok": true,  "wrote": false, "reason": "..."}
        {"ok": false, "reason": "LEAD_NOT_FOUND"}

    400 Bad Request — invalid envelope or payload:
        {"error": "..."}

    500 Internal Server Error — unexpected failure:
        {"error": "internal error"}
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

from execution.events.consume_cory_recommendation import (  # noqa: E402
    consume_cory_recommendation,
)

logger = logging.getLogger(__name__)

CORY_PATH = "/cory-recommendation"
DEFAULT_PORT = 8521
_EXPECTED_EVENT = "cory_recommendation"


# ---------------------------------------------------------------------------
# Pure handler logic — importable and testable without an HTTP server
# ---------------------------------------------------------------------------

def _handle_cory_request(body: dict, db_path: str | None = None) -> tuple[int, dict]:
    """Parse a decoded request body, run the consumer, return (status, response).

    Separating this from the HTTP layer allows it to be unit-tested directly
    without spinning up a real server.

    Args:
        body:    Decoded JSON body as a plain dict.
        db_path: Optional path to the SQLite database; forwarded to the consumer.

    Returns:
        (200, result_dict)    on accepted outcome (write or no-write).
        (400, {"error": ...}) on invalid envelope or payload validation failure.
        (500, {"error": ...}) on unexpected internal failure.
    """
    # ------------------------------------------------------------------
    # 1. Validate envelope: event name must be "cory_recommendation".
    # ------------------------------------------------------------------
    event = body.get("event")
    if event != _EXPECTED_EVENT:
        return 400, {
            "error": f"Expected event={_EXPECTED_EVENT!r}, got {event!r}"
        }

    # ------------------------------------------------------------------
    # 2. Extract and validate the data payload.
    # ------------------------------------------------------------------
    data = body.get("data")
    if not isinstance(data, dict):
        return 400, {"error": "'data' must be a JSON object"}

    # ------------------------------------------------------------------
    # 3. Forward to the consumer.  ValueError = caller's fault (400).
    #    Any other exception is an internal error (500).
    # ------------------------------------------------------------------
    try:
        result = consume_cory_recommendation(data, db_path=db_path)
        return 200, result
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception:
        logger.exception("Unexpected error in consume_cory_recommendation")
        return 500, {"error": "internal error"}


# ---------------------------------------------------------------------------
# HTTP handler — thin wrapper around _handle_cory_request
# ---------------------------------------------------------------------------

class _CoryHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the /cory-recommendation endpoint.

    Class-level db_path is injected by run() (and by tests) so each handler
    instance picks it up without needing constructor arguments.
    """

    db_path: str | None = None  # overridden per-process by run() or by tests

    def do_POST(self) -> None:  # noqa: N802
        if self.path != CORY_PATH:
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

        status, response = _handle_cory_request(body, db_path=self.__class__.db_path)
        self._send(status, response)

    def do_GET(self) -> None:  # noqa: N802
        self._send(405, {"error": "Method not allowed — use POST /cory-recommendation"})

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
    _CoryHandler.db_path = db_path
    server = HTTPServer(("", port), _CoryHandler)
    db_label = db_path or os.environ.get("DB_PATH", "tmp/app.db (default)")
    print(f"Cory recommendation webhook listening on :{port}  →  POST {CORY_PATH}")
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
