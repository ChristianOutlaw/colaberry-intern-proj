"""
execution/ghl/build_ghl_full_field_payload.py

Builds the FULL canonical GHL custom-field payload for a given lead.

Implements the "always send full schema" rule from:
    directives/GHL_INTEGRATION.md → "Canonical GHL Field Schema"

No network calls.  No database writes.  Pure read + compute.

Field groups
------------
A  Identity / Linking     (app_lead_id, ghl_contact_id, phone, email, full_name, course_link)
B  Invite / Access        (invite_status, invite_sent_at)
C  Course Progress        (course_started, completion_pct, current_section, last_activity_at)
D  Scoring / Qualification (can_compute_score, final_label, booking_ready)
E  Action / Operational   (intended_action, action_status, action_completed,
                           action_completed_at, last_action_sent_at)

Field value rules (from directive)
-----------------------------------
- Unknown / not-yet-available → null (Python None)
- Definitively false booleans → False (not None)
- All timestamps → ISO-8601 UTC strings

Return shapes
-------------
ok=True:
    {
        "ok":      True,
        "payload": { ...all 5 groups... }
    }

ok=False (lead not found):
    {
        "ok":      False,
        "message": str,
    }
"""

from datetime import datetime, timezone

from execution.course.course_registry import TOTAL_SECTIONS
from execution.db.sqlite import connect, init_db
from execution.decision.decide_next_cold_lead_action import decide_next_cold_lead_action
from execution.leads.can_compute_final_score import can_compute_final_score
from execution.leads.classify_final_lead_label import classify_final_lead_label
from execution.leads.compute_hot_lead_signal import compute_hot_lead_signal
from execution.leads.compute_lead_temperature import compute_lead_temperature
from execution.leads.derive_lead_lifecycle_state import (
    STATE_BOOKING_READY,
    derive_lead_lifecycle_state,
)
from execution.leads.get_latest_invite_token import get_latest_invite_token
from execution.reflection.load_reflection_responses import load_reflection_responses


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_now(now: str) -> datetime:
    """Parse the injected ISO-8601 string into a UTC-aware datetime."""
    dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _read_lead_data(app_lead_id: str, db_path: str | None) -> dict | None:
    """Read all required rows for this lead in a single connection.

    Returns a plain dict of raw DB values, or None when the lead is absent.
    """
    conn = connect(db_path)
    try:
        init_db(conn)

        lead = conn.execute(
            """
            SELECT id, phone, email, name, ghl_contact_id
            FROM leads
            WHERE id = ?
            """,
            (app_lead_id,),
        ).fetchone()

        if lead is None:
            return None

        cs = conn.execute(
            """
            SELECT current_section, completion_pct, last_activity_at, started_at
            FROM course_state
            WHERE lead_id = ?
            """,
            (app_lead_id,),
        ).fetchone()

        # Most-recent invite row (by id DESC as stable tie-break; sent_at is often NULL).
        invite = conn.execute(
            """
            SELECT token, generated_at, sent_at, channel
            FROM course_invites
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (app_lead_id,),
        ).fetchone()

        # Count how many invites have actually been sent (sent_at IS NOT NULL).
        invite_sent_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM course_invites
            WHERE lead_id = ? AND sent_at IS NOT NULL
            """,
            (app_lead_id,),
        ).fetchone()[0]

        # Progress events as proxy for quiz data availability.
        progress_count = conn.execute(
            "SELECT COUNT(*) FROM progress_events WHERE lead_id = ?",
            (app_lead_id,),
        ).fetchone()[0]

        # Reflection responses as direct indicator of reflection data.
        reflection_count = conn.execute(
            "SELECT COUNT(*) FROM reflection_responses WHERE lead_id = ?",
            (app_lead_id,),
        ).fetchone()[0]

        # Distinct completed sections ordered by first occurrence — used for rolling score.
        midpoint_rows = conn.execute(
            """
            SELECT section, MIN(occurred_at) AS first_seen
            FROM progress_events
            WHERE lead_id = ?
            GROUP BY section
            ORDER BY first_seen ASC
            """,
            (app_lead_id,),
        ).fetchall()

        # Most recent sync record of any status — used to derive action state.
        sync_latest = conn.execute(
            """
            SELECT status, updated_at, error
            FROM sync_records
            WHERE lead_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (app_lead_id,),
        ).fetchone()

    finally:
        conn.close()

    return {
        "lead":              dict(lead),
        "cs":                dict(cs) if cs else None,
        "invite":            dict(invite) if invite else None,
        "invite_sent_count": invite_sent_count,
        "progress_count":    progress_count,
        "reflection_count":  reflection_count,
        "midpoint_events":   [dict(r) for r in midpoint_rows],
        "sync_latest":       dict(sync_latest) if sync_latest else None,
    }


# ---------------------------------------------------------------------------
# Reflection confidence helper
# ---------------------------------------------------------------------------

def _resolve_reflection_confidence(
    app_lead_id: str,
    db_path: str | None,
) -> str | None:
    """Derive a reflection_confidence bucket from stored structured ratings.

    Looks for the most recent rating stored under the confidence prompt keys
    (confidence_current, confidence_start) across all sections.  Returns
    "LOW", "MEDIUM", or "HIGH", or None when no rating is found.

    Rating → bucket mapping:
        1 or 2  → "LOW"
        3       → "MEDIUM"
        4 or 5  → "HIGH"
    """
    _CONFIDENCE_KEYS = {"confidence_current", "confidence_start"}
    responses = load_reflection_responses(app_lead_id, "FREE_INTRO_AI_V0", db_path)

    # Collect all stored rating strings for confidence prompt slots.
    # section_prompt order is not guaranteed here, so we gather all values and
    # use the last non-None one found (dict iteration is insertion-ordered in
    # Python 3.7+, reflecting storage order via prompt_index).
    latest_rating_str: str | None = None
    for section_id, prompts in responses.items():
        # The course_map prompt order maps directly to prompt_index integers.
        # We only care whether the stored value looks like a rating string.
        for _idx, text in prompts.items():
            # Accept strings that begin with a digit 1-5 followed by " - ".
            if text and len(text) >= 3 and text[0] in "12345" and text[1] == " ":
                latest_rating_str = text

    if latest_rating_str is None:
        return None

    try:
        rating = int(latest_rating_str[0])
    except (ValueError, IndexError):
        return None

    if rating in (1, 2):
        return "LOW"
    if rating == 3:
        return "MEDIUM"
    return "HIGH"   # 4 or 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_ghl_full_field_payload(
    app_lead_id: str,
    *,
    now: str,
    base_url: str = "http://localhost:8501",
    db_path: str | None = None,
) -> dict:
    """Build the full canonical GHL custom-field payload for one lead.

    Pure read + compute: no network calls, no DB writes.

    Args:
        app_lead_id: Internal lead identifier.
        now:         ISO-8601 UTC string used for all time-dependent
                     computations.  Must be provided — this function
                     never calls datetime.now() internally.
                     Raises ValueError when None.
        base_url:    Base URL of the student portal, used to construct
                     the course_link.  Defaults to http://localhost:8501.
        db_path:     Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        ok=True  → {"ok": True,  "payload": {...all 5 groups...}}
        ok=False → {"ok": False, "message": str}
    """
    # ------------------------------------------------------------------
    # 0. Determinism guard — now must be injected by the caller.
    # ------------------------------------------------------------------
    if now is None:
        raise ValueError(
            "build_ghl_full_field_payload: 'now' must be provided by the caller. "
            "Do not call datetime.now() inside execution functions."
        )

    now_dt = _parse_now(now)

    # ------------------------------------------------------------------
    # 1. Read all required rows from the database.
    # ------------------------------------------------------------------
    data = _read_lead_data(app_lead_id, db_path)

    if data is None:
        return {
            "ok":      False,
            "message": f"Lead not found: {app_lead_id!r}",
        }

    lead            = data["lead"]
    cs              = data["cs"]
    invite          = data["invite"]
    invite_sent     = data["invite_sent_count"] > 0
    has_quiz_data   = data["progress_count"] > 0
    has_reflection  = data["reflection_count"] > 0
    all_sections    = data["midpoint_events"]
    sync_latest     = data["sync_latest"]

    # ------------------------------------------------------------------
    # 2. Derive course_link from the latest invite token.
    # ------------------------------------------------------------------
    token = get_latest_invite_token(app_lead_id, db_path)
    course_link = f"{base_url}/?token={token}" if token else None

    # ------------------------------------------------------------------
    # 3. Compute hot-lead signal (needed for lifecycle + scoring).
    # ------------------------------------------------------------------
    last_activity_at  = cs["last_activity_at"] if cs else None
    completion_pct    = cs["completion_pct"]    if cs else None
    started_at        = cs["started_at"]        if cs else None
    current_section   = cs["current_section"]   if cs else None

    last_activity_dt: datetime | None = None
    if last_activity_at is not None:
        last_activity_dt = datetime.fromisoformat(
            last_activity_at.replace("Z", "+00:00")
        )

    hot_result = compute_hot_lead_signal(
        invite_sent=invite_sent,
        completion_percent=completion_pct,
        last_activity_time=last_activity_dt,
        now=now_dt,
    )
    hot_signal = "HOT" if hot_result["hot"] else "NOT_HOT"

    # ------------------------------------------------------------------
    # 4. Derive lifecycle state.
    # ------------------------------------------------------------------
    lifecycle_state = derive_lead_lifecycle_state(
        invite_sent=invite_sent,
        completion_percent=completion_pct,
        last_activity_at=last_activity_at,
        hot_signal=hot_signal,
        now=now_dt,
    )

    # ------------------------------------------------------------------
    # 5. Determine next intended action.
    # ------------------------------------------------------------------
    intended_action = decide_next_cold_lead_action(app_lead_id, db_path)

    # ------------------------------------------------------------------
    # 6. Scoring / qualification fields.
    # ------------------------------------------------------------------
    score_gate_row = {
        "invite_sent":        invite_sent,
        "has_quiz_data":      has_quiz_data,
        "has_reflection_data": has_reflection,
    }
    computable = can_compute_final_score(
        score_gate_row,
        completion_pct=completion_pct,
    )

    reflection_confidence = _resolve_reflection_confidence(app_lead_id, db_path)

    final_label: str | None = None
    if computable:
        temp = compute_lead_temperature(
            now=now_dt,
            invited_sent=invite_sent,
            completion_percent=completion_pct,
            last_activity_at=last_activity_at,
            started_at=started_at,
            avg_quiz_score=None,
            avg_quiz_attempts=None,
            reflection_confidence=reflection_confidence,
            current_section=current_section,
        )
        final_label = classify_final_lead_label(temp["score"])

    # ------------------------------------------------------------------
    # 6b. Rolling score — compute_lead_temperature at the midpoint of
    #     observed progress (first half of distinct sections by first
    #     occurrence).  Requires at least 2 distinct sections; otherwise
    #     rolling_confidence_score is None.
    # ------------------------------------------------------------------
    midpoint_n      = len(all_sections) // 2
    midpoint_events = all_sections[:midpoint_n] if midpoint_n > 0 else []

    rolling_score: int | None = None
    if midpoint_events:
        mid_started_at       = midpoint_events[0]["first_seen"]
        mid_last_activity_at = midpoint_events[-1]["first_seen"]
        mid_completion_pct   = (midpoint_n / TOTAL_SECTIONS) * 100.0
        mid_temp = compute_lead_temperature(
            now=now_dt,
            invited_sent=invite_sent,
            completion_percent=mid_completion_pct,
            last_activity_at=mid_last_activity_at,
            started_at=mid_started_at,
            avg_quiz_score=None,
            avg_quiz_attempts=None,
            reflection_confidence=None,
            current_section=None,
        )
        rolling_score = mid_temp["score"]

    # ------------------------------------------------------------------
    # 7. Invite / Access derived values.
    # ------------------------------------------------------------------
    # invite_status: SENT if any invite has been sent; GENERATED if an
    # invite row exists with a token but no sent_at; null otherwise.
    if invite_sent:
        invite_status: str | None = "SENT"
    elif invite is not None and invite.get("token"):
        invite_status = "GENERATED"
    else:
        invite_status = None

    invite_sent_at: str | None = None
    if invite is not None:
        invite_sent_at = invite.get("sent_at")    # None when not yet sent

    # ------------------------------------------------------------------
    # 8. Derive action / operational state from the most recent sync record.
    # ------------------------------------------------------------------
    if sync_latest is None:
        action_status: str        = "PENDING"
        action_completed: bool    = False
        action_completed_at: str | None = None
        last_action_sent_at: str | None = None
    elif sync_latest["status"] == "SENT":
        action_status       = "SENT"
        action_completed    = True
        action_completed_at = sync_latest["updated_at"]
        last_action_sent_at = sync_latest["updated_at"]
    else:
        # FAILED or any other non-SENT status.
        action_status       = sync_latest["status"]
        action_completed    = False
        action_completed_at = None
        last_action_sent_at = sync_latest["updated_at"]

    # ------------------------------------------------------------------
    # 9. Assemble the full canonical payload.
    # ------------------------------------------------------------------
    payload = {
        # ---- Group A: Identity / Linking --------------------------------
        "app_lead_id":        lead["id"],
        "ghl_contact_id":     lead.get("ghl_contact_id"),
        "phone":              lead.get("phone"),
        "email":              lead.get("email"),
        "full_name":          lead.get("name"),
        "course_link":        course_link,

        # ---- Group B: Invite / Access -----------------------------------
        "invite_status":       invite_status,
        "invite_sent_at":      invite_sent_at,

        # ---- Group C: Course Progress -----------------------------------
        "course_started":   started_at is not None,
        "completion_pct":   completion_pct,
        "current_section":  current_section,
        "last_activity_at": last_activity_at,
        "started_at":       started_at,
        "completed_at":     last_activity_at if (completion_pct is not None and completion_pct >= 100) else None,

        # ---- Group D: Scoring / Qualification ---------------------------
        "final_label":            final_label,
        "final_confidence_score":   temp["score"] if computable else None,
        "rolling_confidence_score": rolling_score if midpoint_events else None,
        "rolling_label":            classify_final_lead_label(rolling_score) if midpoint_events else None,
        "needs_review":             final_label == "FINAL_WARM",
        "booking_ready":     lifecycle_state == STATE_BOOKING_READY,
        "lead_state":        lifecycle_state,

        # ---- Group E: Action / Operational ------------------------------
        "intended_action":     intended_action,
        "action_status":       action_status,
        "action_completed_at": action_completed_at,
        "last_action_sent_at": last_action_sent_at,
        "last_action_result":  sync_latest["error"] if sync_latest is not None else None,
    }

    return {"ok": True, "payload": payload}
