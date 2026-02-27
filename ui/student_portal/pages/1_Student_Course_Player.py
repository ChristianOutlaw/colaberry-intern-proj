"""
ui/student_portal/pages/1_Student_Course_Player.py

Student Course Player — guided sequential flow (Flow Engine v1).
Directive: directives/UI_STUDENT_COURSE_PLAYER.md

Run from the repository root:
    streamlit run ui/student_portal/student_app.py
"""

import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# sys.path bootstrap — this file lives three levels below repo root
# (ui/student_portal/pages/).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.course.course_registry import SECTIONS, TOTAL_SECTIONS               # noqa: E402
from execution.course.load_course_map import load_course_map                        # noqa: E402
from execution.course.load_quiz_library import load_quiz_library                    # noqa: E402
from execution.leads.get_lead_status import get_lead_status                         # noqa: E402
from execution.leads.upsert_lead import upsert_lead                                 # noqa: E402
from execution.progress.compute_course_state import compute_course_state            # noqa: E402
from execution.progress.record_progress_event import record_progress_event          # noqa: E402
from execution.reflection.load_reflection_responses import load_reflection_responses  # noqa: E402
from execution.reflection.save_reflection_response import save_reflection_response   # noqa: E402
from ui.theme import apply_colaberry_theme                                          # noqa: E402
from ui.student_portal.ai_tutor import generate_tutor_reply                         # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = str(REPO_ROOT / "tmp" / "app.db")
COURSE_CONTENT_DIR = REPO_ROOT / "course_content" / "FREE_INTRO_AI_V0"
COURSE_ID = "FREE_INTRO_AI_V0"
EM_DASH = "\u2014"

# Human-readable question text for each reflection prompt identifier.
_PROMPT_QUESTIONS: dict[str, str] = {
    "confidence_start": (
        "How confident were you about AI before starting this section? Describe your starting point."
    ),
    "early_surprise": "What surprised you most in this section?",
    "motivation": "What is motivating you to learn about AI?",
    "interest_area": "Which area of AI interests you most so far, and why?",
    "confidence_current": "How has your understanding or confidence changed after this section?",
    "real_world_interest": "Which real-world AI application interests you most? Why?",
    "data_to_decision_reflection": (
        "How do you see data being used to make better decisions in your life or work?"
    ),
    "intent_level": (
        "How likely are you to continue learning AI after this course? "
        "What factors are influencing you?"
    ),
    "preferred_path": (
        "What learning path feels most right for you next — "
        "hands-on projects, structured courses, or something else?"
    ),
    "open_reflection": (
        "Is there anything else you want to capture about your learning journey so far?"
    ),
}


# ---------------------------------------------------------------------------
# Cached course data loaders — file I/O runs once per session.
# ---------------------------------------------------------------------------
@st.cache_data
def _cached_course_map() -> dict:
    return load_course_map(COURSE_ID)


@st.cache_data
def _cached_quiz_library() -> dict:
    return load_quiz_library(COURSE_ID)


# ---------------------------------------------------------------------------
# Markdown chunker — pure, deterministic, no randomness, stdlib only.
# ---------------------------------------------------------------------------

def _chunk_markdown(text: str) -> list[str]:
    """Split markdown into deterministic chunks for the guided lesson flow.

    Strategy 1 — heading split: each H1/H2/H3 heading plus its body becomes
    one chunk.  Requires ≥ 2 headings to activate (single-heading docs fall
    through to Strategy 2).

    Strategy 2 — paragraph groups: blank-line-separated paragraphs are
    collected and merged into at most 5 evenly-sized chunks.

    Returns at least one non-empty string.  Pure function; no I/O, no imports
    beyond stdlib re (already imported at module level).
    """
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return [""]

    # Strategy 1: insert a NUL sentinel before every heading line, then split.
    _SENTINEL = "\x00CHUNK\x00"
    marked = re.sub(r"^(#{1,3} )", _SENTINEL + r"\1", text, flags=re.MULTILINE)
    parts = [p.strip() for p in marked.split(_SENTINEL) if p.strip()]
    if len(parts) >= 2:
        return parts

    # Strategy 2: blank-line paragraph groups, capped at 5 chunks.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    n = len(paragraphs)
    if n <= 5:
        return paragraphs or [text]
    target = 5
    size = (n + target - 1) // target  # ceiling division for even distribution
    chunks = ["\n\n".join(paragraphs[i : i + size]) for i in range(0, n, size)]
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file.
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Student Course Player", layout="wide")
apply_colaberry_theme("Student Portal", "Free Intro to AI")

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
if "player_completed" not in st.session_state:
    st.session_state["player_completed"] = set()
