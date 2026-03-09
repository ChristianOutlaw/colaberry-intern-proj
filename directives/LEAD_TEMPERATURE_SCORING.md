# LeadTemperatureScore — Multi-Signal Weighted Scoring Spec (v1)

## Context

This directive specifies the **LeadTemperatureScore** engine, a deterministic
weighted scoring model that extends the binary `HotLeadSignal` (v1) with a
three-tier classification (HOT / WARM / COLD) and a numeric score 0–100.

It is designed to answer the question: _"How engaged is this lead right now,
and why?"_ using all available engagement signals, not just the three binary
gates used by `HotLeadSignal`.

Both engines coexist. `HotLeadSignal` (v1) remains the canonical gate for
GHL push decisions. `LeadTemperatureScore` is the richer diagnostic surface
for instructor-facing views, export, and future automation.

Reference: `PROJECT_BLUEPRINT.md §3 O4`, `directives/HOT_LEAD_SIGNAL.md`.

---

## Inputs

| Field                  | Type               | Description |
|------------------------|--------------------|-------------|
| `now`                  | `datetime`         | Current UTC time, **injected by the caller**. Never called internally. |
| `invited_sent`         | `bool`             | True if a CourseInvite record exists for this lead. |
| `completion_percent`   | `float \| None`    | Course completion 0.0–100.0. `None` when no ProgressEvent rows exist. |
| `last_activity_at`     | `str \| None`      | ISO-8601 UTC string of the most recent ProgressEvent. `None` if no events. |
| `started_at`           | `str \| None`      | ISO-8601 UTC string of the first ProgressEvent (from `course_state.started_at`). `None` if no events. |
| `avg_quiz_score`       | `float \| None`    | Mean quiz score across all completed quizzes (0–100). `None` if no quizzes. |
| `avg_quiz_attempts`    | `float \| None`    | Mean number of attempts per quiz question. `None` if no quiz data. |
| `reflection_confidence`| `str \| None`      | Qualitative engagement depth: `"HIGH"`, `"MEDIUM"`, `"LOW"`, or `None`. |
| `current_section`      | `str \| None`      | Lead's current course section. Accepted as input; not scored in v1. |

### Missing-data policy

The engine must **never raise** on missing inputs. Each component applies a
conservative neutral value when data is absent:

| Component | Absent value | Rationale |
|-----------|-------------|-----------|
| `completion_percent` | 0 pts | No evidence of progress |
| `last_activity_at` | 0 pts, code `NO_ACTIVITY` | No evidence of engagement |
| `started_at` | 5 pts (half of W_VELOCITY=10), code `VELOCITY_UNKNOWN` | Absence ≠ slow learner |
| `avg_quiz_score` | 10 pts (half of W_QUIZ=20) | Absence ≠ failure |
| `avg_quiz_attempts` | 0 penalty | Absence ≠ friction |
| `reflection_confidence` | 7 pts (near half of W_REFLECTION=15) | Absence ≠ low confidence |

---

## Scoring Model

The score is the sum of five component scores minus an optional retry penalty,
clamped to [0, 100]. If no invite has been sent, the final score is further
capped at `INVITE_CAP = 15`.

### Component weights

| Component | Max Points | Weight constant |
|-----------|-----------|----------------|
| Course completion | 40 | `W_COMPLETION = 40` |
| Recency (days inactive) | 25 | `W_RECENCY = 25` |
| Quiz performance | 20 | `W_QUIZ = 20` |
| Reflection confidence | 15 | `W_REFLECTION = 15` |
| Learning velocity | 10 | `W_VELOCITY = 10` |
| **Maximum positive total** | **110** (clamped to 100) | |
| Retry friction penalty | up to −15 | `MAX_RETRY_PENALTY = 15` |

### Completion component (0–40 pts)

