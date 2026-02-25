# COURSE_STRUCTURE — Free Intro to AI Class (v0)

## Course Identity

| Field       | Value |
|-------------|-------|
| `course_id` | `FREE_INTRO_AI_V0` |
| `name`      | Free Introduction to Artificial Intelligence |
| `version`   | `v0` (MVP shell — content is illustrative; full content is out of scope) |
| `purpose`   | Re-engage cold leads by offering a free, self-paced AI class. Progress through this course is the primary signal used to compute `CourseState` and `HotLeadSignal`. |

---

## Phase and Section Structure

The course contains **3 phases** and **9 sections** total (3 sections per phase).
Section IDs are stable permanent keys used in `ProgressEvent.section_id`.
They must never be renamed or reordered once data has been written.

### Phase 1 — AI Foundations

| `section_id` | Title | Description |
|--------------|-------|-------------|
| `P1_S1` | What Is AI? | Introduces artificial intelligence: what it is, where it appears in everyday life, and why it matters now. |
| `P1_S2` | How Machines Learn | Explains the core idea behind machine learning — finding patterns in data — without requiring math or code. |
| `P1_S3` | AI in the Real World | Surveys practical AI applications across industries (healthcare, retail, finance) with short real-world examples. |

### Phase 2 — Working with Data

| `section_id` | Title | Description |
|--------------|-------|-------------|
| `P2_S1` | Understanding Data | Covers what data is, common data types, and why data quality determines AI quality. |
| `P2_S2` | Exploring Data | Introduces exploratory data analysis: reading charts, spotting trends, and asking the right questions. |
| `P2_S3` | Preparing Data for AI | Explains data cleaning and feature selection at a conceptual level — the steps that happen before a model is trained. |

### Phase 3 — AI in Practice

| `section_id` | Title | Description |
|--------------|-------|-------------|
| `P3_S1` | Building Your First Model | Guides the learner through a no-code or low-code exercise that produces a simple predictive model. |
| `P3_S2` | Evaluating Results | Explains accuracy, precision, and recall in plain language; shows how to judge whether a model is trustworthy. |
| `P3_S3` | Next Steps in AI | Presents learning paths, career options, and resources for going deeper — including Colaberry's programs. |

---

## Completion Definition

```
completion_pct = (count of distinct completed section_ids for this lead) / 9 × 100
```

- Computed by `execution/progress/compute_course_state.py` from all `ProgressEvent` rows for the lead.
- Only **distinct** `section_id` values are counted; duplicate events for the same section do not increase the percentage.
- `total_sections` is **9** for `FREE_INTRO_AI_V0`. Callers must pass `total_sections=9` when this course is in use.
- Result is a `REAL` value stored in `course_state.completion_pct`.

---

## Valid Event Rule

A `ProgressEvent.section_id` must be one of the 9 canonical IDs:

```
P1_S1  P1_S2  P1_S3
P2_S1  P2_S2  P2_S3
P3_S1  P3_S2  P3_S3
```

- Any `section_id` not in this set is **invalid** and must be **rejected** by execution logic before persistence.
- Rejection means: raise a descriptive error and do not write the row. Do not silently drop or coerce.
- This validation is a future execution step; the canonical list in this directive is its single source of truth.

---

## Activity Rule

- **Activity** means a persisted `ProgressEvent` row for this lead.
- The `occurred_at` timestamp on every `ProgressEvent` must be **UTC**.
- `last_activity_at` in `course_state` is derived from the most recent `occurred_at` across all events.
- Invite delivery, link opens, and passive views do **not** count as activity (consistent with `HOT_LEAD_SIGNAL.md`).

---

## Future Hooks — Assessments (not implemented in v0)

Each phase may eventually include a short quiz or assessment. Placeholder shape:

```
phase_assessments:
  phase_id: P1 | P2 | P3
  score: float (0.0–100.0)
  passed: bool
  completed_at: datetime (UTC)
```

Assessments are **not** tracked in `ProgressEvent` in v0 and do not affect `completion_pct`.
When added, they would require a new `AssessmentResult` entity and a separate computation path.
`HotLeadSignal` v1 does not consume assessment data.

---

## Acceptance Criteria

The following must be verified by unit tests once validation logic is implemented:

| # | Scenario | Expected result |
|---|----------|-----------------|
| AC1 | `record_progress_event` called with a valid `section_id` (e.g. `P2_S2`) | Row persisted; no error |
| AC2 | `record_progress_event` called with an unknown `section_id` (e.g. `PHASE_X_S99`) | Error raised; no row written |
| AC3 | Same `section_id` recorded twice for the same lead | Second write is idempotent; `completion_pct` does not double-count |
| AC4 | All 9 unique sections recorded | `completion_pct` == `100.0` |
| AC5 | 5 of 9 unique sections recorded | `completion_pct` ≈ `55.56` |
| AC6 | `compute_course_state` called with `total_sections=9` | Result consistent with AC4 / AC5 |
