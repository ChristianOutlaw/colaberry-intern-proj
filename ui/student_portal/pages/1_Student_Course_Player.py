"""
ui/student_portal/pages/1_Student_Course_Player.py

Student Course Player — guided sequential flow (Flow Engine v1).
Directive: directives/UI_STUDENT_COURSE_PLAYER.md

Run from the repository root:
    streamlit run ui/student_portal/student_app.py
"""

import json
import logging
import re
import sqlite3
import time
import uuid
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# sys.path bootstrap — this file lives three levels below repo root
# (ui/student_portal/pages/).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.course.course_registry import SECTIONS, TOTAL_SECTIONS               # noqa: E402
from execution.course.load_course_map import load_course_map                        # noqa: E402
from execution.course.load_quiz_library import load_quiz_library                    # noqa: E402
from execution.leads.get_lead_status import get_lead_status                         # noqa: E402
from execution.leads.upsert_lead import upsert_lead                                 # noqa: E402
from execution.progress.compute_course_state import compute_course_state            # noqa: E402
from execution.progress.record_progress_event import record_progress_event          # noqa: E402
from execution.reflection.load_reflection_responses import load_reflection_responses  # noqa: E402
from execution.reflection.save_reflection_response import save_reflection_response   # noqa: E402
from ui.theme import apply_colaberry_theme                                          # noqa: E402
from ui.student_portal.ai_tutor import generate_tutor_reply                         # noqa: E402
from ui.student_portal._player_debug import log as _dbg_log, snap as _dbg_snap, enabled as _dbg_enabled  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = str(REPO_ROOT / "tmp" / "app.db")
COURSE_CONTENT_DIR = REPO_ROOT / "course_content" / "FREE_INTRO_AI_V0"
COURSE_ID = "FREE_INTRO_AI_V0"
EM_DASH = "\u2014"


# ── Hydration helper ──────────────────────────────────────────────────────────
def _hydrate_completed_from_status(status: dict | None) -> None:
    """Merge DB-completed sections into player_completed.

    Some status payloads don't include explicit completed section IDs; in that
    case, synthesize a completion prefix from completion_pct (best-effort).
    """
    try:
        done = _status_completed_sections(status)

        # Fallback: synthesize consecutive completed prefix from completion_pct.
        if not done:
            try:
                if status and status.get("lead_exists"):
                    cs = status.get("course_state") or {}
                    pct = cs.get("completion_pct")
                    if pct is not None:
                        total = max(1, len(SECTIONS))
                        completed_count = max(0, min(total, int(round((float(pct) / 100.0) * total))))
                        done = {sid for sid, _t in SECTIONS[:completed_count]}
            except Exception:
                pass

        if done:
            st.session_state["player_completed"] |= set(done)
    except Exception:
        pass


def _clamp_section_idx(idx: int) -> int:
    return max(0, min(len(SECTIONS) - 1, int(idx)))


# ── DB reset helpers ───────────────────────────────────────────────────────────

