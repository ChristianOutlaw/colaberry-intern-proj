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
"""

from __future__ import annotations

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

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "colaberry_logo(wide).png"

# ---------------------------------------------------------------------------
# CSS — injected once per page render.
# Double braces {{ }} produce literal CSS braces in the f-string.
# ---------------------------------------------------------------------------
_CSS = f"""
<style>
/* --- Global layout tightening --- */
.block-container {{
    padding-top: 0.75rem !important;
    padding-bottom: 2rem !important;
}}

/* Hide Streamlit chrome (keeps your app feeling like a real product) */
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}
header {{ visibility: hidden; }}

/* Sidebar background + spacing */
section[data-testid="stSidebar"] > div:first-child {{
    background-color: {_LIGHT_GRAY};
    padding-top: 0.75rem;
}}

/* Sidebar nav pill styling */
section[data-testid="stSidebar"] .stRadio > div {{
    gap: 0.25rem;
}}
section[data-testid="stSidebar"] label {{
    border-radius: 10px;
    padding: 0.35rem 0.5rem;
}}

/* Buttons (primary + hover) */
.stButton > button[kind="primary"] {{
    background-color: {_PRIMARY_RED} !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.45rem 0.9rem !important;
}}
.stButton > button[kind="primary"]:hover {{
    background-color: #c92d2f !important;
    color: white !important;
}}

/* Secondary buttons look cleaner too */
.stButton > button {{
    border-radius: 10px !important;
}}

/* Metric cards */
div[data-testid="metric-container"] {{
    border: 1px solid #E4E4E4;
    border-radius: 12px;
    padding: 0.55rem 0.85rem;
    background-color: white;
}}

/* Dataframe header bolder (best-effort across Streamlit versions) */
div[data-testid="stDataFrameResizable"] th {{
    font-weight: 650 !important;
}}

/* Cleaner dividers */
hr {{
    border: none !important;
    border-top: 1px solid #E7E7E7 !important;
    margin: 1rem 0 !important;
}}
</style>
"""


def _logo_data_url() -> str | None:
    """Return a base64 data URL for the Colaberry logo, or None if missing."""
    if not _LOGO_PATH.exists():
        return None
    b64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def apply_colaberry_theme(
    portal_title: str,
    subtitle: str | None = None,
) -> None:
    """Inject Colaberry brand CSS and render the shared sticky top bar.

    Must be called immediately after st.set_page_config() in each portal page.
    """
    st.markdown(_CSS, unsafe_allow_html=True)

    logo_url = _logo_data_url()
    subtitle_html = (
        f"<div style='color:{_LIGHT_GRAY}; font-size:0.85rem; margin-top:0.15rem;'>{subtitle}</div>"
        if subtitle else
        ""
    )

    # Sticky SaaS header (prevents the “logo floating weird / clipped” look)
    st.markdown(
        f"""
        <div style="
            position: sticky;
            top: 0;
            z-index: 999;
            background: {_DARK_BLACK};
            border-bottom: 3px solid {_PRIMARY_RED};
            padding: 0.65rem 1.25rem;
            margin: -0.75rem -1rem 1.0rem -1rem;
            display: flex;
            align-items: center;
            gap: 1rem;
        ">
            <div style="display:flex; align-items:center;">
                {"<img src='" + logo_url + "' style='height:44px; width:auto; display:block;' />" if logo_url else ""}
            </div>

            <div style="display:flex; flex-direction:column; line-height:1.1;">
                <div style="color:white; font-size:1.25rem; font-weight:650;">
                    {portal_title}
                </div>
                {subtitle_html}
            </div>

            <div style="margin-left:auto; color:{_DARK_GRAY}; font-size:0.85rem;">
                {/* right-side spacer (future: user badge / env tag) */}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
