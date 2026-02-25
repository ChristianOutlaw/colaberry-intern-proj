"""
execution/course/course_registry.py

Canonical course definition for FREE_INTRO_AI_V0.

Source of truth: directives/COURSE_STRUCTURE.md
No database access. Pure constants and helpers only.
"""

COURSE_ID: str = "FREE_INTRO_AI_V0"

# Immutable set of all valid section IDs for FREE_INTRO_AI_V0.
# Defined in directives/COURSE_STRUCTURE.md — do not add, remove, or rename
# entries without updating the directive first.
SECTION_IDS: frozenset[str] = frozenset({
    "P1_S1", "P1_S2", "P1_S3",
    "P2_S1", "P2_S2", "P2_S3",
    "P3_S1", "P3_S2", "P3_S3",
})

TOTAL_SECTIONS: int = 9


def is_valid_section_id(section_id: str) -> bool:
    """Return True if section_id is a canonical section for FREE_INTRO_AI_V0.

    Args:
        section_id: The section identifier to validate.

    Returns:
        True when section_id is in SECTION_IDS; False otherwise.
    """
    return section_id in SECTION_IDS
