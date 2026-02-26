"""
ui/student_portal/student_app.py

Student Portal — entry point.
Pages are discovered automatically from the sibling pages/ directory.

Run from the repository root:
    streamlit run ui/student_portal/student_app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Student Portal",
    page_icon="🎓",
    layout="wide",
)

st.switch_page("pages/1_Student_Course_Player.py")
st.info("Redirecting… If you are not redirected, use the sidebar.")
