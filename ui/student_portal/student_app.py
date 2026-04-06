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

from dotenv import load_dotenv
load_dotenv()  # loads .env from repo root into os.environ before anything else runs

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
# Process-level warmup — runs once per server process, not per session.
# Subsequent calls are instant (cache hit).
# ---------------------------------------------------------------------------
@st.cache_resource
def _prewarm_player_resources() -> bool:
    """Initialise DB schema and load course data files before any student
    reaches interactive controls.

    Returns True on success, False on any error (caller shows retry prompt).
    """
    import time
    try:
        from execution.course.load_course_map import load_course_map
        from execution.course.load_quiz_library import load_quiz_library
        from execution.db.sqlite import connect, init_db, get_db_path

        t_total = time.perf_counter()

        t0 = time.perf_counter()
        conn = connect(get_db_path())
        init_db(conn)
        conn.close()
        print(f"[WARMUP] db_init: {round((time.perf_counter() - t0) * 1000)} ms", flush=True)

        t0 = time.perf_counter()
        load_course_map("FREE_INTRO_AI_V0")
        print(f"[WARMUP] course_map: {round((time.perf_counter() - t0) * 1000)} ms", flush=True)

        t0 = time.perf_counter()
        load_quiz_library("FREE_INTRO_AI_V0")
        print(f"[WARMUP] quiz_library: {round((time.perf_counter() - t0) * 1000)} ms", flush=True)

        print(f"[WARMUP] total: {round((time.perf_counter() - t_total) * 1000)} ms", flush=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Readiness gate — warm resources, then switch to the player.
# ---------------------------------------------------------------------------
st.markdown("### 🎓 Loading your course…")
with st.spinner("Setting up your learning session…"):
    _ready = _prewarm_player_resources()

if not _ready:
    st.error("Course resources could not be loaded.")
    st.caption("This is usually a temporary issue. Please refresh the page to try again.")
    st.stop()

st.switch_page("pages/1_Student_Course_Player.py")
