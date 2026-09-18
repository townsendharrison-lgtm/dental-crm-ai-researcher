# Phase 6 — complete (admin review / override)

Phase 6 was authorized after Phase 5. No production rows were deleted. Override
history is append-only; approval columns are additive.

## Implemented and tested

- Migration `0004_rubric_status`: `schools.rubric_status` (`draft`/`approved`),
  `rubric_approved_at`, `rubric_approved_by`
- `GET /schools/{id}/rubric` includes approval status
- `PATCH /schools/{id}/rubric/{factor}` → audit `rubric_overrides` + `manual_override`
- `POST /schools/{id}/rubric/approve`
- Scoring gate: `POST /schools/{id}/score` → **409** if draft, allowed if approved
- Generate/override reset status to draft

## Actual results

```text
uv run pytest -q --tb=short
128 passed, 27 skipped, 2 warnings in 9.24s
```

## Operator note

```powershell
uv run alembic upgrade head
```

## STOP

Phase 6 Definition of Done is satisfied. Confirm before Phase 7 (scoring agent).
