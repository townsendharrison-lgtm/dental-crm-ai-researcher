# Phase 1 — complete

Phase 1 was authorized and completed on 2026-09-17. Phase 2 was subsequently authorized; see [PHASE2_STATUS.md](PHASE2_STATUS.md).
See [SCHEMA.md](SCHEMA.md) for the table guide and
[IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) for the full cumulative report.

## Delivered

- Eight SQLAlchemy models: schools, documents, raw facts, rubric factors,
  correction history, cross-school statistics, scoring runs and jobs.
- Frozen, additive Alembic revision `0002_domain_models`, applied to `school_ai`.
- pgvector `0.8.0` available; no embedding model or dimensions assumed.
- Source/confidence checks, same-school document references, document/factor
  uniqueness, timestamp triggers and append-only correction history.
- Database tests and plain-language schema/weight-source documentation.

## Actual passing results

```text
Offline suite:
64 passed, 27 skipped, 2 warnings in 4.08s

Explicitly enabled hosted Phase 1 tests:
24 passed in 189.28s (0:03:09)

Post-migration hosted health:
1 passed, 2 warnings in 17.43s

Applied migration: 0002_domain_models
Existing table identities preserved: true
Rows in each of the eight new domain tables: 0
```

That is 89 distinct passing tests across the three runs. Offline skips are hosted
tests; they were not counted as passes. The two pre-existing queue/Storage write
tests were not rerun this phase; they passed during Phase 0. Warnings concern
upstream Starlette TestClient deprecations.

## Data preservation

The user explicitly prohibited clearing/deleting existing data. Tests applied
the migration to a unique empty schema inside an outer transaction. Synthetic
records exercised all models and the full school → facts → rubric → scoring
chain. The transaction was rolled back and the temporary schema's absence was
verified. No DROP/DELETE/TRUNCATE cleanup statements were used.

This validates migration from a fresh application schema on hosted PostgreSQL;
it does not reset the database or provision a separate database server. The
committed migration adds only new service tables, indexes, constraints and
functions/triggers, enables vector if absent, and advances service revision
tracking. Existing CRM records were not changed. Destructive downgrade is disabled.

## Remaining work

Phase 2: agree on the fourteen-category factor taxonomy, implement PDF/DOCX
extraction and OCR, chunking, GPT-4o structured output validation, citation-grounded
raw facts, upload jobs and content-hash reuse. No extraction/inference/scoring
algorithm, review workflow or frontend/Node integration was added in Phase 1.
