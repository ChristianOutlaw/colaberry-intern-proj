"""
execution/leads/classify_final_lead_label.py

Classify a final numeric lead score into a FINAL_* label.
Pure function — no DB access, no dispatch.
"""

from execution.leads.compute_lead_temperature import SCORE_HOT, SCORE_WARM


def classify_final_lead_label(score: int | float | None) -> str:
    """
    Classify a final numeric score into:
    - FINAL_COLD
    - FINAL_WARM
    - FINAL_HOT

    Rules:
    - if score is None -> FINAL_COLD
    - if score >= SCORE_HOT (70) -> FINAL_HOT
    - elif score >= SCORE_WARM (35) -> FINAL_WARM
    - else -> FINAL_COLD
    """
    if score is None:
        return "FINAL_COLD"
    if score >= SCORE_HOT:
        return "FINAL_HOT"
    if score >= SCORE_WARM:
        return "FINAL_WARM"
    return "FINAL_COLD"
