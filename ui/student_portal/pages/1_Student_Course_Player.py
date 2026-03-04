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
if "tutor_messages" not in st.session_state:
    st.session_state["tutor_messages"] = []  # list[{"role": str, "content": str}]
if "tutor_pending" not in st.session_state:
    st.session_state["tutor_pending"] = None  # prompt enqueued by quick-action button
if "tutor_section_id" not in st.session_state:
    st.session_state["tutor_section_id"] = None  # tracks section for history reset
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
    st.session_state["_backnav_pending_idx"] = None
# Suppress false back-nav intercept on internal forward navigation reruns.
if "_suppress_backnav_once" not in st.session_state:
    st.session_state["_suppress_backnav_once"] = False
if "_last_sidebar_idx" not in st.session_state:
    st.session_state["_last_sidebar_idx"] = 0

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

    # Apply deferred section navigation BEFORE the radio is instantiated.
    # (Setting _section_radio after the widget exists raises a Streamlit error.)
    if "_section_radio_pending" in st.session_state:
        _pend = max(0, min(len(SECTIONS) - 1, int(st.session_state["_section_radio_pending"])))
        st.session_state["_section_radio"] = _pend
        del st.session_state["_section_radio_pending"]
        # PLAYER_DEBUG: pending-apply log
        _dbg_log(
            "pending_applied",
            applied_section=int(st.session_state.get("_section_radio", -1)),
            pending_was=int(_pend),
            frontier=int(_frontier) if "_frontier" in dir() else None,
            state=_dbg_snap(st.session_state),
        )

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

        active_idx: int = st.radio(
            "Select a section",
            options=range(len(SECTIONS)),
            format_func=lambda i: (
                f"\u2705 {SECTIONS[i][1]}"
                if SECTIONS[i][0] in completed
                else (
                    f"\U0001f512 {SECTIONS[i][1]}"
                    if i > allowed_max_idx
                    else (
                        f"\u25b6 {SECTIONS[i][1]}"
                        if i == st.session_state.get("_section_radio", 0)
                        else SECTIONS[i][1]
                    )
                )
            ),
            key="_section_radio",
            label_visibility="collapsed",
        )
        active_section_id, active_title = SECTIONS[active_idx]

        # Keep "confirmed" aligned with where the UI has actually landed.
        # This prevents false back-nav warnings during forward navigation/reruns.
        _prev_confirmed_idx = int(st.session_state.get("_section_radio_confirmed", 0))
        if active_idx > _prev_confirmed_idx:
            st.session_state["_section_radio_confirmed"] = int(active_idx)

        # Back-nav confirmation intercept (user-driven only):
        # Only trigger when the student *clicks* a previously completed section in the sidebar.
        # Prevents false warnings during internal reruns / pending navigation.
        _confirmed_idx = int(st.session_state.get("_section_radio_confirmed", 0))
        _last_idx      = int(st.session_state.get("_last_sidebar_idx", _confirmed_idx))
        _has_pending   = "_section_radio_pending" in st.session_state

        # One-shot suppression: forward nav and Mark Complete set this flag before rerun.
        if st.session_state.get("_suppress_backnav_once"):
            _dbg_log(
                "backnav_intercept_SUPPRESSED",
                active_idx=int(active_idx),
                confirmed_idx=int(_confirmed_idx),
                state=_dbg_snap(st.session_state),
            )
            st.session_state["_suppress_backnav_once"] = False
        else:
            # Only treat it as a sidebar click when the index actually changed AND
            # there is no internal pending navigation in flight.
            _user_changed_sidebar = (active_idx != _last_idx) and (not _has_pending)
            _target_completed     = SECTIONS[active_idx][0] in st.session_state.get("player_completed", set())

            if _user_changed_sidebar and _target_completed and active_idx < _confirmed_idx:
                _dbg_log(
                    "backnav_intercept_TRIPPED",
                    active_idx=int(active_idx),
                    confirmed_idx=int(_confirmed_idx),
                    last_sidebar_idx=int(_last_idx),
                    state=_dbg_snap(st.session_state, {"allowed_max_idx": int(allowed_max_idx)}),
                )
                st.session_state["_backnav_pending_idx"] = int(active_idx)
                st.session_state["_section_radio_pending"] = _confirmed_idx
                st.rerun()

        # Track last rendered sidebar selection (stabilises click detection).
        st.session_state["_last_sidebar_idx"] = int(active_idx)

        # Enforce lock: redirect back to the furthest allowed section.
        if active_idx > allowed_max_idx:
            st.session_state["_section_radio_pending"] = allowed_max_idx
            st.session_state["player_flash"] = (
                "info",
                "Complete the current section to unlock the next one.",
            )
            st.rerun()

        # Reset tutor history when the student navigates to a new section.
        if active_section_id != st.session_state["tutor_section_id"]:
            st.session_state["tutor_messages"] = []
            st.session_state["tutor_pending"] = None
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
                st.session_state["_backnav_pending_idx"] = None
                st.rerun()
        with _c2:
            if st.button("Cancel", key="btn_backnav_cancel"):
                st.session_state["_backnav_pending_idx"] = None
                st.rerun()

    st.stop()


