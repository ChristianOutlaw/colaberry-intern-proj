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

import base64
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

    # Logo: embed as base64 data URL so it renders inside st.markdown HTML.
    if _LOGO_PATH.exists():
        _img_b64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
        logo_html = (
            f'<img src="data:image/png;base64,{_img_b64}"'
            f' height="40" style="object-fit: contain;" />'
        )
    else:
        logo_html = f'<span style="color: white; font-size: 1rem;">{portal_title}</span>'

    subtitle_html = (
        f'<span style="color: {_LIGHT_GRAY}; font-size: 0.875rem;">{subtitle}</span>'
        if subtitle else ""
    )

    st.markdown(
        f"""
        <div style="
            background-color: {_DARK_BLACK};
            padding: 1rem 1.5rem;
            display: flex;
            align-items: center;
            gap: 1.5rem;
            border-bottom: 3px solid {_PRIMARY_RED};
            margin-bottom: 1rem;
        ">
            {logo_html}
            <div style="display: flex; flex-direction: column;">
                <span style="color: white; font-size: 1.4rem; font-weight: 600; line-height: 1.2;">
                    {portal_title}
                </span>
                {subtitle_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
