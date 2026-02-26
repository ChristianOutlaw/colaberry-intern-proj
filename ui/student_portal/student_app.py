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

st.title("Student Portal")
st.markdown(
    """
Welcome to the **Colaberry Free Intro to AI** student portal.

Use the sidebar to navigate to your course.

---

**Run command (from repo root):**
```
streamlit run ui/student_portal/student_app.py
```
"""
)