# ── Course-level welcome screen ────────────────────────────────────────────────
# Portal gate: always shown until the student clicks "Begin Course →".
if not st.session_state.get("player_course_started"):
    _cw_lines = [
        "This course guides you through the fundamentals of AI in 9 short sections.",
        "Each section follows the same pattern: read a guided lesson, test your "
        "understanding with a quiz, then capture a brief reflection.",
        "Work at your own pace — your progress is saved automatically after each section.",
    ]
    _cw_key = "course_welcome_typed"
    with st.container(border=True):
        st.markdown("## Welcome to **Intro to AI**")
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
        st.markdown("<div style='height: 18px'></div>", unsafe_allow_html=True)
        if st.button("Begin Course →", type="primary", key="btn_begin_course"):
            if not lead_id:
                st.info("Enter your Lead ID in the sidebar to begin.")
            else:
                # Resume: pick up at the last recorded section when available.
                status = st.session_state.get("player_status")
                if status is None:
                    try:
                        st.session_state["player_status"] = _fetch_status(lead_id)
                        status = st.session_state["player_status"]
                    except Exception:
                        status = None

                _hydrate_completed_from_status(status)
                resume_idx = _unlocked_frontier_idx(st.session_state.get("player_completed", set()), status)
                # If DB current_section points further ahead, prefer it.
                try:
                    cs = (status or {}).get("course_state") or {}
                    current_section = cs.get("current_section")
                    if current_section:
                        _idx_map = {sid: i for i, (sid, _t) in enumerate(SECTIONS)}
                        resume_idx = max(resume_idx, _idx_map.get(current_section, 0))
                except Exception:
                    pass
                resume_idx = max(0, min(len(SECTIONS) - 1, int(resume_idx)))

                st.session_state["player_course_started"] = True
                st.session_state["player_flow_step"] = "lesson"
                st.session_state["player_flow_chunk_idx"] = 0
                st.session_state["player_quiz_idx"] = 0
                st.session_state["player_quiz_q_idx"] = 0
                st.session_state["player_quiz_attempts"] = {}
                st.session_state["player_quiz_correct"] = set()
                st.session_state["player_refl_idx"] = 0
                # Write before rerun so the radio picks it up on the next render.
                st.session_state["_section_radio"] = resume_idx
                st.session_state["_section_radio_confirmed"] = int(resume_idx)
                st.rerun()
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

st.markdown(
    f"""<div class="cb-topbar">
      <p class="cb-topbar-caption">Section {active_idx + 1} of {len(SECTIONS)}</p>
      <p class="cb-topbar-title">{active_title}</p>
    </div>""",
    unsafe_allow_html=True,
)
st.progress(_bar_val)


