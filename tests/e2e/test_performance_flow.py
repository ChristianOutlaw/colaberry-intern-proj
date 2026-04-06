"""
tests/e2e/test_performance_flow.py

Wall-clock performance measurement for the Student Course Player.

Measures elapsed time from clicking "Save & Continue →" on the second
reflection prompt (early_surprise, a free-text field) until the
"Reflections saved" confirmation appears.

This is the step that triggers save_reflection_response() + st.rerun(),
and whose timing we want to baseline before investigating the complete-step
write chain (finalize_on_completion / write_ghl_contact_fields).

Pre-conditions (manual setup required before running):
  - Streamlit app must be running at http://localhost:8501
      streamlit run ui/student_portal/student_app.py
  - TEST_LEAD_ID must have a course invite row in tmp/app.db.
    Insert one via the Admin Test Mode portal or directly in SQLite:
      INSERT OR IGNORE INTO course_invites (lead_id, course_id, invited_at)
      VALUES ('pw_perf_test', 'FREE_INTRO_AI_V0', datetime('now'));
  - Section P1_S1 must NOT already be completed for this lead
    (progress is recorded on the complete step; this test stops at reflection).

Run command:
  pytest tests/e2e/test_performance_flow.py --browser chromium --headed -s

The -s flag lets the print() timing line appear in the terminal.
"""

import time

from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:8501"

# Lead ID used for this perf run.
# Must exist in course_invites — see pre-conditions above.
TEST_LEAD_ID = "pw_perf_test"


def test_reflection_save_and_continue_timing(page: Page) -> None:
    """Navigate P1_S1 lesson → quiz → reflection and time Save & Continue."""

    # ── 1. Load the student portal ────────────────────────────────────────────
    page.goto(BASE_URL)
    page.wait_for_selector("text=Welcome to", timeout=15_000)

    # ── 2. Enter the test lead ID ─────────────────────────────────────────────
    page.get_by_placeholder("Enter your access code").fill(TEST_LEAD_ID)
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Begin Course", timeout=10_000)

    # ── 3. Start the course ───────────────────────────────────────────────────
    page.get_by_role("button", name="Begin Course").click()
    page.wait_for_selector("text=Begin Section", timeout=10_000)

    # ── 4. Enter Section 1 ────────────────────────────────────────────────────
    page.get_by_role("button", name="Begin Section →").click()
    page.wait_for_selector("text=Continue", timeout=10_000)

    # ── 5. Step through 9 lesson chunks ──────────────────────────────────────
    # P1_S1.md has 9 H2 headings → 9 chunks.
    # Chunks 0–7: button label is "Continue →"
    # Chunk 8 (last): button label is "Continue to Quiz →"
    # No loop — each click is explicit. Timeout gives Streamlit time to rerun.
    page.get_by_role("button", name="Continue →").click()
    btn = page.get_by_role("button", name="Continue →")
    btn.wait_for(state="visible")
    expect(btn).to_be_enabled()
    page.get_by_role("button", name="Continue →").click()
    page.wait_for_timeout(1_500)
    page.get_by_role("button", name="Continue →").click()
    page.wait_for_timeout(1_500)
    page.get_by_role("button", name="Continue →").click()
    page.wait_for_timeout(1_500)
    page.get_by_role("button", name="Continue →").click()
    page.wait_for_timeout(1_500)
    page.get_by_role("button", name="Continue →").click()
    page.wait_for_timeout(1_500)
    page.get_by_role("button", name="Continue →").click()
    page.wait_for_timeout(1_500)
    page.get_by_role("button", name="Continue →").click()
    page.wait_for_timeout(1_500)

    page.get_by_role("button", name="Continue to Quiz →").click()
    page.wait_for_selector("text=Submit Answer", timeout=10_000)

    # ── 6. Quiz 1 of 2 — two questions ───────────────────────────────────────
    # Q1: correct answer index 0 → "Artificial Intelligence"
    page.get_by_text("Artificial Intelligence").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_timeout(1_000)

    # Q2: correct answer index 1 → "Systems that simulate human-like reasoning or learning"
    page.get_by_text("Systems that simulate human-like reasoning or learning").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_timeout(1_000)

    # ── 7. Quiz 2 of 2 — two questions ───────────────────────────────────────
    # Q1: correct answer index 2 → "A spam filter that learns from feedback"
    page.get_by_text("A spam filter that learns from feedback").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_timeout(1_000)

    # Q2: correct answer index 1 → "Computer science"
    page.get_by_text("Computer science").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_selector("text=Quiz complete", timeout=10_000)

    # ── 8. Advance to Reflection step ─────────────────────────────────────────
    page.get_by_role("button", name="Continue to Reflection →").click()
    page.wait_for_selector("text=Reflection 1 of", timeout=10_000)

    # ── 9. Reflection 1 — diagnostic: what does the page show after Save? ────
    btn = page.get_by_role("button", name="Save & Continue →")
    btn.wait_for(state="visible")
    btn.scroll_into_view_if_needed()
    btn.hover()
    btn.click(force=True)
    page.wait_for_timeout(10_000)
    _page_text = page.inner_text("body")
    _refl_snippet = "\n".join(
        line for line in _page_text.splitlines() if "reflection" in line.lower()
    )
    print(f"\n[DIAG] Page text lines containing 'reflection' after Refl-1 save:\n{_refl_snippet or '(none found)'}")
    print("\n[DIAG] Full page text (first 800 chars):")
    import sys
    sys.stdout.buffer.write((_page_text[:800] + "\n").encode("utf-8", errors="ignore"))

    # ── 10. Reflection 2 — text prompt (early_surprise): TIMED STEP ──────────
    page.get_by_role("textbox", name="Your response").fill("The section was clear and the real-world examples helped.")

    t0 = time.perf_counter()
    page.get_by_role("button", name="Save & Continue →").click()
    page.wait_for_selector("text=Reflections saved", timeout=15_000)
    elapsed = (time.perf_counter() - t0) * 1000

    print(f"\n[PERF] Save & Continue (reflection 2, early_surprise) -> "
          f"Reflections saved: {elapsed:.0f} ms")

    # ── 11. Complete step — timed transition ──────────────────────────────────
    page.get_by_role("button", name="Continue to Complete", exact=False).wait_for(state="visible", timeout=10_000)
    _t0_complete = time.perf_counter()
    page.get_by_role("button", name="Continue to Complete", exact=False).click()
    page.wait_for_selector("text=Section complete", timeout=30_000)
    _elapsed_complete = round((time.perf_counter() - _t0_complete) * 1000)
    print(f"\n[PERF] Completion transition -> Section complete: {_elapsed_complete} ms")


