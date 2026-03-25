"""
services/worker/run_completion_finalization_scan.py

Worker entry point for the completion finalization scan.

Calls find_completion_finalization_leads and returns a summary dict.
No side effects — does not finalize leads, dispatch actions, or write to DB.
"""

from execution.scans.find_completion_finalization_leads import find_completion_finalization_leads


def run_completion_finalization_scan(limit: int = 100, db_path: str | None = None) -> dict:
    """
    Run the completion finalization scan and return a summary.

    Returns:
        {
            "scan_name": "COMPLETION_FINALIZATION_SCAN",
            "count":     <number of qualifying leads>,
            "lead_ids":  [<lead_id>, ...],
            "limit_used": <int>,
        }
    """
    rows = find_completion_finalization_leads(limit=limit, db_path=db_path)
    score_summary = {"HAS_SCORE": 0, "MISSING_SCORE": 0}
    fallback_final_label_summary = {"FINAL_COLD": 0, "FINAL_WARM": 0, "FINAL_HOT": 0}
    enrichment_summary = {"INVITE_SENT_TRUE": 0, "INVITE_SENT_FALSE": 0}
    for row in rows:
        if row["score"] is None:
            score_summary["MISSING_SCORE"] += 1
            # Fallback: mirrors finalize_lead_score fallback — hot_signal absent in
            # scan rows, so all current candidates land in FINAL_WARM.
            if row.get("hot_signal") == "HOT":
                fallback_final_label_summary["FINAL_HOT"] += 1
            else:
                fallback_final_label_summary["FINAL_WARM"] += 1
        else:
            score_summary["HAS_SCORE"] += 1
            from execution.leads.classify_final_lead_label import classify_final_lead_label
            label = classify_final_lead_label(row["score"])
            fallback_final_label_summary[label] += 1
        if row["invite_sent"]:
            enrichment_summary["INVITE_SENT_TRUE"] += 1
        else:
            enrichment_summary["INVITE_SENT_FALSE"] += 1
    return {
        "scan_name":                   "COMPLETION_FINALIZATION_SCAN",
        "count":                       len(rows),
        "lead_ids":                    [row["lead_id"] for row in rows],
        "limit_used":                  limit,
        "score_summary":               score_summary,
        "fallback_final_label_summary": fallback_final_label_summary,
        "enrichment_summary":          enrichment_summary,
    }
