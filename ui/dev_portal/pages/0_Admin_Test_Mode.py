"""
ui/dev_portal/pages/0_Admin_Test_Mode.py

Admin / Test Mode harness — DEV ONLY
Directive: directives/ADMIN_TEST_MODE.md

Exposes the three harness operations as a Streamlit page.  No business logic
lives here; all writes are delegated to execution/admin/*.

Run from the repository root:
    streamlit run ui/dev_portal/dev_app.py
"""

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# sys.path bootstrap — this file lives three levels below repo root
# (ui/dev_portal/pages/).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.admin.reset_progress import (          # noqa: E402
    OperationNotConfirmedError,
    reset_progress,
)
from execution.admin.seed_lead import seed_lead        # noqa: E402
from execution.admin.simulate_scenario import simulate_scenario  # noqa: E402
from execution.leads.write_hot_lead_sync_record import (         # noqa: E402
    write_hot_lead_sync_record,
)
from ui.theme import apply_colaberry_theme             # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = str(REPO_ROOT / "tmp" / "app.db")

SCENARIO_IDS: list[str] = [
    "COLD_NO_INVITE",
    "INVITED_NO_PROGRESS",
    "PARTIAL_PROGRESS",
    "HOT_READY",
    "STALE_ACTIVITY",
    "FULL_COMPLETION",
]

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file.
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Admin / Test Mode", layout="centered")
apply_colaberry_theme("Dev Portal", "Admin & diagnostics (internal)")

st.title("Admin / Test Mode (Dev Only)")
st.warning("⚠ DEV ONLY — This tool modifies local SQLite data.")

# ===========================================================================
# SECTION 1 — Seed Lead
# ===========================================================================
st.divider()
st.header("Seed Lead")
st.caption(
    "Creates or updates a lead row and optionally records a course invite. "
    "Safe to run multiple times — both operations are idempotent."
)

s1_lead_id = st.text_input(
    "Lead ID (required)",
    key="s1_lead_id",
    placeholder="e.g. lead-001",
)
s1_name  = st.text_input("Name (optional)",  key="s1_name")
s1_phone = st.text_input("Phone (optional)", key="s1_phone")
s1_email = st.text_input("Email (optional)", key="s1_email")

s1_mark_invite = st.checkbox("Mark invite sent", key="s1_mark_invite")

# Invite sub-fields — only visible when the checkbox is ticked.
s1_invite_id: str | None = None
s1_sent_at:   str | None = None
s1_channel:   str | None = None

if s1_mark_invite:
    s1_invite_id = st.text_input(
        "Invite ID (required when marking invite)",
        key="s1_invite_id",
        placeholder="e.g. invite-001",
    )
    s1_sent_at = st.text_input(
        "Sent At (ISO 8601 UTC, optional)",
        key="s1_sent_at",
        # Convenience default — only for display; datetime.now() is permitted
        # here per directive constraint "except for optional default sent_at display".
        value=datetime.now(timezone.utc).isoformat(),
    )
    s1_channel = st.text_input(
        "Channel (optional, e.g. sms / email)",
        key="s1_channel",
    )

if st.button("Seed Lead", key="btn_seed_lead"):
    try:
        result = seed_lead(
            lead_id=s1_lead_id,
            name=s1_name or None,
            phone=s1_phone or None,
            email=s1_email or None,
            mark_invite_sent=s1_mark_invite,
            invite_id=s1_invite_id or None,
            sent_at=s1_sent_at or None,
            channel=s1_channel or None,
            db_path=DB_PATH,
        )
        if result["ok"]:
            st.success(result["message"])
        else:
            st.error(result["message"])
    except ValueError as exc:
        st.error(str(exc))
    except sqlite3.OperationalError:
        st.error("Database unavailable. Check that tmp/app.db is accessible.")
    except Exception:
        logging.exception("Unexpected error in Seed Lead")
        st.error("An unexpected error occurred. See console for details.")

# ===========================================================================
# SECTION 2 — Reset Progress
# ===========================================================================
st.divider()
st.header("Reset Progress")
st.caption(
    "Deletes all progress events for a lead and optionally clears invite records. "
    "Destructive and irreversible — the lead row is preserved."
)

s2_lead_id      = st.text_input(
    "Lead ID (required)",
    key="s2_lead_id",
    placeholder="e.g. lead-001",
)
s2_reset_invite = st.checkbox(
    "Also clear invite record(s)",
    key="s2_reset_invite",
)
s2_confirm      = st.checkbox(
    "I confirm this is a destructive action",
    key="s2_confirm",
)

