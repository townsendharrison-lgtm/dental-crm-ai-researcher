# AI School Research & Rubric Engine — implementation report

**Date:** 17 September 2026  
**Current phase:** Phase 2 in progress; approved taxonomy required  
**Service:** `school-ai-service`  
**Overall result:** infrastructure and all eight Phase 1 database models are
implemented. The additive migration is applied to Supabase. Existing records were
preserved; all new domain tables were empty after migration. Phase 2 parsing,
structured extraction and validation are implemented and tested. Production
taxonomy and the upload/job/persistence workflow remain incomplete.

## 1. Scope and reset

The previous `ai-server` folder was removed at your request. Researcher-specific
frontend pages, controls, navigation, and API methods were removed during that
reset, while shared comparison code was retained. The legacy `ai-server` folder
is confirmed absent.

The rebuild follows your newly supplied plan rather than treating the previous
implementation as its architecture. All rebuild work is contained in
`school-ai-service`. No React or Node code was changed during this rebuild.
Existing Supabase credentials were read from the backend's local `.env` and
copied into the new service's ignored `.env`, without modifying the backend file
or displaying the secrets.

There is no new frontend researcher interface or Node integration yet. Existing
frontend calls that depended on the removed server have not been connected to
this replacement. Integration is planned for Phase 8.

## 2. Current architecture and approved changes

| Component | Current decision | Implementation status |
| --- | --- | --- |
| Service | Python 3.11+, FastAPI; tested with Python 3.12 | Implemented; Uvicorn starts |
| Dependency management | `uv`, with `uv.lock` | Installed and reproducible |
| Configuration | Pydantic v2 and pydantic-settings | Implemented; `.env` ignored |
| Database | Existing Supabase PostgreSQL, async SQLAlchemy, asyncpg | Verified hosted connection with certificate and hostname checks |
| Migrations | Alembic, dedicated `school_ai` schema | Domain migration applied; hosted revision `0002_domain_models` |
| Queue | Supabase Queues (`pgmq`) and a Python worker | Real task enqueue, duplicate handling, execution and archival verified |
| Files | Native Supabase Storage REST API over async httpx | Implemented and verified against the hosted project |
| LLM | OpenAI `gpt-4o` for extraction and reasoning | SDK extraction wrapper implemented; strict schema, grounding and mocked-call tests |
| Search | Tavily | Key present; search wrapper deferred to Phase 3 |
| Tests | pytest, pytest-asyncio, respx, synthetic fixtures | Offline suite and opt-in hosted tests implemented |

These decisions incorporate your explicit changes: Supabase replaces local
Docker infrastructure, Redis/Celery and S3/R2 access; native Storage requires no
S3 credentials; GPT-4o replaces Anthropic Haiku/Sonnet. The original plan and
these amendments are recorded in [BUILD_PLAN.md](BUILD_PLAN.md).

## 3. What has been built

### Service and configuration

- A standalone FastAPI entrypoint with `/health`, `/docs`, and OpenAPI metadata.
- Settings for database, queue, storage, timeouts, retries, logging, API keys,
  and the exact requested model `gpt-4o`.
- `.env.example`, `.gitignore`, `pyproject.toml`, and dependency lockfile.
- Reserved directories/modules for later agents, schemas and API routes. Those
  research/rubric/scoring modules remain scaffolding. The Phase 2 document parser,
  extraction core and OpenAI wrapper now contain tested implementations.
- Health reports provider-key presence without making paid provider calls or
  exposing key values. It returns 503 when configuration or dependencies are missing.

### Database and migration foundation

- Lazy async SQLAlchemy connection setup, verified TLS by default, bounded
  connection/command timeouts, and an async session factory.
- An empty baseline followed by a frozen Phase 1 domain migration, with version
  tracking in the service's own schema.
- Schema-scoped migration autogeneration to avoid treating existing CRM tables
  as this service's migration targets.
- Applied `0002_domain_models`: eight new tables plus indexes, constraints,
  timestamp triggers and append-only correction-history protection. All eight
  domain tables are empty; no CRM records were changed.
