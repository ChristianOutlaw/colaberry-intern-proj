"""
execution/course/load_course_map.py

Loads and validates course_map.json for a given course_id.

No database access. No randomness. Pure file I/O + validation.
Returns a dict keyed by section_id so callers get O(1) section lookup.
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

def load_course_map(course_id: str) -> dict:
    """Load and validate course_map.json for the given course_id.

    Returns a dict keyed by section_id, where each value is the
    corresponding section dict from the course map.

    Args:
        course_id: Identifier for the course (e.g. "FREE_INTRO_AI_V0").

    Returns:
        dict mapping section_id (str) -> section data dict.

    Raises:
        ValueError: If course_id is unsupported or the JSON structure is invalid.
        FileNotFoundError: If course_map.json does not exist for the course.
    """
    if course_id not in _SUPPORTED_COURSES:
        raise ValueError(
            f"Unsupported course_id: {course_id!r}. "
            f"Supported courses: {sorted(_SUPPORTED_COURSES)}"
        )

    map_path = _REPO_ROOT / "course_content" / course_id / "course_map.json"
    if not map_path.exists():
        raise FileNotFoundError(
            f"course_map.json not found for course {course_id!r}: {map_path}"
        )

    with map_path.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    return _build_and_validate(raw, course_id)


# ---------------------------------------------------------------------------
# Internal helpers (importable for unit tests)
# ---------------------------------------------------------------------------

def _build_and_validate(raw: object, course_id: str) -> dict:
    """Convert raw JSON to a section_id -> section dict mapping and validate it.

    Expects the JSON to have a top-level 'sections' list, each element of
    which is a dict containing at minimum a 'section_id' string key.

    Args:
        raw: Parsed JSON value from course_map.json.
        course_id: Used in error messages for context only.

    Returns:
        dict mapping section_id -> section data dict.

    Raises:
        ValueError: If the structure does not conform to the expected schema.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"[{course_id}] course_map.json top-level must be a dict, "
            f"got {type(raw).__name__}"
        )

    sections_raw = raw.get("sections")
    if sections_raw is None:
        raise ValueError(
            f"[{course_id}] course_map.json missing required top-level key 'sections'"
        )
    if not isinstance(sections_raw, list):
        raise ValueError(
            f"[{course_id}] 'sections' must be a list, "
            f"got {type(sections_raw).__name__}"
        )

    course_map: dict = {}
    for idx, section in enumerate(sections_raw):
        if not isinstance(section, dict):
            raise ValueError(
                f"[{course_id}] sections[{idx}] must be a dict, "
                f"got {type(section).__name__}"
            )

        section_id = section.get("section_id")
        if not isinstance(section_id, str) or not section_id:
            raise ValueError(
                f"[{course_id}] sections[{idx}] missing or invalid 'section_id' "
                f"(must be a non-empty string)"
            )

        # quiz_ids and reflection_prompts must be list[str] when present.
        _validate_list_of_str(section, "quiz_ids", course_id, section_id)
        _validate_list_of_str(section, "reflection_prompts", course_id, section_id)

        # curriculum_refs entries are dicts (phase/module/step), so only
        # validate that the field is a list when present.
        _validate_list(section, "curriculum_refs", course_id, section_id)

        course_map[section_id] = section

    return course_map


def _validate_list_of_str(
    section: dict, field: str, course_id: str, section_id: str
) -> None:
    """Raise ValueError if field is present but not a list of strings."""
    value = section.get(field)
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError(
            f"[{course_id}] section {section_id!r}: "
            f"'{field}' must be a list, got {type(value).__name__}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(
                f"[{course_id}] section {section_id!r}: "
                f"'{field}[{i}]' must be a string, got {type(item).__name__}"
            )


def _validate_list(
    section: dict, field: str, course_id: str, section_id: str
) -> None:
    """Raise ValueError if field is present but not a list (any element type)."""
    value = section.get(field)
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError(
            f"[{course_id}] section {section_id!r}: "
            f"'{field}' must be a list, got {type(value).__name__}"
        )
