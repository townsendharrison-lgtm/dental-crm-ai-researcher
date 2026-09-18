# Phase 8 — complete (Node ↔ Python integration)

## Choices locked

- **Auth:** shared secret header `X-School-AI-Key` (`INTERNAL_API_SECRET` /
  `SCHOOL_AI_INTERNAL_KEY`). `/health` and OpenAPI docs remain public.
- **Job completion:** **polling** `GET /jobs/{id}` via `waitForJob` (no webhooks).

## Delivered

- FastAPI `InternalAuthMiddleware`
- Node client `backend/src/services/schoolAiClient.ts`
- Walkthrough `PHASE8_INTEGRATION.md`
- Offline proof: `uv run python -m scripts.phase8_roundtrip`
- Auth unit tests

## Actual results

```text
uv run pytest -q --tb=short
132 passed, 27 skipped, 2 warnings in 8.01s

uv run python -m scripts.phase8_roundtrip
phase8_roundtrip: ok (auth + generate + approve + score)
```

## STOP

Phase 8 Definition of Done is satisfied. Confirm before Phase 9 (hardening & ops).
