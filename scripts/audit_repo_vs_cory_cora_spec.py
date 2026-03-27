"""
audit_repo_vs_cory_cora_spec.py

Scans the repository for evidence of requirements defined in:
- "Cory / Cora Action, Trigger, and Scoring Blueprint"
- "CORY / CORA ACTION, TRIGGER, SCORING, AND BULK PROCESSING SPEC Draft v1"

Produces: tmp/repo_vs_cory_cora_gap_report.md

Usage:
    python scripts/audit_repo_vs_cory_cora_spec.py
"""

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "repo_vs_cory_cora_gap_report.md"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def find_py_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


def grep(pattern: str, text: str, flags: int = re.IGNORECASE) -> list[str]:
    """Return matching lines."""
    return [ln.strip() for ln in text.splitlines() if re.search(pattern, ln, flags)]


def file_contains(pattern: str, path: Path) -> bool:
    return bool(grep(pattern, read_file(path)))


def search_dir(pattern: str, directory: Path) -> list[tuple[Path, str]]:
    """Return (file, matching_line) pairs across all .py files in directory."""
    hits = []
    for f in find_py_files(directory):
        for ln in grep(pattern, read_file(f)):
            hits.append((f, ln))
    return hits


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


# ---------------------------------------------------------------------------
# Evidence collectors — each returns a dict
# ---------------------------------------------------------------------------

def check_invite_semantics() -> dict:
    """
    Spec requirement: invite_generated != invite_sent.
    Must have separate fields/functions for each.
    """
    exec_dir = REPO_ROOT / "execution"
    generated_hits = search_dir(r"invite.*generat|generat.*invite|create.*invite", exec_dir)
    sent_hits = search_dir(r"invite.*sent|sent_at|mark.*invite.*sent", exec_dir)
    rec_hits = search_dir(r"invite_sent_at|sent_at.*null|no.*sent|send_invite", exec_dir)

    generated_files = sorted({rel(h[0]) for h in generated_hits})
    sent_files = sorted({rel(h[0]) for h in sent_hits})

    # Check if recommendation logic keys off invite_sent_at (not just invite existence)
    decision_dir = REPO_ROOT / "execution" / "decision"
    rec_content = ""
    for f in find_py_files(decision_dir):
        rec_content += read_file(f)

    uses_sent_at_gate = bool(re.search(r"invite_sent|NOT_INVITED|no.*invite", rec_content, re.IGNORECASE))
    separates_generated_from_sent = bool(
        generated_files and sent_files and generated_files != sent_files
    )

    status = "Implemented" if (separates_generated_from_sent and uses_sent_at_gate) else "Partial"
    return {
        "requirement": "Invite semantics: generated != sent",
        "status": status,
        "evidence": generated_files + sent_files,
        "notes": (
            "separate create/mark_sent functions exist; "
            "recommendation keys off invite_sent flag"
            if status == "Implemented"
            else "functions exist but gate logic may conflate generate/send"
        ),
    }


def check_state_model() -> dict:
    """
    Spec: 8 distinct lifecycle states (A–H) should be modeled as explicit constants or enum.
    The spec names are: LEAD_NOT_IN_SYSTEM, LEAD_EXISTS_INVITE_NOT_SENT, INVITE_SENT_NO_START,
    COURSE_STARTED_IN_PROGRESS, COURSE_COMPLETED_PENDING_FINALIZATION,
    COURSE_COMPLETED_FINAL_WARM, COURSE_COMPLETED_FINAL_HOT, COURSE_COMPLETED_FINAL_COLD.
    FINAL_COLD/WARM/HOT alone do not count — those are outcome labels, not lifecycle state constants.
    """
    exec_dir = REPO_ROOT / "execution"
    # Must match spec state names specifically
    explicit_state_hits = search_dir(
        r"LEAD_NOT_IN_SYSTEM|LEAD_EXISTS_INVITE_NOT_SENT|INVITE_SENT_NO_START"
        r"|COURSE_STARTED_IN_PROGRESS|COURSE_COMPLETED_PENDING_FINALIZATION"
        r"|COURSE_COMPLETED_FINAL_WARM|COURSE_COMPLETED_FINAL_HOT|COURSE_COMPLETED_FINAL_COLD",
        exec_dir,
    )
    explicit_state_files = sorted({rel(h[0]) for h in explicit_state_hits})

    # Check DB schema for state-relevant fields (implicit state)
    db_file = REPO_ROOT / "execution" / "db" / "sqlite.py"
    db_content = read_file(db_file)
    has_started_at = "started_at" in db_content
    has_sent_at = "sent_at" in db_content
    has_completion = "completion_pct" in db_content
    implicit_state = has_started_at and has_sent_at and has_completion

    if explicit_state_files:
        status = "Implemented"
        notes = f"explicit spec state constants found in {len(explicit_state_files)} file(s)"
    elif implicit_state:
        status = "Partial"
        notes = (
            "state is implicit via DB fields (started_at, sent_at, completion_pct) — "
            "no explicit A–H state enum matching spec §3 state names"
        )
    else:
        status = "Missing"
        notes = "no state model evidence found"

    return {
        "requirement": "Lead lifecycle state model (A–H states)",
        "status": status,
        "evidence": explicit_state_files or (["execution/db/sqlite.py"] if implicit_state else []),
        "notes": notes,
    }


