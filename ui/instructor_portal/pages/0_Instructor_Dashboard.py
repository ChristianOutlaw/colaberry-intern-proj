"""
ui/instructor_portal/pages/0_Instructor_Dashboard.py

Instructor Dashboard — All Leads overview with search and detail drill-down.
Directive: directives/UI_LEAD_STATUS_VIEW.md (adapted for instructor view)

Run from the repository root:
    streamlit run ui/instructor_portal/instructor_app.py
"""

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# sys.path bootstrap — this file lives three levels below repo root
# (ui/instructor_portal/pages/).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.list_leads_overview import list_leads_overview          # noqa: E402
from execution.leads.get_lead_status import get_lead_status                  # noqa: E402
from execution.decision.decide_next_cold_lead_action import (                # noqa: E402
    decide_next_cold_lead_action,
)
from ui.theme import apply_colaberry_theme                                   # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = str(REPO_ROOT / "tmp" / "app.db")

_ACTION_LABELS: dict[str, str] = {
    "NO_LEAD":           "Lead not found in database.",
    "SEND_INVITE":       "Lead exists but has not received a course invite yet.",
    "NUDGE_START_CLASS": "Invite sent — lead has not started the course.",
    "NUDGE_PROGRESS":    "Course started — lead has not yet completed it.",
    "READY_FOR_BOOKING": "Course complete — lead is ready for a booking call.",
}

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file.
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Instructor Dashboard",
    page_icon="📋",
    layout="wide",
)
apply_colaberry_theme("Instructor Portal", "Lead progress & next actions")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Instructor Dashboard")
st.caption(
    "Read-only view of all leads, their course progress, and recommended next action. "
    "Select a lead from the table or search by name, email, phone, or ID."
)

st.divider()

# ---------------------------------------------------------------------------
# Controls row
# ---------------------------------------------------------------------------
col_search, col_limit, col_hot = st.columns([3, 1, 1])

with col_search:
    search = st.text_input(
        "Search",
        placeholder="Filter by lead ID, name, email, or phone…",
    )

with col_limit:
    limit = st.number_input(
        "Limit",
        min_value=1,
        max_value=1000,
        value=200,
        step=1,
    )

with col_hot:
    show_hot_only = st.checkbox(
        "HOT leads only",
        value=False,
        help="Show only leads with invite sent, ≥25% completion, and activity within the last 7 days.",
    )

# ---------------------------------------------------------------------------
# Load overview — auto-loads on every page render (read-only, fast).
# ---------------------------------------------------------------------------
all_rows: list[dict] = []
load_error = False
now_utc = datetime.now(timezone.utc)  # captured once per render; passed to execution layer

try:
    all_rows = list_leads_overview(db_path=DB_PATH, limit=int(limit), now=now_utc)
except sqlite3.OperationalError:
    st.error(
        "Database unavailable. "
        "Run `streamlit run ui/instructor_portal/instructor_app.py` from the repo root "
        "to ensure tmp/app.db is initialised."
    )
    load_error = True
except Exception:
    logging.exception("Unexpected error loading leads overview")
    st.error("An unexpected error occurred loading leads. See console for details.")
    load_error = True

# ---------------------------------------------------------------------------
# Client-side filters — search then HOT-only (order matters: search first)
# ---------------------------------------------------------------------------
filtered_rows: list[dict] = all_rows
q = search.strip().lower()
if q and not load_error:
    filtered_rows = [
        r for r in all_rows
        if q in (r["lead_id"]  or "").lower()
        or q in (r["name"]     or "").lower()
        or q in (r["email"]    or "").lower()
        or q in (r["phone"]    or "").lower()
    ]

if show_hot_only and not load_error:
    filtered_rows = [r for r in filtered_rows if r["is_hot"]]

# ---------------------------------------------------------------------------
# Summary metrics row — computed from all_rows (unfiltered totals)
# ---------------------------------------------------------------------------
if not load_error:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Leads",  len(all_rows))
    m2.metric("HOT Leads",    sum(1 for r in all_rows if r["is_hot"] == 1))
    m3.metric("Invited",      sum(1 for r in all_rows if r["invited_sent_at"] is not None))
    m4.metric("Completed",    sum(1 for r in all_rows if r["completion_pct"] == 100.0))

