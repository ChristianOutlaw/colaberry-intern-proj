# GHL_INTEGRATION.md
**Directive v1 — GoHighLevel Integration Contract**

---

## Purpose

This directive defines how our application integrates with GoHighLevel (GHL).
It is the single source of truth for integration direction, data flow, identity
matching rules, field schema, and verification criteria.

The division of responsibility is fixed:

- **Our application** is the intelligence engine. It owns lead matching, unique
  course link generation, course progress tracking, engagement scoring, lifecycle
  classification, and the determination of what action is needed next.
- **GHL** is the operational and staff-facing system. It owns contact storage,
  custom field display, campaign messaging, and staff workflow visibility.

Our application computes state. GHL displays it and acts on it.

A separate internal instructor/admin portal may exist for development, testing,
and analytics purposes, but it is **not** the primary staff-facing interface.
GHL serves that role.

---

## Core Architecture Model

The integration follows a three-phase loop:

**Phase 1 — Inbound (GHL → our app)**
GHL sends a lead payload to our application via webhook. Our application
receives it, matches or creates the lead record, and generates the unique
course access link.

**Phase 2 — Writeback (our app → GHL)**
Our application POSTs the full canonical custom field schema back to GHL.
This includes the generated `course_link`, the `invite_ready` flag, and all
other learner-state and operational fields.

**Phase 3 — Send (GHL acts)**
GHL sends the invite communication to the lead using the `course_link` field.
GHL must only send after `invite_ready = true` is confirmed on the contact.

```
GHL → webhook → our app → generates link → HTTP update → GHL → sends invite
```

No other message or link delivery flow is permitted before Phase 2 completes.

---

## Handshake Flow

This is the required sequence for every new lead entering the system from GHL.
Steps must occur in order. No step may be skipped.

### Step 1 — Lead enters GHL

A new contact is created or enters a workflow inside GHL. This is a GHL-side
event. No action is required from our application at this point.

---

### Step 2 — GHL sends lead to our application

GHL fires a webhook to our application's inbound endpoint, delivering the
contact's available identity fields. At minimum the payload includes whatever
combination of phone, email, and name GHL has on file for the contact.

Our application's endpoint must:
- Accept the inbound payload
- Respond with a 2xx status to acknowledge receipt
- Proceed to Step 3 synchronously or queue for immediate processing

---

### Step 3 — Our application matches or creates the lead and generates the link

**Step 3a — Identity resolution**

Our application attempts to match the inbound contact to an existing lead
record using the identity matching hierarchy defined below (phone first,
email second, name as weak fallback only). If no match is found, a new lead
record is created using whatever identity fields were supplied.

The internal `app_lead_id` is assigned at this point if it does not already
exist.

**Step 3b — Course link generation**

Our application generates a unique, token-secured course access link for the
matched or newly created lead. The link is stored against the lead record
internally. The `invite_generated_at` timestamp is recorded.

The `course_link` must exist and be persisted before Step 4 begins.

---

### Step 4 — Our application writes back to GHL

Our application POSTs the full canonical GHL custom field payload to the
contact in GHL. This single update must include all five field groups:

- **Identity / Linking** — including `app_lead_id`, `course_link`
- **Invite / Access** — including `invite_ready = true`, `invite_generated_at`
- **Course Progress** — with current known values; unknown fields as `null`
- **Scoring / Qualification** — with current known values; unknown fields as `null` or `false`
- **Action / Operational** — including `intended_action`, `action_status`

The full schema is always sent. Partial payloads are not permitted.

`invite_ready = true` in this payload is the signal that authorizes GHL to
proceed to Step 5.

---

### Step 5 — GHL sends the invite communication

GHL's workflow detects that `invite_ready = true` on the contact and uses the
`course_link` custom field to send the invite message (SMS, email, or other
configured channel).

**GHL must not send before `invite_ready = true` is set by our application.**
If `invite_ready` is absent or false, GHL must wait. There is no valid
shortcut around this gate.

---

## Identity Matching Rules

Our application uses a matching hierarchy when resolving an inbound GHL contact
to an existing lead record. Rules apply in the order listed. The first
successful match wins.

| Priority | Field | Notes |
|---|---|---|
| 1 | `phone` | Primary matcher. Most reliable in practice. Normalize before comparison (strip spaces, dashes, country code variation). |
| 2 | `email` | Strong fallback when phone is absent or does not match. |
| 3 | `name` | Weak fallback only. Must not be used as the sole matching criterion. Use only when phone and email are both absent and name is sufficiently specific. |

**Partial data is acceptable.** If only some identity fields are present,
the system must still proceed using the best available field. A lead missing
phone can be matched on email. A lead missing both may be matched on name
only if the name is unique in the local dataset — otherwise a new record is
created.

**GHL contact ID as a matching key** is useful when available and should be
stored when provided in the webhook payload. However, the system must not
depend on it as the sole or primary matching field. Phone/email matching must
work independently of GHL contact ID availability.

If all three identity fields are present, all three should be stored on the
lead record regardless of which one was used for matching.