- Supabase `vector` extension version `0.8.0` is available. No embeddings or
  guessed vector dimensions have been added.
- Added the official Supabase public CA certificate and configured it in local
  `.env`; certificate and hostname verification remain enabled. Certificate origin
  and checksum are recorded in [certs/README.md](certs/README.md).

### Supabase queue and worker

- Parameterized `pgmq` operations and validated queue identifiers.
- A strictly validated trivial `ping` task and a CLI polling worker.
- Duplicate submissions with the same task ID reuse the existing message,
  including archived messages.
- For the short Phase 0 task, processing, result persistence, and acknowledgement
  occur in one database transaction. Completed results can be read from the archive.
- Malformed tasks are not acknowledged. Long-running task visibility renewal,
  production failure/retry workflows, and generic job tracking are still future work.

### Native Supabase Storage

- Authenticated native Storage operations using the project URL and server-only
  service-role key, without boto3 or separate S3 credentials.
- A private-bucket readiness check.
- Repeatable bucket provisioning: create the configured bucket if missing; leave
  an existing private bucket unchanged; refuse to alter an existing public bucket.
- Content-hash object keys, cache reuse on repeat uploads, and explicit force refresh.
- Upload, authenticated download, existence lookup, and targeted object deletion.
- Bounded retries for transient failures, error redaction, path validation, and
  support for both modern and legacy missing-object responses.
- Storage-only bootstrap and smoke modes that do not require PostgreSQL credentials.

### External operations and tests

- A shared wrapper records operation, attempt, latency, success/failure and exception
  type without including payloads or raw provider error text.
- Read/idempotent-operation retries use bounded exponential backoff. Database writes
  with ambiguous commit outcomes are not blindly retried.
- Cancellation of a timed-out health probe now logs as failure rather than success.
- Infrastructure logs keep token/cost fields null. No provider usage or cost is invented.
- Offline tests cover health degradation, timeouts, secret redaction, settings,
  queue submission/processing, storage caching/errors/provisioning, and smoke cleanup.

## 4. Phase 1 implementation

Added SQLAlchemy models and the frozen Alembic revision `0002_domain_models` for:

| Table | Implemented purpose |
| --- | --- |
| `schools` | Identity, official URL, metadata, ingestion status and timestamps |
| `school_documents` | Private Supabase file references, SHA-256, source and parsing status |
| `school_raw_facts` | Atomic JSON facts, units, document/web sources, excerpt/page/section and confidence |
| `rubric_factors` | Unique school/factor row, value, nullable weight, tier, confidence, reasoning and citations |
| `rubric_overrides` | Before/after snapshots, editor, reason and timestamp; append-only history |
| `cross_school_stats` | Factor/unit reference statistics, sample size, method and provenance |
| `scoring_runs` | External CRM student ID, school, score, breakdown, rubric snapshot and explanation |
| `jobs` | Work type, optional school, status, payload, result/error, attempts and timestamps |

The database enforces document deduplication per school, factor uniqueness,
confidence bounds, evidence requirements and same-school document references.
Unknown raw values use SQL NULL with confidence zero. Nonmanual rubric rows require
source links and reasoning. SQL triggers refresh update times and block updates,
deletes and truncation of override history. Parent references use RESTRICT rather
than cascading deletions. Destructive downgrade is disabled in accordance with
the user's preservation requirement.

[SCHEMA.md](SCHEMA.md) explains all tables, the four weight-source tiers, evidence
rules and remaining workflow responsibilities in plain language. Migration tests
also compare the resulting schema to the ORM models to catch drift.

## 5. Verification evidence

### Offline suite

```text
python -m pytest -q --tb=short
64 passed, 27 skipped, 2 warnings in 4.08s
```

The 27 skips are opt-in hosted tests: the three existing Phase 0 checks and 24 new
Phase 1 checks. Skipping them was not counted as hosted verification. The two
warnings are existing upstream Starlette TestClient deprecations.

