"""
execution/reflection/load_reflection_responses.py

Retrieves all stored reflection responses for a given lead and course,
returning them grouped by section and prompt slot.

Read-only — no writes, no datetime.now(), no business logic.
"""

from execution.db.sqlite import connect, init_db


def load_reflection_responses(
    lead_id: str,
    course_id: str,
    db_path: str | None = None,
) -> dict[str, dict[int, str]]:
    """Return all reflection responses for a lead/course pair.

    Args:
        lead_id:   Non-empty string identifying the lead.
        course_id: Non-empty string identifying the course.
        db_path:   Path to the SQLite file; defaults to tmp/app.db.

    Returns:
        Nested dict:  section_id -> {prompt_index -> response_text}

        Returns an empty dict when no responses have been recorded for the
        given lead/course combination.

    Raises:
        ValueError: If lead_id or course_id is not a non-empty string.
    """
    for name, value in (("lead_id", lead_id), ("course_id", course_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"load_reflection_responses: '{name}' must be a non-empty string, "
                f"got {value!r}"
            )

    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT section_id, prompt_index, response_text
            FROM   reflection_responses
            WHERE  lead_id = ? AND course_id = ?
            ORDER  BY section_id, prompt_index
            """,
            (lead_id, course_id),
        ).fetchall()
    finally:
        conn.close()

    result: dict[str, dict[int, str]] = {}
    for row in rows:
        section = row["section_id"]
        idx = row["prompt_index"]        # stored as INTEGER in SQLite → int in Python
        text = row["response_text"]
        result.setdefault(section, {})[idx] = text

    return result