```
pts  = min(40, int(completion_percent × 40 / 100))
code = COMPLETION_STRONG   if completion_percent >= 75
     | COMPLETION_MODERATE if completion_percent >= 25
     | COMPLETION_LOW      if completion_percent > 0
     | COMPLETION_NONE     if completion_percent is None or == 0
```

### Recency component (0–25 pts)

Days inactive = `(now − last_activity_at).days` (integer floor).

| Days inactive | Points | Reason code |
|--------------|--------|-------------|
| `None` (no activity) | 0 | `NO_ACTIVITY` |
| ≤ 7 | 25 | `RECENTLY_ACTIVE` |
| ≤ 14 | 18 | `ACTIVITY_MODERATE` |
| ≤ 21 | 10 | `ACTIVITY_STALE` |
| ≤ 30 | 4 | `ACTIVITY_VERY_STALE` |
| > 30 | 0 | `ACTIVITY_DORMANT` |

### Quiz component (0–20 pts)

```
pts  = min(20, int(avg_quiz_score × 20 / 100))   if not None
     | 10 (neutral half-credit)                   if None
code = QUIZ_STRONG   if avg_quiz_score >= 80
     | QUIZ_MODERATE if avg_quiz_score >= 50
     | QUIZ_WEAK     if avg_quiz_score < 50
     | QUIZ_UNKNOWN  if None
```

### Reflection component (0–15 pts)

| Value | Points | Reason code |
|-------|--------|-------------|
| `"HIGH"` | 15 | `REFLECTION_HIGH` |
| `"MEDIUM"` | 9 | `REFLECTION_MEDIUM` |
| `"LOW"` | 3 | `REFLECTION_LOW` |
| `None` / unknown | 7 | `REFLECTION_UNKNOWN` |

### Velocity component (0–10 pts)

Measures how quickly the lead is progressing through the course relative to
how long they have been enrolled.

```
elapsed_days     = max(1, days_since(started_at, now))   # floor prevents division by zero
velocity         = completion_percent / elapsed_days      # pct-points per day

pts  = 10  if velocity > 5.0   →  VELOCITY_FAST
     |  6  if velocity > 1.5   →  VELOCITY_MODERATE
     |  3  if velocity > 0.0   →  VELOCITY_SLOW
     |  0  if velocity == 0.0  →  VELOCITY_NONE        (enrolled but no progress)
     |  5  if started_at None or completion_percent None  →  VELOCITY_UNKNOWN
```

Neutral half-credit (5 pts) is awarded when `started_at` or `completion_percent`
is absent so leads who have not yet been given access are not penalised.

### Retry friction penalty (0 to −15 pts)

| Avg attempts | Penalty | Reason code |
|-------------|---------|-------------|
| `None` or ≤ 1.5 | 0 | _(no code emitted)_ |
| ≤ 2.5 | −5 | `RETRY_MILD` |
| ≤ 3.5 | −10 | `RETRY_MODERATE` |
| > 3.5 | −15 | `RETRY_HIGH` |

### Invite gate

If `invited_sent is False`:
```
final_score = min(raw_score, INVITE_CAP)   # INVITE_CAP = 15
reason_codes += ["NOT_INVITED"]
```

Rationale: leads who have not received an invite cannot be meaningfully scored
on course engagement. The cap keeps them COLD without zeroing out any partial
signals that may exist.

---

## Score → Signal Mapping

```
score >= SCORE_HOT  (70)  →  "HOT"
score >= SCORE_WARM (35)  →  "WARM"
score <  SCORE_WARM (35)  →  "COLD"
```

### Locked thresholds (v1)

| Constant | Value |
|---------|-------|
| `SCORE_HOT` | `70` |
| `SCORE_WARM` | `35` |
| `INVITE_CAP` | `15` |
| `W_COMPLETION` | `40` |
| `W_RECENCY` | `25` |
| `W_QUIZ` | `20` |
| `W_REFLECTION` | `15` |
| `W_VELOCITY` | `10` |
| `MAX_RETRY_PENALTY` | `15` |

