"""
execution/course/load_quiz_library.py

Loads and validates all quiz JSON files for a given course_id.

No database access. No randomness. Pure file I/O + validation.

Each JSON file under course_content/<course_id>/quizzes/ may contain
either a single quiz dict or a list of quiz dicts.  All quizzes are
aggregated into a single mapping:  quiz_id -> quiz dict.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Supported course IDs.  Add new entries here as courses are onboarded.
# ---------------------------------------------------------------------------
_SUPPORTED_COURSES: frozenset[str] = frozenset({"FREE_INTRO_AI_V0"})

# Repo root: execution/course/ -> execution/ -> repo root
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_quiz_library(course_id: str) -> dict[str, dict]:
    """Load and validate all quizzes for the given course_id.

    Scans every *.json file inside course_content/<course_id>/quizzes/ and
    merges the results into one dict keyed by quiz_id.

    Each file may contain a single quiz dict or a JSON list of quiz dicts.

    Args:
        course_id: Identifier for the course (e.g. "FREE_INTRO_AI_V0").

    Returns:
        dict mapping quiz_id (str) -> quiz data dict.

    Raises:
        ValueError: If course_id is unsupported, a quiz fails validation, or
                    a duplicate quiz_id is detected across files.
        FileNotFoundError: If the quizzes directory is missing or contains no
                           JSON files.
    """
    if course_id not in _SUPPORTED_COURSES:
        raise ValueError(
            f"Unsupported course_id: {course_id!r}. "
            f"Supported courses: {sorted(_SUPPORTED_COURSES)}"
        )

    quiz_dir = _REPO_ROOT / "course_content" / course_id / "quizzes"
    if not quiz_dir.exists() or not quiz_dir.is_dir():
        raise FileNotFoundError(
            f"Quiz directory not found for course {course_id!r}: {quiz_dir}"
        )

    json_files = sorted(quiz_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(
            f"No quiz JSON files found in {quiz_dir}"
        )

    library: dict[str, dict] = {}

    for json_file in json_files:
        with json_file.open(encoding="utf-8") as fh:
            raw = json.load(fh)

        # Normalise: a file may hold a single quiz dict or a list of quiz dicts.
        entries: list = raw if isinstance(raw, list) else [raw]

        for entry in entries:
            quiz = _validate_quiz(entry, source=json_file.name, course_id=course_id)
            quiz_id: str = quiz["quiz_id"]

            if quiz_id in library:
                raise ValueError(
                    f"[{course_id}] Duplicate quiz_id {quiz_id!r} "
                    f"found in {json_file.name}"
                )

            library[quiz_id] = quiz

    return library


# ---------------------------------------------------------------------------
# Internal helpers (importable for unit tests)
# ---------------------------------------------------------------------------

def _validate_quiz(raw: object, source: str, course_id: str) -> dict:
    """Validate a single quiz dict and return it unchanged if valid.

    Args:
        raw: Candidate quiz object parsed from JSON.
        source: Filename string used in error messages.
        course_id: Course context used in error messages.

    Returns:
        The validated quiz dict (same object, not a copy).

    Raises:
        ValueError: If any required field is missing, the wrong type, or
                    out of range.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"[{course_id}] Quiz entry in {source!r} must be a dict, "
            f"got {type(raw).__name__}"
        )

    # --- quiz_id -------------------------------------------------------
    quiz_id = raw.get("quiz_id")
    if not isinstance(quiz_id, str) or not quiz_id:
        raise ValueError(
            f"[{course_id}] Quiz in {source!r}: "
            f"'quiz_id' must be a non-empty string, got {quiz_id!r}"
        )

    # --- questions -----------------------------------------------------
    questions = raw.get("questions")
    if not isinstance(questions, list) or len(questions) == 0:
        raise ValueError(
            f"[{course_id}] Quiz {quiz_id!r} in {source!r}: "
            f"'questions' must be a non-empty list"
        )

    for idx, q in enumerate(questions):
        _validate_question(q, idx=idx, quiz_id=quiz_id, source=source, course_id=course_id)

    return raw


def _validate_question(
    q: object,
    *,
    idx: int,
    quiz_id: str,
    source: str,
    course_id: str,
) -> None:
    """Validate one question entry within a quiz.

    Raises:
        ValueError: On any structural or type violation.
    """
    loc = f"[{course_id}] Quiz {quiz_id!r} in {source!r}, question[{idx}]"

    if not isinstance(q, dict):
        raise ValueError(f"{loc}: must be a dict, got {type(q).__name__}")

    # question text
    text = q.get("question")
    if not isinstance(text, str) or not text:
        raise ValueError(f"{loc}: 'question' must be a non-empty string, got {text!r}")

    # options
    options = q.get("options")
    if not isinstance(options, list) or len(options) < 2:
        raise ValueError(
            f"{loc}: 'options' must be a list with at least 2 items, got {options!r}"
        )
    for oi, opt in enumerate(options):
        if not isinstance(opt, str):
            raise ValueError(
                f"{loc}: 'options[{oi}]' must be a string, got {type(opt).__name__}"
            )

    # correct_index
    ci = q.get("correct_index")
    # bool is a subclass of int — reject it explicitly
    if isinstance(ci, bool) or not isinstance(ci, int):
        raise ValueError(
            f"{loc}: 'correct_index' must be an int, got {type(ci).__name__}"
        )
    if not (0 <= ci < len(options)):
        raise ValueError(
            f"{loc}: 'correct_index' {ci} is out of range "
            f"[0, {len(options) - 1}] for {len(options)} options"
        )
