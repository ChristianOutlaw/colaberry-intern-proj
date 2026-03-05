"""
tests/test_ai_tutor.py

Unit tests for ui/student_portal/ai_tutor.py — deterministic path only.

Design constraints:
- No network calls (OPENAI_API_KEY is never set in this module).
- No openai package required.
- No Streamlit required.
- All tests are pure-Python pytest style.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap — repo root is two levels above this file (tests/).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Guarantee no OpenAI key leaks in from the shell environment.
os.environ.pop("OPENAI_API_KEY", None)

from ui.student_portal.ai_tutor import (  # noqa: E402
    _extract_headings,
    _extract_key_ideas,
    generate_tutor_reply,
)

# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

_SECTION_TITLE = "What Is AI?"

_RICH_MARKDOWN = """\
# What Is AI?

Artificial intelligence is the simulation of human intelligence by machines.

## How Machines Learn

Machines learn from data by identifying patterns.

### Types of learning

- Supervised learning
- Unsupervised learning

## Key ideas

- AI mimics human reasoning
- Data is the fuel for machine learning
- Models improve with more examples
"""

_NO_HEADING_MARKDOWN = """\
Artificial intelligence is broad.
It covers many subfields.
"""

_NO_KEY_IDEAS_MARKDOWN = """\
## Introduction

