"""
tests/test_load_quiz_library.py

Unit tests for execution/course/load_quiz_library.py.

Four test groups:
  1. Happy path — real FREE_INTRO_AI_V0 quiz files load correctly and
     quiz_ids overlap with those referenced in course_map.json.
  2. Invalid course_id — ValueError for unknown course IDs.
  3. Validation errors — synthetic bad quiz dicts trigger descriptive
     ValueErrors (tested via _validate_quiz directly, no file I/O).
  4. FileNotFoundError — missing quizzes directory via _REPO_ROOT monkeypatch.

No database access. No randomness. No network calls.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.course.load_quiz_library import (  # noqa: E402
    load_quiz_library,
    _validate_quiz,
)
from execution.course.load_course_map import load_course_map  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal valid quiz fixture reused across validation tests
# ---------------------------------------------------------------------------
def _make_quiz(**overrides) -> dict:
    """Return a minimal valid quiz dict, with optional field overrides."""
    base = {
        "quiz_id": "quiz_test_1",
        "questions": [
            {
                "question": "Sample question?",
                "options": ["Option A", "Option B"],
                "correct_index": 0,
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

class TestLoadQuizLibraryHappyPath(unittest.TestCase):

    def setUp(self):
        self.library = load_quiz_library("FREE_INTRO_AI_V0")
        self.course_map = load_course_map("FREE_INTRO_AI_V0")

    def test_returns_dict(self):
        """Result is a dict."""
        self.assertIsInstance(self.library, dict)

    def test_all_keys_are_strings(self):
        """Every key in the returned dict is a string."""
        for k in self.library:
            self.assertIsInstance(k, str, f"Key {k!r} is not a string")

    def test_all_values_are_dicts(self):
        """Every value in the returned dict is a dict."""
        for qid, qdata in self.library.items():
            with self.subTest(quiz_id=qid):
                self.assertIsInstance(qdata, dict)

    def test_quiz_ids_overlap_with_course_map(self):
        """At least one quiz_id in the library is referenced by course_map.json."""
        map_quiz_ids: set[str] = set()
        for section in self.course_map.values():
            map_quiz_ids.update(section.get("quiz_ids", []))

        overlap = set(self.library.keys()) & map_quiz_ids
        self.assertGreater(
            len(overlap),
            0,
            f"No overlap between library keys {set(self.library.keys())} "
            f"and course_map quiz_ids {map_quiz_ids}",
        )

    def test_all_course_map_quiz_ids_present(self):
        """Every quiz_id listed in course_map.json is present in the library."""
        for section_id, section in self.course_map.items():
            for qid in section.get("quiz_ids", []):
                with self.subTest(section_id=section_id, quiz_id=qid):
                    self.assertIn(
                        qid,
                        self.library,
                        f"quiz_id {qid!r} from course_map section {section_id!r} "
                        f"not found in quiz library",
                    )

    def test_each_quiz_has_non_empty_questions(self):
        """Every loaded quiz has at least one question."""
        for qid, qdata in self.library.items():
            with self.subTest(quiz_id=qid):
                self.assertIsInstance(qdata.get("questions"), list)
                self.assertGreater(len(qdata["questions"]), 0)

    def test_each_question_has_valid_correct_index(self):
        """correct_index is within bounds for every question in every quiz."""
        for qid, qdata in self.library.items():
            for i, q in enumerate(qdata["questions"]):
                with self.subTest(quiz_id=qid, question_idx=i):
                    ci = q["correct_index"]
                    options = q["options"]
                    self.assertGreaterEqual(ci, 0)
                    self.assertLess(ci, len(options))


# ---------------------------------------------------------------------------
# 2. Invalid course_id
# ---------------------------------------------------------------------------

class TestLoadQuizLibraryInvalidCourseId(unittest.TestCase):

    def test_unknown_course_id_raises_value_error(self):
        """ValueError for an unrecognised course_id; message contains the bad ID."""
        with self.assertRaises(ValueError) as ctx:
            load_quiz_library("NONEXISTENT_COURSE")
        self.assertIn("NONEXISTENT_COURSE", str(ctx.exception))

    def test_empty_course_id_raises_value_error(self):
        """ValueError for empty-string course_id."""
        with self.assertRaises(ValueError):
            load_quiz_library("")

    def test_wrong_case_course_id_raises_value_error(self):
        """ValueError for course_id with wrong casing."""
        with self.assertRaises(ValueError):
            load_quiz_library("free_intro_ai_v0")


# ---------------------------------------------------------------------------
# 3. Validation errors (synthetic bad structures via _validate_quiz)
# ---------------------------------------------------------------------------

class TestValidateQuizErrors(unittest.TestCase):

    _SRC = "test_file.json"
    _CID = "TEST_COURSE"

    def _call(self, raw):
        return _validate_quiz(raw, source=self._SRC, course_id=self._CID)

    # --- top-level type --------------------------------------------------

    def test_not_a_dict_raises(self):
        """ValueError when entry is a list, not a dict."""
        with self.assertRaises(ValueError) as ctx:
            self._call(["not", "a", "dict"])
        self.assertIn("dict", str(ctx.exception))

    def test_none_raises(self):
        """ValueError when entry is None."""
        with self.assertRaises(ValueError):
            self._call(None)

    # --- quiz_id ---------------------------------------------------------

    def test_missing_quiz_id_raises(self):
        """ValueError when quiz_id key is absent."""
        with self.assertRaises(ValueError) as ctx:
            self._call({"questions": [{"question": "Q?", "options": ["A", "B"], "correct_index": 0}]})
        self.assertIn("quiz_id", str(ctx.exception))

    def test_empty_quiz_id_raises(self):
        """ValueError when quiz_id is an empty string."""
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(quiz_id=""))
        self.assertIn("quiz_id", str(ctx.exception))

    def test_numeric_quiz_id_raises(self):
        """ValueError when quiz_id is an int."""
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(quiz_id=42))
        self.assertIn("quiz_id", str(ctx.exception))

    # --- questions -------------------------------------------------------

    def test_missing_questions_raises(self):
        """ValueError when questions key is absent."""
        with self.assertRaises(ValueError) as ctx:
            self._call({"quiz_id": "q1"})
        self.assertIn("questions", str(ctx.exception))

    def test_empty_questions_list_raises(self):
        """ValueError when questions is an empty list."""
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[]))
        self.assertIn("questions", str(ctx.exception))

    def test_questions_not_list_raises(self):
        """ValueError when questions is a string."""
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions="not a list"))
        self.assertIn("questions", str(ctx.exception))

    # --- question entries ------------------------------------------------

    def test_question_not_dict_raises(self):
        """ValueError when a question entry is a string."""
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=["just a string"]))
        self.assertIn("dict", str(ctx.exception))

    def test_missing_question_text_raises(self):
        """ValueError when 'question' key is missing from a question."""
        bad_q = {"options": ["A", "B"], "correct_index": 0}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("question", str(ctx.exception))

    def test_empty_question_text_raises(self):
        """ValueError when 'question' is an empty string."""
        bad_q = {"question": "", "options": ["A", "B"], "correct_index": 0}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("question", str(ctx.exception))

    # --- options ---------------------------------------------------------

    def test_options_too_short_raises(self):
        """ValueError when options has only one item."""
        bad_q = {"question": "Q?", "options": ["Only one"], "correct_index": 0}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("options", str(ctx.exception))

    def test_options_not_list_raises(self):
        """ValueError when options is a dict."""
        bad_q = {"question": "Q?", "options": {"a": 1}, "correct_index": 0}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("options", str(ctx.exception))

    def test_option_non_string_raises(self):
        """ValueError when one option element is an int."""
        bad_q = {"question": "Q?", "options": ["A", 2], "correct_index": 0}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("options", str(ctx.exception))

    # --- correct_index ---------------------------------------------------

    def test_correct_index_missing_raises(self):
        """ValueError when correct_index key is absent."""
        bad_q = {"question": "Q?", "options": ["A", "B"]}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("correct_index", str(ctx.exception))

    def test_correct_index_out_of_range_raises(self):
        """ValueError when correct_index equals len(options)."""
        bad_q = {"question": "Q?", "options": ["A", "B"], "correct_index": 2}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("correct_index", str(ctx.exception))

    def test_correct_index_negative_raises(self):
        """ValueError when correct_index is negative."""
        bad_q = {"question": "Q?", "options": ["A", "B"], "correct_index": -1}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("correct_index", str(ctx.exception))

    def test_correct_index_bool_raises(self):
        """ValueError when correct_index is True (bool subclasses int)."""
        bad_q = {"question": "Q?", "options": ["A", "B"], "correct_index": True}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("correct_index", str(ctx.exception))

    def test_correct_index_float_raises(self):
        """ValueError when correct_index is a float."""
        bad_q = {"question": "Q?", "options": ["A", "B"], "correct_index": 0.0}
        with self.assertRaises(ValueError) as ctx:
            self._call(_make_quiz(questions=[bad_q]))
        self.assertIn("correct_index", str(ctx.exception))

    # --- valid quiz passes -----------------------------------------------

    def test_valid_quiz_passes(self):
        """A fully valid quiz dict returns without error."""
        valid = _make_quiz()
        result = self._call(valid)
        self.assertIs(result, valid)

    def test_valid_quiz_with_extra_keys_passes(self):
        """Extra keys on quiz or question are allowed (open schema)."""
        valid = _make_quiz()
        valid["description"] = "Extra field"
        valid["questions"][0]["hint"] = "Extra hint"
        result = self._call(valid)
        self.assertIs(result, valid)


# ---------------------------------------------------------------------------
# 4. FileNotFoundError — missing quizzes directory
# ---------------------------------------------------------------------------

class TestLoadQuizLibraryFileNotFound(unittest.TestCase):

    def test_missing_quiz_dir_raises_file_not_found(self):
        """FileNotFoundError when the quizzes/ directory does not exist."""
        import execution.course.load_quiz_library as mod

        original_root = mod._REPO_ROOT
        try:
            with tempfile.TemporaryDirectory() as td:
                mod._REPO_ROOT = Path(td)
                with self.assertRaises(FileNotFoundError):
                    load_quiz_library("FREE_INTRO_AI_V0")
        finally:
            mod._REPO_ROOT = original_root

    def test_empty_quiz_dir_raises_file_not_found(self):
        """FileNotFoundError when the quizzes/ directory exists but has no JSON files."""
        import execution.course.load_quiz_library as mod

        original_root = mod._REPO_ROOT
        try:
            with tempfile.TemporaryDirectory() as td:
                # Create the empty quizzes directory
                quiz_dir = Path(td) / "course_content" / "FREE_INTRO_AI_V0" / "quizzes"
                quiz_dir.mkdir(parents=True)
                mod._REPO_ROOT = Path(td)
                with self.assertRaises(FileNotFoundError):
                    load_quiz_library("FREE_INTRO_AI_V0")
        finally:
            mod._REPO_ROOT = original_root


if __name__ == "__main__":
    unittest.main()
