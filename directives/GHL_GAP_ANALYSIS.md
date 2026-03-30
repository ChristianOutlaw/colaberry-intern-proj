# GHL_GAP_ANALYSIS.md
**Directive — GHL Integration Gap Analysis**

---

## Purpose

This document compares the current state of the repository against the
integration decisions documented in the Kes GHL meeting. It identifies
what is already built and aligned, what is partially done, what is missing,
and where drift risks exist.

It is not a planning document or a task list. It is a snapshot of the
gap between the meeting decisions and the repo as of the time of writing.

---

## 1. Confirmed Target Architecture

From the Kes meeting, the agreed architecture is:

**Our app** owns:
- Lead matching and upsert
- Unique course link generation
- Course progress tracking
- Engagement intelligence and scoring
- Determining the intended next action

**GHL** owns:
- Contact storage and staff visibility
- Custom field display
- Campaign sends and messaging
- Staff workflows and operational reporting

**Integration handshake (critical path):**
```
GHL → webhook → our app (Steps 3a + 3b: match/create lead + generate link)
our app → HTTP update → GHL (Step 4: write full canonical field schema)
GHL sends invite using course_link (Step 5: only after invite_ready = true)
```

Additional rules confirmed in the meeting:
- Always send the full canonical field schema — never partial payloads
- Use null/false for unknown or not-yet-true values, not placeholder strings
- Identity matching hierarchy: phone first, email second, name as weak fallback
- GHL must never send before `invite_ready = true` is set by our app
- Multiple round-trips between GHL and our app are by design — not a problem
- The instructor/admin portal is not the long-term primary staff interface

---

## 2. Already Aligned in Current Repo / Docs

The following items from the meeting are fully documented in directives and
have corresponding execution scripts or tests.

### Architecture split (our app = intelligence, GHL = ops layer)
**Directive:** `directives/GHL_INTEGRATION.md` — Core Architecture Model section.
**Code:** This split is reflected across all execution scripts. No business
logic exists inside `services/webhook/ghl_lead_intake_endpoint.py` — it is
pure HTTP routing.

### Five-step handshake flow
**Directive:** `directives/GHL_INTEGRATION.md` — Handshake Flow section,
Steps 1–5 fully described.
**Code:**
- Step 2 inbound: `services/webhook/ghl_lead_intake_endpoint.py` — POST `/ghl-lead`
- Steps 3+4: `execution/leads/process_ghl_lead_intake.py`
- Step 3a matching: `execution/leads/match_or_create_lead_from_ghl_payload.py`
- Step 3b link gen: `execution/leads/create_student_invite_from_payload.py`
- Step 4 writeback: `execution/ghl/write_ghl_contact_fields.py`
**Tests:** `test_process_ghl_lead_intake.py`, `test_ghl_lead_intake_endpoint.py`,
`test_write_ghl_contact_fields.py`

### Identity matching hierarchy (phone → email → name)
**Directive:** `directives/GHL_INTEGRATION.md` — Identity Matching Rules table.
**Code:** `execution/leads/match_or_create_lead_from_ghl_payload.py` —
implements all three tiers with explicit fallback logic and idempotency note.
GHL contact ID is stored when present but not used as the primary matcher.
**Tests:** `test_match_or_create_lead_from_ghl_payload.py`

### Full canonical field schema always sent (five field groups)
**Directive:** `directives/GHL_INTEGRATION.md` — Canonical GHL Field Schema,
Non-Negotiable Rules.
**Code:** `execution/ghl/build_ghl_full_field_payload.py` — assembles all
five groups (A–E) in every call. No partial payload path exists.
**Tests:** `test_build_ghl_full_field_payload.py`

### Null/false value rules
**Directive:** `directives/GHL_INTEGRATION.md` — Field Value Rules section.
**Code:** `build_ghl_full_field_payload.py` — booleans default to `False`,
unknown scalars default to `None`. No placeholder strings are used.

### `invite_ready` gate before GHL send
**Directive:** `directives/GHL_INTEGRATION.md` — Step 5 and Non-Negotiable Rules.
**Code:** `build_ghl_full_field_payload.py` line ~322 —
`"invite_ready": course_link is not None` — the gate is enforced by tying
the boolean directly to whether the course link has been generated.

### Instructor portal de-prioritized
**Directive:** `directives/GHL_INTEGRATION.md` — first section, explicitly
states the portal is for dev/admin/testing only, not the primary staff interface.

