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
from execution.leads.compute_lead_temperature import compute_lead_temperature # noqa: E402
from execution.decision.build_cora_recommendation import (                   # noqa: E402
    build_cora_recommendation,
)
from ui.theme import apply_colaberry_theme                                   # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = str(REPO_ROOT / "tmp" / "app.db")

# Human-readable labels for Cora recommendation event types (see directives/CORA_RECOMMENDATION_EVENTS.md).
_CORA_EVENT_LABELS: dict[str, str] = {
    "SEND_INVITE":           "Send course invite",
    "NUDGE_START_CLASS":     "Nudge to start class",
    "HOT_LEAD_BOOKING":      "Schedule booking call",
    "REENGAGE_STALLED_LEAD": "Re-engage stalled lead",
    "NUDGE_PROGRESS":        "Nudge to continue progress",
    "NO_ACTION":             "No action needed",
}

# Human-readable explanations for HOT signal reason codes (see directives/HOT_LEAD_SIGNAL.md).
_REASON_LABELS: dict[str, str] = {
    "HOT_ENGAGED":                "All gates passed — invite sent, ≥25% complete, active within 7 days.",
    "NOT_INVITED":                "No course invite has been sent yet.",
    "COMPLETION_UNKNOWN":         "No progress events have been recorded.",
    "COMPLETION_BELOW_THRESHOLD": "Course completion is below the 25% threshold.",
    "NO_ACTIVITY_RECORDED":       "No activity recorded since the invite was sent.",
    "ACTIVITY_STALE":             "Last activity was more than 7 days ago.",
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
if "lead_filter" not in st.session_state:
    st.session_state["lead_filter"] = "TOTAL"
if "prev_lead_filter" not in st.session_state:
    st.session_state["prev_lead_filter"] = "TOTAL"
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
    ("TOTAL",     "Total Leads",  lambda rows: len(rows)),
    ("HOT",       "HOT Leads",    lambda rows: sum(1 for r in rows if r["is_hot"] == 1)),
    ("INVITED",   "Invited",      lambda rows: sum(1 for r in rows if r["invited_sent_at"] is not None)),
    ("COMPLETED", "Completed",    lambda rows: sum(1 for r in rows if r["completion_pct"] == 100.0)),
]
def _set_lead_filter(value: str) -> None:
    st.session_state["lead_filter"] = value

with left_col:
    if not load_error:
        fc1, fc2, fc3, fc4 = st.columns(4)
        for _col, (_key, _label, _count_fn) in zip(
            [fc1, fc2, fc3, fc4], _card_defs
        ):
            _count = _count_fn(all_rows)
            _btn_type = "primary" if st.session_state["lead_filter"] == _key else "secondary"
            _col.button(
                f"{_label} ({_count})",
                key=f"filter_{_key}",
                use_container_width=True,
                type=_btn_type,
                on_click=_set_lead_filter,
                args=(_key,),
            )

selected_filter: str = st.session_state["lead_filter"]

# Detect filter change → reset table row selection so detail panel clears cleanly
if st.session_state["prev_lead_filter"] != selected_filter:
    st.session_state["selected_lead_id"] = None
    st.session_state["leads_table_key_version"] += 1
    st.session_state["selection_reset_pending"] = True
st.session_state["prev_lead_filter"] = selected_filter

# Temperature selectbox — rendered in left column, below stat-card buttons.
# Value is read before the temperature filter is applied below.
with left_col:
    if not load_error:
        temp_filter: str = st.selectbox(
            "Temperature filter",
            options=["ALL", "HOT", "WARM", "COLD"],
            index=0,
            key="temp_filter",
        )
    else:
        temp_filter = "ALL"

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
    if selected_filter == "HOT":
        filtered_rows = [r for r in filtered_rows if r["is_hot"] == 1]
    elif selected_filter == "INVITED":
        filtered_rows = [r for r in filtered_rows if r["invited_sent_at"] is not None]
    elif selected_filter == "COMPLETED":
        filtered_rows = [r for r in filtered_rows if r["completion_pct"] == 100.0]