Some introductory text with no Key ideas block.
"""

_EMPTY_MARKDOWN = ""


# ---------------------------------------------------------------------------
# T1–T2: _extract_headings
# ---------------------------------------------------------------------------

class TestExtractHeadings:
    def test_returns_headings_in_order(self):
        result = _extract_headings(_RICH_MARKDOWN)
        assert "What Is AI?" in result
        assert "How Machines Learn" in result
        assert "Types of learning" in result
        # Order: first heading is the H1
        assert result[0] == "What Is AI?"

    def test_returns_empty_list_when_no_headings(self):
        result = _extract_headings(_NO_HEADING_MARKDOWN)
        assert result == []

    def test_returns_empty_list_for_empty_string(self):
        result = _extract_headings(_EMPTY_MARKDOWN)
        assert result == []

    def test_does_not_include_body_text(self):
        result = _extract_headings(_RICH_MARKDOWN)
        # Body text should never appear as a heading
        assert "Artificial intelligence" not in result


# ---------------------------------------------------------------------------
# T3–T4: _extract_key_ideas
# ---------------------------------------------------------------------------

class TestExtractKeyIdeas:
    def test_extracts_bullet_items_from_key_ideas_block(self):
        result = _extract_key_ideas(_RICH_MARKDOWN)
        assert "AI mimics human reasoning" in result
        assert "Data is the fuel for machine learning" in result
        assert "Models improve with more examples" in result

    def test_returns_empty_list_when_section_absent(self):
        result = _extract_key_ideas(_NO_KEY_IDEAS_MARKDOWN)
        assert result == []

    def test_returns_empty_list_for_empty_string(self):
        result = _extract_key_ideas(_EMPTY_MARKDOWN)
        assert result == []

    def test_case_insensitive_section_header(self):
        md = "## KEY IDEAS\n- First idea\n- Second idea\n"
        result = _extract_key_ideas(md)
        assert "First idea" in result
        assert "Second idea" in result


# ---------------------------------------------------------------------------
# T5–T10: generate_tutor_reply — deterministic fallback path
# (OPENAI_API_KEY is unset; openai package may or may not be installed)
# ---------------------------------------------------------------------------

class TestGenerateTutorReplyDeterministic:
    """All tests confirm the deterministic fallback is exercised.

    Stable markers come directly from _deterministic_reply() strings in
    ai_tutor.py; they are hard-coded literals, not generated text, so they
    will only change if the source file is intentionally edited.
    """

    def _reply(self, user_message: str, markdown: str = _RICH_MARKDOWN) -> str:
        return generate_tutor_reply(
            section_title=_SECTION_TITLE,
            section_markdown=markdown,
            user_message=user_message,
        )

    # T5 — summarize
    def test_summarize_prompt_returns_summary_marker(self):
        reply = self._reply("Summarize this section for me.")
        # _deterministic_reply uses the literal string "Summary" in the first line
        assert "Summary" in reply

    def test_summarize_includes_section_title(self):
        reply = self._reply("Summarize this section for me.")
        assert _SECTION_TITLE in reply

    # T6 — quiz
    def test_quiz_prompt_returns_two_questions(self):
        reply = self._reply("Quiz me with 2 questions about this section.")
        assert "Q1" in reply
        assert "Q2" in reply

    def test_quiz_prompt_references_section_title(self):
        reply = self._reply("Quiz me with 2 questions about this section.")
        assert _SECTION_TITLE in reply

    # T7 — explain
    def test_explain_prompt_returns_non_empty(self):
        reply = self._reply("Explain this section like I'm completely new to the topic.")
        assert reply.strip() != ""

    def test_explain_prompt_references_section_title(self):
        reply = self._reply("Explain this section like I'm completely new to the topic.")
        assert _SECTION_TITLE in reply

    # T8 — example
    def test_example_prompt_contains_example_marker(self):
        reply = self._reply("Give me a concrete example of the key ideas in this section.")
        # _deterministic_reply formats as '**Example for "..."**'
        assert "Example" in reply

    def test_example_prompt_returns_non_empty(self):
        reply = self._reply("Give me a concrete example of the key ideas in this section.")
        assert reply.strip() != ""

    # T9 — unknown / catch-all
    def test_unknown_prompt_returns_non_empty(self):
        reply = self._reply("What is the meaning of life?")
        assert reply.strip() != ""

    def test_unknown_prompt_references_section_title(self):
        # The catch-all includes the section title in the opening line
        reply = self._reply("What is the meaning of life?")
        assert _SECTION_TITLE in reply

    # T10 — empty section_markdown does not raise
    def test_empty_section_markdown_does_not_raise(self):
        for message in [
            "Summarize this section for me.",
            "Quiz me with 2 questions about this section.",
            "Explain this section like I'm completely new to the topic.",
            "Give me a concrete example of the key ideas in this section.",
            "What is the meaning of life?",
        ]:
            reply = self._reply(message, markdown=_EMPTY_MARKDOWN)
            assert isinstance(reply, str)
            assert reply.strip() != ""

    # T11 — function signature accepts keyword-only args correctly (incl. new optional params)
    def test_keyword_only_args_enforced(self):
        import inspect
        sig = inspect.signature(generate_tutor_reply)
        params = list(sig.parameters.values())
        # All parameters must be keyword-only (no positional args).
        for p in params:
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"Parameter '{p.name}' should be keyword-only"
            )
        # New optional params must be present with None defaults.
        param_names = {p.name for p in params}
        for expected in ("section_idx", "total_sections", "chunk_idx", "total_chunks", "flow_step"):
            assert expected in param_names, f"Expected optional param '{expected}' in signature"

    # T13 — new optional context kwargs are accepted and do not raise
    def test_context_kwargs_accepted_deterministic(self):
        """All new optional kwargs are forwarded without raising (deterministic path)."""
        for flow in ("lesson", "quiz", "reflection", None):
            reply = generate_tutor_reply(
                section_title=_SECTION_TITLE,
                section_markdown=_RICH_MARKDOWN,
                user_message="Summarize this section for me.",
                section_idx=2,
                total_sections=9,
                chunk_idx=1,
                total_chunks=5,
                flow_step=flow,
            )
            assert "Summary" in reply, f"Expected summary marker with flow_step={flow!r}"

    # T14 — optional kwargs default to None; deterministic output is identical
    def test_context_kwargs_default_none_same_output(self):
        """Passing explicit None kwargs yields the same reply as omitting them."""
        reply_default = generate_tutor_reply(
            section_title=_SECTION_TITLE,
            section_markdown=_RICH_MARKDOWN,
            user_message="Summarize this section for me.",
        )
        reply_explicit_none = generate_tutor_reply(
            section_title=_SECTION_TITLE,
            section_markdown=_RICH_MARKDOWN,
            user_message="Summarize this section for me.",
            section_idx=None,
            total_sections=None,
            chunk_idx=None,
            total_chunks=None,
            flow_step=None,
        )
        assert reply_default == reply_explicit_none

    # T12 — no network call made when OPENAI_API_KEY is unset
    def test_no_openai_call_when_key_absent(self, monkeypatch):
        """Confirm the function does NOT attempt to import or call openai
        when the environment key is missing."""
        # If openai were called it would raise or return a different object;
        # patching __import__ lets us detect an attempt to import it.
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        import builtins
        import_calls: list[str] = []

        original = builtins.__import__

        def tracking_import(name, *args, **kwargs):
            if name == "openai":
                import_calls.append(name)
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", tracking_import)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        reply = generate_tutor_reply(
            section_title=_SECTION_TITLE,
            section_markdown=_RICH_MARKDOWN,
            user_message="Summarize this section for me.",
        )

        assert reply.strip() != ""
        assert import_calls == [], (
            "openai should not be imported when OPENAI_API_KEY is absent"
        )