# ---------------------------------------------------------------------------
# Overview table — clean column labels for display
# ---------------------------------------------------------------------------
def _lifecycle_status(r: dict) -> str:
    """Derive a single display label from a lead overview row. UI-only helper."""
    if r["is_hot"] == 1:
        return "🔥 HOT"
    if r["completion_pct"] == 100.0:
        return "✅ Completed"
    if r["completion_pct"] is not None and r["completion_pct"] > 0:
        return "📚 In Progress"
    if r["invited_sent_at"] is not None:
        return "📩 Invited"
    return "❄️ Cold"


st.subheader(f"Leads ({len(filtered_rows)} shown)")

if not load_error:
    if filtered_rows:
        display_rows = [
            {
                "Status":        _lifecycle_status(r),
                "Lead ID":       r["lead_id"],
                "Invited":       "Yes" if r["invited_sent_at"] else "No",
                "Completion":    (
                    f"{r['completion_pct']:.1f} %"
                    if r["completion_pct"] is not None
                    else "—"
                ),
                "Section":       r["current_section"] or "—",
                "Last Activity": r["last_activity_at"] or "—",
                "Hot":           "🔥 HOT" if r["is_hot"] == 1 else "Cold",
            }
            for r in filtered_rows
        ]
        st.dataframe(display_rows, use_container_width=True)
    else:
        st.info("No leads match your search. Try a different term or clear the search box.")

st.divider()

# ---------------------------------------------------------------------------
# Lead selection — selectbox from filtered results + optional manual entry
# ---------------------------------------------------------------------------
st.subheader("Lead Detail")

selected_lead_id: str | None = None

if filtered_rows:
    col_pick, col_manual = st.columns([2, 1])

    with col_pick:
        selected_from_list: str = st.selectbox(
            "Select a lead from the list above",
            options=[r["lead_id"] for r in filtered_rows],
        )

    with col_manual:
        manual_input = st.text_input(
            "Or type a Lead ID directly",
            placeholder="e.g. lead-123",
        )

    selected_lead_id = manual_input.strip() if manual_input.strip() else selected_from_list

else:
    manual_input = st.text_input(
        "Type a Lead ID directly",
        placeholder="e.g. lead-123",
    )
    selected_lead_id = manual_input.strip() or None

# ---------------------------------------------------------------------------
# Details panel — rendered only when a lead_id is resolved
# ---------------------------------------------------------------------------
if selected_lead_id:
    st.markdown(f"#### Details — `{selected_lead_id}`")

    # ---- get_lead_status ------------------------------------------------
    status: dict | None = None
    try:
        status = get_lead_status(selected_lead_id, db_path=DB_PATH)
    except sqlite3.OperationalError:
        st.error("Database unavailable when loading lead details.")
    except Exception:
        logging.exception("Unexpected error in get_lead_status for %s", selected_lead_id)
        st.error("An unexpected error occurred loading lead details. See console.")

    if status is not None:
        if not status["lead_exists"]:
            st.warning(f"Lead `{selected_lead_id}` does not exist in the database.")
        else:
            cs = status["course_state"]
            hl = status["hot_lead"]

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Invite Sent",      "Yes" if status["invite_sent"] else "No")
            col_b.metric("Completion",       f"{cs['completion_pct']:.1f} %" if cs["completion_pct"] is not None else "—")
            col_c.metric("Hot Lead Signal",  hl["signal"] or "—")

            col_d, col_e = st.columns(2)
            col_d.markdown(f"**Current Section:** {cs['current_section'] or '—'}")
            col_e.markdown(f"**Last Activity:** {cs['last_activity_at'] or '—'}")

            if hl["reason"]:
                st.caption(f"Signal reason: {hl['reason']}")

    st.divider()

    # ---- decide_next_cold_lead_action -----------------------------------
    st.markdown("**Recommended Next Action**")

    action: str | None = None
    try:
        action = decide_next_cold_lead_action(selected_lead_id, db_path=DB_PATH)
    except sqlite3.OperationalError:
        st.error("Database unavailable when computing recommended action.")
    except Exception:
        logging.exception("Unexpected error in decide_next_cold_lead_action for %s", selected_lead_id)
        st.error("An unexpected error occurred computing the recommended action.")

    if action is not None:
        label = _ACTION_LABELS.get(action, action)
        st.write(label)

else:
    st.info("Select or type a Lead ID above to view details.")
