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

st.title("Dev Portal")
st.warning("⚠ DEV ONLY — This portal exposes admin and diagnostic tools.")
st.markdown(
    """
Use the sidebar to navigate to admin and outbox tools.

---

**Run command (from repo root):**
```
streamlit run ui/dev_portal/dev_app.py
```
"""
)
