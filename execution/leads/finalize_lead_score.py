def finalize_lead_score(lead_id: str, payload: dict) -> dict:
    """
    Finalization boundary for completed leads.

    Assigns final_label based on hot_signal when requires_finalization is True.
    Returns payload unchanged for non-finalization paths.

    TODO: FINAL_COLD requires finalized scoring layer — temperature_signal is
          pre-finalization (MODE B, reflection not scored); unsafe to emit
          FINAL_COLD until this function receives a reliable finalized signal.
    """
    if payload.get("requires_finalization"):
        if payload.get("hot_signal") == "HOT":
            payload["final_label"] = "FINAL_HOT"
        else:
            payload["final_label"] = "FINAL_WARM"
    return payload
