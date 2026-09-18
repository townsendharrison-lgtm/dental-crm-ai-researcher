# Phase 5 — complete (rubric inference)

Phase 5 was authorized after Phase 4. Generation upserts `rubric_factors` and
never deletes production rows. `manual_override` rows are preserved.

## Implemented and tested

- Three-tier branch logic: `stated` → `cross_school_inferred` → `qualitative_inferred`
- Stated weights parsed from `*_stated_weights` facts (e.g. `avg_gpa: 25%`)
- Cross-school uses Phase 4 family defaults + stats reasoning when available
- Qualitative: GPT-4o returns `low|medium|high`; code maps to 0.05 / 0.15 / 0.30
- Category weight reconciliation documented in `SCHEMA.md`
- Pydantic rejects automated rows without `source_urls` / confidence
- API: `POST /schools/{id}/rubric/generate`, `GET /schools/{id}/rubric`

## Actual results

```text
uv run pytest -q --tb=short
124 passed, 27 skipped, 2 warnings in 6.76s
```

## STOP

Phase 5 Definition of Done is satisfied. Confirm before Phase 6 (admin review/override).