# ── Tutor expander — closure over active_title / section_markdown ─────────────
def _render_tutor_expander() -> None:
    with st.expander("Tutor"):
        st.subheader("AI Tutor")

        # Process any prompt enqueued by a quick-action button click.
        if st.session_state["tutor_pending"] is not None:
            pending = st.session_state["tutor_pending"]
            st.session_state["tutor_pending"] = None
            st.session_state["tutor_messages"].append({"role": "user", "content": pending})
            tutor_reply = generate_tutor_reply(
                section_title=active_title,
                section_markdown=section_markdown or "",
                user_message=pending,
            )
            st.session_state["tutor_messages"].append(
                {"role": "assistant", "content": tutor_reply}
            )

        # Quick-action buttons — 2 × 2 grid.
        b_left, b_right = st.columns(2)
        with b_left:
            if st.button("Summarize", use_container_width=True, key="btn_summarize"):
                st.session_state["tutor_pending"] = "Summarize this section for me."
                st.rerun()
            if st.button("Give me an example", use_container_width=True, key="btn_example"):
                st.session_state["tutor_pending"] = (
                    "Give me a concrete example of the key ideas in this section."
                )
                st.rerun()
        with b_right:
            if st.button("Explain like I'm new", use_container_width=True, key="btn_explain"):
                st.session_state["tutor_pending"] = (
                    "Explain this section like I'm completely new to the topic."
                )
                st.rerun()
            if st.button(
                "Quiz me (2 questions)", use_container_width=True, key="btn_quiz"
            ):
                st.session_state["tutor_pending"] = (
                    "Quiz me with 2 questions about this section."
                )
                st.rerun()

        st.divider()

        # Chat history — rendered top-to-bottom.
        for msg in st.session_state["tutor_messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Free-form input.
        user_input = st.chat_input("Ask about this section…")
        if user_input:
            st.session_state["tutor_messages"].append(
                {"role": "user", "content": user_input}
            )
            tutor_reply = generate_tutor_reply(
                section_title=active_title,
                section_markdown=section_markdown or "",
                user_message=user_input,
            )
            st.session_state["tutor_messages"].append(
                {"role": "assistant", "content": tutor_reply}
            )
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
    with st.container(border=True):
        st.markdown("<div style='height: 4px'></div>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.8rem;color:#6B7280;margin-bottom:6px;'>"
            f"Chunk {chunk_idx + 1} of {n_chunks}</div>",
            unsafe_allow_html=True,
        )
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
            fwd_label = f"Continue → (Part {chunk_idx + 2} of {n_chunks})"

        st.markdown("<div style='height: 8px'></div>", unsafe_allow_html=True)
        col_back, col_fwd = st.columns([1, 3])
        with col_back:
            if chunk_idx > 0:
                if st.button("← Back"):
                    st.session_state["player_flow_chunk_idx"] = chunk_idx - 1
                    st.rerun()
        with col_fwd:
            if st.button(fwd_label, type="primary"):
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

    _render_tutor_expander()

# ── QUIZ ──────────────────────────────────────────────────────────────────────
elif step == "quiz":
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

    _render_tutor_expander()

# ── REFLECTION ────────────────────────────────────────────────────────────────
elif step == "reflection":
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
            if _has_next and st.button("Go to next section \u2192", type="primary"):
                # PLAYER_DEBUG: next-section click log
                _dbg_log(
                    "next_section_clicked",
                    from_active_idx=int(active_idx),
                    to_next_idx=int(_next_idx),
                    flow_step=st.session_state.get("player_flow_step"),
                    state=_dbg_snap(st.session_state),
                )
                # Suppress intercept on the immediate rerun (internal forward navigation).
                st.session_state["_suppress_backnav_once"] = True
                # Defer navigation: pending key is resolved before the radio renders.
                st.session_state["_section_radio_pending"] = _next_idx
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
