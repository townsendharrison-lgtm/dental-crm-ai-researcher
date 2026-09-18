# Phase 7 — complete (scoring agent)

Phase 7 was authorized after Phase 6. Scoring appends `scoring_runs` only; no
production rows are deleted.

## Implemented and tested

- `StudentProfile` contract: `student_id` + `attributes` keyed by taxonomy factors
- Deterministic fit score `0–100` = `100 * Σ(weight × factor_score) / Σ(weight)`
  over scored factors (`deterministic_fit_score_v1` — **not** an acceptance
  probability)
- Factor match: min/avg/max curve when family complete; else ratio-to-expectation
- Missing student values skipped with reason (never zero-filled or invented)
- GPT-4o narrates the precomputed breakdown only
- Persists `scoring_runs`; requires approved rubric (HTTP 409 otherwise)
- `POST /schools/{id}/score` returns score, breakdown, skipped, narrative

## Actual results

```text
uv run pytest -q --tb=short
130 passed, 27 skipped, 2 warnings in 8.32s
```

Hand-checked fixture: GPA 3.5/3.5 @0.5 + DAT 18/20 @0.5 → **95.0**; missing
shadowing reported as `missing_student_value`.

## STOP

Phase 7 Definition of Done is satisfied. Confirm before Phase 8 (Node integration).
