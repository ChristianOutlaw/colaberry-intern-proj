"""
execution/leads/can_compute_final_score.py

Pure gating helper — no DB access, no dispatch, no score computation.
"""


def can_compute_final_score(
    row: dict,
    completion_pct: float | None = None,
) -> bool:
    """
    Return True only when the current completion-finalization row has enough
    data to safely compute a numeric final score.

    Override: if completion_pct >= 100, return True unconditionally — a fully
    completed lead always has enough signal to finalize, even when reflection
    data is absent (missing reflection scores as UNKNOWN = 7 pts).

    Otherwise requires:
    - invite_sent is True
    - has_quiz_data is True
    - has_reflection_data is True
    """
    if completion_pct is not None and completion_pct >= 100:
        return True

    return (
        row.get("invite_sent") is True
        and row.get("has_quiz_data") is True
        and row.get("has_reflection_data") is True
    )