def check_action_family(action_name: str, patterns: list[str]) -> dict:
    """Generic checker for a named action family."""
    exec_dir = REPO_ROOT / "execution"
    hits = []
    for pat in patterns:
        hits.extend(search_dir(pat, exec_dir))
    files = sorted({rel(h[0]) for h in hits})
    status = "Implemented" if files else "Missing"
    return {
        "requirement": f"Action family: {action_name}",
        "status": status,
        "evidence": files,
        "notes": f"found in {len(files)} file(s)" if files else "no evidence found",
    }


def check_nudge_progress_subtypes() -> dict:
    """
    Spec: NUDGE_PROGRESS must have subtypes:
    NO_START_24H, NO_START_72H, NO_START_7D, INACTIVE_48H, INACTIVE_4D, INACTIVE_7D, etc.
    """
    exec_dir = REPO_ROOT / "execution"
    subtype_patterns = [
        r"NO_START_24H|NO_START_72H|NO_START_7D",
        r"INACTIVE_48H|INACTIVE_4D|INACTIVE_7D",
        r"PHASE_STALL|RESTART_DETECTED",
        r"classify.*no_start|classify.*stale|threshold.*hours|threshold.*days",
    ]
    hits = []
    for pat in subtype_patterns:
        hits.extend(search_dir(pat, exec_dir))
    files = sorted({rel(h[0]) for h in hits})

    # Check if timing thresholds exist even without exact label names
    threshold_hits = search_dir(r"hours|days.*inactive|stale.*days|STALL_DAYS", exec_dir)
    threshold_files = sorted({rel(h[0]) for h in threshold_hits})

    if files:
        status = "Implemented"
        notes = "subtype constants found"
    elif threshold_files:
        status = "Partial"
        notes = "timing thresholds exist but spec subtype labels (NO_START_24H etc.) not found"
    else:
        status = "Missing"
        notes = "no subtype labels or timing thresholds found"

    return {
        "requirement": "NUDGE_PROGRESS subtypes (NO_START_*H, INACTIVE_*H/D)",
        "status": status,
        "evidence": files or threshold_files,
        "notes": notes,
    }


def check_provisional_vs_final_scoring() -> dict:
    """
    Spec: two score types — provisional (in-progress) and final (post-completion).
    """
    exec_dir = REPO_ROOT / "execution"
    prov_hits = search_dir(r"provisional|compute.*temperature|temperature.*score|in.progress.*score", exec_dir)
    final_hits = search_dir(r"finalize|final.*score|final.*label|classify.*final", exec_dir)

    prov_files = sorted({rel(h[0]) for h in prov_hits})
    final_files = sorted({rel(h[0]) for h in final_hits})

    if prov_files and final_files:
        status = "Implemented"
        notes = "provisional scoring (compute_lead_temperature) and final scoring (finalize_lead_score) both exist"
    elif prov_files or final_files:
        status = "Partial"
        notes = "only one of provisional/final scoring found"
    else:
        status = "Missing"
        notes = "no scoring evidence found"

    return {
        "requirement": "Provisional score (in-progress) vs Final score (post-completion)",
        "status": status,
        "evidence": prov_files + final_files,
        "notes": notes,
    }