### Real Phase 1 database tests

With `RUN_INTEGRATION_TESTS=true` temporarily set and restored after the command:

```text
python -m pytest -q app/tests/test_models_hosted.py --tb=short -x
24 passed in 189.28s (0:03:09)
```

This run verified the real migration from an empty application schema, the full
school → raw facts → rubric → scoring-run read-back chain, all eight models,
invalid confidence/evidence rejection, document deduplication, school boundaries,
unique factors, immutable override updates, timestamp triggers and model/migration
consistency. SQL generation tests additionally verify delete/truncate protection
triggers and the absence of destructive upgrade statements.

**Isolation:** tests used a unique `school_ai_test_<uuid>` schema inside one
transaction on the existing Supabase database. Each test used savepoints. Session
commits released only savepoints; the outer transaction was rolled back afterward,
and the test schema's absence was verified. No DROP/DELETE/TRUNCATE cleanup ran.
This is fresh application-schema verification, not a new database/server reset.

### Applied hosted migration

The tested migration was then applied through Alembic's Python API in a committed
transaction. Actual result:

```text
migration_revision: 0002_domain_models
existing_table_identities_preserved: true
pgvector_version: 0.8.0

cross_school_stats: 0 rows
schools: 0 rows
jobs: 0 rows
rubric_factors: 0 rows
school_documents: 0 rows
scoring_runs: 0 rows
rubric_overrides: 0 rows
school_raw_facts: 0 rows
```

The check confirms existing table identities were retained and the newly added
service tables contain no test records. The upgrade creates new objects and
updates only its own Alembic revision tracking; it does not modify CRM records.

### Infrastructure evidence carried forward

Phase 0 previously passed its entire 65-test suite, including real queue execution,
Storage upload/download/cache/cleanup and live HTTP 200 health. The certificate
trust and pgmq archive-overload issues found in that phase were fixed. Details
remain in [PHASE0_STATUS.md](PHASE0_STATUS.md).

The current Phase 1 run does not repeat queue/storage write tests because the
change concerns database models and migrations. The read-only hosted health check
passed after the applied migration:

```text
python -m pytest -q app/tests/test_hosted_integration.py::test_hosted_health_is_green --tb=short
1 passed, 2 warnings in 17.43s
```

This verifies HTTP 200 and green database, queue, Storage and key-presence checks.
Across the separate Phase 1 verification runs, **89 distinct tests passed**:
64 offline, 24 hosted model tests and one hosted health test. OpenAI and Tavily have only been
checked for key presence; there have been no provider calls or credit usage.

## 6. Phase 1 gate

| Requirement | Current result |
| --- | --- |
| Eight database models and frozen migration | Implemented |
| Migration from an empty application schema | Passed on hosted PostgreSQL in isolated transaction |
| Full school/fact/rubric/scoring round trip | Passed |
| Additional persistence/integrity checks | All 24 hosted tests passed |
| Offline suite | 64 passed |
| Post-migration hosted HTTP health | Passed; HTTP 200 |
| Apply additive migration to service schema | Done; `0002_domain_models` |
| Preserve existing data | No existing records cleared or deleted; no destructive cleanup |
| Short administrator-oriented schema documentation | Written in `SCHEMA.md` |
| Phase 2 extraction implementation | Authorized; see current progress below |

**Phase 1 is complete.** Phase 2 was subsequently authorized. Future phases must
continue preserving existing data and use forward migrations when changing this
schema. Secrets remain in the ignored service `.env`.

## Current Phase 2 status — complete

[PHASE2_STATUS.md](PHASE2_STATUS.md) records the gate evidence.

Implemented PDF/DOCX parsing, OCR routing, bounded chunking, GPT-4o structured
extraction, taxonomy `client-criteria-v1`, durable Storage/pgmq worker checkpoints
into `school_raw_facts`, HTTP document endpoints, and CLI. `python-multipart` was
added for uploads. Offline API + workflow tests use mocked OpenAI; no production
rows were deleted.

