from __future__ import annotations
import os, json
from datetime import datetime, timezone


def enabled() -> bool:
    return os.environ.get("PLAYER_DEBUG", "0") == "1"


def _now():
    return datetime.now(timezone.utc).isoformat()


_FORCE_EVENTS = frozenset({
    "next_section_gate", "next_section_clicked", "next_section_click",
    "pending_applied", "radio_forensics",
})


def log(event: str, **data):
    force = os.getenv("PLAYER_DEBUG_FORCE", "") == "1" and event in _FORCE_EVENTS
    if not force and not enabled():
        return
    payload = {"ts": _now(), "event": event, **data}
    # one-line JSON for easy grepping
    print(f"[PLAYER_DEBUG] {json.dumps(payload, default=str)}", flush=True)


def snap(session_state: dict, extra: dict | None = None) -> dict:
    keys = [
        "player_lead_id",
        "player_course_started",
        "player_flow_step",
        "player_flow_chunk_idx",
        "_section_radio",
        "_section_radio_pending",
        "_section_radio_confirmed",
        "_backnav_pending_idx",
        "_last_sidebar_idx",
        "_suppress_backnav_once",
        "player_flash",
    ]
    out = {k: session_state.get(k) for k in keys if k in session_state}
    if "player_completed" in session_state:
        out["player_completed"] = sorted(list(session_state.get("player_completed") or []))
    if extra:
        out.update(extra)
    return out
