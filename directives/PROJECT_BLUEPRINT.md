# PROJECT_BLUEPRINT — Cold Lead "Free Intro to AI Class" Conversion System

## 1) Problem Statement
- Colaberry's AI agent (Cora) runs 3 campaigns: New Leads (fast callback), Inbound Calls, and Cold Leads.
- Cold Leads are high-volume but underperform:
  - Pickup rate ~3%
  - Booked appointment rate <1% (effectively ~0%)
- Current cold messaging (AI/analytics pitch + testimonials/open house links) looks impressive but is not converting.

## 2) Target Users
- Primary: Cold leads who previously showed interest but went inactive (e.g., 30+ days).
- Secondary: Sales/admissions team who wants more booked appointments and "hot lead" signals.
- Operators: Marketing/ops team monitoring pickup/book rates and campaign performance.

## 3) MVP Outcomes (measurable)
- O1: Cold-lead campaign can send a "Free Intro to AI Class" invitation and record that it was sent.
- O2: The system can track a lead's course progress at "phase/section level" and persist updates reliably.
- O3: Cora (or the workflow) can query a lead's "course state" to personalize follow-up messaging (e.g., "I see you're on section 3…").
- O4: The system can identify and surface "hot leads" based on engagement signals (progress activity), preparing them to be pushed to GHL.
- O5: Deterministic local runs exist for core operations (store progress, fetch status) with passing unit tests.

## 4) Non-Goals
- NG1: Rebuilding the full Cora calling system, ad platform, or telephony provider logic.
- NG2: Full marketing content generation engine (beyond minimal placeholders needed for workflow).
- NG3: Production-grade authentication/roles (unless required for access control).
- NG4: Fully polished analytics dashboard (basic UI can come after data is correct).
- NG5: Any reliance on Google Sheets or Zapier as the primary data store (explicitly not the target).

## 5) System Boundaries by Layer (strict)
### Layer 1 — Directives (/directives)
- Defines: business intent, workflow rules, acceptance criteria, and test expectations.
- No business logic; no pseudo-code for execution.

### Layer 2 — Orchestration (Claude / human planning)
- Chooses next step, designs tests, validates evidence, enforces repo rules and approval gates.

### Layer 3 — Execution (/execution, /services/worker)
- Deterministic scripts/services to:
  - Create/update lead tracking records
  - Record course invite sent
  - Record progress events and compute current "course state"
  - Retrieve a lead's status for personalization and hot-lead flagging
- One script = one responsibility. No prompts.

### Layer 4 — Verification (/tests)
- Unit tests for all non-trivial execution logic (status computation, persistence behavior).
- Integration tests are opt-in and must never touch production systems.
- E2E tests only if/when a UI flow exists (Playwright preferred).

## 6) Primary Data Entities (names + 1 line)
- Lead: A person record (name/phone/email + external IDs like GHL contact ID if available).
- CourseInvite: A record that the "free intro to AI class" invite was sent (timestamp + channel metadata).
- ProgressEvent: A single progress update (lead ID, phase/section, timestamp, optional metadata).
- CourseState: Computed "where they are now" (current phase/section, completion %, last activity time).
- HotLeadSignal: A derived indicator that a lead is likely ready for booking/handoff (rule-based for MVP).
- SyncRecord: Tracks whether/when a lead was pushed to GHL (status, timestamp, response metadata).

## 7) First Vertical Slice (end-to-end)
### Trigger
- A single deterministic local run (CLI/script) that simulates the "cold lead follow-up decision" path.

### Inputs
- Lead identifier + contact fields (minimal).
- "Progress update" payload OR "invite decision check" request.

### Execution scripts (MVP)
- Script A: Upsert Lead + ensure persistence works even with missing optional fields.
- Script B: Mark "Free Intro to AI Class" invite sent (idempotent behavior preferred).
- Script C: Record ProgressEvent (phase/section-level) and update/derive CourseState.
- Script D: Get LeadStatus summary (invite-sent?, course state, hot-lead signal).

### Outputs
- Stored records (local DB or deterministic storage layer per repo standards).
- A printed/returned LeadStatus summary object for downstream usage (Cora personalization / future GHL push).

### Verification
- Unit tests:
  - Progress persistence (full-row save behavior; no silent drop when fields are empty)
  - CourseState derivation correctness
  - Invite idempotency and "sent/not sent" decision
  - HotLeadSignal rules (minimal v1)
- Integration tests (opt-in):
  - Only when introducing GHL push; must run against a sandbox/non-prod target.

## 8) Definition of Done
- Unit tests exist for all MVP execution modules and pass.
- Directives updated for any behavior-changing logic.
- No secrets committed; config through /config; explicit environment checks for any external calls.
- If a UI is added, basic E2E tests exist (Playwright) for the core flow (view lead status / progress).
- The system produces repeatable results given the same inputs.

## 9) Open Questions
- What is the canonical "course structure" (phases/sections) and how is completion determined?
- What defines "hot lead" for MVP (e.g., completion threshold, recent activity window)?
- What is the system of record for leads today (GHL?) and what identifiers are guaranteed?
- Where will the free class be hosted and how do we receive progress signals (webhook, event log, polling)?
- Is there a required dashboard view in MVP, or is storage + API sufficient first?