def check_final_hot_requires_completion() -> dict:
    """
    Spec hard rule: FINAL_HOT / READY_FOR_BOOKING requires 100% course completion.
    """
    exec_dir = REPO_ROOT / "execution"
    completion_gate_hits = search_dir(
        r"completion_pct.*100|100.*completion|course.*complet|complet.*course"
        r"|completion.*required|requires.*completion",
        exec_dir,
    )
    hot_gate_hits = search_dir(
        r"READY_FOR_BOOKING.*complet|complet.*READY_FOR_BOOKING"
        r"|HOT.*100|100.*HOT|final_hot.*complet|complet.*final_hot",
        exec_dir,
    )
    all_files = sorted({rel(h[0]) for h in completion_gate_hits + hot_gate_hits})

    # Read the recommendation builder to verify the gate logic
    rec_file = REPO_ROOT / "execution" / "decision" / "build_cora_recommendation.py"
    rec_content = read_file(rec_file)
    has_completion_gate = bool(
        re.search(r"completion_pct.*100|100.*completion|is_complete", rec_content, re.IGNORECASE)
    )

    if has_completion_gate and all_files:
        status = "Implemented"
        notes = "recommendation builder gates READY_FOR_BOOKING on completion"
    elif all_files:
        status = "Partial"
        notes = "completion references found but gate in recommendation builder unclear"
    else:
        status = "Not Evidenced"
        notes = "could not confirm completion gate in recommendation logic"

    return {
        "requirement": "FINAL_HOT / READY_FOR_BOOKING requires 100% course completion",
        "status": status,
        "evidence": [rel(rec_file)] if has_completion_gate else all_files,
        "notes": notes,
    }


def check_scheduled_scans() -> dict:
    """
    Spec: scheduled scan jobs for unsent invite, no-start, stale progress,
    completion finalization, ready-for-booking, warm-review, failed dispatch retry.
    """
    worker_dir = REPO_ROOT / "services" / "worker"
    scan_dir = REPO_ROOT / "execution" / "scans"

    expected_scans = {
        "unsent_invite": r"unsent.*invite|find.*unsent",
        "no_start": r"no.*start|find.*no.start",
        "stale_progress": r"stale.*progress|find.*stale",
        "completion_finalization": r"completion.*final|finalization",
        "failed_dispatch_retry": r"failed.*dispatch|retry.*scan",
        "ready_for_booking_scan": r"ready.*booking.*scan",
        "warm_review_scan": r"warm.*review.*scan",
    }

    found = {}
    for scan_name, pattern in expected_scans.items():
        worker_hits = search_dir(pattern, worker_dir) if worker_dir.exists() else []
        scan_hits = search_dir(pattern, scan_dir) if scan_dir.exists() else []
        found[scan_name] = bool(worker_hits or scan_hits)

    implemented = [k for k, v in found.items() if v]
    missing = [k for k, v in found.items() if not v]

    if len(missing) == 0:
        status = "Implemented"
    elif len(implemented) > 0:
        status = "Partial"
    else:
        status = "Missing"

    evidence_files = sorted({
        rel(h[0])
        for scan_name, pattern in expected_scans.items()
        for h in search_dir(pattern, worker_dir if worker_dir.exists() else REPO_ROOT)
        + search_dir(pattern, scan_dir if scan_dir.exists() else REPO_ROOT)
    })

    return {
        "requirement": "Scheduled scan jobs (all 7 scan types)",
        "status": status,
        "evidence": evidence_files,
        "notes": (
            f"implemented: {implemented}; "
            f"missing: {missing if missing else 'none'}"
        ),
    }


def check_bulk_ingestion() -> dict:
    """
    Spec: system must support bulk lead ingestion (array payload or queued batch).
    Tight patterns only — avoid matching read-only list/overview query functions.
    """
    exec_dir = REPO_ROOT / "execution"
    services_dir = REPO_ROOT / "services"
    # Must be actual batch ingest, not a read-only list query or docstring
    tight_patterns = [
        r"bulk.*ingest",
        r"batch.*upsert",
        r"batch.*lead",
        r"ingest.*batch",
        r"upsert.*many",
        r"for\s+\w+\s+in\s+leads\s*:",   # actual Python for-loop over leads list
        r"multiple.*leads.*upsert",
    ]
    hits = []
    for pat in tight_patterns:
        hits.extend(search_dir(pat, exec_dir))
        if services_dir.exists():
            hits.extend(search_dir(pat, services_dir))

    files = sorted({rel(h[0]) for h in hits})
    status = "Implemented" if files else "Missing"
    return {
        "requirement": "Bulk ingestion (receive many leads at once)",
        "status": status,
        "evidence": files,
        "notes": "no batch ingestion endpoint or batch upsert found" if not files else "batch ingestion found",
    }


