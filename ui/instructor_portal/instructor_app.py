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

st.title("Instructor Portal")
st.markdown(
    """
Welcome to the **Colaberry Instructor Portal**.

Use the sidebar to navigate to instructor tools.

---

**Run command (from repo root):**
```
streamlit run ui/instructor_portal/instructor_app.py
```
"""
)
