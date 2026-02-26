"""
execution/reflection/save_reflection_response.py

Persists a student's free-text reflection answer for a specific
course section and prompt slot.

Upserts on UNIQUE(lead_id, course_id, section_id, prompt_index):
a second write for the same slot overwrites the previous response.

No datetime.now() — created_at must be supplied by the caller or omitted
(stored as NULL).  No business logic lives here.
"""

from execution.db.sqlite import connect, init_db


def save_reflection_response(
    lead_id: str,
    course_id: str,
    section_id: str,
    prompt_index: int,
    response_text: str,
    created_at: str | None = None,
    db_path: str | None = None,
) -> None:
    """Insert or replace a reflection response for a lead/course/section/prompt slot.

    The UNIQUE constraint on (lead_id, course_id, section_id, prompt_index)
    means a second save for the same slot silently replaces the existing row
    (upsert via INSERT OR REPLACE).

    Args:
        lead_id:       Non-empty string identifying the lead.
        course_id:     Non-empty string identifying the course.
        section_id:    Non-empty string identifying the course section.
        prompt_index:  Non-negative integer index of the reflection prompt
                       within the section.
        response_text: Non-empty string containing the student's answer.
        created_at:    Optional ISO 8601 timestamp supplied by the caller;
                       stored as NULL when omitted.  Must NOT be generated
                       inside this function.
        db_path:       Path to the SQLite file; defaults to tmp/app.db.

    Raises:
        ValueError: If any required argument fails type or content validation.
    """
    _validate(lead_id, course_id, section_id, prompt_index, response_text)

    conn = connect(db_path)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO reflection_responses
                (lead_id, course_id, section_id, prompt_index, response_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (lead_id, course_id, section_id, prompt_index, response_text, created_at),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal validation (importable for unit tests)
# ---------------------------------------------------------------------------

def _validate(
    lead_id: object,
    course_id: object,
    section_id: object,
    prompt_index: object,
    response_text: object,
) -> None:
    """Raise ValueError if any argument fails type or content rules.

    Raises:
        ValueError: With a message naming the offending argument.
    """
    for name, value in (
        ("lead_id", lead_id),
        ("course_id", course_id),
        ("section_id", section_id),
        ("response_text", response_text),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"save_reflection_response: '{name}' must be a non-empty string, "
                f"got {value!r}"
            )

    # bool is a subclass of int — reject it to prevent accidental misuse.
    if isinstance(prompt_index, bool) or not isinstance(prompt_index, int):
        raise ValueError(
            f"save_reflection_response: 'prompt_index' must be an int, "
            f"got {type(prompt_index).__name__}"
        )
    if prompt_index < 0:
        raise ValueError(
            f"save_reflection_response: 'prompt_index' must be >= 0, "
            f"got {prompt_index}"
        )
