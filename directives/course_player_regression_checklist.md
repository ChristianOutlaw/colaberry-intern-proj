# Course Player Regression Checklist

## Purpose
Verify Student Course Player navigation stability after changes to session state, sidebar radio, or step routing logic.

## Preconditions
- Use a test lead ID (never a production lead)
- Start app locally: `streamlit run ui/student_portal/student_app.py`
- `PLAYER_DEBUG_FORCE` is **not** required for normal runs; enable only on failure (see below)

---

## Tests

### 1. Next advances on first click
- Complete a section (pass quiz / submit reflection as required)
- Click "Go to next section →" **once**
- **Expected:** Player advances to the next section immediately; no second click needed
- Repeat 5 times across consecutive sections

### 2. Back-nav to completed section shows confirmation
- Navigate forward past at least one section
- Click a previously completed section in the sidebar
- **Expected:** Confirmation dialog appears ("You've already completed this section…")

### 3. Continue resets progress and navigates back
- When the confirmation dialog is shown, click **Continue**
- **Expected:** Player navigates to the selected section; lesson/quiz/reflection progress for that section resets to step 0

### 4. Cancel keeps current section
- When the confirmation dialog is shown, click **Cancel**
- **Expected:** Player remains on the current section; no state change

### 5. Refresh resumes at confirmed section and correct step
- Mid-section (e.g. during quiz), refresh the browser
- **Expected:** Player resumes at the same section and the same step (lesson / quiz / reflection); no regression to section 0

---

## If any test fails

1. Restart with `PLAYER_DEBUG_FORCE=1` set in the shell environment
2. Reproduce the failure
3. Copy all `[PLAYER_DEBUG]` lines from the Streamlit terminal
4. Record the `run_id` sequence across events (`next_section_gate` → `next_section_clicked` → `next_section_click`)
5. Note which event shows the unexpected value (e.g. `clicked=false`, `_section_radio` drifted to 0)
6. File a bug with the captured log snippet and `run_id`
