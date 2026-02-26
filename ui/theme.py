"""
ui/theme.py

Colaberry shared theme helper.
Call apply_colaberry_theme() immediately after st.set_page_config() in any
portal page to inject brand styling and render the consistent header bar.

Brand tokens:
    primary red:  #EB3537
    dark black:   #0D0D0D
    light gray:   #EBEBE9
    dark gray:    #5B5A59
    muted teal:   #669091
    slate blue:   #497095
    text:         #1F2937
"""

from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Brand tokens
# ---------------------------------------------------------------------------
_PRIMARY_RED  = "#EB3537"
_DARK_BLACK   = "#0D0D0D"
_LIGHT_GRAY   = "#EBEBE9"
_DARK_GRAY    = "#5B5A59"
_MUTED_TEAL   = "#669091"
_SLATE_BLUE   = "#497095"
_TEXT         = "#1F2937"

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "colaberry_logo(wide).png"

# ---------------------------------------------------------------------------
# CSS — injected once per page render.
# Double braces {{ }} produce literal CSS braces in the f-string.
# ---------------------------------------------------------------------------
_CSS = f"""
<style>
/* Reduce top whitespace */
.block-container {{
    padding-top: 1.2rem !important;
}}

/* Sidebar background */
section[data-testid="stSidebar"] > div:first-child {{
    background-color: {_LIGHT_GRAY};
}}

/* Primary buttons */
.stButton > button[kind="primary"] {{
    background-color: {_PRIMARY_RED} !important;
    color: white !important;
    border: none !important;
    border-radius: 6px !important;
}}
.stButton > button[kind="primary"]:hover {{
    background-color: #c92d2f !important;
    color: white !important;
}}

/* Metric cards */
div[data-testid="metric-container"] {{
    border: 1px solid #E0E0E0;
    border-radius: 8px;
    padding: 0.5rem 0.75rem;
    background-color: white;
}}

/* Red accent line above the header divider */
hr {{
    border-top: 3px solid {_PRIMARY_RED};
}}

/* Dataframe header bolder (best-effort — selector may vary by Streamlit version) */
div[data-testid="stDataFrameResizable"] th {{
    font-weight: 600 !important;
}}
</style>
"""


def apply_colaberry_theme(
    portal_title: str,
    subtitle: str | None = None,
) -> None:
    """Inject Colaberry brand CSS and render the shared header bar.

    Must be called immediately after st.set_page_config() in each portal page.

    Args:
        portal_title: Display name for the portal, e.g. "Student Portal".
        subtitle:     Optional one-liner shown as a caption beneath the title.
    """
    # Inject brand CSS
    st.markdown(_CSS, unsafe_allow_html=True)

    # Header row: logo left, portal title + subtitle right
    logo_col, title_col = st.columns([1, 4])

    with logo_col:
        if _LOGO_PATH.exists():
            st.image(str(_LOGO_PATH), use_container_width=True)
        else:
            st.caption(f"Logo not found at expected path: {_LOGO_PATH}")

    with title_col:
        st.markdown(
            f"<h2 style='margin: 0; padding-top: 0.3rem; color: {_DARK_BLACK};'>"
            f"{portal_title}</h2>",
            unsafe_allow_html=True,
        )
        if subtitle:
            st.caption(subtitle)

    st.divider()