Latest runnable evidence:

```text
uv run pytest -q --tb=short
101 passed, 27 skipped, 2 warnings in 6.74s
```

Tesseract is optional locally; missing Tesseract fails scanned PDFs explicitly.
OCR routing remains covered by stubbed-engine unit tests.

Implementation follows [OpenAI structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).
Cost estimates use [GPT-4o standard text rates](https://developers.openai.com/api/docs/models/gpt-4o)
checked on 2026-09-17, applied only to returned usage; they are not invoice totals.

## 7. Remaining roadmap

| Phase | Work still required | Proof required at its gate |
| --- | --- | --- |
| 2 — Document extraction | **Complete** | HTTP upload/job/facts; durable worker; taxonomy `client-criteria-v1`; offline API + workflow + extraction tests |
| 3 — Web research | **Complete** | Allow-list; Tavily; httpx/trafilatura (+ optional Playwright); gap analysis; TTL page cache; `POST .../research`; mocked tests |
| 4 — Normalization | **Complete** | Pure distributions + min/avg/max curve; upsert `cross_school_stats`; `POST /normalization/recompute`; five-school unit tests |
| 5 — Rubric inference | **Complete** | Three-tier inference; category weight reconciliation; generate/list APIs; grounding enforced |
| 6 — Review/override | **Complete** | Draft/approved status; PATCH override + audit; approve; score gate 409 until approved |
| 7 — Scoring | **Complete** | Deterministic fit score; skip missing factors; GPT-4o narrative only; `scoring_runs`; approved-only |
| 8 — Integration | **Complete** | Shared-secret auth; Node `schoolAiClient`; polling jobs; walkthrough + offline round-trip script |
| 9 — Operations | Rate/cost caps, provider usage metering, failure queries, operational documentation and measured load/cost report | Full suite, real usage-based cost evidence, and reproducible operator setup |

Every phase still has its own evidence and confirmation gate. None of the later
phase rows above is marked implemented simply because a placeholder module exists.

## 8. Decisions resolved for integration

- **Auth:** `X-School-AI-Key` shared secret (`INTERNAL_API_SECRET` /
  `SCHOOL_AI_INTERNAL_KEY`).
- **Job completion:** client polling of durable `jobs` rows (no webhooks in Phase 8).

See `PHASE8_INTEGRATION.md` and `backend/src/services/schoolAiClient.ts`.

## 9. Deliverables and current limitations

Main files:

- [README.md](README.md): environment setup and executable commands.
- [BUILD_PLAN.md](BUILD_PLAN.md): original requirements and approved stack changes.
- [PHASE0_STATUS.md](PHASE0_STATUS.md): completed infrastructure gate.
- [PHASE1_STATUS.md](PHASE1_STATUS.md): data-model gate evidence.
- [PHASE2_STATUS.md](PHASE2_STATUS.md): Phase 2 gate evidence (complete).
- [SCHEMA.md](SCHEMA.md): plain-language table and weight-source guide.
- [app/main.py](app/main.py): service entrypoint, health and document HTTP API.
- [app/config.py](app/config.py): environment validation and secrets handling.
- [app/db/](app/db/): SQLAlchemy models, baseline and additive domain migration.
- [app/documents.py](app/documents.py) / [app/document_cli.py](app/document_cli.py): durable document workflow.
- [app/clients/](app/clients/): storage, queue, LLM and shared operation wrappers.
- [app/bootstrap.py](app/bootstrap.py): bucket + ping/document queue provisioning.
- [app/smoke.py](app/smoke.py): real hosted infrastructure checks.
- [app/workers/](app/workers/): ping worker and document extraction worker.
- [app/tests/](app/tests/): offline and explicitly enabled hosted tests.

There are no generated rubrics, student scores or measured per-school costs yet.
Phases 3–9 remain. **Never delete or truncate production database data.**

**Next action:** confirm Phase 8 gate, then authorize Phase 9 (hardening & ops).
