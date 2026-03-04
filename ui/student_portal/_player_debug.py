from __future__ import annotations
import os, json
from datetime import datetime, timezone


def enabled() -> bool:
    return os.environ.get("PLAYER_DEBUG", "0") == "1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def log(event: str, **data):
    if not enabled():
        return
    payload = {"ts": _now(), "event": event, **data}
    # one-line JSON for easy grepping
    print("[PLAYER_DEBUG] " + json.dumps(payload, ensure_ascii=False))


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
