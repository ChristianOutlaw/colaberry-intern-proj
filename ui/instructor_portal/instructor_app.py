"""
ui/instructor_portal/instructor_app.py

Instructor Portal — entry point.
Pages are discovered automatically from the sibling pages/ directory.

Run from the repository root:
    streamlit run ui/instructor_portal/instructor_app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Instructor Portal",
    page_icon="📋",
    layout="wide",
)

st.switch_page("pages/0_Instructor_Dashboard.py")
st.info("Redirecting… If you are not redirected, use the sidebar.")
