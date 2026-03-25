from execution.leads.classify_final_lead_label import classify_final_lead_label


def finalize_lead_score(lead_id: str, payload: dict) -> dict:
    """
    Finalization boundary for completed leads.

    Assigns final_label when requires_finalization is True:
    - If a numeric "score" is present, delegates to classify_final_lead_label
      (can produce FINAL_COLD / FINAL_WARM / FINAL_HOT).
    - Otherwise falls back to hot_signal:
        hot_signal == "HOT" -> FINAL_HOT
        otherwise           -> FINAL_WARM

    Returns payload unchanged for non-finalization paths.
    """
    if payload.get("requires_finalization"):
        if "score" in payload and payload["score"] is not None:
            payload["final_label"] = classify_final_lead_label(payload["score"])
        elif payload.get("hot_signal") == "HOT":
            payload["final_label"] = "FINAL_HOT"
        else:
            payload["final_label"] = "FINAL_WARM"
    return payload
