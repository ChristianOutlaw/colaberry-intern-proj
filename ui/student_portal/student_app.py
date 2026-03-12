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
# Entry-screen shell — narrow centred container, phone + desktop safe.
# ---------------------------------------------------------------------------
st.markdown("""
<style>
section.main .block-container {
    max-width: 520px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 4rem !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
    padding-bottom: 3rem !important;
}
@media (max-width: 640px) {
    section.main .block-container {
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
        padding-top: 2.5rem !important;
    }
}
</style>
""", unsafe_allow_html=True)

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
        st.markdown("### Access unavailable")
        st.markdown("This invite link is **invalid or could not be verified.**")
        st.markdown("Please contact your instructor for a new access link.")
        st.stop()

# ---------------------------------------------------------------------------
# Brief loading state — visible for a moment while the player page loads.
# ---------------------------------------------------------------------------
st.markdown("### 🎓 Loading your course…")
st.caption("Setting up your learning session — you'll be redirected automatically.")

st.switch_page("pages/1_Student_Course_Player.py")
