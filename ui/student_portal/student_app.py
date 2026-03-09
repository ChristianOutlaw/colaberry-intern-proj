"""
ui/student_portal/student_app.py

Student Portal — entry point.
Pages are discovered automatically from the sibling pages/ directory.

Run from the repository root:
    streamlit run ui/student_portal/student_app.py

Invite-token flow:
    If a ?token=... query param is present, it is resolved to a lead_id
    and stored in session state before the player is loaded.  An invalid
    or unrecognised token shows an error and stops navigation.
    A missing token passes through normally (manual Lead ID entry still works).
"""

import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.resolve_invite_token import resolve_invite_token  # noqa: E402

st.set_page_config(
    page_title="Student Portal",
    page_icon="🎓",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Invite-token resolution — runs before the page switch.
# ---------------------------------------------------------------------------
token = st.query_params.get("token")

if token:
    resolved = resolve_invite_token(token)
    if resolved:
        # Pre-populate the lead identity used by the course player.
        st.session_state["player_lead_id"] = resolved["lead_id"]
    else:
        st.error(
            "This invite link is invalid or has already been used. "
            "Please contact your instructor for a new link."
        )
        st.stop()

st.switch_page("pages/1_Student_Course_Player.py")
st.info("Redirecting… If you are not redirected, use the sidebar.")