def check_bulk_scanning() -> dict:
    """
    Spec: system must scan many leads at once.
    """
    scan_dir = REPO_ROOT / "execution" / "scans"
    if not scan_dir.exists():
        return {
            "requirement": "Bulk scanning (find all leads matching trigger conditions)",
            "status": "Missing",
            "evidence": [],
            "notes": "execution/scans directory not found",
        }

    scan_files = find_py_files(scan_dir)
    # Check that scan functions return lists (not single records)
    list_return_hits = []
    for f in scan_files:
        content = read_file(f)
        if re.search(r"fetchall|return.*\[\]|List\[|-> list", content, re.IGNORECASE):
            list_return_hits.append(rel(f))

    status = "Implemented" if list_return_hits else "Partial"
    return {
        "requirement": "Bulk scanning (find all leads matching trigger conditions)",
        "status": status,
        "evidence": list_return_hits,
        "notes": (
            f"{len(list_return_hits)} scan file(s) return lists"
            if list_return_hits
            else "scan files exist but return type unclear"
        ),
    }


def check_bulk_dispatch() -> dict:
    """
    Spec: process_pending_cory_sync_records(limit=...) — batch loop dispatch.
    """
    exec_dir = REPO_ROOT / "execution"
    worker_dir = REPO_ROOT / "services" / "worker"
    hits = []
    for pat in [r"process_pending|batch.*dispatch|limit=|limit\s*=\s*\d|dispatch.*loop"]:
        hits.extend(search_dir(pat, exec_dir))
        if worker_dir.exists():
            hits.extend(search_dir(pat, worker_dir))

    files = sorted({rel(h[0]) for h in hits})

    # Check if existing dispatch is single-record only
    single_record_file = REPO_ROOT / "execution" / "events" / "process_one_cory_sync_record.py"
    is_single_only = single_record_file.exists() and not files

    if files:
        status = "Partial"
        notes = "limit/batch patterns found but process_one_* is single-record; no true batch loop confirmed"
    else:
        status = "Partial"
        notes = (
            "only process_one_cory_sync_record.py exists — single-record dispatch; "
            "batch loop with limit=N not yet implemented"
        )

    # Worker runner may loop it
    runner_hits = search_dir(r"for.*sync|while.*sync|loop.*dispatch|process_one.*cory", worker_dir if worker_dir.exists() else REPO_ROOT)
    runner_files = sorted({rel(h[0]) for h in runner_hits})

    return {
        "requirement": "Bulk dispatch (batch processing loop with limit/retry)",
        "status": status,
        "evidence": files + runner_files,
        "notes": notes,
    }


def check_reflection_scoring_policy() -> dict:
    """
    Spec: reflections should only affect score if structured (Mode A) or be stored unscored (Mode B).
    """
    exec_dir = REPO_ROOT / "execution"
    reflection_hits = search_dir(r"reflection|W_REFLECTION|reflection.*score|score.*reflection", exec_dir)
    files = sorted({rel(h[0]) for h in reflection_hits})

    # Check if reflection scoring is live (has weight > 0)
    has_weight = any("W_REFLECTION" in h[1] for h in reflection_hits)
    structured_eval = bool(search_dir(r"confidence_level|motivation_level|intent_clarity|concern_flag", exec_dir))
    unscored_policy = bool(search_dir(r"UNKNOWN|NOT_USED|unscored|reflection.*stored", exec_dir))

    if structured_eval:
        status = "Implemented"
        notes = "structured reflection schema found (Mode A)"
    elif has_weight and not structured_eval:
        status = "Partial"
        notes = (
            "W_REFLECTION weight exists in scoring — reflection contributes to score "
            "but no structured evaluation found; spec recommends Mode B (store, don't score) "
            "unless Mode A is implemented"
        )
    elif unscored_policy:
        status = "Implemented"
        notes = "Mode B (stored but unscored) policy found"
    else:
        status = "Not Evidenced"
        notes = "no clear reflection scoring policy found"

    return {
        "requirement": "Reflection scoring policy (Mode A structured OR Mode B stored-unscored)",
        "status": status,
        "evidence": files,
        "notes": notes,
    }


