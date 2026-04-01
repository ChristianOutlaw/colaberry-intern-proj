"""
services/worker/run_booking_recovery_loop.py

Recovery-only runner for the booking-ready dispatch worker.

Runs run_booking_ready_dispatch once every 6 hours as a background
recovery process.  Its sole purpose is to catch leads whose event-driven
GHL writeback failed or was missed at course completion.

See directives/TRIGGER_OWNERSHIP_MATRIX.md:
  - Event-driven triggers own the real-time completion path.
  - This runner is the secondary / recovery path only.
  - The 6-hour interval is intentional: long enough to avoid competing
    with a fresh event-driven write, short enough to recover failed leads
    within the same day.

Usage:
    python services/worker/run_booking_recovery_loop.py

Environment variables (optional — worker is a no-op when absent):
    GHL_API_URL       GHL contact-update endpoint URL
    GHL_LOOKUP_URL    GHL contact-lookup endpoint URL (optional)
    DB_PATH           Path to SQLite file (defaults to tmp/app.db)
"""

import logging
import os
import time
from datetime import datetime, timezone

from services.worker.run_booking_ready_dispatch import run_booking_ready_dispatch

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INTERVAL_SECONDS: int = 6 * 60 * 60   # 6 hours
DISPATCH_LIMIT:   int = 100            # max leads per run
COOLDOWN_HOURS:   int = 24             # passed through to the worker

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("booking_recovery_loop")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ghl_api_url    = os.environ.get("GHL_API_URL")
    ghl_lookup_url = os.environ.get("GHL_LOOKUP_URL")
    db_path        = os.environ.get("DB_PATH") or None   # None → default tmp/app.db

    logger.info("Booking recovery loop starting.")
    logger.info("  GHL_API_URL set: %s", bool(ghl_api_url))
    logger.info("  interval:        %dh", INTERVAL_SECONDS // 3600)
    logger.info("  cooldown_hours:  %dh", COOLDOWN_HOURS)
    logger.info("  dispatch_limit:  %d", DISPATCH_LIMIT)

    while True:
        now = datetime.now(timezone.utc)
        logger.info("Recovery run starting at %s", now.isoformat())

        try:
            result = run_booking_ready_dispatch(
                now=now,
                limit=DISPATCH_LIMIT,
                cooldown_hours=COOLDOWN_HOURS,
                ghl_api_url=ghl_api_url,
                ghl_lookup_url=ghl_lookup_url,
                db_path=db_path,
            )
            logger.info(
                "Recovery run complete: scanned=%d dispatched=%d "
                "skipped=%d failed=%d cooldown_skipped=%d",
                result.get("total_scanned",    0),
                result.get("dispatched",        0),
                result.get("skipped",           0),
                result.get("failed",            0),
                result.get("cooldown_skipped",  0),
            )
            if result.get("message"):
                logger.info("  message: %s", result["message"])

        except Exception:
            logger.exception("Recovery run raised an unhandled exception. Will retry next cycle.")

        logger.info("Sleeping %dh until next recovery run.", INTERVAL_SECONDS // 3600)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
