# UI Student Portal UX Upgrades (Student-Facing)

## Scope
Student Course Player UI only.
Exclude admin/analytics features for now.

## Goals
1) Make progress and navigation feel like a professional LMS.
2) Make the AI Tutor feel like a GPT chat: user can ask anything, gets responses, and can also use quick actions.

---

## A) Welcome / Entry UX

### Resume Banner (if progress exists)
If a lead has saved progress, the Welcome screen must show:
- Primary CTA: "Resume" (goes to confirmed section + correct step)
- Secondary action: "Restart course" (clears progress, returns to Section 1 Lesson)

If no progress exists:
- Show "Begin Course" (existing behavior)

### Course Outline Preview
Welcome screen should show a compact outline card:
- Total sections count
- Short description of flow: Lesson → Quiz → Reflection
- Optional: estimated time can be omitted for now (nice-to-have)

---

## B) Unified Progress Display

### Canonical progress model
Use one consistent model:
- Section index / total sections (e.g., "Section 3 of 9")
- Within-section part/chunk progress (e.g., "Part 2 of 9")
Avoid multiple competing labels that say the same thing.

### Continue UX
- Continue button should clearly indicate what comes next when possible:
  e.g., "Continue → (Part 2 of 9)" or "Continue → Quiz"
- Prefer a consistent placement (end of content) and optionally a sticky footer later.

---

## C) Sidebar Navigation UX

### Visual clarity
- Current section must be visually distinct (not only the radio dot)
- Completed sections should remain clearly marked
- Locked sections should show a lock icon and (optional) tooltip messaging

### Micro-status
Each section should have a status state shown in the sidebar:
- Not started
- In progress
- Completed

---

## D) AI Tutor (GPT-like)

### GPT-like chat behavior
Tutor must behave like a chat:
- Student types a question, presses send
- Tutor replies below, conversation persists during the session
- Conversation is per-lead (switching lead resets or loads that lead's tutor history)
- Chat should be contextual to the current section by default

### Context rules
Each tutor response must have access to:
- current section_id and title
- current chunk content (or the section content currently displayed)
- student progress state (section index, part index, step)

### Quick actions (keep these)
Keep buttons for:
- Summarize
- Explain simply
- Give an example
- Quiz me (2 questions)

Behavior:
- Clicking a quick action should inject a user message into the chat (e.g., "Summarize this section")
- Then the tutor responds like normal chat

### UX integration
Tutor panel should feel integrated:
- Collapsible container (collapsed by default is acceptable)
- Quick actions should be compact (row or segmented)
- Responses should render inline in the chat stream

### Persistence (phase 1)
- Store tutor chat history in Streamlit session_state.
- SQLite persistence is optional later (not required in this phase).

---

## Definition of Done
- Welcome page shows Resume vs Begin behavior correctly.
- Progress display is simplified and consistent.
- Sidebar is clearer and shows micro-status.
- Tutor is GPT-like chat + quick actions and uses current section context.