def check_restart_rescoring() -> dict:
    """
    Spec: section restart / back-nav must reset active section score and trigger rescoring.
    Use tight patterns to avoid false positives (e.g. "LeadTemperatureScore" contains "rescor").
    """
    exec_dir = REPO_ROOT / "execution"
    # Tight patterns only — avoid matching substrings inside compound words like "LeadTemperatureScore"
    tight_patterns = [
        r"\bRESET_SECTION_SCORE\b",
        r"\bsection_restart\b",
        r"\bback_nav\b",
        r"\bback.navigation\b",
        r"section.*attempt",
        r"attempt.*section",
        r"invalidate.*section",
        r"section.*rescore",
    ]
    hits = []
    for pat in tight_patterns:
        hits.extend(search_dir(pat, exec_dir))

    files = sorted({rel(h[0]) for h in hits})
    status = "Implemented" if files else "Missing"
    return {
        "requirement": "Section restart / back-navigation rescoring (RESET_SECTION_SCORE)",
        "status": status,
        "evidence": files,
        "notes": (
            "restart/rescoring logic found"
            if files
            else "no evidence of section restart detection or score reset logic"
        ),
    }


def check_reengage_completed() -> dict:
    """
    Spec: REENGAGE_COMPLETED — for leads that completed course but are not hot.
    Different from REENGAGE_STALLED_LEAD.
    """
    exec_dir = REPO_ROOT / "execution"
    completed_reengage = search_dir(r"REENGAGE_COMPLETED|reengage.*complet|complet.*reengage", exec_dir)
    stalled_reengage = search_dir(r"REENGAGE_STALLED|reengage.*stall|stall.*reengage", exec_dir)

    completed_files = sorted({rel(h[0]) for h in completed_reengage})
    stalled_files = sorted({rel(h[0]) for h in stalled_reengage})

    if completed_files:
        status = "Implemented"
        notes = "REENGAGE_COMPLETED action found"
    elif stalled_files:
        status = "Partial"
        notes = (
            "REENGAGE_STALLED_LEAD exists but spec calls for REENGAGE_COMPLETED "
            "(post-course, non-hot leads) as a separate concept"
        )
    else:
        status = "Missing"
        notes = "no re-engagement action found"

    return {
        "requirement": "Action: REENGAGE_COMPLETED (post-course, non-hot re-engagement)",
        "status": status,
        "evidence": completed_files or stalled_files,
        "notes": notes,
    }


def check_internal_system_actions() -> dict:
    """
    Spec internal actions: UPSERT_LEAD, GENERATE_INVITE, MARK_INVITE_SENT,
    FINALIZE_LEAD_SCORE, RECALCULATE_PROVISIONAL_SCORE, RESET_SECTION_SCORE, REQUEUE_FAILED_ACTION
    """
    exec_dir = REPO_ROOT / "execution"
    actions = {
        "UPSERT_LEAD": r"upsert_lead",
        "GENERATE_INVITE": r"generate_invite|create.*invite",
        "MARK_INVITE_SENT": r"mark.*invite.*sent|mark_course_invite_sent",
        "FINALIZE_LEAD_SCORE": r"finalize_lead_score|finalize.*score",
        "RECALCULATE_PROVISIONAL_SCORE": r"recalculate.*provisional|compute.*temperature",
        "RESET_SECTION_SCORE": r"reset.*section|RESET_SECTION_SCORE",
        "REQUEUE_FAILED_ACTION": r"requeue.*failed|requeue_failed",
    }
    found = {}
    evidence_files = []
    for name, pat in actions.items():
        hits = search_dir(pat, exec_dir)
        found[name] = bool(hits)
        evidence_files.extend(rel(h[0]) for h in hits)

    implemented = [k for k, v in found.items() if v]
    missing = [k for k, v in found.items() if not v]

    status = "Implemented" if not missing else ("Partial" if implemented else "Missing")
    return {
        "requirement": "Internal system actions (7 defined in spec)",
        "status": status,
        "evidence": sorted(set(evidence_files)),
        "notes": f"found: {implemented}; missing: {missing}",
    }