COMPLETION_LEAD_ID = "pw_completion_perf"


def test_section_completion_timing(page: Page) -> None:
    """Standalone benchmark for the section completion transition.

    Navigates P1_S1 through lesson → quiz → reflection (untimed setup), then
    measures: 'Continue to Complete →' click → 'Section complete' visible.

    Pre-conditions (manual setup required before running):
      - Streamlit app must be running at http://localhost:8501
          streamlit run ui/student_portal/student_app.py
      - COMPLETION_LEAD_ID must have a course invite row in tmp/app.db:
          INSERT OR IGNORE INTO course_invites (lead_id, course_id, invited_at)
          VALUES ('pw_completion_perf', 'FREE_INTRO_AI_V0', datetime('now'));
      - Section P1_S1 must NOT already be completed for this lead.

    To surface finalize_on_completion / write_ghl_contact_fields sub-timings,
    start the app with PLAYER_DEBUG=1 and run pytest with -s, then grep the
    terminal output for '"event": "timing"'.
    """

    # ── Setup: login ──────────────────────────────────────────────────────────
    page.goto(BASE_URL)
    page.wait_for_selector("text=Welcome to", timeout=15_000)
    page.get_by_placeholder("Enter your access code").fill(COMPLETION_LEAD_ID)
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Begin Course", timeout=10_000)

    # ── Setup: start course, enter section ───────────────────────────────────
    page.get_by_role("button", name="Begin Course").click()
    page.wait_for_selector("text=Begin Section", timeout=10_000)
    page.get_by_role("button", name="Begin Section →").click()
    page.wait_for_selector("text=Continue", timeout=10_000)

    # ── Setup: step through 9 lesson chunks ──────────────────────────────────
    for _ in range(8):
        page.get_by_role("button", name="Continue →").click()
        page.wait_for_timeout(1_500)
    page.get_by_role("button", name="Continue to Quiz →").click()
    page.wait_for_selector("text=Submit Answer", timeout=10_000)

    # ── Setup: Quiz 1 of 2 ───────────────────────────────────────────────────
    page.get_by_text("Artificial Intelligence").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_timeout(1_000)
    page.get_by_text("Systems that simulate human-like reasoning or learning").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_timeout(1_000)

    # ── Setup: Quiz 2 of 2 ───────────────────────────────────────────────────
    page.get_by_text("A spam filter that learns from feedback").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_timeout(1_000)
    page.get_by_text("Computer science").click()
    page.get_by_role("button", name="Submit Answer").click()
    page.wait_for_selector("text=Correct", timeout=8_000)
    page.get_by_role("button", name="Next →").click()
    page.wait_for_selector("text=Quiz complete", timeout=10_000)

    # ── Setup: Reflection 1 (no text input) ───────────────────────────────────
    page.get_by_role("button", name="Continue to Reflection →").click()
    page.wait_for_selector("text=Reflection 1 of", timeout=10_000)
    btn = page.get_by_role("button", name="Save & Continue →")
    btn.wait_for(state="visible")
    btn.click(force=True)
    page.wait_for_timeout(5_000)

    # ── Setup: Reflection 2 (free text) ──────────────────────────────────────
    page.get_by_role("textbox", name="Your response").fill(
        "Solid intro — the examples made the concepts concrete."
    )
    page.get_by_role("button", name="Save & Continue →").click()
    page.wait_for_selector("text=Reflections saved", timeout=15_000)

    # ── TIMED: completion transition ──────────────────────────────────────────
    page.get_by_role("button", name="Continue to Complete", exact=False).wait_for(
        state="visible", timeout=10_000
    )
    t0 = time.perf_counter()
    page.get_by_role("button", name="Continue to Complete", exact=False).click()
    page.wait_for_selector("text=Section complete", timeout=30_000)
    elapsed = round((time.perf_counter() - t0) * 1000)

    print(f"\n[PERF] Completion transition -> Section complete: {elapsed} ms")
    print("[PERF] Sub-timings (requires PLAYER_DEBUG=1 on the app process):")
    print("[PERF]   finalize_on_completion  -> grep terminal for '\"step\": \"finalize_on_completion\"'")
    print("[PERF]   write_ghl_contact_fields -> grep terminal for '\"step\": \"write_ghl_contact_fields\"'")