# ---------------------------------------------------------------------------
# Pre-compute temperature for each row in the current view.
# Done once here — reused for temperature filtering, score-based sort, and the
# table display columns. Quiz/reflection inputs are not yet wired (partial signal).
# ---------------------------------------------------------------------------
_temp_map: dict[str, dict] = {}  # lead_id → {signal, label, score}
if not load_error:
    _icons = {"HOT": "🔥 HOT", "WARM": "🌡️ WARM", "COLD": "❄️ COLD"}
    for _r in filtered_rows:
        try:
            _res = compute_lead_temperature(
                now=now_utc,
                invited_sent=_r["invited_sent_at"] is not None,
                completion_percent=_r["completion_pct"],
                last_activity_at=_r["last_activity_at"],
                avg_quiz_score=None,
                avg_quiz_attempts=None,
                reflection_confidence=None,
                current_section=_r["current_section"],
            )
            _temp_map[_r["lead_id"]] = {
                "signal": _res["signal"],
                "label":  _icons.get(_res["signal"], _res["signal"]),
                "score":  _res["score"],
            }
        except Exception:
            logging.exception("Error pre-computing temperature for %s", _r.get("lead_id"))
            _temp_map[_r["lead_id"]] = {"signal": "", "label": "—", "score": 0}

    if temp_filter != "ALL":
        filtered_rows = [
            r for r in filtered_rows
            if _temp_map.get(r["lead_id"], {}).get("signal") == temp_filter
        ]

    # Default sort: higher temperature score first so hotter leads are easy to spot
    filtered_rows = sorted(
        filtered_rows,
        key=lambda r: _temp_map.get(r["lead_id"], {}).get("score", 0),
        reverse=True,
    )

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


