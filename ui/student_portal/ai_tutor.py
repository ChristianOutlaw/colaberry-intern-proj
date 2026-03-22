"""
ui/student_portal/ai_tutor.py

AI Tutor for the Student Course Player.

Tries OpenAI if OPENAI_API_KEY is set and the 'openai' package is importable.
Falls back to a fully deterministic local reply otherwise.

No secrets stored here; no requirements files modified.
"""

from __future__ import annotations

import json
import os
import pathlib
import re


# ---------------------------------------------------------------------------
# Follow-up guidance lines — deterministic rotation, no randomness
# ---------------------------------------------------------------------------

_FOLLOWUP_LINES: tuple[str, ...] = (
    "Want me to break that down further?",
    "Want a quick example for this?",
    "Want to test your understanding?",
    "I can simplify that more if you want.",
)

# ---------------------------------------------------------------------------
# Course-content grounding — loaded once at import time
# ---------------------------------------------------------------------------

# Absolute path to the course content root, resolved relative to this file.
# ai_tutor.py lives at  ui/student_portal/ai_tutor.py
# course content lives at  course_content/FREE_INTRO_AI_V0/
_CONTENT_ROOT: pathlib.Path = (
    pathlib.Path(__file__).parent.parent.parent / "course_content" / "FREE_INTRO_AI_V0"
)

# Ordered list of (section_id, display_title) — index matches the 0-based section_idx
# passed to generate_tutor_reply, so hints can be resolved without a new parameter.
_SECTION_FILES: tuple[tuple[str, str], ...] = (
    ("P1_S1", "What Is AI?"),
    ("P1_S2", "How Machines Learn"),
    ("P1_S3", "AI in the Real World"),
    ("P2_S1", "Understanding Data"),
    ("P2_S2", "Exploring Data"),
    ("P2_S3", "Preparing Data for AI"),
    ("P3_S1", "Building Your First Model"),
    ("P3_S2", "Evaluating Results"),
    ("P3_S3", "Next Steps in AI"),
)


def _build_course_summary() -> str:
    """One-paragraph-per-section overview used as low-priority background context."""
    lines: list[str] = []
    for section_id, title in _SECTION_FILES:
        try:
            text = (_CONTENT_ROOT / f"{section_id}.md").read_text(encoding="utf-8")
        except OSError:
            continue
        first_para = ""
        for para in re.split(r"\n{2,}", text):
            para = para.strip()
            if para and not para.startswith("#") and para != "---" and len(para) > 50:
                # Truncate very long intros to keep the summary compact.
                first_para = para[:200].rstrip()
                break
        if first_para:
            lines.append(f"- **{title}:** {first_para}")
    return "\n".join(lines)


def _build_quiz_hints() -> dict[str, list[str]]:
    """Return section_id -> [hint, ...] for every section that has quiz hints."""
    try:
        course_map = json.loads((_CONTENT_ROOT / "course_map.json").read_text(encoding="utf-8"))
    except OSError:
        return {}

    # Build quiz_id -> hints lookup by scanning every quiz JSON file.
    quiz_hints: dict[str, list[str]] = {}
    try:
        for quiz_file in (_CONTENT_ROOT / "quizzes").glob("*.json"):
            for quiz in json.loads(quiz_file.read_text(encoding="utf-8")):
                qid = quiz.get("quiz_id", "")
                hints = list(dict.fromkeys(  # deduplicate within quiz, preserve order
                    q["hint"] for q in quiz.get("questions", []) if q.get("hint")
                ))
                if qid and hints:
                    quiz_hints[qid] = hints
    except OSError:
        return {}

    # Map section_id -> flat, deduplicated hint list.
    section_hints: dict[str, list[str]] = {}
    for section in course_map.get("sections", []):
        sid = section.get("section_id", "")
        all_hints: list[str] = []
        for qid in section.get("quiz_ids", []):
            all_hints.extend(quiz_hints.get(qid, []))
        deduped = list(dict.fromkeys(all_hints))  # deduplicate across quizzes
        if deduped:
            section_hints[sid] = deduped
    return section_hints


# Built once at import time; empty strings/dicts are safe fallbacks if files are missing.
_FULL_COURSE_SUMMARY: str = _build_course_summary()
_SECTION_QUIZ_HINTS: dict[str, list[str]] = _build_quiz_hints()


# ---------------------------------------------------------------------------
# Internal parsing helpers — purely functional, no randomness
# ---------------------------------------------------------------------------

def _extract_headings(markdown: str) -> list[str]:
    """Return all heading texts (H1–H3) from markdown, in order."""
    return re.findall(r"^#{1,3}\s+(.+)", markdown, re.MULTILINE)


def _extract_key_ideas(markdown: str) -> list[str]:
    """Return bullet items from the '## Key ideas' section, if present."""
    match = re.search(
        r"^##\s+Key ideas\s*\n((?:[-*]\s+.+\n?)+)",
        markdown,
        re.MULTILINE | re.IGNORECASE,
    )
    if match:
        return re.findall(r"^[-*]\s+(.+)", match.group(1), re.MULTILINE)
    return []