if st.button(
    "Reset Progress",
    key="btn_reset_progress",
    disabled=not s2_confirm,
):
    try:
        result = reset_progress(
            lead_id=s2_lead_id,
            reset_invite=s2_reset_invite,
            confirm=True,
            db_path=DB_PATH,
        )
        if result["ok"]:
            st.success(result["message"])
        else:
            st.error(result["message"])
    except OperationNotConfirmedError as exc:
        st.error(str(exc))
    except sqlite3.OperationalError:
        st.error("Database unavailable. Check that tmp/app.db is accessible.")
    except Exception:
        logging.exception("Unexpected error in Reset Progress")
        st.error("An unexpected error occurred. See console for details.")

# ===========================================================================
# SECTION 3 — Simulate Scenario
# ===========================================================================
st.divider()
st.header("Simulate Scenario")
st.caption(
    "Places a lead into one of six deterministic states. "
    "Any existing progress and invite data is reset before the scenario is applied."
)

s3_lead_id  = st.text_input(
    "Lead ID (required)",
    key="s3_lead_id",
    placeholder="e.g. lead-001",
)
s3_scenario = st.selectbox(
    "Scenario",
    options=SCENARIO_IDS,
    key="s3_scenario",
    help=(
        "COLD_NO_INVITE — lead exists, no invite, no progress.\n"
        "INVITED_NO_PROGRESS — invited but no sections completed.\n"
        "PARTIAL_PROGRESS — invited + 3 sections completed (33.33 %).\n"
        "HOT_READY — invited + 3 recent sections (≥ 25 %, within 7 days).\n"
        "STALE_ACTIVITY — invited + 3 sections but last event > 7 days ago.\n"
        "FULL_COMPLETION — all 9 sections completed, invite sent."
    ),
)
s3_confirm  = st.checkbox(
    "I confirm this is a destructive action",
    key="s3_confirm",
)

if st.button(
    "Apply Scenario",
    key="btn_apply_scenario",
    disabled=not s3_confirm,
):
    try:
        result = simulate_scenario(
            scenario_id=s3_scenario,
            lead_id=s3_lead_id,
            confirm=True,
            now=None,   # defaults to real UTC inside simulate_scenario
            db_path=DB_PATH,
        )
        if result["ok"]:
            st.success(result["message"])
        else:
            st.error(result["message"])
    except (ValueError, OperationNotConfirmedError) as exc:
        st.error(str(exc))
    except sqlite3.OperationalError:
        st.error("Database unavailable. Check that tmp/app.db is accessible.")
    except Exception:
        logging.exception("Unexpected error in Simulate Scenario")
        st.error("An unexpected error occurred. See console for details.")

# ===========================================================================
# SECTION 4 — Sync Outbox (Dev)
# ===========================================================================
st.divider()
st.header("Sync Outbox (Dev)")
st.caption(
    "Places a lead into HOT_READY state and writes a NEEDS_SYNC outbox row. "
    "Use this to generate real rows for the Sync Outbox Viewer."
)

s4_lead_id = st.text_input(
    "Lead ID (required)",
    key="s4_lead_id",
    value="OUTBOX_DEMO_01",
)

if st.button("Create HOT lead + write NEEDS_SYNC", key="btn_outbox_demo"):
    try:
        now = datetime.now(timezone.utc)
        sim_result = simulate_scenario(
            scenario_id="HOT_READY",
            lead_id=s4_lead_id,
            confirm=True,
            now=now,
            db_path=DB_PATH,
        )
        if not sim_result["ok"]:
            st.error(sim_result["message"])
        else:
            outbox_result = write_hot_lead_sync_record(
                lead_id=s4_lead_id,
                now=now,
                db_path=DB_PATH,
            )
            if outbox_result["ok"]:
                st.success(
                    f"Lead **{s4_lead_id}** set to HOT_READY. "
                    f"Outbox result: `{outbox_result}`"
                )
                st.info("Open Sync Outbox Viewer to see the NEEDS_SYNC row.")
            else:
                st.error(f"Outbox write failed: {outbox_result}")
    except (ValueError, OperationNotConfirmedError) as exc:
        st.error(str(exc))
    except sqlite3.OperationalError:
        st.error("Database unavailable. Check that tmp/app.db is accessible.")
    except Exception:
        logging.exception("Unexpected error in Sync Outbox (Dev)")
        st.error("An unexpected error occurred. See console for details.")