def _fmt_activity(raw: str | None, now: datetime) -> str:
    """Convert a stored ISO timestamp to a compact relative label (e.g. '2d ago').

    Args:
        raw: ISO-8601 string from the database, or None.
        now: Reference datetime injected by the caller (must be timezone-aware).

    Returns:
        Human-readable relative label, or '—' when raw is None or unparseable.
    """
    if raw is None:
        return "—"
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        total_seconds = int((now.astimezone(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds())
        if total_seconds < 60:
            return "just now"
        if total_seconds < 3600:
            return f"{total_seconds // 60}m ago"
        if total_seconds < 86400:
            return f"{total_seconds // 3600}h ago"
        days = total_seconds // 86400
        if days <= 60:
            return f"{days}d ago"
        if days <= 365:
            return f"{days // 7}w ago"
        return f"{days // 365}y ago"
    except (ValueError, TypeError):
        return raw[:10] if raw else "—"


with left_col:
    st.subheader(f"Leads ({len(filtered_rows)} shown)")

    if not load_error:
        if filtered_rows:
            display_rows = []
            for r in filtered_rows:
                _t = _temp_map.get(r["lead_id"], {"label": "—", "score": 0})
                display_rows.append({
                    "lead_id":    r["lead_id"],
                    "Name":       r["name"] or "—",
                    "ID":         r["lead_id"],
                    "Completion": r["completion_pct"],           # float | None → ProgressColumn
                    "Started":    _fmt_activity(r.get("started_at"), now_utc),
                    "Section":    r["current_section"] or "—",
                    "Activity":   _fmt_activity(r["last_activity_at"], now_utc),
                    "Temp":       _t["label"],
                    "Score":      _t["score"],
                })
            display_df = pd.DataFrame(display_rows)

            def _row_style(row):
                temp = row.get("Temp", "")
                if temp and "HOT" in temp:
                    return ["background-color: #ffe6e6"] * len(row)
                if temp and "WARM" in temp:
                    return ["background-color: #fff4e0"] * len(row)
                return [""] * len(row)

            styled_df = display_df.style.apply(_row_style, axis=1)
            tbl = st.dataframe(
                styled_df,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "lead_id":    None,  # hidden key column for row selection
                    "Completion": st.column_config.ProgressColumn(
                        "Completion",
                        min_value=0,
                        max_value=100,
                        format="%.0f%%",
                        width="small",
                    ),
                    "Score": st.column_config.NumberColumn(
                        "Score",
                        min_value=0,
                        max_value=100,
                        width="small",
                    ),
                },
                key=f"leads_table_{st.session_state['leads_table_key_version']}",
            )
            _sel_rows = tbl.selection.rows
            if st.session_state["selection_reset_pending"]:
                st.session_state["selection_reset_pending"] = False
            elif _sel_rows:
                st.session_state["selected_lead_id"] = display_df.iloc[_sel_rows[0]]["lead_id"]
            else:
                st.session_state["selected_lead_id"] = None
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

        # Identity — show name when available from the already-loaded overview
        # rows (no extra DB call). Falls back to the raw ID.
        _lead_row = next((r for r in all_rows if r["lead_id"] == selected_lead_id), None)
        _display_name = _lead_row["name"] if (_lead_row and _lead_row["name"]) else None
        if _display_name:
            st.markdown(f"### {_display_name}")
            st.caption(f"ID: `{selected_lead_id}`")
        else:
            st.markdown(f"### `{selected_lead_id}`")

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

                # Progress & activity — 2×2 metric grid
                col_a, col_b = st.columns(2)
                col_a.metric(
                    "Completion",
                    f"{cs['completion_pct']:.0f}%" if cs["completion_pct"] is not None else "—",
                )
                col_b.metric(
                    "Last Activity",
                    _fmt_activity(cs["last_activity_at"], now_utc),
                )
                col_c, col_d = st.columns(2)
                col_c.metric("Invite Sent", "Yes" if status["invite_sent"] else "No")
                col_d.metric("Section", cs["current_section"] or "—")

                st.divider()

                # Lead Temperature Score v1 — additive signal, separate from binary HOT.
                # Quiz scores, avg attempts, and reflection confidence are not yet
                # available in the current data flow; passed as None (partial signal).
                # Pre-declared so Cora section below can always read them safely.
                _ts: str | None = None
                _sc: int | None = None
                _temp_codes: list[str] = []
                st.markdown("**Lead Temperature v1**")
                st.caption("_Partial signal — quiz scores and reflection not yet connected._")
                try:
                    _temp = compute_lead_temperature(
                        now=now_utc,
                        invited_sent=status["invite_sent"],
                        completion_percent=cs["completion_pct"],
                        last_activity_at=cs["last_activity_at"],
                        started_at=cs["started_at"],
                        avg_quiz_score=None,
                        avg_quiz_attempts=None,
                        reflection_confidence=None,
                        current_section=cs["current_section"],
                    )
                    _ts = _temp["signal"]
                    _sc = _temp["score"]
                    _su = _temp["reason_summary"]
                    _temp_codes = _temp["reason_codes"]
                    if _ts == "HOT":
                        st.success(f"🔥 HOT — score {_sc}/100")
                    elif _ts == "WARM":
                        st.warning(f"🌡️ WARM — score {_sc}/100")
                    else:
                        st.info(f"❄️ COLD — score {_sc}/100")
                    # Split "SIGNAL (score N): phrase one; phrase two." into bullet lines.
                    _phrase_block = _su.split(": ", 1)[-1].rstrip(".")
                    _bullets = "\n".join(
                        f"- {p.strip()}" for p in _phrase_block.split("; ") if p.strip()
                    )
                    st.markdown(_bullets)
                except Exception:
                    logging.exception(
                        "Error computing lead temperature for %s", selected_lead_id
                    )
                    st.caption("Temperature score unavailable.")

                st.divider()

                # Cora Recommendation v1 — maps current lead state to a structured
                # outreach event. Preparation only; no outreach is sent here.
                st.markdown("**Cora Recommendation**")
                try:
                    _cora = build_cora_recommendation(
                        now=now_utc,
                        lead_id=selected_lead_id,
                        invite_sent=status["invite_sent"],
                        completion_percent=cs["completion_pct"],
                        current_section=cs["current_section"],
                        last_activity_at=cs["last_activity_at"],
                        hot_signal=hl["signal"],
                        temperature_signal=_ts,
                        temperature_score=_sc,
                        reason_codes=_temp_codes,
                    )
                    _ce = _cora["event_type"]
                    _cp = _cora["priority"]
                    _cc = _cora["recommended_channel"]
                    _cr = _cora["reason_codes"]
                    _ce_label  = _CORA_EVENT_LABELS.get(_ce, _ce)
                    _cc_str    = f"via {_cc}" if _cc else "no outreach"
                    _cora_line = f"{_ce_label} · {_cp} · {_cc_str}"
                    if _ce in ("HOT_LEAD_BOOKING", "NO_ACTION"):
                        st.success(_cora_line)
                    elif _ce == "REENGAGE_STALLED_LEAD":
                        st.error(_cora_line)
                    elif _ce in ("NUDGE_PROGRESS", "NUDGE_START_CLASS"):
                        st.warning(_cora_line)
                    else:
                        st.info(_cora_line)
                    st.caption(", ".join(_cr))
                except Exception:
                    logging.exception(
                        "Error building cora recommendation for %s", selected_lead_id
                    )
                    st.caption("Cora recommendation unavailable.")


    else:
        st.info("Select a lead from the table, or type a Lead ID above.")