### Deterministic execution (no datetime.now() inside execution layer)
**Directive:** `CLAUDE.md` — Core Principle.
**Code:** `process_ghl_lead_intake.py` and `build_ghl_full_field_payload.py`
both raise `ValueError` when `now` is not injected by the caller.

---

## 3. Partially Aligned / Needs Clarification

### `invite_generated_at` is always null in the outbound payload

**What the meeting said:** Group B (Invite / Access) should include
`invite_generated_at` — when the unique link was first generated (Very High
priority, §9.2).

**What the directive says:** `directives/GHL_INTEGRATION.md` — Group B table
lists `invite_generated_at` as a required field.

**What the code does:** `build_ghl_full_field_payload.py` line ~324 sets
`"invite_generated_at": None` with the comment "not persisted in this schema
version". `process_ghl_lead_intake.py` captures the `invite_generated_at`
timestamp in the return dict but does not write it to the database.

**Gap:** The DB table `course_invites` has no `generated_at` column — only
`sent_at`. Until a column is added and the intake path writes to it, GHL will
always receive `invite_generated_at = null` even when a link exists.

**What a junior developer needs to do:** Add a `generated_at` column to
`course_invites`, write to it in `create_student_invite_from_payload.py`,
and read it back in `build_ghl_full_field_payload.py`.

---

### `action_status` and `action_completed` do not reflect actual dispatch state

**What the meeting said:** GHL must know both what action is needed AND
whether that action has been completed (§5.2, Very High priority).

**What the directive says:** `directives/GHL_INTEGRATION.md` — Group E
defines `action_status` (PENDING / SENT / FAILED), `action_completed` (bool),
and `action_completed_at` (timestamp).

**What the code does:** `build_ghl_full_field_payload.py` line ~341 hardcodes
`action_status = "PENDING"` and `action_completed = False` unconditionally,
even for leads where `sync_records` shows a SENT status.
`last_action_sent_at` is correctly derived from `sync_records`, but the two
completion fields do not use that data.

**Gap:** A lead that has already had an action dispatched and confirmed
still shows `action_status = "PENDING"` and `action_completed = False` in
GHL. This defeats the operational purpose of these fields.

**What a junior developer needs to do:** `build_ghl_full_field_payload.py`
should derive `action_status` and `action_completed` from `sync_records`
(the same row used for `last_action_sent_at`), not hardcode them.

---

### Phone normalization is intentionally incomplete

**What the meeting said:** Phone is the strongest identity matcher (§3.2).

**What the directive says:** `directives/GHL_INTEGRATION.md` — Identity
Matching Rules table: "Normalize before comparison (strip spaces, dashes,
country code variation)."

**What the code does:** `match_or_create_lead_from_ghl_payload.py` — the
`_norm_phone` function only strips surrounding whitespace. An inline comment
says E.164 normalization is deferred until a canonical format is confirmed.

**Gap:** Two GHL payloads representing the same phone number in different
formats (e.g., `"+1 555-123-4567"` vs `"5551234567"`) will not match and
will create two separate lead records.

**What a junior developer needs to do:** Confirm the canonical phone format
used by the GHL deployment, then upgrade `_norm_phone` to normalize to that
format before comparison.

---

### GHL contact ID resolution fails silently when neither stored nor looked up

**What the meeting said:** Do not depend on GHL contact ID as the sole matching
key (§3.1), but store it when available.

**What the code does:** `write_ghl_contact_fields.py` — if `ghl_contact_id`
is not stored on the lead AND no `ghl_lookup_url` is provided, the function
returns `ok=False` and no writeback is sent. This is a safe no-op by design.

**Gap:** In production, if the GHL inbound webhook does not include a
`ghl_contact_id` and no lookup URL is configured, the handshake stalls at
Step 4 and GHL never receives the field update or the course link. There is
currently no documented fallback or retry path for this scenario.

---

## 4. Missing / Not Yet Implemented

### GHL inbound webhook payload contract is not documented

**What the meeting said:** Open Question 13.2 — exact fields in GHL's outbound
webhook payload still need confirmation.

**What exists:** `directives/GHL_INTEGRATION.md` Open Questions table (item 1)
acknowledges this but leaves it unresolved.

**What is missing:** A concrete example of the GHL webhook body that the
`/ghl-lead` endpoint should expect. Without this, the endpoint's behavior on
edge cases (extra fields, missing fields, different key names) is untested
against the real GHL format.

---

### GHL API authentication is implemented but the credential format is not confirmed with GHL

**What the meeting said:** §17 checkpoint 4 — define outgoing GHL field
update payload contract.