# ---------------------------------------------------------------------------
# Deterministic reply builder
# ---------------------------------------------------------------------------

def _deterministic_reply(
    *,
    section_title: str,
    section_markdown: str,
    user_message: str,
) -> str:
    """Build a deterministic reply using only markdown parsing — no randomness."""
    lower = user_message.lower()

    headings = _extract_headings(section_markdown)
    key_ideas = _extract_key_ideas(section_markdown)

    # Deterministic follow-up rotation keyed by message length (no history available here).
    _followup = _FOLLOWUP_LINES[len(user_message) % len(_FOLLOWUP_LINES)]

    # ── Summarize ──────────────────────────────────────────────────────────
    if "summarize" in lower or "summary" in lower:
        parts = ["Here's what this section is really getting at:\n"]
        if headings:
            parts.append("**Topics covered:** " + ", ".join(headings))
        if key_ideas:
            parts.append("\nThe core ideas:")
            for idea in key_ideas:
                parts.append(f"- {idea}")
        if not headings and not key_ideas:
            parts.append(
                f"This section is all about **{section_title}**. "
                "Read through the lesson above for the full picture."
            )
        parts.append(f"\n{_followup}")
        return "\n".join(parts)

    # ── Quiz ───────────────────────────────────────────────────────────────
    if "quiz" in lower or "question" in lower:
        if len(key_ideas) >= 2:
            return (
                "Let's check your understanding — two quick questions:\n\n"
                f"**Q1.** In your own words, explain:\n> *{key_ideas[0]}*\n\n"
                f"**Q2.** Why does this matter?\n> *{key_ideas[1]}*\n\n"
                "*(Write your answers, then compare with the lesson content above.)*"
                f"\n\n{_followup}"
            )
        if len(key_ideas) == 1:
            return (
                "Here's a quick check on this section:\n\n"
                f"**Q1.** In your own words, explain:\n> *{key_ideas[0]}*\n\n"
                "**Q2.** How would you apply this concept in a real-world scenario?\n\n"
                "*(Write your answers, then compare with the lesson content above.)*"
                f"\n\n{_followup}"
            )
        return (
            "Here's a quick check on this section:\n\n"
            "**Q1.** What is the main idea of this section?\n\n"
            "**Q2.** How does what you learned here connect to something you already know?\n\n"
            "*(Write your answers, then compare with the lesson content above.)*"
            f"\n\n{_followup}"
        )

    # ── Explain like I'm new ───────────────────────────────────────────────
    if "explain" in lower or "new" in lower or "beginner" in lower or "simple" in lower:
        parts = ["Let's break this down simply.\n"]
        parts.append(
            f"Think of **{section_title}** as one big idea made up of smaller, connected pieces."
        )
        if key_ideas:
            parts.append("\nHere's what it really comes down to:")
            for idea in key_ideas:
                parts.append(f"- {idea}")
        elif headings:
            parts.append(f"\nThe section walks through: {', '.join(headings)}.")
        parts.append(
            "\nTake it one piece at a time — re-read the section above if anything feels unclear."
        )
        parts.append(f"\n{_followup}")
        return "\n".join(parts)

    # ── Give me an example ─────────────────────────────────────────────────
    if "example" in lower:
        if key_ideas:
            return (
                "Here's a concrete way to think about it.\n\n"
                f"Take this idea: *{key_ideas[0]}*\n\n"
                "Imagine explaining it to someone who's never heard of it. "
                "You'd start by naming what it is, then show one real situation where it shows up.\n\n"
                "The lesson above has specific examples — look for tables, "
                f"code blocks, or numbered steps.\n\n{_followup}"
            )
        return (
            "The lesson above has worked examples worth revisiting.\n\n"
            "Look for tables, code blocks, or numbered steps — "
            f"those are the concrete illustrations.\n\n{_followup}"
        )

    # ── Catch-all ──────────────────────────────────────────────────────────
    parts = [f"Happy to help with **{section_title}**.\n"]
    if key_ideas:
        parts.append("Here's what this section is really about:")
        for idea in key_ideas:
            parts.append(f"- {idea}")
        parts.append(
            "\nFeel free to ask a follow-up, or use the quick-action buttons for a summary, "
            "plain explanation, example, or quiz."
        )
    elif headings:
        parts.append(f"This section covers: {', '.join(headings)}.")
        parts.append("Ask a follow-up or try the quick-action buttons.")
    else:
        parts.append(
            "Use the quick-action buttons above for a summary, simple explanation, "
            "example, or quiz — or just ask me directly."
        )
    parts.append(f"\n{_followup}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_tutor_reply(
    *,
    section_title: str,
    section_markdown: str,
    user_message: str,
    history: list[dict] | None = None,
    section_idx: int | None = None,
    total_sections: int | None = None,
    chunk_idx: int | None = None,
    total_chunks: int | None = None,
    flow_step: str | None = None,
) -> str:
    """Generate an AI tutor reply for the given section and user message.

    Tries OpenAI when OPENAI_API_KEY is set and the ``openai`` package is
    importable.  Falls back to a fully deterministic local reply otherwise.

    Args:
        section_title:    Display title of the current section.
        section_markdown: Raw markdown content of the current section.
        user_message:     The student's message or quick-action prompt text.
        section_idx:      0-based section index (optional, enriches system prompt).
        total_sections:   Total number of sections (optional).
        chunk_idx:        0-based lesson chunk index within the section (optional).
        total_chunks:     Total chunks in the current section (optional).
        flow_step:        Current player step — lesson|quiz|reflection|complete (optional).

    Returns:
        A markdown-formatted reply string.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if api_key:
        try:
            import openai  # noqa: PLC0415 — intentional late import

            client = openai.OpenAI(api_key=api_key)

            # Build a concise context line from optional progress params.
            _ctx_parts: list[str] = []
            if section_idx is not None and total_sections is not None:
                _ctx_parts.append(f"Section {section_idx + 1} of {total_sections}")
            if chunk_idx is not None and total_chunks is not None:
                _ctx_parts.append(f"Part {chunk_idx + 1} of {total_chunks}")
            if flow_step:
                _ctx_parts.append(f"Step: {flow_step}")
            _ctx_line = " | ".join(_ctx_parts)

            # Step-specific behavioral guidance injected into the prompt.
            _step_guidance: dict[str, str] = {
                "lesson":     "The student is reading the lesson. Explain ideas clearly and simply. Use concrete examples instead of abstract definitions.",
                "quiz":       "The student is taking a quiz. Guide their thinking without giving away answers. Ask a helpful question back if they seem stuck.",
                "reflection": "The student is in a reflection exercise. Help them go deeper — connect ideas, challenge assumptions, and surface what they actually learned.",
                "complete":   "The student just finished this section. Reinforce their understanding, highlight what matters, and build their confidence for what comes next.",
            }
            _guidance = _step_guidance.get(flow_step or "", "Help the student engage with the material in a meaningful way.")

            # Resolve active section_id for quiz-hint lookup (uses section_idx if available).
            _active_sid: str = (
                _SECTION_FILES[section_idx][0]
                if section_idx is not None and 0 <= section_idx < len(_SECTION_FILES)
                else ""
            )
            _active_hints: list[str] = _SECTION_QUIZ_HINTS.get(_active_sid, [])

            system_prompt = (
                "You are an encouraging learning guide — warm, clear, and direct. "
                "Your job is to help students genuinely understand ideas, not just answer questions. "
                "Avoid sounding like a textbook or a formal instructor. "
                "Be concise: 2–4 short paragraphs max unless the student explicitly asks for more. "
                "Prefer concrete examples over abstract explanations. "
                "Do not repeat the section title unnecessarily. "
                "Never say 'as an AI' or refer to yourself as a language model.\n\n"

                "## Conversational behavior\n"
                "Respond naturally to greetings, casual remarks, and vague inputs. "
                "If a student says something like 'hey' or 'hi', greet them warmly and invite them "
                "to ask about the current lesson or use the quick-action options. "
                "If the input is vague or unclear, infer from the current section context first; "
                "if context doesn't resolve it, ask a short clarifying question rather than guessing.\n\n"

                "## Scope — CRITICAL\n"
                "You may only answer questions related to artificial intelligence, data, machine learning, "
                "or concepts covered in this course. "
                "If a student asks something unrelated to AI, data, or this course — such as general trivia, "
                "unrelated homework, personal advice, or topics outside the curriculum — do not answer it directly. "
                "Instead, respond briefly and warmly, acknowledge their question, and redirect them back to the "
                "current lesson. Example: 'That's a bit outside what I cover here — I'm focused on AI and this "
                "course. Want me to help with something from the current section?'\n\n"

                + (
                    "## Full course overview (background — lower priority)\n"
                    "The course covers 9 sections. Use this only for cross-section questions "
                    "or to help students connect ideas across the course:\n"
                    + _FULL_COURSE_SUMMARY + "\n\n"
                    if _FULL_COURSE_SUMMARY else ""
                )

                + f"## Current section (primary grounding source)\n"
                + f"Section: \"{section_title}\"\n"
                + (f"Progress: {_ctx_line}\n" if _ctx_line else "")
                + f"Context: {_guidance}\n"
                + f"\nSection content:\n{section_markdown}\n\n"

                + (
                    "## Supplemental explanations for this section\n"
                    "These plain-language hints explain concepts students often find tricky here:\n"
                    + "\n".join(f"- {h}" for h in _active_hints) + "\n\n"
                    if _active_hints else ""
                )

                + "Use markdown formatting in your reply."
            )
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=(
                    [{"role": "system", "content": system_prompt}]
                    + (history or [])
                    + [{"role": "user", "content": user_message}]
                ),
                max_tokens=512,
                temperature=0.7,
            )
            content = response.choices[0].message.content
            if content:
                followup = _FOLLOWUP_LINES[len(history or []) % len(_FOLLOWUP_LINES)]
                return content + f"\n\n{followup}"
        except Exception:
            pass  # Fall through to deterministic reply

    # Deterministic fallback ignores progress context (pure markdown parsing).
    return _deterministic_reply(
        section_title=section_title,
        section_markdown=section_markdown,
        user_message=user_message,
    )
