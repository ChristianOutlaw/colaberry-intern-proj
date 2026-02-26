"""
ui/instructor_portal/pages/0_Instructor_Dashboard.py

Instructor Dashboard — placeholder (not yet implemented).

Run from the repository root:
    streamlit run ui/instructor_portal/instructor_app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Instructor Dashboard",
    page_icon="📋",
    layout="wide",
)

st.title("Instructor Dashboard")
st.info("🚧 Coming soon — this page is a placeholder.")
st.markdown(
    """
Planned features for this dashboard:

- **Lead overview** — view all enrolled leads and their course progress
- **Completion metrics** — aggregate completion rates by section and cohort
- **Hot lead list** — leads flagged as HOT by the signal engine
- **Outbox status** — CRM sync status at a glance

No business logic has been implemented here yet.
"""
)