---

## Output Shape

```python
{
    "signal":         str,        # "HOT" | "WARM" | "COLD"
    "score":          int,        # 0–100 (clamped)
    "reason_codes":   list[str],  # 4–6 codes; one per component + optional gate/penalty codes
    "reason_summary": str,        # Human-readable one-line explanation
    "evaluated_at":   str,        # ISO-8601 UTC with trailing "Z", derived from injected now
}
```

`reason_codes` always contains exactly one code for each scored component
(completion, recency, quiz, reflection) plus any applicable penalty or gate
codes. Order: [completion, recency, quiz, reflection, (retry?), (NOT_INVITED?)].

---

## Reason Codes (exhaustive for v1)

| Code | Meaning |
|------|---------|
| `COMPLETION_STRONG` | completion_percent ≥ 75 |
| `COMPLETION_MODERATE` | 25 ≤ completion_percent < 75 |
| `COMPLETION_LOW` | 0 < completion_percent < 25 |
| `COMPLETION_NONE` | completion_percent is None or 0 |
| `RECENTLY_ACTIVE` | last activity ≤ 7 days ago |
| `ACTIVITY_MODERATE` | last activity 8–14 days ago |
| `ACTIVITY_STALE` | last activity 15–21 days ago |
| `ACTIVITY_VERY_STALE` | last activity 22–30 days ago |
| `ACTIVITY_DORMANT` | last activity > 30 days ago |
| `NO_ACTIVITY` | last_activity_at is None |
| `QUIZ_STRONG` | avg_quiz_score ≥ 80 |
| `QUIZ_MODERATE` | 50 ≤ avg_quiz_score < 80 |
| `QUIZ_WEAK` | avg_quiz_score < 50 |
| `QUIZ_UNKNOWN` | avg_quiz_score is None |
| `REFLECTION_HIGH` | reflection_confidence = "HIGH" |
| `REFLECTION_MEDIUM` | reflection_confidence = "MEDIUM" |
| `REFLECTION_LOW` | reflection_confidence = "LOW" |
| `REFLECTION_UNKNOWN` | reflection_confidence is None or unrecognised |
| `RETRY_MILD` | 1.5 < avg_quiz_attempts ≤ 2.5 |
| `RETRY_MODERATE` | 2.5 < avg_quiz_attempts ≤ 3.5 |
| `RETRY_HIGH` | avg_quiz_attempts > 3.5 |
| `VELOCITY_FAST` | completion_percent / elapsed_days > 5.0 |
| `VELOCITY_MODERATE` | completion_percent / elapsed_days > 1.5 |
| `VELOCITY_SLOW` | completion_percent / elapsed_days > 0.0 |
| `VELOCITY_NONE` | completion_percent / elapsed_days == 0.0 (enrolled, no progress) |
| `VELOCITY_UNKNOWN` | started_at or completion_percent is None |
| `NOT_INVITED` | invited_sent is False — score capped at 15 |

---

## Examples

All examples use `now = 2026-02-25T12:00:00Z`.

### Example 1 — HOT: all signals strong

| Field | Value |
|-------|-------|
| `invited_sent` | `True` |
| `completion_percent` | `90.0` |
| `last_activity_at` | `"2026-02-23T12:00:00+00:00"` (2 days ago) |
| `avg_quiz_score` | `88.0` |
| `avg_quiz_attempts` | `1.0` |
| `reflection_confidence` | `"HIGH"` |

Computation: 36 (completion) + 25 (recency) + 17 (quiz) + 15 (reflection) + 0 (penalty) = **93**
→ `signal = "HOT"`, `reason_codes = ["COMPLETION_STRONG", "RECENTLY_ACTIVE", "QUIZ_STRONG", "REFLECTION_HIGH"]`

---

### Example 2 — WARM: mid-progress, mixed performance

