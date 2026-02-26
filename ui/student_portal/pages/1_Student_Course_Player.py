"""
ui/student_portal/pages/1_Student_Course_Player.py

Student Course Player — GPT-like tutor UI
Directive: directives/UI_STUDENT_COURSE_PLAYER.md

Run from the repository root:
    streamlit run ui/student_portal/student_app.py
"""

import logging
import sqlite3
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

from execution.course.course_registry import SECTIONS, TOTAL_SECTIONS      # noqa: E402
from execution.leads.get_lead_status import get_lead_status                # noqa: E402
from execution.leads.upsert_lead import upsert_lead                        # noqa: E402
from execution.progress.compute_course_state import compute_course_state   # noqa: E402
from execution.progress.record_progress_event import record_progress_event  # noqa: E402
from ui.theme import apply_colaberry_theme                                 # noqa: E402
from ui.student_portal.ai_tutor import generate_tutor_reply                # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = str(REPO_ROOT / "tmp" / "app.db")
COURSE_CONTENT_DIR = REPO_ROOT / "course_content" / "FREE_INTRO_AI_V0"
EM_DASH = "\u2014"

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file.
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Student Course Player", layout="wide")
apply_colaberry_theme("Student Portal", "Free Intro to AI")

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

# ---------------------------------------------------------------------------
# Sidebar — Lead ID only (auth boundary; stays out of main columns)
# ---------------------------------------------------------------------------
st.sidebar.title("Course Player")
raw_lead_id = st.sidebar.text_input("Lead ID", placeholder="e.g. lead-123")
lead_id = raw_lead_id.strip()

# Reset per-session tracking whenever the lead changes.
if lead_id != st.session_state["player_lead_id"]:
    st.session_state["player_completed"] = set()
    st.session_state["player_status"] = None
    st.session_state["player_lead_id"] = lead_id

if not lead_id:
    st.sidebar.error("Lead ID is required.")
    st.stop()

# ---------------------------------------------------------------------------
# Helper — fetch lead status (unchanged from original)
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


# Load status once per session (or after a lead change).
if st.session_state["player_status"] is None:
    st.session_state["player_status"] = _fetch_status(lead_id)

# ---------------------------------------------------------------------------
# 3-column layout  [nav | content | tutor]
# ---------------------------------------------------------------------------
col_nav, col_content, col_tutor = st.columns([1.2, 2.5, 1.3])

# ── LEFT: Section navigation + progress + Mark Complete ─────────────────────
with col_nav:
    st.subheader("Sections")
    completed: set[str] = st.session_state["player_completed"]

    active_idx: int = st.radio(
        "Select a section",
        options=range(len(SECTIONS)),
        format_func=lambda i: (
            f"\u2713 {SECTIONS[i][1]}" if SECTIONS[i][0] in completed else SECTIONS[i][1]
        ),
        label_visibility="collapsed",
    )
    active_section_id, active_title = SECTIONS[active_idx]

    # Reset tutor history when the student navigates to a new section.
    if active_section_id != st.session_state["tutor_section_id"]:
        st.session_state["tutor_messages"] = []
        st.session_state["tutor_pending"] = None
        st.session_state["tutor_section_id"] = active_section_id

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
        current = cs["current_section"] or EM_DASH
        last_activity = cs["last_activity_at"] or EM_DASH

    st.metric("Completion", f"{pct:.2f} %")
    st.progress(pct / 100.0)
    st.write(f"**Current:** {current}")
    st.write(f"**Last activity:** {last_activity}")

    st.divider()

    # Mark Complete — unchanged business logic; just relocated from main area.
    if st.button("Mark Complete", type="primary", use_container_width=True):
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
            st.session_state["player_completed"].add(active_section_id)
            st.session_state["player_flash"] = (
                "success",
                f"\u2713 '{active_title}' marked complete.",
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

# ── MIDDLE: Section title + content ─────────────────────────────────────────
with col_content:
    # Flash message — stored before st.rerun() so it survives the cycle.
    if st.session_state["player_flash"] is not None:
        level, msg = st.session_state["player_flash"]
        st.session_state["player_flash"] = None
        if level == "success":
            st.success(msg)
        else:
            st.error(msg)

    st.title(f"{active_section_id} {EM_DASH} {active_title}")

    content_path = COURSE_CONTENT_DIR / f"{active_section_id}.md"
    try:
        section_markdown = content_path.read_text(encoding="utf-8")
    except Exception:
        section_markdown = None

    if section_markdown is None:
        st.warning("Section content unavailable.")
    else:
        st.markdown(section_markdown)

# ── RIGHT: AI Tutor panel ────────────────────────────────────────────────────
with col_tutor:
    st.subheader("AI Tutor")

    # Process any prompt enqueued by a quick-action button click.
    # This runs first so the new exchange appears immediately in the history.
    if st.session_state["tutor_pending"] is not None:
        pending = st.session_state["tutor_pending"]
        st.session_state["tutor_pending"] = None
        st.session_state["tutor_messages"].append({"role": "user", "content": pending})
        reply = generate_tutor_reply(
            section_title=active_title,
            section_markdown=section_markdown or "",
            user_message=pending,
        )
        st.session_state["tutor_messages"].append({"role": "assistant", "content": reply})

    # Quick-action buttons — 2 × 2 grid above the chat.
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
        if st.button("Quiz me (2 questions)", use_container_width=True, key="btn_quiz"):
            st.session_state["tutor_pending"] = (
                "Quiz me with 2 questions about this section."
            )
            st.rerun()

    st.divider()

    # Chat history — rendered top-to-bottom inside a scrollable container.
    for msg in st.session_state["tutor_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Free-form input — student can type any question about the section.
    user_input = st.chat_input("Ask about this section…")
    if user_input:
        st.session_state["tutor_messages"].append({"role": "user", "content": user_input})
        reply = generate_tutor_reply(
            section_title=active_title,
            section_markdown=section_markdown or "",
            user_message=user_input,
        )
        st.session_state["tutor_messages"].append({"role": "assistant", "content": reply})
        st.rerun()
