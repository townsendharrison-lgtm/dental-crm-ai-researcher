# Phase 0 — complete

Verified on 2026-09-17. Phase 1 was subsequently authorized and completed;
see [PHASE1_STATUS.md](PHASE1_STATUS.md). Results below are the Phase 0 evidence.
See [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) for the full report and roadmap.

## Passing evidence

```text
Complete suite, with RUN_INTEGRATION_TESTS=true:
65 passed, 2 warnings in 50.44s

Hosted Alembic revision:
0001_baseline (head)

Live HTTP health:
HTTP/1.1 200 OK
{"status":"ok","checks":{"database":true,"queue":true,"storage":true},"api_keys":{"openai":true,"tavily":true},"errors":{}}
```

All three hosted tests ran and passed; none were skipped. The two warnings are
upstream Starlette TestClient deprecations.

## Implemented and verified

- Independent FastAPI service, uv dependencies, validated settings and ignored `.env`.
- Verified-TLS Supabase database connection using the official public CA certificate.
- Empty Alembic baseline in the dedicated `school_ai` schema.
- Dedicated `school_ai_phase0` pgmq queue; real task execution, idempotency and archival.
- Private `school-ai-documents` bucket; upload/download/cache and test-object cleanup.
- OpenAI `gpt-4o` configuration and OpenAI/Tavily key presence checks.
- Shared external-operation logging and retry infrastructure.

Hosted tests exposed an ambiguous pgmq archive overload, now fixed with explicit
SQL parameter casts. Local connection and health timeouts are 30 seconds to allow
for observed hosted latency. TLS and hostname verification remain enabled.

No paid LLM/search calls were made; key validity remains untested. Tiny synthetic
queue messages remain archived; test Storage objects were removed. No CRM records
were modified. The temporary API verification server was stopped.

## Subsequent gate (completed)

Phase 1: models and migrations for schools, documents, raw facts, rubric factors,
overrides, cross-school statistics, scoring runs and jobs; pgvector; schema docs;
and database round-trip tests. Extraction and the fourteen-category taxonomy
start in Phase 2. No later-phase behavior is implemented yet.