BACKNAV_LEAD_ID = "Test 2"


def test_backnav_reset_timing(page: Page) -> None:
    """Log in as Test 2 (has completed sections), navigate back to a completed
    section, confirm the reset, and measure wall-clock time to Begin Section."""

    # ── 1. Load portal ────────────────────────────────────────────────────────
    page.goto(BASE_URL)
    page.wait_for_selector("text=Welcome to", timeout=15_000)

    # ── 2. Enter lead ID ──────────────────────────────────────────────────────
    page.get_by_placeholder("Enter your access code").fill(BACKNAV_LEAD_ID)
    page.keyboard.press("Enter")

    # Test 2 has progress → welcome screen shows "Resume →"
    page.wait_for_selector("text=Resume", timeout=10_000)
    page.get_by_role("button", name="Resume", exact=False).click()

    # ── 3. Wait for sidebar section list to be ready ──────────────────────────
    # ── 4. Click the second completed section label (nth(1) skips index 0) ────
    sidebar = page.locator('[data-testid="stSidebar"]')
    completed_items = sidebar.locator("label").filter(has_text="Completed")
    completed_items.nth(1).wait_for(state="visible", timeout=15_000)
    completed_items.nth(1).click()

    # ── 5. Wait for the back-nav confirm dialog ───────────────────────────────
    page.wait_for_selector("text=Continue and reset progress", timeout=10_000)

    # ── 6. TIME: confirm click → Begin Section visible ────────────────────────
    _t0_backnav = time.perf_counter()
    page.get_by_role("button", name="Continue and reset progress").click()
    page.wait_for_selector("text=Begin Section", timeout=30_000)
    _elapsed_backnav = round((time.perf_counter() - _t0_backnav) * 1000)

    print(f"\n[PERF] Back-nav transition -> Begin Section: {_elapsed_backnav} ms")