if "player_lead_id" not in st.session_state:
    st.session_state["player_lead_id"] = ""
if "player_status" not in st.session_state:
    st.session_state["player_status"] = None
if "player_flash" not in st.session_state:
    st.session_state["player_flash"] = None  # (level, message) or None
if "tutor_messages" not in st.session_state:
    st.session_state["tutor_messages"] = []  # list[{"role": str, "content": str}]
if "tutor_pending" not in st.session_state:
    st.session_state["tutor_pending"] = None  # prompt enqueued by quick-action button
if "tutor_section_id" not in st.session_state:
    st.session_state["tutor_section_id"] = None  # tracks section for history reset
if "quiz_submitted" not in st.session_state:
    st.session_state["quiz_submitted"] = set()  # set of "{section_id}:{quiz_id}"
# Flow Engine v1 — guided sequential step state.
if "player_flow_step" not in st.session_state:
    st.session_state["player_flow_step"] = "welcome"   # welcome|lesson|quiz|reflection|complete
if "player_flow_chunk_idx" not in st.session_state:
    st.session_state["player_flow_chunk_idx"] = 0      # current lesson chunk index
if "player_flow_section_id" not in st.session_state:
    st.session_state["player_flow_section_id"] = None  # tracks section for flow reset
# Step 2B — per-question quiz and per-prompt reflection indices.
if "player_quiz_idx" not in st.session_state:
    st.session_state["player_quiz_idx"] = 0          # index into section_quiz_ids
if "player_quiz_q_idx" not in st.session_state:
    st.session_state["player_quiz_q_idx"] = 0        # index into quiz["questions"]
if "player_quiz_attempts" not in st.session_state:
    st.session_state["player_quiz_attempts"] = {}    # {qk: attempt_count}
if "player_quiz_correct" not in st.session_state:
    st.session_state["player_quiz_correct"] = set()  # set of correct question keys
if "player_refl_idx" not in st.session_state:
    st.session_state["player_refl_idx"] = 0          # index into section_prompt_ids

# ---------------------------------------------------------------------------
# Helper — fetch lead status
# ---------------------------------------------------------------------------
def _fetch_status(lid: str) -> dict | None:
    """Call get_lead_status and return the result, or None on error."""
    try:
        return get_lead_status(lid, db_path=DB_PATH)
    except sqlite3.OperationalError:
        st.error("Could not save progress. Check that tmp/app.db is accessible.")
    except Exception:
        logging.exception("Unexpected error fetching lead status")
        st.error("An unexpected error occurred. See console for details.")
    return None


# ---------------------------------------------------------------------------
# Sidebar — Lead ID + Sections + Progress
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("Course Player")
    raw_lead_id = st.text_input("Lead ID", placeholder="e.g. lead-123")
    lead_id = raw_lead_id.strip()

    # Reset per-session tracking whenever the lead changes.
    if lead_id != st.session_state["player_lead_id"]:
        st.session_state["player_completed"] = set()
        st.session_state["player_status"] = None
        st.session_state["player_lead_id"] = lead_id

    if not lead_id:
        st.error("Lead ID is required.")
        st.stop()

    # Load status once per session (or after a lead change).
    if st.session_state["player_status"] is None:
        st.session_state["player_status"] = _fetch_status(lead_id)

    st.subheader("Sections")
    completed: set[str] = st.session_state["player_completed"]

    active_idx: int = st.radio(
        "Select a section",
        options=range(len(SECTIONS)),
        format_func=lambda i: (
            f"\u2713 {SECTIONS[i][1]}" if SECTIONS[i][0] in completed else SECTIONS[i][1]
        ),
        label_visibility="collapsed",
    )
    active_section_id, active_title = SECTIONS[active_idx]

    # Reset tutor history when the student navigates to a new section.
    if active_section_id != st.session_state["tutor_section_id"]:
        st.session_state["tutor_messages"] = []
        st.session_state["tutor_pending"] = None
        st.session_state["tutor_section_id"] = active_section_id

    # Reset guided flow when the student navigates to a new section.
    if active_section_id != st.session_state["player_flow_section_id"]:
        st.session_state["player_flow_step"] = "welcome"
        st.session_state["player_flow_chunk_idx"] = 0
        st.session_state["player_flow_section_id"] = active_section_id
        st.session_state["player_quiz_idx"] = 0
        st.session_state["player_quiz_q_idx"] = 0
        st.session_state["player_quiz_attempts"] = {}
        st.session_state["player_quiz_correct"] = set()
        st.session_state["player_refl_idx"] = 0

    st.divider()
    st.subheader("Progress")

    status = st.session_state["player_status"]
    if status is None or not status.get("lead_exists"):
        pct = 0.0
        current = EM_DASH
        last_activity = EM_DASH
    else:
        cs = status["course_state"]
        pct = cs["completion_pct"] if cs["completion_pct"] is not None else 0.0
        current = cs["current_section"] or EM_DASH
        last_activity = cs["last_activity_at"] or EM_DASH

    st.metric("Completion", f"{pct:.2f} %")
    st.progress(pct / 100.0)
    st.write(f"**Current:** {current}")
    st.write(f"**Last activity:** {last_activity}")

