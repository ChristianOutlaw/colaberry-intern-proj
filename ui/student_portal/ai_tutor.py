"""
ui/student_portal/ai_tutor.py

AI Tutor for the Student Course Player.

Tries OpenAI if OPENAI_API_KEY is set and the 'openai' package is importable.
Falls back to a fully deterministic local reply otherwise.

No secrets stored here; no requirements files modified.
"""

from __future__ import annotations

import os
import re


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

    # ── Summarize ──────────────────────────────────────────────────────────
    if "summarize" in lower or "summary" in lower:
        parts = [f"**Summary of \"{section_title}\"**\n"]
        if headings:
            parts.append("**Topics covered:** " + ", ".join(headings))
        if key_ideas:
            parts.append("\n**Key ideas:**")
            for idea in key_ideas:
                parts.append(f"- {idea}")
        if not headings and not key_ideas:
            parts.append(
                f"This section covers: **{section_title}**. "
                "Read through the lesson content above for the full picture."
            )
        return "\n".join(parts)

    # ── Quiz ───────────────────────────────────────────────────────────────
    if "quiz" in lower or "question" in lower:
        if len(key_ideas) >= 2:
            return (
                f"**Quiz — {section_title}**\n\n"
                f"**Q1.** In your own words, explain:\n> *{key_ideas[0]}*\n\n"
                f"**Q2.** Why does this matter?\n> *{key_ideas[1]}*\n\n"
                "*(Write your answers, then compare with the lesson content above.)*"
            )
        if len(key_ideas) == 1:
            return (
                f"**Quiz — {section_title}**\n\n"
                f"**Q1.** In your own words, explain:\n> *{key_ideas[0]}*\n\n"
                "**Q2.** How would you apply this concept in a real-world scenario?\n\n"
                "*(Write your answers, then compare with the lesson content above.)*"
            )
        return (
            f"**Quiz — {section_title}**\n\n"
            "**Q1.** What is the main idea of this section?\n\n"
            "**Q2.** How does what you learned here connect to something you already know?\n\n"
            "*(Write your answers, then compare with the lesson content above.)*"
        )

    # ── Explain like I'm new ───────────────────────────────────────────────
    if "explain" in lower or "new" in lower or "beginner" in lower or "simple" in lower:
        parts = [f"**{section_title} — Simply Explained**\n"]
        parts.append(
            f"Think of **{section_title}** as a big idea broken into smaller, digestible pieces."
        )
        if key_ideas:
            parts.append("\nHere are the essentials in plain language:")
            for idea in key_ideas:
                parts.append(f"- {idea}")
        elif headings:
            parts.append(f"\nThe section walks through: {', '.join(headings)}.")
        parts.append(
            "\nTake it one piece at a time — re-read the section above if anything feels unclear."
        )
        return "\n".join(parts)

    # ── Give me an example ─────────────────────────────────────────────────
    if "example" in lower:
        if key_ideas:
            return (
                f"**Example for \"{section_title}\"**\n\n"
                f"Consider this key idea: *{key_ideas[0]}*\n\n"
                "A concrete way to think about it: imagine explaining this to a friend who has "
                "never heard of it. You'd start by naming what it is, then show one real-world "
                "case where it applies.\n\n"
                "The lesson content above includes specific examples — look for tables, "
                "code blocks, or numbered steps."
            )
        return (
            f"**Example for \"{section_title}\"**\n\n"
            "The lesson content above contains worked examples. "
            "Re-read it and look for tables, code blocks, or numbered steps — "
            "those are the concrete illustrations."
        )

    # ── Catch-all ──────────────────────────────────────────────────────────
    parts = [f"Great question about **{section_title}**!\n"]
    if key_ideas:
        parts.append("Here are the key ideas for this section:")
        for idea in key_ideas:
            parts.append(f"- {idea}")
        parts.append(
            "\nReview the lesson content above for more detail, or use the quick-action "
            "buttons for a summary, plain explanation, example, or quiz."
        )
    elif headings:
        parts.append(f"This section covers: {', '.join(headings)}.")
        parts.append("Review the lesson content above, or try the quick-action buttons.")
    else:
        parts.append(
            "I'm here to help! Use the quick-action buttons above for a summary, "
            "simple explanation, example, or quiz based on this section."
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_tutor_reply(
    *,
    section_title: str,
    section_markdown: str,
    user_message: str,
) -> str:
    """Generate an AI tutor reply for the given section and user message.

    Tries OpenAI when OPENAI_API_KEY is set and the ``openai`` package is
    importable.  Falls back to a fully deterministic local reply otherwise.

    Args:
        section_title:    Display title of the current section.
        section_markdown: Raw markdown content of the current section.
        user_message:     The student's message or quick-action prompt text.

    Returns:
        A markdown-formatted reply string.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if api_key:
        try:
            import openai  # noqa: PLC0415 — intentional late import

            client = openai.OpenAI(api_key=api_key)
            system_prompt = (
                "You are a helpful AI tutor for a course called \"Free Intro to AI\".\n"
                f"The student is currently reading the section titled: \"{section_title}\".\n\n"
                f"Section content:\n{section_markdown}\n\n"
                "Answer the student's question concisely and helpfully, "
                "referencing the section content where relevant. "
                "Use markdown formatting in your reply."
            )
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=512,
                temperature=0.7,
            )
            content = response.choices[0].message.content
            if content:
                return content
        except Exception:
            pass  # Fall through to deterministic reply

    return _deterministic_reply(
        section_title=section_title,
        section_markdown=section_markdown,
        user_message=user_message,
    )
