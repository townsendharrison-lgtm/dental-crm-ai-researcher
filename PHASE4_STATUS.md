# Phase 4 — complete (cross-school normalization)

Phase 4 was authorized after Phase 3. No production rows were deleted or
truncated. `cross_school_stats` is updated by upsert only.

## Implemented and tested

- Pure `normalization_service.py`: per numeric taxonomy factor, select best fact
  per school, then min/max/mean/stddev, p10–p90, per-school percentile rank and
  z-score.
- Documented min/avg/max **family weights** (0.05 / 0.90 / 0.05) and **position
  curve** (min→0.05, avg→1.0, max→0.95) in `SCHEMA.md`.
- `POST /normalization/recompute` and `GET /normalization/stats/{factor_key}`.
- Offline unit tests with five seeded schools (hand-checked ranks) and curve
  assertions. No LLM. No mocks required for the math.

## Actual results

```text
uv run pytest -q --tb=short
116 passed, 27 skipped, 2 warnings in 7.81s
```

Math tests cover five-school GPA/DAT ranks, family weights, and the position curve.

## STOP

Phase 4 Definition of Done is satisfied. Confirm before Phase 5 (rubric inference).
