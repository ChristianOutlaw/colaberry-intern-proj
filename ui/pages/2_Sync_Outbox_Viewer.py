"""
ui/pages/2_Sync_Outbox_Viewer.py

Sync Outbox Viewer — read-only view of sync_records.

Run from the repository root:
    streamlit run ui/app.py
"""

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# sys.path bootstrap — this file lives two levels below repo root (ui/pages/).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.list_sync_records import list_sync_records  # noqa: E402
from execution.leads.mark_sync_record_sent import mark_sync_record_sent  # noqa: E402
from execution.leads.mark_sync_record_failed import mark_sync_record_failed  # noqa: E402

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
if refresh:
    st.session_state["sync_outbox_last_refresh"] = datetime.now(timezone.utc).isoformat()
    st.rerun()

_last_refresh = st.session_state.get("sync_outbox_last_refresh", None)
st.caption(f"Last refreshed: {_last_refresh if _last_refresh else '(never)'}")

# ---------------------------------------------------------------------------
# Dev Actions — manually transition an outbox row to SENT or FAILED.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Dev Actions")

col_dest, col_rjson = st.columns([1, 3])
with col_dest:
    dev_destination = st.text_input("Destination", value="GHL")
with col_rjson:
    dev_response_json = st.text_area(
        "response_json (optional)", placeholder='{"http_status": 200}', height=68
    )

dev_error = st.text_input("error (optional, used for FAILED only)", placeholder="HTTP 500")

col_sent_btn, col_failed_btn = st.columns(2)
with col_sent_btn:
    mark_sent_clicked = st.button("Mark SENT", type="primary")
with col_failed_btn:
    mark_failed_clicked = st.button("Mark FAILED")

_dev_lead_id = lead_id_input.strip() or None

if mark_sent_clicked or mark_failed_clicked:
    if _dev_lead_id is None:
        st.warning("Enter a Lead ID first.")
    else:
        _now = datetime.now(tz=timezone.utc)
        _rjson = dev_response_json.strip() or None
        try:
            if mark_sent_clicked:
                result = mark_sync_record_sent(
                    lead_id=_dev_lead_id,
                    now=_now,
                    destination=dev_destination.strip() or "GHL",
                    response_json=_rjson,
                    db_path=DB_PATH,
                )
                st.success(f"mark_sync_record_sent → {result}")
            else:
                _err = dev_error.strip() or None
                result = mark_sync_record_failed(
                    lead_id=_dev_lead_id,
                    now=_now,
                    destination=dev_destination.strip() or "GHL",
                    error=_err,
                    response_json=_rjson,
                    db_path=DB_PATH,
                )
                st.success(f"mark_sync_record_failed → {result}")
            st.info("Click **Refresh** above to reload the table.")
        except Exception:
            logging.exception("Dev Action failed")
            st.error("Action failed. See console for details.")

st.divider()

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