---

## Canonical GHL Field Schema

All outgoing GHL contact updates must include this complete field set. Every
field in every group must be present in every update. Unknown or not-yet-
available values are sent as `null` or `false` per the field value rules below.

---

### Group A — Identity / Linking

| Field | Type | Description |
|---|---|---|
| `app_lead_id` | string | Our internal lead identifier. Set on first inbound match or create. |
| `ghl_contact_id` | string | GHL's own contact identifier. Written when available. |
| `phone` | string | Phone number as supplied by GHL or updated by our app. |
| `email` | string | Email address as supplied by GHL or updated by our app. |
| `full_name` | string | Display name for the lead. |
| `course_link` | string | The unique, token-secured URL to the lead's course access page. Null until generated. |

---

### Group B — Invite / Access

| Field | Type | Description |
|---|---|---|
| `invite_ready` | boolean | True only after our app has generated `course_link` and written it back to GHL. This is the gate for GHL to send. |
| `invite_status` | string | Current invite state. Examples: `GENERATED`, `SENT`, `OPENED`. Null until known. |
| `invite_generated_at` | timestamp | When our app first generated the unique course link. |
| `invite_sent_at` | timestamp | When the invite communication was confirmed sent. Null until sent. |
| `invite_channel` | string | Channel used for invite delivery. Examples: `SMS`, `EMAIL`. Null until sent. |

---

### Group C — Course Progress

| Field | Type | Description |
|---|---|---|
| `course_started` | boolean | True once the lead has opened their course link and recorded at least one progress event. |
| `completion_pct` | number | Percentage of course content completed. 0.0–100.0. Null until first progress event. |
| `current_section` | string | The section the lead is currently on or most recently completed. Null until progress begins. |
| `last_activity_at` | timestamp | UTC timestamp of the most recent recorded course activity. Null until first progress event. |

---

### Group D — Scoring / Qualification

| Field | Type | Description |
|---|---|---|
| `can_compute_score` | boolean | True when our app has enough data (quiz + reflection) to compute a reliable final score. False until then. |
| `final_label` | string | Computed lead quality label. Examples: `FINAL_HOT`, `FINAL_WARM`, `FINAL_COLD`. Null until score is finalized. |
| `booking_ready` | boolean | True when the lead has completed the course and the hot lead signal is active. False otherwise. |

---

### Group E — Action / Operational

| Field | Type | Description |
|---|---|---|
| `intended_action` | string | The action our system has determined is appropriate for this lead right now. Examples: `SEND_INVITE`, `NUDGE_START`, `NUDGE_PROGRESS`, `READY_FOR_BOOKING`, `FINALIZE_LEAD_SCORE`. |
| `action_status` | string | Whether the intended action has been acted on. Examples: `PENDING`, `SENT`, `FAILED`. |
| `action_completed` | boolean | True when the intended action has been successfully dispatched. False otherwise. |
| `action_completed_at` | timestamp | When the action was confirmed completed. Null until then. |
| `last_action_sent_at` | timestamp | When the most recent action was last dispatched toward GHL. Null until first dispatch. |

---

## Field Value Rules

These rules govern how values are represented in the outgoing GHL payload.
They are non-negotiable.

**1. Unknown values use `null`, not placeholder text.**

When a field does not yet have a meaningful value, send `null`. Do not send
strings like `"NONE"`, `"N/A"`, `"pending"`, or empty strings in place of
`null`. These are harder to filter and report on in GHL.

Correct: `"final_label": null`
Incorrect: `"final_label": "NONE"`

**2. Boolean fields use `false` when definitively false.**

When a boolean state is known to be false (not merely unknown), send `false`.

Correct: `"booking_ready": false`
Correct: `"invite_ready": false`

Do not send `null` for a boolean when `false` is the accurate answer.

**3. Timestamps use ISO-8601 UTC format.**

All timestamp values must be ISO-8601 strings in UTC. Null is acceptable when
the event has not yet occurred.

Correct: `"invite_generated_at": "2026-03-27T14:00:00+00:00"`
Correct: `"invite_sent_at": null`

**4. No fake or synthetic values.**

All field values must be truthful. If a value is not yet known, `null` or
`false` is the correct representation. Do not fabricate values to satisfy a
field requirement.

---

## Non-Negotiable Rules

**Always send the full schema.**
Every outgoing GHL contact update must include all five field groups in their
entirety. Partial updates — sending only the fields that changed, or only
fields relevant to the current stage — are not permitted. They create
inconsistent GHL context, harder reporting, and harder debugging.

**GHL must always have full lead context.**
Every field in the schema must be present on every GHL contact, even when
most values are `null` or `false`. A lead on Section 1 still has
`final_label = null`, `booking_ready = false`, and `can_compute_score = false`
set explicitly in GHL.

**Our application never depends on GHL for logic decisions.**
GHL is a display and messaging layer. Our application computes all state
independently using its local data store. GHL custom fields are the output
of our intelligence layer, not an input to it. No decision in our application
may be conditional on data fetched from GHL at decision time.