# ---------------------------------------------------------------------------
# Main content area — guided flow (full width, no column wrapper)
# ---------------------------------------------------------------------------

# Flash message — stored before st.rerun() so it survives the cycle.
if st.session_state["player_flash"] is not None:
    level, msg = st.session_state["player_flash"]
    st.session_state["player_flash"] = None
    if level == "success":
        st.success(msg)
    else:
        st.error(msg)

st.title(f"{active_section_id} {EM_DASH} {active_title}")

# Load section markdown (shared across all steps).
content_path = COURSE_CONTENT_DIR / f"{active_section_id}.md"
try:
    section_markdown = content_path.read_text(encoding="utf-8")
except Exception:
    section_markdown = None

# Load cached course data (file I/O once per session).
try:
    course_map = _cached_course_map()
    quiz_library = _cached_quiz_library()
except Exception:
    logging.exception("Failed to load course map or quiz library")
    course_map = {}
    quiz_library = {}

section_data = course_map.get(active_section_id, {})
section_quiz_ids: list[str] = section_data.get("quiz_ids", [])
section_prompt_ids: list[str] = section_data.get("reflection_prompts", [])

# Chunk the lesson markdown (deterministic — no randomness).
chunks = _chunk_markdown(section_markdown) if section_markdown else ["Content unavailable."]
n_chunks = len(chunks)

# Clamp chunk_idx in case section content shrinks after a nav change.
chunk_idx = min(st.session_state["player_flow_chunk_idx"], max(0, n_chunks - 1))
step = st.session_state["player_flow_step"]


