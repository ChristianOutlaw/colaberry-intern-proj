"""
ui/dev_portal/dev_app.py

Dev Portal — entry point.
Pages are discovered automatically from the sibling pages/ directory.

Run from the repository root:
    streamlit run ui/dev_portal/dev_app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Dev Portal",
    page_icon="🔧",
    layout="wide",
)

st.switch_page("pages/0_Admin_Test_Mode.py")
st.info("Redirecting… If you are not redirected, use the sidebar.")