**What the code does:** `write_ghl_contact_fields.py` — the outbound HTTP
request sends an `Authorization: Bearer {GHL_API_KEY}` header. The key is
read from the `GHL_API_KEY` environment variable at runtime. If the variable
is absent, the function returns `ok=False` with a FAILED sync_records row
and no HTTP request is made. This is documented in `directives/GHL_INTEGRATION.md`.

**What is still missing:** The Bearer token format assumed here (a single
static API key) has not been validated against the actual GHL account that
will be used in production. GHL uses location-specific API keys scoped by
account. The correct key and its expected header format must be confirmed
with the GHL account owner before the first live writeback can succeed.

---

### GHL workflow configuration rules (wait conditions, triggers) are not defined

**What the meeting said:** Priority 5 — define GHL wait/send rules: GHL must
not send before `course_link` exists and `invite_ready = true` (§14, §11.5).

**What exists:** `directives/GHL_INTEGRATION.md` — Step 5 documents the rule
from our app's perspective. Non-Goals section explicitly excludes GHL workflow
configuration.

**What is missing:** No document describes what the GHL-side workflow should
look like — what trigger condition to use, how to check `invite_ready`, what
delay logic to configure, or how to guard against GHL retrying the webhook
before our app has finished writing back.

---

### Downstream booking and review workflow is not defined

**What the meeting said:** Deferred until handshake is stable (§12.3, §15).

**What exists:** `execution/orchestration/run_booking_ready_scan.py` and
`execution/scans/find_ready_for_booking_leads.py` exist and are tested, so
the signal is computed. The `booking_ready` field is sent to GHL correctly.

**What is missing:** No directive defines what happens in GHL when
`booking_ready = true` — what workflow fires, what staff see, what the
admissions handoff looks like.

---

## 5. Architectural Contradictions or Drift Risks

### `action_status` hardcoded to PENDING undermines the operational purpose of Group E

The meeting explicitly required GHL to know whether an action has been
completed (§5.2, Very High priority). The directive defines `action_status`,
`action_completed`, and `action_completed_at` for exactly this purpose. But
the current implementation always sends `PENDING / False / null` for these
fields regardless of actual dispatch history. If no one catches this, GHL
will never show a completed action state even after years of real usage.

### `invite_generated_at` is a required directive field but is always null

The directive lists `invite_generated_at` in Group B as a named field that
must be present. The payload builder sends it as `null` because the DB has
no column for it. This means the directive and the code are out of sync. If
the directive is authoritative, the code needs to catch up. If this field is
genuinely deferred, the directive should say so.

### GHL API key format is assumed but not confirmed with the real GHL account

`write_ghl_contact_fields.py` sends `Authorization: Bearer {GHL_API_KEY}`
where `GHL_API_KEY` is read from the environment at runtime. Authentication
is implemented and tests mock it with `@patch.dict(os.environ, {"GHL_API_KEY": "..."})`.
The remaining risk is that the Bearer token format, key scope, or key name
expected by the specific GHL account has not been validated in a live test.
This will surface only when the integration is wired to a real GHL account.

---

## 6. Immediate Priorities (Top 5)

These are listed in order of blocking impact. Each one either blocks the
handshake from completing end-to-end or causes misleading data in GHL.

**Priority 1 — Confirm the GHL API key format with the real GHL account**
`write_ghl_contact_fields.py` sends `Authorization: Bearer {GHL_API_KEY}`.
The header is implemented and tested. Before the first live writeback, confirm
the correct key value, its scope (location vs agency), and that the target
GHL endpoint accepts Bearer tokens in this format.

**Priority 2 — Fix `action_status` and `action_completed` to reflect actual dispatch state**
In `build_ghl_full_field_payload.py`, derive these from `sync_records` instead
of hardcoding them. This directly fixes the meeting's §5.2 requirement.

**Priority 3 — Persist `invite_generated_at` and include it in the outbound payload**
Add a `generated_at` column to `course_invites`, write it in the intake path,
and read it back in the payload builder. Aligns the directive with the code.

**Priority 4 — Confirm the GHL inbound webhook payload format and add tests for it**
Get an example of the actual GHL webhook body. Add test cases in
`test_ghl_lead_intake_endpoint.py` that use the real field names GHL sends.
This closes Open Question 1 in the directive.

**Priority 5 — Decide and document the phone normalization standard**
Pick a canonical format (E.164 is recommended), upgrade `_norm_phone` in
`match_or_create_lead_from_ghl_payload.py`, and update the directive's
Identity Matching Rules section to specify the format explicitly.

---

*End of gap analysis.*
