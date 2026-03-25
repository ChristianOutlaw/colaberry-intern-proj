"""
execution/leads/can_compute_final_score.py

Pure gating helper — no DB access, no dispatch, no score computation.
"""


def can_compute_final_score(row: dict) -> bool:
    """
    Return True only when the current completion-finalization row has enough
    data to safely compute a numeric final score.

    Current required inputs:
    - invite_sent is True
    - has_quiz_data is True
    - has_reflection_data is True

    Otherwise return False.
    """
    return (
        row.get("invite_sent") is True
        and row.get("has_quiz_data") is True
        and row.get("has_reflection_data") is True
    )