| Field | Value |
|-------|-------|
| `invited_sent` | `True` |
| `completion_percent` | `40.0` |
| `last_activity_at` | `"2026-02-13T12:00:00+00:00"` (12 days ago) |
| `avg_quiz_score` | `62.0` |
| `avg_quiz_attempts` | `2.0` |
| `reflection_confidence` | `"MEDIUM"` |

Computation: 16 + 18 + 12 + 9 − 5 (RETRY_MILD) = **50**
→ `signal = "WARM"`, includes `RETRY_MILD`

---

### Example 3 — COLD: inactive, low progress

| Field | Value |
|-------|-------|
| `invited_sent` | `True` |
| `completion_percent` | `8.0` |
| `last_activity_at` | `"2026-01-11T12:00:00+00:00"` (45 days ago) |
| `avg_quiz_score` | `None` |
| `reflection_confidence` | `"LOW"` |

Computation: 3 + 0 (ACTIVITY_DORMANT) + 10 (QUIZ_UNKNOWN) + 3 (REFLECTION_LOW) = **16**
→ `signal = "COLD"`

---

### Example 4 — COLD: not invited despite all other strong signals

| Field | Value |
|-------|-------|
| `invited_sent` | `False` |
| `completion_percent` | `60.0` |
| `last_activity_at` | `"2026-02-22T12:00:00+00:00"` (3 days ago) |
| `avg_quiz_score` | `80.0` |
| `reflection_confidence` | `"HIGH"` |

Raw score: 24 + 25 + 16 + 15 = 80. Invite cap → **15**
→ `signal = "COLD"`, includes `NOT_INVITED`

---

## Test Matrix

Tests live in `/tests/test_compute_lead_temperature.py`. All tests inject a
fixed `now`. No DB, no network access.

| # | Scenario | Expected signal | Key assertion |
|---|----------|:--------------:|---------------|
| T1 | All signals strong (completion=90, 2 days, quiz=88, HIGH reflection, low retries) | `HOT` | score ≥ 70; codes include COMPLETION_STRONG, RECENTLY_ACTIVE |
| T2 | Mid-progress, moderate signals (completion=40, 12 days, quiz=62, MEDIUM, mild retries) | `WARM` | 35 ≤ score < 70; codes include RETRY_MILD |
| T3 | Inactive, low progress (completion=8, 45 days, no quiz, LOW reflection) | `COLD` | score < 35; ACTIVITY_DORMANT present |
| T4 | Not invited, all other signals strong | `COLD` | score ≤ INVITE_CAP; NOT_INVITED in codes |
| T5 | All inputs None (only invited_sent=True) | `COLD` | score < 35; NO_ACTIVITY and QUIZ_UNKNOWN present |
| T6 | Stale activity prevents HOT (completion=70, 40 days inactive) | `WARM` | ACTIVITY_DORMANT penalises enough to prevent HOT |
| T7 | High retry friction prevents HOT (attempts=4.5) | `WARM` | RETRY_HIGH in codes; score < 70 |
| T8 | HIGH vs LOW reflection tips balance (all else equal) | HIGH→HOT, LOW→WARM | 12-pt reflection swing changes classification |
| T9 | Output shape is always complete and valid | any | All 5 keys present; signal in {"HOT","WARM","COLD"}; 0 ≤ score ≤ 100 |
| T10 | Module constants match locked directive values | — | SCORE_HOT=70, SCORE_WARM=35, INVITE_CAP=15 |

---

## Non-Goals (v1)

- **GHL push decisions:** Use `HotLeadSignal` (v1) for those. This engine is read-only diagnostic.
- **ML or probabilistic models:** All rules are hand-authored, fully deterministic.
- **Composite persona scoring across multiple leads:** Single-lead evaluation only.
- **Automatic threshold tuning:** Thresholds are locked constants; any change requires a directive update.
- **Scoring `current_section`:** Accepted as input for forward compatibility; not evaluated in v1.