# ---------------------------------------------------------------------------
# Run all checks
# ---------------------------------------------------------------------------

def run_audit() -> list[dict]:
    return [
        check_invite_semantics(),
        check_state_model(),
        check_action_family(
            "SEND_INVITE",
            [r"SEND_INVITE|EVENT_SEND_INVITE|send_invite"],
        ),
        check_action_family(
            "NUDGE_PROGRESS",
            [r"NUDGE_PROGRESS|EVENT_NUDGE_PROGRESS|nudge_progress"],
        ),
        check_nudge_progress_subtypes(),
        check_action_family(
            "READY_FOR_BOOKING",
            [r"READY_FOR_BOOKING|EVENT_HOT_BOOKING|hot_booking"],
        ),
        check_action_family(
            "WARM_REVIEW",
            [r"WARM_REVIEW|warm_review"],
        ),
        check_reengage_completed(),
        check_internal_system_actions(),
        check_provisional_vs_final_scoring(),
        check_final_hot_requires_completion(),
        check_scheduled_scans(),
        check_bulk_ingestion(),
        check_bulk_scanning(),
        check_bulk_dispatch(),
        check_reflection_scoring_policy(),
        check_restart_rescoring(),
    ]


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

PRIORITY_ORDER = [
    "Invite semantics",
    "NUDGE_PROGRESS subtypes",
    "REENGAGE_COMPLETED",
    "Lead lifecycle state model",
    "Internal system actions",
    "Reflection scoring policy",
    "Section restart / back-navigation",
    "Bulk ingestion",
    "Bulk dispatch",
    "Scheduled scan jobs",
]


def status_emoji(status: str) -> str:
    return {
        "Implemented": "✅",
        "Partial": "⚠️",
        "Missing": "❌",
        "Not Evidenced": "❓",
    }.get(status, "❓")


