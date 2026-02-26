"""
ui/student_portal/pages/1_Student_Course_Player.py

Student Course Player — MVP v0
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

# ---------------------------------------------------------------------------
# Constants — hard-coded per directive; not exposed to the learner.
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
    # section_ids the learner has marked complete in this session
    st.session_state["player_completed"] = set()
if "player_lead_id" not in st.session_state:
    st.session_state["player_lead_id"] = ""
if "player_status" not in st.session_state:
    st.session_state["player_status"] = None
if "player_flash" not in st.session_state:
    st.session_state["player_flash"] = None  # (level, message) or None

# ---------------------------------------------------------------------------
# Sidebar — Lead ID
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
# Sidebar — Section navigation
#
# Integer options avoid label-change issues when checkmarks are added/removed
# after a rerun (changing labels would reset the widget selection).
# ---------------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("Sections")

completed: set[str] = st.session_state["player_completed"]

active_idx: int = st.sidebar.radio(
    "Select a section",
    options=range(len(SECTIONS)),
    format_func=lambda i: (
        f"\u2713 {SECTIONS[i][1]}" if SECTIONS[i][0] in completed else SECTIONS[i][1]
    ),
    label_visibility="collapsed",
)
active_section_id, active_title = SECTIONS[active_idx]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fetch_status(lid: str) -> dict | None:
    """Call get_lead_status and return the result, or None on DB/unexpected error."""
    try:
        return get_lead_status(lid, db_path=DB_PATH)
    except sqlite3.OperationalError:
        st.error("Could not save progress. Check that tmp/app.db is accessible.")
    except Exception:
        logging.exception("Unexpected error fetching lead status")
        st.error("An unexpected error occurred. See console for details.")
    return None


def _render_progress_sidebar(status: dict | None) -> None:
    """Render the always-visible progress panel in the sidebar."""
    st.sidebar.divider()
    st.sidebar.subheader("Progress")

    if status is None or not status.get("lead_exists"):
        pct = 0.0
        current = EM_DASH
        last_activity = EM_DASH
    else:
        cs = status["course_state"]
        pct = cs["completion_pct"] if cs["completion_pct"] is not None else 0.0
        current = cs["current_section"] or EM_DASH
        last_activity = cs["last_activity_at"] or EM_DASH

    st.sidebar.metric("Completion", f"{pct:.2f} %")
    st.sidebar.progress(pct / 100.0)
    st.sidebar.write(f"**Current Section:** {current}")
    st.sidebar.write(f"**Last Activity:** {last_activity}")


# ---------------------------------------------------------------------------
# Load status once per session (or after a lead change).
# ---------------------------------------------------------------------------
if st.session_state["player_status"] is None:
    st.session_state["player_status"] = _fetch_status(lead_id)

_render_progress_sidebar(st.session_state["player_status"])

# ---------------------------------------------------------------------------
# Flash message — stored before st.rerun() so it survives the cycle.
# ---------------------------------------------------------------------------
if st.session_state["player_flash"] is not None:
    level, msg = st.session_state["player_flash"]
    st.session_state["player_flash"] = None
    if level == "success":
        st.success(msg)
    else:
        st.error(msg)

# ---------------------------------------------------------------------------
# Main area — section title + read-only content
# ---------------------------------------------------------------------------
st.title(f"{active_section_id} \u2014 {active_title}")

content_path = COURSE_CONTENT_DIR / f"{active_section_id}.md"
try:
    section_markdown = content_path.read_text(encoding="utf-8")
except Exception:
    section_markdown = None

if section_markdown is None:
    st.warning("Section content unavailable.")
else:
    st.markdown(section_markdown)

st.divider()

# ---------------------------------------------------------------------------
# Mark Complete button — calls execution functions in directive-specified order.
# ---------------------------------------------------------------------------
if st.button("Mark Complete", type="primary"):
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
        status = get_lead_status(lead_id, db_path=DB_PATH)

        st.session_state["player_status"] = status
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