**`invite_ready` gates GHL send — no exceptions.**
GHL workflows must be configured to wait for `invite_ready = true` before
sending any invite communication. Our application is responsible for setting
this field only after `course_link` exists and is stored internally.

**Multiple round-trips are by design.**
The handshake requires GHL to call our app and our app to call GHL. This is
intentional and correct. It is not a problem to be eliminated. Attempts to
short-circuit the handshake by having GHL send before our app has generated
the link will result in sending a broken or absent link.

---

## Verification — Definition of Done

Integration for a given lead or change is not complete until all of the
following are true.

### Handshake correctness

| Criterion | What to verify |
|---|---|
| GHL sends lead to our app | Inbound webhook is received and acknowledged with 2xx |
| Identity matched or created | Lead record exists in our data store with `app_lead_id` assigned |
| `course_link` generated before writeback | `invite_generated_at` is set and `course_link` is non-null in our data store before the GHL update is sent |
| Full schema sent | All five field groups are present in the outgoing GHL payload; no field is omitted |
| `invite_ready = true` in writeback | The outgoing payload includes `invite_ready: true` as part of the Invite/Access group |
| GHL does not send before writeback | No invite communication is triggered by GHL before `invite_ready` is set |

### Field schema correctness

| Criterion | What to verify |
|---|---|
| Unknown fields are `null`, not placeholder strings | No `"NONE"`, `"N/A"`, or empty-string values in place of `null` |
| Definitively false booleans are `false`, not `null` | `booking_ready`, `invite_ready`, `can_compute_score`, `action_completed` use `false` when appropriate |
| All timestamps are ISO-8601 UTC | No local-timezone strings; no epoch integers |

### Determinism

| Criterion | What to verify |
|---|---|
| Same inbound payload → same internal lead state | Re-sending the same GHL webhook produces the same result (idempotency) |
| Same lead state → same outgoing GHL payload | Two calls with identical lead state produce identical field payloads |

### Negative cases

| Criterion | What to verify |
|---|---|
| Phone-only contact is matched correctly | A contact with no email is matched and processed using phone |
| Email-only contact is matched correctly | A contact with no phone is matched and processed using email |
| Partial identity does not cause crash or data loss | A contact with only name (no phone, no email) either matches or creates cleanly without error |
| GHL contact ID absent does not block flow | If `ghl_contact_id` is not in the inbound payload, matching still proceeds via phone/email |
| `course_link` is never sent before it is generated | No GHL writeback occurs with `course_link: null` and `invite_ready: true` simultaneously |

---

## Open Questions (as of v1)

The following items are acknowledged and must be resolved before implementation
begins in the affected areas. They are not blockers for the directive itself.

| # | Question | Impact |
|---|---|---|
| 1 | What exact fields does GHL include in its outbound webhook payload? | Determines the identity fields available for Step 3a matching |
| 2 | What URL, method, and authentication does GHL's contact update API require? | Required for Step 4 writeback implementation |
| 3 | How does GHL's workflow detect `invite_ready = true` and trigger the send? | Required for Step 5 GHL configuration |
| 4 | What race condition guards are needed if GHL retries the webhook? | Determines idempotency requirements for the inbound endpoint |
| 5 | Is `ghl_contact_id` present in GHL's outbound webhook payload? | Determines whether it can be stored on first inbound or requires a separate lookup |

---

## Outbound Authentication (confirmed)

The GHL contact-update API uses the following confirmed contract for Step 4
writeback calls.

**Method and endpoint:**

```
PUT https://services.leadconnectorhq.com/contacts/{contact_id}
```

`{contact_id}` is the GHL contact's own identifier (`ghl_contact_id` in our
lead record). It must be resolved before the request is made.

**Required headers:**

```
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

**Credential rule (non-negotiable):**
The API key must never be hardcoded in source files or committed to the
repository. It must be supplied at runtime via an environment variable.
Once wired, the variable name and any required config must be documented
in `/config`.

**Current state of the code:**
`execution/ghl/write_ghl_contact_fields.py` currently sends `Content-Type`
and `Content-Length` only. The `Authorization: Bearer` header has not yet
been added. The function will fail with an authentication error against a
live GHL account until this header is wired.

---

## Non-Goals (v1)

- **Building or replacing GHL's internal messaging workflows.** Our application
  delivers the link and signals readiness. GHL owns all send logic.
- **Managing GHL automation rules.** GHL workflow configuration (wait conditions,
  triggers, retry logic) is out of scope for this directive.
- **Booking and admissions workflow automation.** Downstream behavior after a
  lead becomes booking-ready is defined separately and is not part of the
  handshake flow.
- **Richer analytics reporting fields in GHL.** The schema above covers the
  operational and learner-state fields needed for the handshake and staff
  visibility. Extended reporting fields are deferred.
- **Bi-directional sync or real-time polling.** Our application does not watch
  GHL for changes. The flow is event-driven from GHL's webhook only.
