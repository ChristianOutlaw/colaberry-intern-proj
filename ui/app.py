"""
ui/app.py

Lead Status Viewer — MVP v1
Directive: directives/UI_LEAD_STATUS_VIEW.md

Run from the repository root:
    streamlit run ui/app.py
"""

import logging
import sqlite3
import sys
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path so execution.* imports work regardless of
# where Streamlit is launched from.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.get_lead_status import get_lead_status  # noqa: E402
from execution.leads.upsert_lead import upsert_lead          # noqa: E402

DB_PATH = str(REPO_ROOT / "tmp" / "app.db")
EM_DASH = "\u2014"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Lead Status Viewer", layout="centered")
st.title("Lead Status Viewer")

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
lead_id = st.text_input("Lead ID", placeholder="e.g. lead-123")
total_sections = st.number_input(
    "Total Sections",
    min_value=1,
    value=10,
    step=1,
    help="Accepted for future use; not applied during a read-only fetch.",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_FIELD_LABELS = [
    "Invite Sent",
    "Course Completion",
    "Last Activity",
    "Current Section",
    "Hot Lead Signal",
    "Reason",
]


def _fmt(val) -> str:
    """Format a single status value for display. None → em dash."""
    if val is None:
        return EM_DASH
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, float):
        return f"{val:.1f}%"
    return str(val)


def _render_table(values: list) -> None:
    """Render all six status rows. Always renders every label."""
    for label, value in zip(_FIELD_LABELS, values):
        col1, col2 = st.columns([1, 2])
        col1.markdown(f"**{label}**")
        col2.write(value)


# ---------------------------------------------------------------------------
# Fetch action
# ---------------------------------------------------------------------------
if st.button("Fetch Status"):

    # -- Input validation (before any DB call) ------------------------------
    if not lead_id or not lead_id.strip():
        st.error("Lead ID is required.")
    elif total_sections < 1:
        st.error("Total sections must be at least 1.")

    else:
        status = None
        try:
            upsert_lead(lead_id, db_path=DB_PATH)
            status = get_lead_status(lead_id, db_path=DB_PATH)
        except sqlite3.OperationalError:
            st.error("Database unavailable. Check that tmp/app.db exists.")
        except Exception:
            logging.exception("Unexpected error in Lead Status Viewer")
            st.error("An unexpected error occurred. See console for details.")

        if status is not None:
            if not status["lead_exists"]:
                st.warning("Lead not found.")
                _render_table([EM_DASH] * 6)
            else:
                cs = status["course_state"]
                hl = status["hot_lead"]
                _render_table([
                    _fmt(status["invite_sent"]),
                    _fmt(cs["completion_pct"]),
                    _fmt(cs["last_activity_at"]),
                    _fmt(cs["current_section"]),
                    _fmt(hl["signal"]),
                    _fmt(hl["reason"]),
                ])