# ── Tutor expander — closure over active_title / section_markdown ─────────────
def _render_tutor_expander() -> None:
    with st.expander("Tutor"):
        st.subheader("AI Tutor")

        # Process any prompt enqueued by a quick-action button click.
        if st.session_state["tutor_pending"] is not None:
            pending = st.session_state["tutor_pending"]
            st.session_state["tutor_pending"] = None
            st.session_state["tutor_messages"].append({"role": "user", "content": pending})
            tutor_reply = generate_tutor_reply(
                section_title=active_title,
                section_markdown=section_markdown or "",
                user_message=pending,
            )
            st.session_state["tutor_messages"].append(
                {"role": "assistant", "content": tutor_reply}
            )

        # Quick-action buttons — 2 × 2 grid.
        b_left, b_right = st.columns(2)
        with b_left:
            if st.button("Summarize", use_container_width=True, key="btn_summarize"):
                st.session_state["tutor_pending"] = "Summarize this section for me."
                st.rerun()
            if st.button("Give me an example", use_container_width=True, key="btn_example"):
                st.session_state["tutor_pending"] = (
                    "Give me a concrete example of the key ideas in this section."
                )
                st.rerun()
        with b_right:
            if st.button("Explain like I'm new", use_container_width=True, key="btn_explain"):
                st.session_state["tutor_pending"] = (
                    "Explain this section like I'm completely new to the topic."
                )
                st.rerun()
            if st.button(
                "Quiz me (2 questions)", use_container_width=True, key="btn_quiz"
            ):
                st.session_state["tutor_pending"] = (
                    "Quiz me with 2 questions about this section."
                )
                st.rerun()

        st.divider()

        # Chat history — rendered top-to-bottom.
        for msg in st.session_state["tutor_messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Free-form input.
        user_input = st.chat_input("Ask about this section…")
        if user_input:
            st.session_state["tutor_messages"].append(
                {"role": "user", "content": user_input}
            )
            tutor_reply = generate_tutor_reply(
                section_title=active_title,
                section_markdown=section_markdown or "",
                user_message=user_input,
            )
            st.session_state["tutor_messages"].append(
                {"role": "assistant", "content": tutor_reply}
            )
            st.rerun()


# ── WELCOME ───────────────────────────────────────────────────────────────────
if step == "welcome":
    st.markdown(
        "Work through this section at your own pace. "
        "You'll read the lesson content one part at a time, "
        "then answer a short quiz and save a reflection before marking it done."
    )
    st.markdown("---")
    if st.button("Start Section →", type="primary"):
        st.session_state["player_flow_step"] = "lesson"
        st.session_state["player_flow_chunk_idx"] = 0
        st.rerun()

# ── LESSON ────────────────────────────────────────────────────────────────────
elif step == "lesson":
    st.caption(f"Chunk {chunk_idx + 1} of {n_chunks}")
    st.markdown(chunks[chunk_idx])
    st.divider()

    col_back, col_fwd = st.columns([1, 2])
    with col_back:
        if chunk_idx > 0:
            if st.button("← Back", use_container_width=True):
                st.session_state["player_flow_chunk_idx"] = chunk_idx - 1
                st.rerun()
    with col_fwd:
        is_last_chunk = chunk_idx >= n_chunks - 1
        if is_last_chunk:
            if section_quiz_ids:
                fwd_label = "Continue to Quiz →"
            elif section_prompt_ids:
                fwd_label = "Continue to Reflection →"
            else:
                fwd_label = "Continue to Complete →"
        else:
            fwd_label = f"Continue → (Part {chunk_idx + 2} of {n_chunks})"

        if st.button(fwd_label, type="primary", use_container_width=True):
            if is_last_chunk:
                if section_quiz_ids:
                    st.session_state["player_flow_step"] = "quiz"
                elif section_prompt_ids:
                    st.session_state["player_flow_step"] = "reflection"
                else:
                    st.session_state["player_flow_step"] = "complete"
                st.session_state["player_flow_chunk_idx"] = 0
            else:
                st.session_state["player_flow_chunk_idx"] = chunk_idx + 1
            st.rerun()

    _render_tutor_expander()

# ── QUIZ ──────────────────────────────────────────────────────────────────────
elif step == "quiz":
    if not section_quiz_ids:
        st.info("No quiz for this section.")
        if st.button(
            "Continue to Reflection →" if section_prompt_ids else "Continue to Complete →",
            type="primary",
        ):
            st.session_state["player_flow_step"] = (
                "reflection" if section_prompt_ids else "complete"
            )
            st.rerun()
    else:
        quiz_idx = st.session_state["player_quiz_idx"]

        if quiz_idx >= len(section_quiz_ids):
            # All quizzes in this section finished — show continue.
            next_label = (
                "Continue to Reflection →" if section_prompt_ids else "Continue to Complete →"
            )
            if st.button(next_label, type="primary"):
                st.session_state["player_flow_step"] = (
                    "reflection" if section_prompt_ids else "complete"
                )
                st.rerun()
        else:
            quiz_id = section_quiz_ids[quiz_idx]
            quiz = quiz_library.get(quiz_id)

            if quiz is None:
                st.warning(f"Quiz '{quiz_id}' not found in library.")
            else:
                questions = quiz.get("questions", [])

                if not questions:
                    st.info("This quiz has no questions.")
                    if st.button("Next →", key=f"skip_quiz_{quiz_id}"):
                        st.session_state["player_quiz_idx"] = quiz_idx + 1
                        st.session_state["player_quiz_q_idx"] = 0
                        st.rerun()
                else:
                    # Clamp question index (safe guard against content changes).
                    q_idx = min(
                        st.session_state["player_quiz_q_idx"], len(questions) - 1
                    )
                    q = questions[q_idx]
                    opts = q["options"]

                    # Progress caption.
                    n_quizzes = len(section_quiz_ids)
                    if n_quizzes > 1:
                        st.caption(
                            f"Quiz {quiz_idx + 1} of {n_quizzes}"
                            f" — Question {q_idx + 1} of {len(questions)}"
                        )
                    else:
                        st.caption(f"Question {q_idx + 1} of {len(questions)}")

                    if quiz.get("title"):
                        st.subheader(quiz["title"])

                    st.markdown(f"**{q['question']}**")

                    radio_key = f"qsel_{active_section_id}_{quiz_id}_{q_idx}"
                    chosen = st.radio(
                        "Choose your answer:",
                        options=list(range(len(opts))),
                        format_func=lambda j, o=opts: o[j],
                        key=radio_key,
                        label_visibility="collapsed",
                    )

                    qk = f"{active_section_id}:{quiz_id}:{q_idx}"
                    attempts = st.session_state["player_quiz_attempts"].get(qk, 0)
                    already_correct = qk in st.session_state["player_quiz_correct"]

                    if already_correct:
                        st.success("Correct!")
                    elif attempts >= 3:
                        correct_text = opts[q["correct_index"]]
                        st.info(f"Correct answer: **{correct_text}**")
                    else:
                        if st.button("Submit Answer", key=f"submit_ans_{qk}"):
                            if chosen == q["correct_index"]:
                                st.session_state["player_quiz_correct"].add(qk)
                                st.rerun()
                            else:
                                new_attempts = attempts + 1
                                st.session_state["player_quiz_attempts"][qk] = new_attempts
                                if new_attempts < 3:
                                    st.warning("Not quite — try again.")
                                else:
                                    st.rerun()  # rerun to reveal correct answer

                    # Next → shown when correct or all attempts exhausted.
                    if already_correct or attempts >= 3:
                        if st.button("Next →", type="primary", key=f"next_{qk}"):
                            if q_idx < len(questions) - 1:
                                st.session_state["player_quiz_q_idx"] = q_idx + 1
                            else:
                                # Last question in this quiz — advance to next quiz.
                                st.session_state["player_quiz_idx"] = quiz_idx + 1
                                st.session_state["player_quiz_q_idx"] = 0
                            st.rerun()

    _render_tutor_expander()

# ── REFLECTION ────────────────────────────────────────────────────────────────
elif step == "reflection":
    if not section_prompt_ids:
        st.info("No reflection prompts for this section.")
        if st.button("Continue to Complete →", type="primary"):
            st.session_state["player_flow_step"] = "complete"
            st.rerun()
    else:
        refl_idx = st.session_state["player_refl_idx"]

        if refl_idx >= len(section_prompt_ids):
            # All prompts answered — show continue.
            if st.button("Continue to Complete →", type="primary"):
                st.session_state["player_flow_step"] = "complete"
                st.rerun()
        else:
            # Clamp (safe guard against content changes).
            refl_idx = min(refl_idx, len(section_prompt_ids) - 1)
            prompt_id = section_prompt_ids[refl_idx]
            question = _PROMPT_QUESTIONS.get(
                prompt_id,
                prompt_id.replace("_", " ").capitalize(),
            )

            st.caption(f"Reflection {refl_idx + 1} of {len(section_prompt_ids)}")
            st.markdown(f"**{question}**")

            txt_key = f"reflection_txt_{active_section_id}_{refl_idx}"
            st.text_area(
                label=f"Prompt {refl_idx + 1}",
                key=txt_key,
                height=120,
                placeholder="Write your response here…",
                label_visibility="collapsed",
            )

            if st.button(
                "Save & Continue →",
                type="primary",
                key=f"refl_save_{active_section_id}_{refl_idx}",
            ):
                current_text = st.session_state.get(txt_key, "").strip()
                if current_text:
                    try:
                        save_reflection_response(
                            lead_id,
                            COURSE_ID,
                            active_section_id,
                            refl_idx,
                            current_text,
                            created_at=datetime.now(timezone.utc).isoformat(),
                            db_path=DB_PATH,
                        )
                        st.session_state["player_refl_idx"] = refl_idx + 1
                        st.rerun()
                    except Exception:
                        logging.exception("Error saving reflection response")
                        st.error("Could not save. Please try again.")
                else:
                    st.warning("Please write something before continuing.")

# ── COMPLETE ──────────────────────────────────────────────────────────────────
elif step == "complete":
    st.success(f"You've worked through all the content for **{active_title}**!")
    st.markdown(
        "Click **Mark Complete** below to record your progress, "
        "then select another section from the left panel."
    )

    if st.button("Mark Complete", type="primary"):
        occurred_at = datetime.now(timezone.utc).isoformat()
        event_id = f"{lead_id}:{active_section_id}"
        try:
            upsert_lead(lead_id, db_path=DB_PATH)
            record_progress_event(
                event_id,
                lead_id,
                active_section_id,
                occurred_at=occurred_at,
                db_path=DB_PATH,
            )
            compute_course_state(lead_id, total_sections=TOTAL_SECTIONS, db_path=DB_PATH)
            updated_status = get_lead_status(lead_id, db_path=DB_PATH)
            st.session_state["player_status"] = updated_status
            st.session_state["player_completed"].add(active_section_id)
            st.session_state["player_flash"] = (
                "success",
                f"\u2713 '{active_title}' marked complete.",
            )
            st.rerun()
        except ValueError:
            logging.exception("ValueError marking %s complete", active_section_id)
            st.error("Cannot record completion: unrecognised section.")
        except sqlite3.OperationalError:
            st.error("Could not save progress. Check that tmp/app.db is accessible.")
        except Exception:
            logging.exception("Unexpected error in Mark Complete")
            st.error("An unexpected error occurred. See console for details.")

    st.markdown("---")
    if st.button("← Restart this Section"):
        st.session_state["player_flow_step"] = "welcome"
        st.session_state["player_flow_chunk_idx"] = 0
        st.rerun()