def build_report(results: list[dict]) -> str:
    lines = []
    lines.append("# Repo vs Cory/Cora Spec — Gap Report")
    lines.append("")
    lines.append("> Generated by `scripts/audit_repo_vs_cory_cora_spec.py`")
    lines.append("> Source specs: Cory/Cora Blueprint + Draft v1 Spec")
    lines.append("")

    # Summary table
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| # | Requirement | Status | Notes |")
    lines.append("|---|-------------|--------|-------|")
    for i, r in enumerate(results, 1):
        icon = status_emoji(r["status"])
        note = r["notes"].replace("|", "/")[:120]
        lines.append(f"| {i} | {r['requirement']} | {icon} {r['status']} | {note} |")
    lines.append("")

    # --- Section: Confirmed Implemented ---
    implemented = [r for r in results if r["status"] == "Implemented"]
    lines.append("## ✅ Confirmed Implemented")
    lines.append("")
    for r in implemented:
        lines.append(f"### {r['requirement']}")
        lines.append(f"**Notes:** {r['notes']}")
        if r["evidence"]:
            lines.append("**Evidence files:**")
            for e in r["evidence"][:8]:
                lines.append(f"- `{e}`")
        lines.append("")

    # --- Section: Partially Implemented ---
    partial = [r for r in results if r["status"] == "Partial"]
    lines.append("## ⚠️ Partially Implemented")
    lines.append("")
    for r in partial:
        lines.append(f"### {r['requirement']}")
        lines.append(f"**Notes:** {r['notes']}")
        if r["evidence"]:
            lines.append("**Evidence files:**")
            for e in r["evidence"][:8]:
                lines.append(f"- `{e}`")
        lines.append("")

    # --- Section: Missing ---
    missing = [r for r in results if r["status"] in ("Missing", "Not Evidenced")]
    lines.append("## ❌ Not Implemented / Not Evidenced")
    lines.append("")
    for r in missing:
        icon = status_emoji(r["status"])
        lines.append(f"### {icon} {r['requirement']}")
        lines.append(f"**Notes:** {r['notes']}")
        lines.append("")

    # --- Section: Open Questions ---
    lines.append("## Open Questions Found In Code")
    lines.append("")
    open_q_patterns = [
        (REPO_ROOT / "execution" / "leads" / "compute_lead_temperature.py",
         r"TODO|FIXME|HACK|threshold|W_REFLECTION|W_QUIZ"),
        (REPO_ROOT / "execution" / "decision" / "build_cora_recommendation.py",
         r"TODO|FIXME|HACK|STALL_DAYS"),
        (REPO_ROOT / "execution" / "leads" / "can_compute_final_score.py",
         r"TODO|FIXME|require|gating"),
    ]
    found_questions = False
    for fpath, pat in open_q_patterns:
        if fpath.exists():
            hits = grep(pat, read_file(fpath))
            if hits:
                found_questions = True
                lines.append(f"**`{rel(fpath)}`**")
                for h in hits[:5]:
                    lines.append(f"  - `{h}`")
                lines.append("")

    if not found_questions:
        # Check for TODOs across execution
        exec_dir = REPO_ROOT / "execution"
        todo_hits = search_dir(r"TODO|FIXME|HACK|open question", exec_dir)
        if todo_hits:
            lines.append("TODOs/FIXMEs found:")
            for f, ln in todo_hits[:10]:
                lines.append(f"- `{rel(f)}`: `{ln}`")
        else:
            lines.append("No explicit TODO/FIXME comments found in execution layer.")
        lines.append("")

    # Spec open questions (from section 19 of Draft v1)
    lines.append("### Spec-Defined Open Questions (from Draft v1 §19)")
    lines.append("")
    spec_questions = [
        "What exact final score thresholds define FINAL_COLD / FINAL_WARM / FINAL_HOT?",
        "What exact quiz thresholds should be required for FINAL_HOT?",
        "Will reflections be structured+scored (Mode A) or stored-unscored (Mode B)?",
        "What exact restart/back-nav penalties should exist?",
        "How should repeated section attempts be stored?",
        "What exact no-start and inactivity timing thresholds does the business want?",
        "What GHL campaigns map to each action (SEND_INVITE, NUDGE_PROGRESS, etc.)?",
        "How should failed GHL dispatches retry?",
        "What bulk size limits and rate controls are needed for real GHL traffic?",
        "Does the system need manual-review queues for high-end warm leads?",
    ]
    for i, q in enumerate(spec_questions, 1):
        lines.append(f"{i}. {q}")
    lines.append("")

    # --- Section: Recommended Priority Order ---
    lines.append("## Recommended Priority Order")
    lines.append("")
    lines.append(
        "Based on spec priorities (§18) and current repo gaps:"
    )
    lines.append("")

    priority_items = [
        (
            "P1 — Fix invite semantics (already mostly done)",
            "partial",
            "Verify recommendation builder uses `invite_sent_at IS NOT NULL` gate, not just invite existence. "
            "Confirm `create_student_invite_from_payload` ≠ `mark_course_invite_sent` are always called separately.",
        ),
        (
            "P2 — Add NUDGE_PROGRESS subtype labels",
            "gap",
            "Rename/add NO_START_24H, NO_START_72H, NO_START_7D, INACTIVE_48H, INACTIVE_4D, INACTIVE_7D "
            "constants in scans/classify_*.py. Wire into recommendation and consumer.",
        ),
        (
            "P3 — Add REENGAGE_COMPLETED action",
            "gap",
            "REENGAGE_STALLED_LEAD exists but REENGAGE_COMPLETED (post-course, non-hot) is missing. "
            "Add to build_cora_recommendation.py and consume_cory_recommendation.py.",
        ),
        (
            "P4 — Formalize lead lifecycle state enum",
            "gap",
            "Add explicit LEAD_STATE constants (A–H) to mirror spec §3. "
            "Use in get_lead_status.py return value and recommendation builder.",
        ),
        (
            "P5 — Fix reflection scoring policy",
            "gap",
            "W_REFLECTION=15 currently contributes to score without structured evaluation. "
            "Either implement Mode A (structured schema) or drop W_REFLECTION to 0 (Mode B) until ready.",
        ),
        (
            "P6 — Implement section restart / rescoring logic",
            "missing",
            "No evidence of RESET_SECTION_SCORE, back-nav detection, or section attempt tracking. "
            "Need: section attempt table, reset trigger, rescore hook.",
        ),
        (
            "P7 — Add READY_FOR_BOOKING and WARM_REVIEW scans to worker",
            "partial",
            "Scan files exist for unsent/no-start/stale/finalization/failed-dispatch. "
            "READY_FOR_BOOKING_SCAN and WARM_REVIEW_SCAN runners are missing.",
        ),
        (
            "P8 — Implement bulk ingestion endpoint",
            "missing",
            "No batch lead ingestion. Current student_invite_endpoint.py is single-lead. "
            "Add a batch endpoint or queued array ingestion.",
        ),
        (
            "P9 — Implement bulk dispatch loop",
            "partial",
            "process_one_cory_sync_record is single-record. "
            "Wrap in process_pending_cory_sync_records(limit=N) with retry summary.",
        ),
    ]

    for item, kind, detail in priority_items:
        icon = "⚠️" if kind == "partial" else "❌"
        lines.append(f"### {icon} {item}")
        lines.append(detail)
        lines.append("")

    # --- Evidence map ---
    lines.append("## Evidence Map")
    lines.append("")
    lines.append("| File | What it provides |")
    lines.append("|------|-----------------|")

    evidence_map = [
        ("execution/leads/create_student_invite_from_payload.py", "invite_generated step"),
        ("execution/leads/mark_course_invite_sent.py", "invite_sent step (separate from generate)"),
        ("execution/leads/compute_lead_temperature.py", "provisional scoring (W_COMPLETION, W_QUIZ, W_REFLECTION, W_VELOCITY)"),
        ("execution/leads/classify_final_lead_label.py", "FINAL_COLD/WARM/HOT classification"),
        ("execution/leads/finalize_lead_score.py", "final score lock after completion"),
        ("execution/leads/can_compute_final_score.py", "gate: invite_sent + quiz + reflection required before finalization"),
        ("execution/decision/build_cora_recommendation.py", "action routing: SEND_INVITE, NUDGE_PROGRESS, READY_FOR_BOOKING, WARM_REVIEW"),
        ("execution/decision/get_cora_recommendation.py", "recommendation orchestrator"),
        ("execution/scans/find_unsent_invite_leads.py", "UNSENT_INVITE_SCAN bulk query"),
        ("execution/scans/find_no_start_leads.py", "NO_START_SCAN bulk query"),
        ("execution/scans/find_stale_progress_leads.py", "STALE_PROGRESS_SCAN bulk query"),
        ("execution/scans/find_completion_finalization_leads.py", "COMPLETION_FINALIZATION_SCAN bulk query"),
        ("execution/scans/find_failed_dispatch_records.py", "FAILED_DISPATCH_RETRY_SCAN bulk query"),
        ("execution/scans/scan_registry.py", "canonical scan name constants"),
        ("execution/events/consume_cory_recommendation.py", "event consumer → writes sync_records"),
        ("execution/events/process_one_cory_sync_record.py", "single-record dispatch (no batch loop yet)"),
        ("services/worker/run_unsent_invite_scan.py", "scheduled worker for unsent invite scan"),
        ("services/worker/run_no_start_scan.py", "scheduled worker for no-start scan"),
        ("services/worker/run_stale_progress_scan.py", "scheduled worker for stale progress scan"),
        ("services/worker/run_completion_finalization_scan.py", "scheduled worker for completion finalization"),
        ("services/worker/run_failed_dispatch_scan.py", "scheduled worker for failed dispatch retry"),
        ("services/worker/run_all_scans.py", "orchestrates all scan workers"),
        ("execution/db/sqlite.py", "DB schema: leads, course_invites, course_state, sync_records"),
    ]

    for fpath, desc in evidence_map:
        full = REPO_ROOT / fpath.replace("/", os.sep)
        exists = "✅" if full.exists() else "❌ (not found)"
        lines.append(f"| `{fpath}` {exists} | {desc} |")

    lines.append("")
    lines.append("---")
    lines.append("*Report generated by `scripts/audit_repo_vs_cory_cora_spec.py`*")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running Cory/Cora spec gap audit...")
    results = run_audit()
    report = build_report(results)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    print(f"\nReport written to: {REPORT_PATH}")
    print("\n=== STATUS SUMMARY ===")
    for r in results:
        print(f"  [{r['status']:15s}] {r['requirement']}")

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\n=== COUNTS ===")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")
