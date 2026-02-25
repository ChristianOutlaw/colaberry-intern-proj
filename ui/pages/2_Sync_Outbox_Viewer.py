"""
ui/pages/2_Sync_Outbox_Viewer.py

Sync Outbox Viewer — read-only view of sync_records.

Run from the repository root:
    streamlit run ui/app.py
"""

import logging
import sqlite3
import sys
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# sys.path bootstrap — this file lives two levels below repo root (ui/pages/).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.list_sync_records import list_sync_records  # noqa: E402

DB_PATH = str(REPO_ROOT / "tmp" / "app.db")

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file.
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Sync Outbox Viewer", layout="wide")
st.title("Sync Outbox Viewer")

# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
col_status, col_lead, col_limit = st.columns([1, 2, 1])

with col_status:
    status_choice = st.selectbox(
        "Status filter",
        options=["ALL", "NEEDS_SYNC", "SENT", "FAILED"],
    )

with col_lead:
    lead_id_input = st.text_input("Lead ID (optional)", placeholder="e.g. lead-123")

with col_limit:
    limit = st.number_input("Limit", min_value=1, max_value=1000, value=100, step=1)

refresh = st.button("Refresh")

# ---------------------------------------------------------------------------
# Fetch — runs on initial load and on every Refresh click.
# ---------------------------------------------------------------------------
status_filter = None if status_choice == "ALL" else status_choice
lead_id_filter = lead_id_input.strip() or None

rows = None
try:
    rows = list_sync_records(
        db_path=DB_PATH,
        status=status_filter,
        lead_id=lead_id_filter,
        limit=int(limit),
    )
except sqlite3.OperationalError:
    st.error("Database unavailable. Check that tmp/app.db exists.")
except Exception:
    logging.exception("Unexpected error in Sync Outbox Viewer")
    st.error("An unexpected error occurred. See console for details.")

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
if rows is not None:
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("No sync records found.")