_BACKNAV_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS backnav_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         TEXT    NOT NULL,
    from_section_id TEXT,
    to_section_id   TEXT,
    from_idx        INTEGER,
    to_idx          INTEGER,
    occurred_at     TEXT    NOT NULL,
    metadata_json   TEXT
)"""


def _reset_db_progress_from_idx(lead_id: str, from_idx: int, to_idx: int) -> None:
    """Reset DB progress for a back-navigation confirm.

    Deletes progress_events and reflection_responses for sections at to_idx and
    beyond (the student will redo those), then directly updates course_state so
    current_section points to the target and completion_pct reflects the
    remaining prefix. Creates backnav_audit if missing and logs one row.

    Schema note (actual DB):
      progress_events  — column is "section"  (not section_id)
      reflection_responses — column is "section_id"
      course_state     — separate table (not a JSON blob in leads)

    Args:
        lead_id:  Lead whose progress is being reset.
        from_idx: Furthest confirmed section index before the reset (for audit).
        to_idx:   Target section index the student is jumping back to.
    """
    if not lead_id:
        return
    sections_to_delete = [sid for sid, _t in SECTIONS[to_idx:]]
    target_sid = SECTIONS[to_idx][0] if to_idx < len(SECTIONS) else None
    from_sid   = SECTIONS[from_idx][0] if 0 <= from_idx < len(SECTIONS) else None
    now_iso    = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)

        if sections_to_delete:
            ph = ", ".join("?" for _ in sections_to_delete)
            # progress_events uses column "section" (singular, no _id suffix)
            conn.execute(
                f"DELETE FROM progress_events WHERE lead_id = ? AND section IN ({ph})",
                [lead_id, *sections_to_delete],
            )
            # reflection_responses uses column "section_id"
            conn.execute(
                f"DELETE FROM reflection_responses WHERE lead_id = ? AND section_id IN ({ph})",
                [lead_id, *sections_to_delete],
            )

        # Recompute completion from whatever events remain.
        remaining = conn.execute(
            "SELECT section, occurred_at FROM progress_events "
            "WHERE lead_id = ? ORDER BY occurred_at ASC",
            [lead_id],
        ).fetchall()
        total = max(1, len(SECTIONS))
        if remaining:
            distinct_count = len({row[0] for row in remaining})
            last_activity  = remaining[-1][1]
            completion_pct = (distinct_count / total) * 100.0
        else:
            distinct_count = 0
            last_activity  = None
            completion_pct = 0.0

        # course_state is a real table — update or insert directly.
        existing = conn.execute(
            "SELECT lead_id FROM course_state WHERE lead_id = ?", [lead_id]
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE course_state "
                "SET current_section=?, completion_pct=?, last_activity_at=?, updated_at=? "
                "WHERE lead_id=?",
                [target_sid, completion_pct, last_activity, now_iso, lead_id],
            )
        else:
            conn.execute(
                "INSERT INTO course_state "
                "(lead_id, current_section, completion_pct, last_activity_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [lead_id, target_sid, completion_pct, last_activity, now_iso],
            )

        # Ensure audit table exists, then log the reset.
        conn.execute(_BACKNAV_AUDIT_DDL)
        conn.execute(
            "INSERT INTO backnav_audit "
            "(lead_id, from_section_id, to_section_id, from_idx, to_idx, occurred_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                lead_id, from_sid, target_sid, from_idx, to_idx, now_iso,
                json.dumps({"reason": "user_backnav_confirm", "ui": "student_course_player"}),
            ],
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Progress helpers ──────────────────────────────────────────────────────────
def _status_completed_sections(status: dict | None) -> set[str]:
    """Extract completed section IDs from a get_lead_status payload (best-effort)."""
    if not status or not status.get("lead_exists"):
        return set()
    cs = status.get("course_state") or {}
    out: set[str] = set()
    for c in [
        cs.get("completed_sections"), cs.get("completed"),
        status.get("completed_sections"), status.get("completed"),
    ]:
        if isinstance(c, (list, tuple, set)):
            out |= {str(x) for x in c}
        elif isinstance(c, dict):
            out |= {str(k) for k, v in c.items() if v is True}
    return out


def _allowed_max_idx(completed: set[str]) -> int:
    """Furthest section index accessible = length of consecutive completion prefix from 0."""
    sid_to_idx = {sid: i for i, (sid, _t) in enumerate(SECTIONS)}
    completed_idxs = {sid_to_idx[sid] for sid in completed if sid in sid_to_idx}
    prefix_len = 0
    while prefix_len < len(SECTIONS) and prefix_len in completed_idxs:
        prefix_len += 1
    return min(prefix_len, len(SECTIONS) - 1)


def _completion_prefix_idx_from_status(status: dict | None) -> int:
    """Fallback unlock/resume frontier derived from completion_pct.

    Example: 22.22% with 9 sections => round(0.2222 * 9) = 2 => index 2 is the frontier.
    """
    try:
        if not status or not status.get("lead_exists"):
            return 0
        cs = status.get("course_state") or {}
        pct = cs.get("completion_pct")
        if pct is None:
            return 0
        completed_count = int(round((float(pct) / 100.0) * max(1, len(SECTIONS))))
        return max(0, min(len(SECTIONS) - 1, completed_count))
    except Exception:
        return 0


def _unlocked_frontier_idx(completed: set[str], status: dict | None) -> int:
    """Combine explicit completions + DB completion_pct fallback for the best resume point."""
    return max(_allowed_max_idx(completed), _completion_prefix_idx_from_status(status))


# Human-readable question text for each reflection prompt identifier.
_PROMPT_QUESTIONS: dict[str, str] = {
    "confidence_start": (
        "How confident were you about AI before starting this section? Describe your starting point."
    ),
    "early_surprise": "What surprised you most in this section?",
    "motivation": "What is motivating you to learn about AI?",
    "interest_area": "Which area of AI interests you most so far, and why?",
    "confidence_current": "How has your understanding or confidence changed after this section?",
    "real_world_interest": "Which real-world AI application interests you most? Why?",
    "data_to_decision_reflection": (
        "How do you see data being used to make better decisions in your life or work?"
    ),
    "intent_level": (
        "How likely are you to continue learning AI after this course? "
        "What factors are influencing you?"
    ),
    "preferred_path": (
        "What learning path feels most right for you next — "
        "hands-on projects, structured courses, or something else?"
    ),
    "open_reflection": (
        "Is there anything else you want to capture about your learning journey so far?"
    ),
}


# ---------------------------------------------------------------------------
# Cached course data loaders — file I/O runs once per session.
# ---------------------------------------------------------------------------
@st.cache_data
def _cached_course_map() -> dict:
    return load_course_map(COURSE_ID)


@st.cache_data
def _cached_quiz_library() -> dict:
    return load_quiz_library(COURSE_ID)


# ---------------------------------------------------------------------------
# Markdown chunker — pure, deterministic, no randomness, stdlib only.
# ---------------------------------------------------------------------------

def _chunk_markdown(text: str) -> list[str]:
    """Split markdown into deterministic chunks for the guided lesson flow.

    Strategy 1 — heading split: each H1/H2/H3 heading plus its body becomes
    one chunk.  Requires ≥ 2 headings to activate (single-heading docs fall
    through to Strategy 2).

    Strategy 2 — paragraph groups: blank-line-separated paragraphs are
    collected and merged into at most 5 evenly-sized chunks.

    Returns at least one non-empty string.  Pure function; no I/O, no imports
    beyond stdlib re (already imported at module level).
    """
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return [""]

    # Strategy 1: insert a NUL sentinel before every heading line, then split.
    _SENTINEL = "\x00CHUNK\x00"
    marked = re.sub(r"^(#{1,3} )", _SENTINEL + r"\1", text, flags=re.MULTILINE)
    parts = [p.strip() for p in marked.split(_SENTINEL) if p.strip()]
    if len(parts) >= 2:
        return parts

    # Strategy 2: blank-line paragraph groups, capped at 5 chunks.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    n = len(paragraphs)
    if n <= 5:
        return paragraphs or [text]
    target = 5
    size = (n + target - 1) // target  # ceiling division for even distribution
    chunks = ["\n\n".join(paragraphs[i : i + size]) for i in range(0, n, size)]
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file.
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Student Course Player", layout="wide")
apply_colaberry_theme("Student Portal", "Free Intro to AI")

st.markdown(
    """
    <style>
    .main .block-container { max-width: 980px; padding-top: 0.25rem; }
    .cb-topbar {
        position: sticky;
        top: 0;
        z-index: 100;
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(4px);
        border-bottom: 1px solid rgba(0, 0, 0, 0.08);
        padding: 0.5rem 0 0.4rem;
        margin-bottom: 0.5rem;
    }
    .cb-topbar-caption { font-size: 0.75rem; color: #5B5A59; margin: 0 0 2px; }
    .cb-topbar-title   { font-size: 1.25rem; font-weight: 700; color: #0D0D0D; margin: 0; line-height: 1.3; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Per-rerun correlation ID — new value on every Streamlit rerun.
# ---------------------------------------------------------------------------
_RUN_ID: str = uuid.uuid4().hex[:8]

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
if "player_completed" not in st.session_state:
    st.session_state["player_completed"] = set()
if "player_lead_id" not in st.session_state:
    st.session_state["player_lead_id"] = ""
if "player_status" not in st.session_state:
    st.session_state["player_status"] = None
if "player_flash" not in st.session_state:
    st.session_state["player_flash"] = None  # (level, message) or None
if "tutor_history" not in st.session_state:
    st.session_state["tutor_history"] = {}    # dict[lead_id -> list[{"role": str, "content": str}]]
if "tutor_lead_id" not in st.session_state:
    st.session_state["tutor_lead_id"] = None  # active lead for history routing
if "tutor_section_id" not in st.session_state:
    st.session_state["tutor_section_id"] = None  # tracks section for per-lead history reset
if "quiz_submitted" not in st.session_state:
    st.session_state["quiz_submitted"] = set()  # set of "{section_id}:{quiz_id}"
# Flow Engine v1 — guided sequential step state.
if "player_course_started" not in st.session_state:
    st.session_state["player_course_started"] = False  # True after Begin Course clicked
if "player_flow_step" not in st.session_state:
    st.session_state["player_flow_step"] = "lesson"    # lesson|quiz|reflection|complete (welcome unused)
if "player_flow_chunk_idx" not in st.session_state:
    st.session_state["player_flow_chunk_idx"] = 0      # current lesson chunk index
if "player_flow_section_id" not in st.session_state:
    st.session_state["player_flow_section_id"] = None  # tracks section for flow reset
# Step 2B — per-question quiz and per-prompt reflection indices.
if "player_quiz_idx" not in st.session_state:
    st.session_state["player_quiz_idx"] = 0          # index into section_quiz_ids
if "player_quiz_q_idx" not in st.session_state:
    st.session_state["player_quiz_q_idx"] = 0        # index into quiz["questions"]
if "player_quiz_attempts" not in st.session_state:
    st.session_state["player_quiz_attempts"] = {}    # {qk: attempt_count}
if "player_quiz_correct" not in st.session_state:
    st.session_state["player_quiz_correct"] = set()  # set of correct question keys
if "player_refl_idx" not in st.session_state:
    st.session_state["player_refl_idx"] = 0          # index into section_prompt_ids
# Back-nav tracking: furthest section the student has confirmed reaching.
if "_section_radio_confirmed" not in st.session_state:
    st.session_state["_section_radio_confirmed"] = 0
if "_backnav_pending_idx" not in st.session_state:
    _dbg_log(
        "backnav_pending_set",
        reason="init", new_value=None, active_idx=None,
        confirmed_idx=st.session_state.get("_section_radio_confirmed"),
        section_radio=st.session_state.get("_section_radio"),
        section_radio_pending=st.session_state.get("_section_radio_pending"),
        section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
        suppress_once=st.session_state.get("_suppress_backnav_once"),
        last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
    )
    st.session_state["_backnav_pending_idx"] = None
# Suppress false back-nav intercept on internal forward navigation reruns.
if "_suppress_backnav_once" not in st.session_state:
    st.session_state["_suppress_backnav_once"] = False
if "_last_sidebar_idx" not in st.session_state:
    st.session_state["_last_sidebar_idx"] = 0
if "_section_radio_user_changed" not in st.session_state:
    st.session_state["_section_radio_user_changed"] = False

# ---------------------------------------------------------------------------
# Back-nav diagnostic trace helper (temporary instrumentation)
# ---------------------------------------------------------------------------
def _trace_backnav(tag: str) -> None:
    _dbg_log(
        "backnav_trace",
        tag=tag,
        backnav_pending_idx=st.session_state.get("_backnav_pending_idx"),
        section_radio=st.session_state.get("_section_radio"),
        section_radio_confirmed=st.session_state.get("_section_radio_confirmed"),
        section_radio_pending=st.session_state.get("_section_radio_pending"),
        last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
        suppress_backnav_once=st.session_state.get("_suppress_backnav_once"),
        state=_dbg_snap(st.session_state),
    )


def _on_section_radio_change():
    # Guard: programmatic rerun / pending-apply in progress — ignore callback.
    if bool(st.session_state.get("_suppress_backnav_once", False)):
        st.session_state["_section_radio_user_changed"] = False
        _dbg_log(
            "section_radio_on_change_ignored",
            reason="suppress_backnav_once",
            raw_value=st.session_state.get("_section_radio"),
            pending=st.session_state.get("_section_radio_pending"),
            confirmed=st.session_state.get("_section_radio_confirmed"),
            last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
        )
        return

    # Streamlit may store an int index OR a formatted string label (e.g. "▶ How Machines Learn").
    # Robustly convert whichever form is present back to an integer index.
    _LABEL_PREFIXES = ("\u2705 ", "\U0001f512 ", "\u25b6 ")  # ✅  🔒  ▶

    raw_value = st.session_state.get("_section_radio")
    if isinstance(raw_value, int):
        new_idx = raw_value
    elif isinstance(raw_value, str):
        # Strip leading emoji prefix added by format_func, then match section title.
        title = raw_value
        for _pfx in _LABEL_PREFIXES:
            if raw_value.startswith(_pfx):
                title = raw_value[len(_pfx):]
                break
        _title_map = {SECTIONS[i][1]: i for i in range(len(SECTIONS))}
        mapped = _title_map.get(title)
        if mapped is not None:
            new_idx = mapped
        else:
            # Unrecognised label — treat as no movement
            new_idx = int(st.session_state.get("_last_sidebar_idx", 0))
            _dbg_log(
                "section_radio_on_change_unrecognised",
                raw_value=raw_value,
                stripped_title=title,
                fallback_idx=new_idx,
            )
    else:
        new_idx = int(st.session_state.get("_last_sidebar_idx", 0))

    last_idx = int(st.session_state.get("_last_sidebar_idx", new_idx))
    moved = (new_idx != last_idx)
    st.session_state["_section_radio_user_changed"] = bool(moved)
    # PLAYER_DEBUG: record on_change outcome
    _dbg_log(
        "section_radio_on_change",
        raw_value=raw_value,
        new_idx=new_idx,
        last_idx=last_idx,
        moved=moved,
        flag_set=bool(moved),
    )


# ---------------------------------------------------------------------------
# Helper — fetch lead status
# ---------------------------------------------------------------------------
def _fetch_status(lid: str) -> dict | None:
    """Call get_lead_status and return the result, or None on error."""
    try:
        return get_lead_status(lid, db_path=DB_PATH)
    except sqlite3.OperationalError:
        st.error("Could not save progress. Check that tmp/app.db is accessible.")
    except Exception:
        logging.exception("Unexpected error fetching lead status")
        st.error("An unexpected error occurred. See console for details.")
    return None


# ---------------------------------------------------------------------------
# Sidebar — Lead ID + Sections + Progress
# ---------------------------------------------------------------------------
_trace_backnav("TOP_OF_RUN")
with st.sidebar:
    # PLAYER_DEBUG: sidebar expander
    if _dbg_enabled():
        with st.expander("Debug: Player state", expanded=False):
            st.json(_dbg_snap(st.session_state))

    st.title("Course Player")
    lead_id = st.text_input(
        "Lead ID",
        value=st.session_state["player_lead_id"],
        placeholder="e.g. lead-123",
    ).strip()

    # Reset per-session tracking whenever the lead changes.
    if lead_id != st.session_state["player_lead_id"]:
        st.session_state["player_completed"] = set()
        st.session_state["player_status"] = None
        st.session_state["player_lead_id"] = lead_id
        st.session_state["player_course_started"] = False

    # Sections + progress only render once lead is entered AND course has started.
    if lead_id and st.session_state.get("player_course_started"):
        # Load status once per session (or after a lead change).
        if st.session_state["player_status"] is None:
            st.session_state["player_status"] = _fetch_status(lead_id)
            _hydrate_completed_from_status(st.session_state.get("player_status"))

        st.subheader("Sections")
        completed: set[str] = st.session_state["player_completed"]
        allowed_max_idx = int(_unlocked_frontier_idx(completed, st.session_state.get("player_status")))
        # PLAYER_DEBUG: allowed_max_idx log
        _dbg_log(
            "frontier_computed",
            allowed_max_idx=int(allowed_max_idx),
            completed_count=len(completed),
            state=_dbg_snap(st.session_state),
        )

        # Guard: skip all programmatic nav mutations while confirm UI is active.
        in_confirm = st.session_state.get("_backnav_pending_idx") is not None
        _dbg_log(
            "confirm_mode_eval",
            in_confirm=in_confirm,
            backnav_pending=st.session_state.get("_backnav_pending_idx"),
            section_radio=st.session_state.get("_section_radio"),
            pending=st.session_state.get("_section_radio_pending"),
            confirmed=st.session_state.get("_section_radio_confirmed"),
        )

        # Apply deferred section navigation BEFORE the radio is instantiated.
        # (Setting _section_radio after the widget exists raises a Streamlit error.)
        _pending_applied_this_run = False
        if (
            "_section_radio_pending" in st.session_state
            and st.session_state.get("_backnav_pending_idx") is None
            and not st.session_state.get("_section_radio_user_changed", False)
        ):
            _pend = max(0, min(len(SECTIONS) - 1, int(st.session_state["_section_radio_pending"])))
            _applied = min(_pend, int(allowed_max_idx))
            st.session_state["_section_radio"] = _applied
            _pending_applied_this_run = True
            del st.session_state["_section_radio_pending"]
            # IMPORTANT: this change was internal, not a user click.
            _trace_backnav("CLEAR_SITE_PENDING_APPLIER_BEFORE")
            _dbg_log(
                "backnav_pending_set",
                reason="apply_pending", new_value=None, active_idx=None,
                confirmed_idx=st.session_state.get("_section_radio_confirmed"),
                section_radio=st.session_state.get("_section_radio"),
                section_radio_pending=st.session_state.get("_section_radio_pending"),
                section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                suppress_once=st.session_state.get("_suppress_backnav_once"),
                last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
            )
            st.session_state["_backnav_pending_idx"] = None
            _trace_backnav("CLEAR_SITE_PENDING_APPLIER_AFTER")
            st.session_state["_suppress_backnav_once"] = True
            st.session_state["_last_sidebar_idx"] = int(_applied)
            # PLAYER_DEBUG: pending-apply log
            _dbg_log(
                "pending_applied",
                run_id=_RUN_ID,
                time=time.monotonic(),
                applied_idx=_applied,
                _section_radio=st.session_state.get("_section_radio"),
                _section_radio_confirmed=st.session_state.get("_section_radio_confirmed"),
                _section_radio_pending=st.session_state.get("_section_radio_pending"),
                _section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                _suppress_backnav_once=st.session_state.get("_suppress_backnav_once"),
                _last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                player_flow_step=st.session_state.get("player_flow_step"),
                player_completed=sorted(list(st.session_state.get("player_completed", []))),
                _backnav_pending_idx=st.session_state.get("_backnav_pending_idx"),
            )

        _radio_options = range(len(SECTIONS))
        _cur_radio_idx = st.session_state.get("_section_radio", 0)

        def _section_label(i: int) -> str:
            sid, title = SECTIONS[i][0], SECTIONS[i][1]
            is_done   = sid in completed
            is_locked = i > allowed_max_idx
            is_cur    = i == _cur_radio_idx
            if is_done:
                return f"\u2705 {title} (Completed)"
            if is_locked:
                return f"\U0001f512 {title} (Locked)"
            if is_cur:
                return f"\u25b6 {title} (In progress)"
            return f"{title} (Not started)"

        _radio_raw = st.radio(
            "Select a section",
            options=_radio_options,
            format_func=_section_label,
            key="_section_radio",
            label_visibility="collapsed",
            on_change=_on_section_radio_change,
        )
        active_idx: int = _radio_raw
        # PLAYER_DEBUG: forensic log — only when state has signal worth capturing.
        if (
            st.session_state.get("_section_radio") != st.session_state.get("_section_radio_confirmed")
            or st.session_state.get("_section_radio_pending") is not None
            or st.session_state.get("_backnav_pending_idx") is not None
            or st.session_state.get("player_flow_step") == "complete"
        ):
            _dbg_log(
                "radio_forensics",
                run_id=_RUN_ID,
                time=time.monotonic(),
                _section_radio=st.session_state.get("_section_radio"),
                _section_radio_confirmed=st.session_state.get("_section_radio_confirmed"),
                _section_radio_pending=st.session_state.get("_section_radio_pending"),
                _section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                _suppress_backnav_once=st.session_state.get("_suppress_backnav_once"),
                _last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                player_flow_step=st.session_state.get("player_flow_step"),
                player_completed=sorted(list(st.session_state.get("player_completed", []))),
                _backnav_pending_idx=st.session_state.get("_backnav_pending_idx"),
                radio_raw=_radio_raw,
                radio_raw_type=str(type(_radio_raw)),
                derived_active_idx=active_idx,
                raw_in_options=(_radio_raw in _radio_options),
            )
        # Drift clamp — must sit AFTER radio_forensics (to log the raw drift) and BEFORE
        # active_section_id assignment (so all downstream logic uses the corrected idx).
        # If the user did NOT click the sidebar and the radio drifted away from confirmed
        # (e.g. Streamlit resets the widget to 0 on rerun), silently restore confirmed.
        _confirmed_for_clamp = int(st.session_state.get("_section_radio_confirmed", active_idx))
        _user_changed_for_clamp = bool(st.session_state.get("_section_radio_user_changed", False))
        if (not in_confirm) and (not _user_changed_for_clamp) and (active_idx != _confirmed_for_clamp):
            _dbg_log(
                "sidebar_drift_clamped",
                from_idx=active_idx,
                to_idx=_confirmed_for_clamp,
                radio_raw=st.session_state.get("_section_radio"),
            )
            if st.session_state.get("player_flow_step") == "complete":
                # Soft clamp: fix active_idx locally so downstream sees the right section,
                # but do NOT set pending state or rerun (that would swallow the form submit).
                _dbg_log(
                    "sidebar_drift_soft_clamped_complete",
                    run_id=_RUN_ID,
                    from_idx=active_idx,
                    to_idx=_confirmed_for_clamp,
                )
                active_idx = _confirmed_for_clamp
            else:
                # Use pending-nav mechanism — never write _section_radio post-widget.
                # Do NOT clear _backnav_pending_idx here: a legitimate intercept may already be set.
                st.session_state["_section_radio_pending"] = _confirmed_for_clamp
                st.session_state["_suppress_backnav_once"] = True
                st.rerun()
        elif _user_changed_for_clamp and (active_idx != _confirmed_for_clamp):
            _dbg_log(
                "sidebar_drift_clamp_skipped",
                reason="user_changed",
                active_idx=active_idx,
                confirmed=st.session_state.get("_section_radio_confirmed"),
                backnav_pending=st.session_state.get("_backnav_pending_idx"),
            )

        active_section_id, active_title = SECTIONS[active_idx]
        _trace_backnav("AFTER_SIDEBAR_RADIO")

        # Snapshot and consume one-shot suppression flag before any intercept logic.
        # Capturing it here ensures the intercept condition sees the correct value
        # even if the flag would otherwise be cleared inside the if/else below.
        _suppress_once = bool(st.session_state.get("_suppress_backnav_once"))
        if _suppress_once:
            st.session_state["_suppress_backnav_once"] = False

        # Back-nav confirmation intercept:
        # Only trigger on a REAL user click to a previously completed earlier section.
        # Never trigger during internal reruns (pending nav) or one-shot suppression.
        _has_pending_nav = "_section_radio_pending" in st.session_state
        if _has_pending_nav:
            # If an internal navigation is in-flight, kill any stale back-nav intent.
            # Skip if a back-nav confirmation is already pending — preserve confirm state.
            if st.session_state.get("_backnav_pending_idx") is None:
                _trace_backnav("CLEAR_SITE_HAS_PENDING_NAV_BEFORE")
                _dbg_log(
                    "backnav_pending_set",
                    reason="has_pending_nav", new_value=None, active_idx=active_idx,
                    confirmed_idx=st.session_state.get("_section_radio_confirmed"),
                    section_radio=st.session_state.get("_section_radio"),
                    section_radio_pending=st.session_state.get("_section_radio_pending"),
                    section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                    suppress_once=st.session_state.get("_suppress_backnav_once"),
                    last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                )
                st.session_state["_backnav_pending_idx"] = None
                _trace_backnav("CLEAR_SITE_HAS_PENDING_NAV_AFTER")

        # One-shot suppression for internal reruns (e.g., Mark Complete / Go to next section).
        if st.session_state.get("_suppress_backnav_once"):
            st.session_state["_suppress_backnav_once"] = False
        else:
            _last_idx_raw = st.session_state.get("_last_sidebar_idx", active_idx)
            if _last_idx_raw is None:
                _last_idx_raw = active_idx
            _last_idx = int(_last_idx_raw)
            _confirmed_idx = int(st.session_state.get("_section_radio_confirmed", 0))
            _sidebar_moved = (active_idx != _last_idx)
            _user_changed_sidebar = _sidebar_moved and (not _has_pending_nav)
            _target_completed = active_section_id in st.session_state.get("player_completed", set())

            # PLAYER_DEBUG: sidebar movement gate evaluation
            _dbg_log(
                "sidebar_moved_eval",
                active_idx=active_idx,
                last_idx=_last_idx,
                sidebar_moved=_sidebar_moved,
                confirmed_idx=_confirmed_idx,
                section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
            )
            # PLAYER_DEBUG: log flag value during backnav intercept evaluation
            _dbg_log(
                "backnav_intercept_eval",
                section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                active_idx=active_idx,
                confirmed_idx=_confirmed_idx,
                target_completed=_target_completed,
                has_pending_nav=_has_pending_nav,
                pending_applied_this_run=_pending_applied_this_run,
                suppress_once=_suppress_once,
            )
            if (not _pending_applied_this_run) and (not _has_pending_nav) and _target_completed and (active_idx < _confirmed_idx) and _sidebar_moved and (not _suppress_once) and st.session_state.get("_section_radio_user_changed"):
                _trace_backnav("INTERCEPT_BEFORE_SET")
                _dbg_log(
                    "backnav_pending_set",
                    reason="intercept", new_value=int(active_idx), active_idx=active_idx,
                    confirmed_idx=st.session_state.get("_section_radio_confirmed"),
                    section_radio=st.session_state.get("_section_radio"),
                    section_radio_pending=st.session_state.get("_section_radio_pending"),
                    section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                    suppress_once=st.session_state.get("_suppress_backnav_once"),
                    last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                )
                st.session_state["_backnav_pending_idx"] = int(active_idx)
                st.session_state["_section_radio_pending"] = _confirmed_idx
                _trace_backnav("INTERCEPT_AFTER_SET")
                # Reset flag here so it is False on the very next rerun — not left
                # lingering across the confirm-UI rerun (st.rerun() below stops
                # execution before the unconditional reset at the end of sidebar).
                st.session_state["_section_radio_user_changed"] = False
                _trace_backnav("INTERCEPT_BEFORE_RERUN")
                st.rerun()

        # Reset user-changed flag — one-shot, consumed for this run.
        st.session_state["_section_radio_user_changed"] = False

        # Update last rendered sidebar idx at the end of sidebar logic (not a user action).
        st.session_state["_last_sidebar_idx"] = int(active_idx)

        # Enforce lock: redirect back to the furthest allowed section.
        if (not in_confirm) and active_idx > allowed_max_idx:
            st.session_state["_section_radio_pending"] = allowed_max_idx
            st.session_state["_suppress_backnav_once"] = True
            st.session_state["player_flash"] = (
                "info",
                "Complete the current section to unlock the next one.",
            )
            st.rerun()

        # Reset tutor history (per-lead) when the student navigates to a new section.
        if active_section_id != st.session_state["tutor_section_id"]:
            _tutor_lid = lead_id or "unknown"
            st.session_state["tutor_history"][_tutor_lid] = []
            st.session_state["tutor_lead_id"] = _tutor_lid
            st.session_state["tutor_section_id"] = active_section_id

        # Reset guided flow when the student navigates to a new section.
        if active_section_id != st.session_state["player_flow_section_id"]:
            st.session_state["player_flow_step"] = "lesson"
            st.session_state["player_flow_chunk_idx"] = 0
            st.session_state["player_flow_section_id"] = active_section_id
            st.session_state["player_quiz_idx"] = 0
            st.session_state["player_quiz_q_idx"] = 0
            st.session_state["player_quiz_attempts"] = {}
            st.session_state["player_quiz_correct"] = set()
            st.session_state["player_refl_idx"] = 0

        st.divider()
        st.subheader("Progress")

        status = st.session_state["player_status"]
        if status is None or not status.get("lead_exists"):
            pct = 0.0
            current = EM_DASH
            last_activity = EM_DASH
        else:
            cs = status["course_state"]
            pct = cs["completion_pct"] if cs["completion_pct"] is not None else 0.0
            _raw_current = cs["current_section"]
            _sid_to_title = {sid: title for sid, title in SECTIONS}
            current = _sid_to_title.get(_raw_current, _raw_current) if _raw_current else EM_DASH
            last_activity = cs["last_activity_at"] or EM_DASH

        st.metric("Completion", f"{pct:.2f} %")
        st.progress(pct / 100.0)
        st.write(f"**Current:** {current}")
        st.write(f"**Last activity:** {last_activity}")

# ---------------------------------------------------------------------------
# Main content area — guided flow (full width, no column wrapper)
# ---------------------------------------------------------------------------

# Flash message — stored before st.rerun() so it survives the cycle.
if st.session_state["player_flash"] is not None:
    level, msg = st.session_state["player_flash"]
    st.session_state["player_flash"] = None
    if level == "success":
        st.success(msg)
    else:
        st.error(msg)


# ── Back-nav reset confirmation UI ────────────────────────────────────────────
# Shown when student selects a previously completed section.
# The sidebar intercept stashes the target index here and bounces the radio back.
if st.session_state.get("_backnav_pending_idx") is not None:
    _target_idx = int(st.session_state["_backnav_pending_idx"])
    _target_sid, _target_title = SECTIONS[_target_idx]

    _trace_backnav("CONFIRM_BLOCK_ENTER")
    st.error("CONFIRM BLOCK ENTERED (diagnostic)")
    # PLAYER_DEBUG: confirm-screen render log
    _dbg_log(
        "backnav_confirm_rendered",
        backnav_pending_idx=int(st.session_state["_backnav_pending_idx"]),
        section_radio=st.session_state.get("_section_radio"),
        section_radio_confirmed=st.session_state.get("_section_radio_confirmed"),
        state=_dbg_snap(st.session_state),
    )
    st.warning("BACKNAV CONFIRM ACTIVE — if you see this, the confirm screen block is executing.")

    with st.container(border=True):
        st.warning(
            f"### Jump back to **{_target_title}**?\n"
            "You've already completed this section. If you continue:\n\n"
            "\u2022 Sections after this one will be **reset and relocked**\n"
            "\u2022 Later quiz/reflection progress will be **cleared**\n"
            "\u2022 Your saved progress will roll back to this point\n\n"
            "**This action can't be undone.**"
        )
        _c1, _c2 = st.columns(2)
        with _c1:
            if st.button("Continue and reset progress", type="primary", key="btn_backnav_confirm"):
                _keep = {
                    sid for i, (sid, _t) in enumerate(SECTIONS)
                    if i < _target_idx
                    and sid in st.session_state.get("player_completed", set())
                }
                st.session_state["player_completed"] = set(_keep)
                # Clear per-section animation + quiz-choice keys for reset sections.
                _sids_reset = {sid for sid, _t in SECTIONS[_target_idx:]}
                st.session_state["quiz_submitted"] = {
                    k for k in st.session_state.get("quiz_submitted", set())
                    if k.split(":")[0] not in _sids_reset
                }
                for _sk in [k for k in list(st.session_state.keys())
                             if any(k.startswith(f"chunk_typed_{s}") or
                                    k.startswith(f"welcome_typed_{s}") or
                                    k.startswith(f"reflection_txt_{s}")
                                    for s in _sids_reset)]:
                    del st.session_state[_sk]
                _reset_db_progress_from_idx(
                    lead_id,
                    from_idx=int(st.session_state.get("_section_radio_confirmed", _target_idx)),
                    to_idx=_target_idx,
                )
                try:
                    st.session_state["player_status"] = get_lead_status(lead_id, db_path=DB_PATH)
                    _hydrate_completed_from_status(st.session_state.get("player_status"))
                except Exception:
                    pass
                st.session_state["_section_radio_confirmed"] = _target_idx
                st.session_state["_section_radio_pending"] = _target_idx
                st.session_state["player_flow_step"] = "lesson"
                st.session_state["player_flow_chunk_idx"] = 0
                st.session_state["player_quiz_idx"] = 0
                st.session_state["player_quiz_q_idx"] = 0
                st.session_state["player_quiz_attempts"] = {}
                st.session_state["player_quiz_correct"] = set()
                st.session_state["player_refl_idx"] = 0
                _trace_backnav("CLEAR_SITE_CONFIRM_BTN_BEFORE")
                _dbg_log(
                    "backnav_pending_set",
                    reason="confirm_btn", new_value=None, active_idx=active_idx,
                    confirmed_idx=st.session_state.get("_section_radio_confirmed"),
                    section_radio=st.session_state.get("_section_radio"),
                    section_radio_pending=st.session_state.get("_section_radio_pending"),
                    section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                    suppress_once=st.session_state.get("_suppress_backnav_once"),
                    last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                )
                st.session_state["_backnav_pending_idx"] = None
                _trace_backnav("CLEAR_SITE_CONFIRM_BTN_AFTER")
                st.rerun()
        with _c2:
            if st.button("Cancel", key="btn_backnav_cancel"):
                _trace_backnav("CLEAR_SITE_CANCEL_BTN_BEFORE")
                _dbg_log(
                    "backnav_pending_set",
                    reason="cancel_btn", new_value=None, active_idx=active_idx,
                    confirmed_idx=st.session_state.get("_section_radio_confirmed"),
                    section_radio=st.session_state.get("_section_radio"),
                    section_radio_pending=st.session_state.get("_section_radio_pending"),
                    section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                    suppress_once=st.session_state.get("_suppress_backnav_once"),
                    last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                )
                st.session_state["_backnav_pending_idx"] = None
                _trace_backnav("CLEAR_SITE_CANCEL_BTN_AFTER")
                st.rerun()

    _trace_backnav("BEFORE_STOP_CONFIRM_BLOCK")
    st.stop()


# ── Course-level welcome screen ────────────────────────────────────────────────
# Portal gate: always shown until the student clicks a begin/resume CTA.
if not st.session_state.get("player_course_started"):
    # Proactively fetch status (once per session) so we can choose the right CTA
    # before any button is clicked.  Reuses the same player_status key used
    # everywhere else — no new DB path, no extra queries on repeated reruns.
    if lead_id and st.session_state.get("player_status") is None:
        try:
            st.session_state["player_status"] = _fetch_status(lead_id)
            _hydrate_completed_from_status(st.session_state.get("player_status"))
        except Exception:
            pass

    _wc_status = st.session_state.get("player_status") if lead_id else None
    _wc_has_progress = bool(
        _wc_status
        and _wc_status.get("lead_exists")
        and float((_wc_status.get("course_state") or {}).get("completion_pct") or 0) > 0
    )

    # Shared helper: compute resume section index from status.
    def _wc_compute_resume_idx(status: dict | None) -> int:
        idx = _unlocked_frontier_idx(st.session_state.get("player_completed", set()), status)
        try:
            cs = (status or {}).get("course_state") or {}
            cur = cs.get("current_section")
            if cur:
                _imap = {sid: i for i, (sid, _t) in enumerate(SECTIONS)}
                idx = max(idx, _imap.get(cur, 0))
        except Exception:
            pass
        return max(0, min(len(SECTIONS) - 1, int(idx)))

    # Shared helper: apply all flow-start state and rerun.
    def _wc_start(resume_idx: int) -> None:
        st.session_state["player_course_started"] = True
        st.session_state["player_flow_step"] = "lesson"
        st.session_state["player_flow_chunk_idx"] = 0
        st.session_state["player_quiz_idx"] = 0
        st.session_state["player_quiz_q_idx"] = 0
        st.session_state["player_quiz_attempts"] = {}
        st.session_state["player_quiz_correct"] = set()
        st.session_state["player_refl_idx"] = 0
        st.session_state["_section_radio"] = resume_idx
        st.session_state["_section_radio_confirmed"] = int(resume_idx)
        st.rerun()

    _cw_lines = [
        "This course guides you through the fundamentals of AI in 9 short sections.",
        "Each section follows the same pattern: read a guided lesson, test your "
        "understanding with a quiz, then capture a brief reflection.",
        "Work at your own pace — your progress is saved automatically after each section.",
    ]
    _cw_key = "course_welcome_typed"
    with st.container(border=True):
        st.markdown("## Welcome to **Intro to AI**")

        # Typewriter intro (runs once per session, then renders static).
        _cw_ph = st.empty()
        if _cw_key not in st.session_state:
            st.session_state[_cw_key] = False
        if st.session_state.get(_cw_key) is False:
            _cw_text = ""
            for _cw_line in _cw_lines:
                for _cw_char in _cw_line:
                    _cw_text += _cw_char
                    _cw_ph.markdown(_cw_text)
                    time.sleep(0.01)
                _cw_text += "\n\n"
            st.session_state[_cw_key] = True
        else:
            _cw_ph.markdown("\n\n".join(_cw_lines))

        # ── Course outline preview card ────────────────────────────────────────
        st.markdown("---")
        _sid_to_title_wc = {sid: title for sid, title in SECTIONS}
        st.markdown(
            f"**{len(SECTIONS)} sections** &nbsp;·&nbsp; "
            "Each section: **Lesson → Quiz → Reflection**",
            unsafe_allow_html=True,
        )
        with st.expander("View course outline"):
            for _wc_i, (_wc_sid, _wc_stitle) in enumerate(SECTIONS):
                st.markdown(f"{_wc_i + 1}. {_wc_stitle}")

        st.markdown("<div style='height: 18px'></div>", unsafe_allow_html=True)

        # ── CTA: Resume / Restart vs Begin ────────────────────────────────────
        if not lead_id:
            # No lead entered yet — show a disabled Begin button with guidance.
            st.button("Begin Course →", type="primary", key="btn_begin_course", disabled=True)
            st.caption("Enter your Lead ID in the sidebar to continue.")

        elif _wc_has_progress:
            # Progress exists — show Resume (primary) + Restart (secondary).
            _wc_cs = _wc_status["course_state"]                             # type: ignore[index]
            _wc_pct = float(_wc_cs.get("completion_pct") or 0)
            _wc_cur_sid = _wc_cs.get("current_section") or ""
            _wc_cur_title = _sid_to_title_wc.get(_wc_cur_sid, "") if _wc_cur_sid else ""
            _wc_summary = f"{_wc_pct:.0f}% complete"
            if _wc_cur_title:
                _wc_summary += f" · last section: **{_wc_cur_title}**"
            st.info(f"Saved progress found — {_wc_summary}")

            _rb_col, _rs_col = st.columns([2, 1])
            with _rb_col:
                if st.button(
                    "Resume →", type="primary",
                    key="btn_resume_course", use_container_width=True,
                ):
                    _wc_start(_wc_compute_resume_idx(_wc_status))
            with _rs_col:
                if st.button(
                    "Restart course",
                    key="btn_restart_course", use_container_width=True,
                ):
                    # Clear DB progress so the next login also starts from scratch.
                    _wc_frontier = _unlocked_frontier_idx(
                        st.session_state.get("player_completed", set()), _wc_status
                    )
                    _reset_db_progress_from_idx(
                        lead_id,
                        from_idx=int(_wc_frontier),
                        to_idx=0,
                    )
                    # Reset in-session state.
                    st.session_state["player_completed"] = set()
                    st.session_state["player_status"] = None
                    _tutor_lid_wc = lead_id or "unknown"
                    st.session_state["tutor_history"][_tutor_lid_wc] = []
                    st.session_state["_backnav_pending_idx"] = None
                    st.session_state["_last_sidebar_idx"] = 0
                    st.session_state["_suppress_backnav_once"] = True
                    _wc_start(0)

        else:
            # No progress — standard Begin Course CTA.
            if st.button("Begin Course →", type="primary", key="btn_begin_course"):
                _hydrate_completed_from_status(_wc_status)
                _wc_start(_wc_compute_resume_idx(_wc_status))

    _trace_backnav("BEFORE_STOP_WELCOME")
    st.stop()

# Load section markdown (shared across all steps).
content_path = COURSE_CONTENT_DIR / f"{active_section_id}.md"
try:
    section_markdown = content_path.read_text(encoding="utf-8")
except Exception:
    section_markdown = None

# Load cached course data (file I/O once per session).
try:
    course_map = _cached_course_map()
    quiz_library = _cached_quiz_library()
except Exception:
    logging.exception("Failed to load course map or quiz library")
    course_map = {}
    quiz_library = {}

section_data = course_map.get(active_section_id, {})
section_quiz_ids: list[str] = section_data.get("quiz_ids", [])
section_prompt_ids: list[str] = section_data.get("reflection_prompts", [])

# Chunk the lesson markdown (deterministic — no randomness).
chunks = _chunk_markdown(section_markdown) if section_markdown else ["Content unavailable."]
n_chunks = len(chunks)

# Clamp chunk_idx in case section content shrinks after a nav change.
chunk_idx = min(st.session_state["player_flow_chunk_idx"], max(0, n_chunks - 1))
step = st.session_state["player_flow_step"]

# ── Sticky section header — weighted monotonic section progress ────────────────
_WELCOME_W = 0.05
_LESSON_W  = 0.55
_QUIZ_W    = 0.25
_REFL_W    = 0.10
# _COMPLETE_BONUS = 0.05  (sum of weights = 1.0 at complete)

_lesson_frac = (chunk_idx + 1) / max(1, n_chunks) if step != "welcome" else 0.0
_n_quizzes   = len(section_quiz_ids)
_quiz_frac   = (
    1.0 if _n_quizzes == 0
    else min(1.0, st.session_state["player_quiz_idx"] / _n_quizzes)
)
_n_prompts   = len(section_prompt_ids)
_refl_frac   = (
    1.0 if _n_prompts == 0
    else min(1.0, st.session_state["player_refl_idx"] / _n_prompts)
)

if step == "welcome":
    _bar_val = 0.0
elif step == "lesson":
    _bar_val = _WELCOME_W + _LESSON_W * _lesson_frac
elif step == "quiz":
    _bar_val = _WELCOME_W + _LESSON_W + _QUIZ_W * _quiz_frac
elif step == "reflection":
    _bar_val = _WELCOME_W + _LESSON_W + _QUIZ_W + _REFL_W * _refl_frac
else:  # complete
    _bar_val = 1.0
_bar_val = max(0.0, min(1.0, _bar_val))

_topbar_caption = f"Section {active_idx + 1} of {len(SECTIONS)}"
if step == "lesson" and n_chunks > 1:
    _topbar_caption += f" • Part {chunk_idx + 1} of {n_chunks}"
st.markdown(
    f"""<div class="cb-topbar">
      <p class="cb-topbar-caption">{_topbar_caption}</p>
      <p class="cb-topbar-title">{active_title}</p>
    </div>""",
    unsafe_allow_html=True,
)
st.progress(_bar_val)


# ── Tutor expander — closure over active_title / section_markdown / step ───────
def _render_tutor_expander() -> None:
    # Per-lead message list: switching lead preserves each lead's history.
    _active_lid = lead_id or "unknown"
    messages = st.session_state["tutor_history"].setdefault(_active_lid, [])

    def _call_tutor(user_msg: str) -> None:
        """Append user message, generate tutor reply, append assistant message."""
        messages.append({"role": "user", "content": user_msg})
        reply = generate_tutor_reply(
            section_title=active_title,
            section_markdown=section_markdown or "",
            user_message=user_msg,
            section_idx=active_idx,
            total_sections=len(SECTIONS),
            chunk_idx=chunk_idx,
            total_chunks=n_chunks,
            flow_step=step,
        )
        messages.append({"role": "assistant", "content": reply})

    with st.expander("AI Tutor", expanded=True):
        st.subheader("AI Tutor")

        # Quick-action buttons — 2 × 2 grid.
        # Each button directly calls the tutor in-place; the implicit Streamlit
        # rerun from the button click re-renders the updated chat history.
        # No tutor_pending / extra st.rerun() needed here.
        b_left, b_right = st.columns(2)
        with b_left:
            if st.button("Summarize", use_container_width=True, key="btn_summarize"):
                _call_tutor("Summarize this section for me.")
            if st.button("Give me an example", use_container_width=True, key="btn_example"):
                _call_tutor("Give me a concrete example of the key ideas in this section.")
        with b_right:
            if st.button("Explain like I'm new", use_container_width=True, key="btn_explain"):
                _call_tutor("Explain this section like I'm completely new to the topic.")
            if st.button(
                "Quiz me (2 questions)", use_container_width=True, key="btn_quiz"
            ):
                _call_tutor("Quiz me with 2 questions about this section.")

        st.divider()

        # Chat history — rendered top-to-bottom.
        for msg in messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Free-form chat input — st.chat_input triggers its own rerun on submit;
        # the explicit st.rerun() below ensures a clean second pass that clears
        # the widget state and renders the updated history at the top.
        user_input = st.chat_input("Ask about this section…")
        if user_input:
            _call_tutor(user_input)
            st.rerun()


# ── WELCOME ───────────────────────────────────────────────────────────────────
if step == "welcome":
    with st.container(border=True):
        st.markdown(f"## Welcome to **{active_title}**")

        _welcome_lines = [
            "In this section, we'll explore the core ideas step by step.",
            "You'll read a guided lesson, test your understanding, and reflect briefly.",
            "Move at your own pace — your progress is saved automatically.",
        ]
        _placeholder = st.empty()
        _typed_key = f"welcome_typed_{active_section_id}"

        if _typed_key not in st.session_state:
            st.session_state[_typed_key] = False

        if st.session_state.get(_typed_key) is False:
            _current_text = ""
            for _line in _welcome_lines:
                for _char in _line:
                    _current_text += _char
                    _placeholder.markdown(_current_text)
                    time.sleep(0.01)
                _current_text += "\n\n"
            st.session_state[_typed_key] = True
        else:
            _placeholder.markdown("\n\n".join(_welcome_lines))

        st.markdown("<div style='height: 18px'></div>", unsafe_allow_html=True)
        if st.button("Begin Section →", type="primary"):
            st.session_state["player_flow_step"] = "lesson"
            st.session_state["player_flow_chunk_idx"] = 0
            st.rerun()

# ── LESSON ────────────────────────────────────────────────────────────────────
elif step == "lesson":
    _col_main, _col_tutor = st.columns([5, 3], gap="large")
    with _col_main:
        with st.container(border=True):
            st.markdown("<div style='height: 4px'></div>", unsafe_allow_html=True)
            _chunk_key = f"chunk_typed_{active_section_id}_{chunk_idx}"
            _chunk_ph = st.empty()
            if _chunk_key not in st.session_state:
                st.session_state[_chunk_key] = False
            if st.session_state.get(_chunk_key) is False:
                _chunk_text = chunks[chunk_idx]
                _built = ""
                for _line in _chunk_text.splitlines():
                    _line_words = _line.split()
                    if not _line_words:
                        _built += "\n"
                        continue
                    _line_built = ""
                    for _wi in range(0, len(_line_words), 3):
                        _line_built = " ".join(_line_words[: _wi + 3])
                        _chunk_ph.write(_built + _line_built)
                        time.sleep(0.02)
                    _built += _line_built + "\n"
                _chunk_ph.markdown(_chunk_text)
                st.session_state[_chunk_key] = True
            else:
                _chunk_ph.markdown(chunks[chunk_idx])
            st.markdown("<div style='height: 12px'></div>", unsafe_allow_html=True)
            st.divider()

            is_last_chunk = chunk_idx >= n_chunks - 1
            if is_last_chunk:
                if section_quiz_ids:
                    fwd_label = "Continue to Quiz →"
                elif section_prompt_ids:
                    fwd_label = "Continue to Reflection →"
                else:
                    fwd_label = "Continue to Complete →"
            else:
                fwd_label = "Continue →"

            st.markdown("<div style='height: 8px'></div>", unsafe_allow_html=True)
            col_back, col_gap, col_fwd = st.columns([1, 1, 6])
            with col_back:
                if chunk_idx > 0:
                    if st.button("← Back", use_container_width=True):
                        st.session_state["player_flow_chunk_idx"] = chunk_idx - 1
                        st.rerun()
            with col_fwd:
                if st.button(fwd_label, type="primary", use_container_width=True):
                    if is_last_chunk:
                        if section_quiz_ids:
                            st.session_state["player_flow_step"] = "quiz"
                        elif section_prompt_ids:
                            st.session_state["player_flow_step"] = "reflection"
                        else:
                            st.session_state["player_flow_step"] = "complete"
                        st.session_state["player_flow_chunk_idx"] = 0
                    else:
                        st.session_state["player_flow_chunk_idx"] = chunk_idx + 1
                    st.rerun()
    with _col_tutor:
        with st.container(border=True):
            _render_tutor_expander()

# ── QUIZ ──────────────────────────────────────────────────────────────────────
elif step == "quiz":
    _col_main, _col_tutor = st.columns([5, 3], gap="large")
    with _col_main:
        with st.container(border=True):
            if not section_quiz_ids:
                st.info("No quiz for this section.")
                if st.button(
                    "Continue to Reflection →" if section_prompt_ids else "Continue to Complete →",
                    type="primary",
                ):
                    st.session_state["player_flow_step"] = (
                        "reflection" if section_prompt_ids else "complete"
                    )
                    st.rerun()
            else:
                quiz_idx = st.session_state["player_quiz_idx"]

                if quiz_idx >= len(section_quiz_ids):
                    # All quizzes in this section finished — show continue.
                    next_label = (
                        "Continue to Reflection →" if section_prompt_ids else "Continue to Complete →"
                    )
                    if st.button(next_label, type="primary"):
                        st.session_state["player_flow_step"] = (
                            "reflection" if section_prompt_ids else "complete"
                        )
                        st.rerun()
                else:
                    quiz_id = section_quiz_ids[quiz_idx]
                    quiz = quiz_library.get(quiz_id)

                    if quiz is None:
                        st.warning(f"Quiz '{quiz_id}' not found in library.")
                    else:
                        questions = quiz.get("questions", [])

                        if not questions:
                            st.info("This quiz has no questions.")
                            if st.button("Next →", key=f"skip_quiz_{quiz_id}"):
                                st.session_state["player_quiz_idx"] = quiz_idx + 1
                                st.session_state["player_quiz_q_idx"] = 0
                                st.rerun()
                        else:
                            # Clamp question index (safe guard against content changes).
                            q_idx = min(
                                st.session_state["player_quiz_q_idx"], len(questions) - 1
                            )
                            q = questions[q_idx]
                            opts = q["options"]

                            # Progress caption.
                            n_quizzes = len(section_quiz_ids)
                            if n_quizzes > 1:
                                st.caption(f"Quiz {quiz_idx + 1} of {n_quizzes}")

                            if quiz.get("title"):
                                st.subheader(quiz["title"])

                            st.markdown(f"**{q['question']}**")

                            radio_key = f"qsel_{active_section_id}_{quiz_id}_{q_idx}"
                            chosen = st.radio(
                                "Choose your answer:",
                                options=list(range(len(opts))),
                                format_func=lambda j, o=opts: o[j],
                                key=radio_key,
                                label_visibility="collapsed",
                            )

                            qk = f"{active_section_id}:{quiz_id}:{q_idx}"
                            attempts = st.session_state["player_quiz_attempts"].get(qk, 0)
                            already_correct = qk in st.session_state["player_quiz_correct"]

                            if already_correct:
                                st.success("Correct!")
                            elif attempts >= 3:
                                correct_text = opts[q["correct_index"]]
                                st.info(f"Correct answer: **{correct_text}**")
                            else:
                                if st.button("Submit Answer", key=f"submit_ans_{qk}"):
                                    if chosen == q["correct_index"]:
                                        st.session_state["player_quiz_correct"].add(qk)
                                        st.rerun()
                                    else:
                                        new_attempts = attempts + 1
                                        st.session_state["player_quiz_attempts"][qk] = new_attempts
                                        if new_attempts < 3:
                                            st.warning("Not quite — try again.")
                                        else:
                                            st.rerun()  # rerun to reveal correct answer

                            # Next → shown when correct or all attempts exhausted.
                            if already_correct or attempts >= 3:
                                if st.button("Next →", type="primary", key=f"next_{qk}"):
                                    if q_idx < len(questions) - 1:
                                        st.session_state["player_quiz_q_idx"] = q_idx + 1
                                    else:
                                        # Last question in this quiz — advance to next quiz.
                                        st.session_state["player_quiz_idx"] = quiz_idx + 1
                                        st.session_state["player_quiz_q_idx"] = 0
                                    st.rerun()
    with _col_tutor:
        with st.container(border=True):
            _render_tutor_expander()

# ── REFLECTION ────────────────────────────────────────────────────────────────
elif step == "reflection":
    _col_main, _col_tutor = st.columns([5, 3], gap="large")
    with _col_main:
        with st.container(border=True):
            if not section_prompt_ids:
                st.info("No reflection prompts for this section.")
                if st.button("Continue to Complete →", type="primary"):
                    st.session_state["player_flow_step"] = "complete"
                    st.rerun()
            else:
                refl_idx = st.session_state["player_refl_idx"]

                if refl_idx >= len(section_prompt_ids):
                    # All prompts answered — show continue.
                    if st.button("Continue to Complete →", type="primary"):
                        st.session_state["player_flow_step"] = "complete"
                        st.rerun()
                else:
                    # Clamp (safe guard against content changes).
                    refl_idx = min(refl_idx, len(section_prompt_ids) - 1)
                    prompt_id = section_prompt_ids[refl_idx]
                    question = _PROMPT_QUESTIONS.get(
                        prompt_id,
                        prompt_id.replace("_", " ").capitalize(),
                    )

                    st.caption(f"Reflection {refl_idx + 1} of {len(section_prompt_ids)}")
                    st.markdown(f"**{question}**")

                    txt_key = f"reflection_txt_{active_section_id}_{refl_idx}"
                    st.text_area(
                        label=f"Prompt {refl_idx + 1}",
                        key=txt_key,
                        height=120,
                        placeholder="Write your response here…",
                        label_visibility="collapsed",
                    )

                    if st.button(
                        "Save & Continue →",
                        type="primary",
                        key=f"refl_save_{active_section_id}_{refl_idx}",
                    ):
                        current_text = st.session_state.get(txt_key, "").strip()
                        if current_text:
                            try:
                                save_reflection_response(
                                    lead_id,
                                    COURSE_ID,
                                    active_section_id,
                                    refl_idx,
                                    current_text,
                                    created_at=datetime.now(timezone.utc).isoformat(),
                                    db_path=DB_PATH,
                                )
                                st.session_state["player_refl_idx"] = refl_idx + 1
                                st.rerun()
                            except Exception:
                                logging.exception("Error saving reflection response")
                                st.error("Could not save. Please try again.")
                        else:
                            st.warning("Please write something before continuing.")
    with _col_tutor:
        with st.container(border=True):
            _render_tutor_expander()

# ── COMPLETE ──────────────────────────────────────────────────────────────────
elif step == "complete":
    with st.container(border=True):
        st.subheader("Section completed")
        st.markdown(
            "You've finished all the content for this section. "
            "Record your progress below, then move on to the next section."
        )

        _already_completed = active_section_id in st.session_state.get("player_completed", set())

        # Compact progress summary — prefer already-fetched player_status.
        _status = st.session_state.get("player_status")
        if (
            _status
            and _status.get("lead_exists")
            and _status["course_state"]["completion_pct"] is not None
        ):
            st.metric("Course progress", f"{_status['course_state']['completion_pct']:.1f} %")
        else:
            st.metric(
                "Completed sections",
                f"{len(st.session_state['player_completed'])}/9",
            )

        st.divider()

        if _already_completed:
            st.success("Progress saved — this section is marked complete.")
        elif st.button("Mark Complete", type="primary"):
            occurred_at = datetime.now(timezone.utc).isoformat()
            event_id = f"{lead_id}:{active_section_id}"
            try:
                upsert_lead(lead_id, db_path=DB_PATH)
                record_progress_event(
                    event_id,
                    lead_id,
                    active_section_id,
                    occurred_at=occurred_at,
                    db_path=DB_PATH,
                )
                compute_course_state(lead_id, total_sections=TOTAL_SECTIONS, db_path=DB_PATH)
                updated_status = get_lead_status(lead_id, db_path=DB_PATH)
                st.session_state["player_status"] = updated_status
                _hydrate_completed_from_status(updated_status)
                st.session_state["player_completed"].add(active_section_id)
                # Suppress intercept on the immediate Mark Complete rerun (student stays on same section).
                _trace_backnav("CLEAR_SITE_MARK_COMPLETE_BEFORE")
                _dbg_log(
                    "backnav_pending_set",
                    reason="mark_complete", new_value=None, active_idx=active_idx,
                    confirmed_idx=st.session_state.get("_section_radio_confirmed"),
                    section_radio=st.session_state.get("_section_radio"),
                    section_radio_pending=st.session_state.get("_section_radio_pending"),
                    section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                    suppress_once=st.session_state.get("_suppress_backnav_once"),
                    last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                )
                st.session_state["_backnav_pending_idx"] = None
                _trace_backnav("CLEAR_SITE_MARK_COMPLETE_AFTER")
                st.session_state["_suppress_backnav_once"] = True
                # PLAYER_DEBUG: mark-complete log
                _dbg_log(
                    "marked_complete",
                    active_idx=int(active_idx),
                    active_section_id=active_section_id,
                    state=_dbg_snap(st.session_state),
                )
                # Show unlock feedback when a new section becomes available.
                try:
                    _unlock_before = _allowed_max_idx(
                        st.session_state["player_completed"] - {active_section_id}
                    )
                    _unlock_after = _allowed_max_idx(st.session_state["player_completed"])
                    if _unlock_after > _unlock_before and _unlock_after < len(SECTIONS):
                        _unlocked_title = SECTIONS[_unlock_after][1]
                        try:
                            st.toast(f"Unlocked: {_unlocked_title}")
                        except Exception:
                            pass
                        st.session_state["player_flash"] = (
                            "success", f"Unlocked: {_unlocked_title}"
                        )
                    else:
                        st.session_state["player_flash"] = (
                            "success", f"\u2713 '{active_title}' marked complete."
                        )
                except Exception:
                    st.session_state["player_flash"] = (
                        "success", f"\u2713 '{active_title}' marked complete."
                    )
                st.rerun()
            except ValueError:
                logging.exception("ValueError marking %s complete", active_section_id)
                st.error("Cannot record completion: unrecognised section.")
            except sqlite3.OperationalError:
                st.error("Could not save progress. Check that tmp/app.db is accessible.")
            except Exception:
                logging.exception("Unexpected error in Mark Complete")
                st.error("An unexpected error occurred. See console for details.")

        st.markdown("---")
        _has_next = active_idx < (len(SECTIONS) - 1)
        _next_idx = active_idx + 1
        _already_completed = active_section_id in st.session_state.get("player_completed", set())

        if not _already_completed:
            st.info("Mark the section complete to unlock the next section.")
        elif _already_completed and not _has_next:
            st.success("🎉 Course complete! You've finished all sections.")
            if st.button("← Review this Section"):
                st.session_state["player_flow_step"] = "lesson"
                st.session_state["player_flow_chunk_idx"] = 0
                st.rerun()
        else:
            _dbg_log(
                "next_section_gate",
                run_id=_RUN_ID,
                time=time.monotonic(),
                _section_radio=st.session_state.get("_section_radio"),
                _section_radio_confirmed=st.session_state.get("_section_radio_confirmed"),
                _section_radio_pending=st.session_state.get("_section_radio_pending"),
                _section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                _suppress_backnav_once=st.session_state.get("_suppress_backnav_once"),
                _last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                player_flow_step=st.session_state.get("player_flow_step"),
                player_completed=sorted(list(st.session_state.get("player_completed", []))),
                _backnav_pending_idx=st.session_state.get("_backnav_pending_idx"),
                has_next=_has_next,
                next_idx=int(_next_idx),
            )
            if _has_next:
                with st.form(key=f"next_section_form_{st.session_state.get('_section_radio_confirmed', active_idx)}"):
                    _clicked_next = st.form_submit_button("Go to next section \u2192", type="primary")
            else:
                _clicked_next = False
            _dbg_log(
                "next_section_clicked",
                run_id=_RUN_ID,
                time=time.monotonic(),
                clicked=_clicked_next,
                _section_radio=st.session_state.get("_section_radio"),
                _section_radio_confirmed=st.session_state.get("_section_radio_confirmed"),
                _section_radio_pending=st.session_state.get("_section_radio_pending"),
                _section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                _suppress_backnav_once=st.session_state.get("_suppress_backnav_once"),
                _last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                player_flow_step=st.session_state.get("player_flow_step"),
                player_completed=sorted(list(st.session_state.get("player_completed", []))),
                _backnav_pending_idx=st.session_state.get("_backnav_pending_idx"),
            )
            if _has_next and _clicked_next:
                st.session_state["_backnav_pending_idx"] = None
                st.session_state["_section_radio_pending"] = int(_next_idx)
                st.session_state["_section_radio_confirmed"] = int(_next_idx)
                _dbg_log(
                    "next_section_click",
                    run_id=_RUN_ID,
                    time=time.monotonic(),
                    next_idx=int(_next_idx),
                    _section_radio=st.session_state.get("_section_radio"),
                    _section_radio_confirmed=st.session_state.get("_section_radio_confirmed"),
                    _section_radio_pending=st.session_state.get("_section_radio_pending"),
                    _section_radio_user_changed=st.session_state.get("_section_radio_user_changed"),
                    _suppress_backnav_once=st.session_state.get("_suppress_backnav_once"),
                    _last_sidebar_idx=st.session_state.get("_last_sidebar_idx"),
                    player_flow_step=st.session_state.get("player_flow_step"),
                    player_completed=sorted(list(st.session_state.get("player_completed", []))),
                    _backnav_pending_idx=st.session_state.get("_backnav_pending_idx"),
                )
                st.session_state["_suppress_backnav_once"] = True
                st.session_state["player_flow_step"] = "lesson"
                st.session_state["player_flow_chunk_idx"] = 0
                st.session_state["player_quiz_idx"] = 0
                st.session_state["player_quiz_q_idx"] = 0
                st.session_state["player_quiz_attempts"] = {}
                st.session_state["player_quiz_correct"] = set()
                st.session_state["player_refl_idx"] = 0
                st.rerun()

            if st.button("← Restart this Section"):
                st.session_state["player_flow_step"] = "lesson"
                st.session_state["player_flow_chunk_idx"] = 0
                st.rerun()
