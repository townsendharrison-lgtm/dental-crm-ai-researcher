# Phase 2 — complete (HTTP + durable workflow)

Phase 2 was authorized and its definition of done is satisfied for the OpenAI +
Supabase stack. Existing production rows were not deleted, truncated or cleaned.

## Implemented and tested

- PDF/DOCX parsing with page/section provenance and OCR fallback routing.
- OpenAI `gpt-4o` structured extraction with grounding, retries and mocked-call tests.
- Approved fourteen-category taxonomy (`client-criteria-v1`) in `app/factor_taxonomy.py`.
- Durable upload → private Storage → `jobs` + `pgmq` → worker checkpoints →
  append-only `school_raw_facts` (CLI and HTTP).
- Content-hash idempotency (skip re-extraction unless `force_refresh`).
- HTTP API:
  - `POST /schools`
  - `POST /schools/{id}/documents`
  - `GET /jobs/{id}`
  - `GET /documents/{id}/facts`
- Bootstrap provisions both the phase-0 ping queue and `DOCUMENT_QUEUE_NAME`.

## Actual results

```text
uv run pytest -q --tb=short
101 passed, 27 skipped, 2 warnings in 6.74s
```

The 27 skips are opt-in hosted integration tests. Offline suite includes extraction,
HTTP document API and durable worker workflow tests with mocked OpenAI calls.

## Safety note

This service and its tests must **never** `DELETE`/`TRUNCATE`/`DROP` production
CRM or `school_ai` data. Document jobs archive queue messages; they do not purge
historical rows. Force-refresh re-enqueues extraction without removing prior facts
already written under deterministic fact IDs (`ON CONFLICT DO NOTHING`).

## OCR note

OCR routing is covered with a stubbed engine. A real Tesseract executable is
optional locally via `TESSERACT_CMD`; missing Tesseract fails scanned PDFs
explicitly instead of inventing empty success.

## References

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-4o model and standard text prices](https://developers.openai.com/api/docs/models/gpt-4o)
