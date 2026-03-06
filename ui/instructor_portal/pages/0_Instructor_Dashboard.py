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

import pandas as pd
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
# Session state — persists selected lead and active dashboard filter across reruns
# ---------------------------------------------------------------------------
if "selected_lead_id" not in st.session_state:
    st.session_state["selected_lead_id"] = None
if "dashboard_filter" not in st.session_state:
    st.session_state["dashboard_filter"] = "ALL"
if "prev_dashboard_filter" not in st.session_state:
    st.session_state["prev_dashboard_filter"] = "ALL"
if "leads_table_key_version" not in st.session_state:
    st.session_state["leads_table_key_version"] = 0
if "selection_reset_pending" not in st.session_state:
    st.session_state["selection_reset_pending"] = False

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
# Two-column CRM layout
# ---------------------------------------------------------------------------
left_col, right_col = st.columns([2, 1])

# ---------------------------------------------------------------------------
# LEFT — controls
# ---------------------------------------------------------------------------
with left_col:
    col_search, col_limit = st.columns([4, 1])

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

# ---------------------------------------------------------------------------
# Load overview — auto-loads on every page render (read-only, fast).
# ---------------------------------------------------------------------------
all_rows: list[dict] = []
load_error = False
now_utc = datetime.now(timezone.utc)  # captured once per render; passed to execution layer

try:
    all_rows = list_leads_overview(db_path=DB_PATH, limit=int(limit), now=now_utc)
except sqlite3.OperationalError:
    with left_col:
        st.error(
            "Database unavailable. "
            "Run `streamlit run ui/instructor_portal/instructor_app.py` from the repo root "
            "to ensure tmp/app.db is initialised."
        )
    load_error = True
except Exception:
    logging.exception("Unexpected error loading leads overview")
    with left_col:
        st.error("An unexpected error occurred loading leads. See console for details.")
    load_error = True

# ---------------------------------------------------------------------------
# Clickable filter cards — counts always reflect unfiltered all_rows totals.
# Read current filter before rendering buttons (for type="primary" styling),
# then re-read after to capture any click that fired this render.
# ---------------------------------------------------------------------------
_card_defs = [
    ("ALL",       "Total Leads",  lambda rows: len(rows)),
    ("HOT",       "HOT Leads",    lambda rows: sum(1 for r in rows if r["is_hot"] == 1)),
    ("INVITED",   "Invited",      lambda rows: sum(1 for r in rows if r["invited_sent_at"] is not None)),
    ("COMPLETED", "Completed",    lambda rows: sum(1 for r in rows if r["completion_pct"] == 100.0)),
]
_active_pre: str = st.session_state["dashboard_filter"]

with left_col:
    if not load_error:
        fc1, fc2, fc3, fc4 = st.columns(4)
        for _col, (_key, _label, _count_fn) in zip(
            [fc1, fc2, fc3, fc4], _card_defs
        ):
            _count = _count_fn(all_rows)
            _btn_type = "primary" if _active_pre == _key else "secondary"
            if _col.button(
                f"{_label} ({_count})",
                key=f"filter_{_key}",
                use_container_width=True,
                type=_btn_type,
            ):
                st.session_state["dashboard_filter"] = _key

# Re-read after buttons — captures any click from this render pass
active_filter: str = st.session_state["dashboard_filter"]

# Detect filter change → reset table row selection so detail panel clears cleanly
if st.session_state["prev_dashboard_filter"] != active_filter:
    st.session_state["selected_lead_id"] = None
    st.session_state["leads_table_key_version"] += 1
    st.session_state["selection_reset_pending"] = True
st.session_state["prev_dashboard_filter"] = active_filter

# ---------------------------------------------------------------------------
# Client-side filters — search first, card filter second (order matters)
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

if not load_error:
    if active_filter == "HOT":
        filtered_rows = [r for r in filtered_rows if r["is_hot"] == 1]
    elif active_filter == "INVITED":
        filtered_rows = [r for r in filtered_rows if r["invited_sent_at"] is not None]
    elif active_filter == "COMPLETED":
        filtered_rows = [r for r in filtered_rows if r["completion_pct"] == 100.0]

# ---------------------------------------------------------------------------
# Safety guard — clear selection if the selected lead left the filtered view
# ---------------------------------------------------------------------------
_filtered_ids = {r["lead_id"] for r in filtered_rows}
if st.session_state["selected_lead_id"] not in _filtered_ids:
    st.session_state["selected_lead_id"] = None

# ---------------------------------------------------------------------------
# LEFT — table
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


with left_col:
    st.subheader(f"Leads ({len(filtered_rows)} shown)")

    if not load_error:
        if filtered_rows:
            display_rows = [
                {
                    "lead_id":       r["lead_id"],
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
            display_df = pd.DataFrame(display_rows)
            tbl = st.dataframe(
                display_df,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={"lead_id": None},  # hide key column from display
                key=f"leads_table_{st.session_state['leads_table_key_version']}",
            )
            _sel_rows = tbl.selection.rows
            if st.session_state["selection_reset_pending"]:
                st.session_state["selection_reset_pending"] = False
            elif _sel_rows:
                st.session_state["selected_lead_id"] = display_df.iloc[_sel_rows[0]]["lead_id"]
        else:
            st.info("No leads match your search. Try a different term or clear the search box.")

# ---------------------------------------------------------------------------
# RIGHT — lead selection + detail panel
# ---------------------------------------------------------------------------
with right_col:
    st.subheader("Lead Detail")

    # Manual override — takes priority over table click when non-empty
    manual_input = st.text_input(
        "Or type a Lead ID directly",
        placeholder="e.g. lead-123",
    )
    if manual_input.strip():
        st.session_state["selected_lead_id"] = manual_input.strip()

    selected_lead_id: str | None = st.session_state.get("selected_lead_id")

    # ---- details panel — only when a lead_id is resolved ----------------
    if selected_lead_id:
        st.markdown(f"#### Details — `{selected_lead_id}`")

        # ---- get_lead_status --------------------------------------------
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

        # ---- decide_next_cold_lead_action --------------------------------
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
        st.info("Select a lead from the table to view details.")
