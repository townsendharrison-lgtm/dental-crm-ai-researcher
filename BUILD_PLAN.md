# Approved updates to the supplied plan — 2026-09-17

These later user instructions take precedence over the original stack below:

- Use existing hosted Supabase PostgreSQL, Supabase Queues (`pgmq`) with a Python
  worker, and **native Supabase Storage**. No Redis/Celery, Docker, S3 credentials,
  or boto3 are required. SQLAlchemy async and Alembic remain in place.
- Use the OpenAI Python SDK with **`gpt-4o` for both extraction and reasoning**
  instead of Anthropic Haiku/Sonnet. Use `OPENAI_API_KEY` and `OPENAI_MODEL=gpt-4o`.
  Pydantic validation, grounding, confidence, citations, retries, metering, and
  no-fabrication requirements remain mandatory.
- Supabase Storage uses `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and
  `SUPABASE_STORAGE_BUCKET`. Database migrations still require `DATABASE_URL`.
- Phase 0 health checks hosted PostgreSQL, pgmq, private native Storage bucket
  access, and **OpenAI/Tavily** key presence. Hosted smoke tests replace the
  Docker/Celery infrastructure checks. Phase gates and confirmation rules remain.
- No later-phase agent behavior is implemented until its phase is approved.
- Phase 2 was authorized after Phase 1 passed. The full fourteen-category factor
  list is required from the client; test-only taxonomy definitions are not defaults.
- Phase 1 was authorized. The user explicitly requires preservation of all existing
  database data: additions are allowed; clearing/deleting existing data is not.
  Hosted migration verification uses an empty, uniquely named application schema
  inside a rollback-only transaction in the existing Supabase project. This tests
  the fresh application schema without resetting or replacing the database.

The original plan is preserved below for reference.

---

# Build Plan: AI School Research & Rubric Engine

## READ THIS FIRST — Operating Rules for the Agent

You are building a Python microservice (`school-ai-service`) that sits alongside an
existing React frontend and Node/Express backend (CRM). You do **not** touch the
React or Node codebases except to add a thin internal API client at the very end.

**You must follow these rules for the entire project, not just phase 1:**

1. **No one-shotting.** Work through the phases below **in order**. Do not start
   Phase N+1 until Phase N's "Definition of Done" checklist is fully satisfied and
   you have shown the passing test output.
2. **Stop at every phase gate.** At the end of each phase, stop, summarize what you
   built, show test results, and explicitly ask for confirmation before continuing.
   Do not silently continue to the next phase.
3. **No fabrication, anywhere in the system.** If a value (a rubric weight, a school
   stat, a source URL) is not grounded in an actual document, web page, or computed
   statistic, it must not be produced. Every inferred field must carry a
   `source_urls` list and a `confidence` score. If you cannot find grounding, the
   field must be marked `null` with `confidence: 0`, not guessed.
4. **Write tests as you go, not at the end.** Every phase includes explicit test
   requirements. Do not mark a phase done without runnable tests proving it works.
5. **Secrets never hardcoded.** All API keys (Anthropic, Tavily/Brave, S3/R2, DB)
   come from environment variables via a `.env` file and `pydantic-settings`. Add
   `.env` to `.gitignore` immediately in Phase 0.
6. **Every external call is wrapped and logged.** LLM calls, web fetches, and search
   calls must go through a single wrapper function per type that logs cost/tokens/
   latency and handles retries with backoff. Do not scatter raw `httpx`/SDK calls
   through the codebase.
7. **Idempotency and caching by default.** Never re-fetch a URL or re-parse a
   document you've already processed unless explicitly told to force-refresh. Cache
   keyed by content hash / URL.
8. **If something in this plan is ambiguous or you think a better approach exists,
   stop and ask** — do not silently deviate from the architecture below.
9. **Every phase ends with a real, runnable artifact** — not just code, but something
   that can actually be executed and inspected (a CLI command, an API endpoint hit
   with curl, a test suite run). "It should work" is not acceptable evidence.

---

## Locked Tech Stack (do not substitute without asking)

- **Language/framework:** Python 3.11+, FastAPI
- **LLM:** Anthropic API (`anthropic` Python SDK) — Claude Haiku for high-volume
  extraction, Claude Sonnet for reasoning/synthesis (rubric inference, scoring
  rationale)
- **Search:** Tavily API (`tavily-python`)
- **Web fetch/parse:** `httpx` (async) + `trafilatura` for content extraction;
  `playwright` only as a fallback for JS-rendered pages
- **Doc parsing:** `unstructured`, `pdfplumber`/`PyMuPDF` (fitz), `pytesseract` for
  OCR, `python-docx`
- **DB:** PostgreSQL with `pgvector` extension, accessed via `SQLAlchemy` (async) +
  `alembic` for migrations
- **Job queue:** Redis + Celery (or RQ — pick one in Phase 0 and justify it briefly)
- **File storage:** S3-compatible (Cloudflare R2 or actual S3), via `boto3`
- **Validation/schemas:** `pydantic` v2 everywhere — every LLM structured output
  must be validated against a pydantic model before being trusted or stored
- **Testing:** `pytest`, `pytest-asyncio`, `respx` (for mocking httpx calls),
  `pytest-vcr` or manual fixtures for mocking LLM/search responses so tests don't
  burn API credits
- **Package/env management:** `uv` or `poetry` (pick one in Phase 0)

---

## Repo Structure (create in Phase 0)

```
school-ai-service/
  app/
    main.py                    # FastAPI app entrypoint
    config.py                  # pydantic-settings, env vars
    db/
      models.py                # SQLAlchemy models
      session.py
      migrations/               # alembic
    clients/
      llm_client.py             # wraps Anthropic calls, logs cost/tokens
      search_client.py          # wraps Tavily
      fetch_client.py           # wraps httpx + trafilatura + playwright fallback
      storage_client.py         # wraps S3/R2
    agents/
      document_extraction_agent.py
      web_research_agent.py
      normalization_service.py
      rubric_inference_agent.py
      scoring_agent.py
    schemas/                    # pydantic models for all structured data
      school_profile.py
      rubric.py
      scoring.py
    api/
      routes_schools.py
      routes_rubrics.py
      routes_scoring.py
      routes_jobs.py
    workers/
      celery_app.py
      tasks.py
    tests/
      fixtures/
      test_document_extraction.py
      test_web_research.py
      test_normalization.py
      test_rubric_inference.py
      test_scoring.py
      test_api.py
  alembic.ini
  pyproject.toml
  .env.example
  .gitignore
  README.md
```

---

## PHASE 0 — Scaffolding & Infra

**Goal:** A running, empty-but-correct FastAPI service with DB, queue, and storage
wired up and health-checked. No agent logic yet.

**Tasks:**
- Initialize repo structure above, package manager, `pyproject.toml`.
- Set up `config.py` with `pydantic-settings`, `.env.example` listing every required
  var (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `DATABASE_URL`, `REDIS_URL`,
  `S3_*`, etc.) with **no real secrets committed**.
- Postgres connection via SQLAlchemy async, `alembic` initialized with an empty
  baseline migration.
- Redis + Celery/RQ app configured with a single trivial test task.
- S3/R2 client with a function to upload/download a test file.
- `/health` endpoint that checks DB, Redis, and returns LLM/search API key presence
  (not validity) as booleans.
- `docker-compose.yml` for local Postgres + Redis so this runs without cloud infra.
- Basic `README.md`: how to run locally, run tests, run migrations.

**Definition of Done:**
- `docker-compose up` brings up Postgres + Redis.
- `uvicorn app.main:app` starts cleanly.
- `curl localhost:8000/health` returns 200 with all checks green.
- A Celery/RQ test task can be enqueued and completes, verified by a test.
- `pytest` runs (even if just the health check test) and passes.

**STOP. Report results, then wait for confirmation before Phase 1.**

---

## PHASE 1 — Data Model

**Goal:** Full DB schema for schools, rubrics, students-link, and scoring — built
before any agent logic, so every later phase writes into a stable shape.

**Tasks:** Create SQLAlchemy models + Alembic migration for:
- `schools` — name, official_url, metadata, ingestion status timestamps
- `school_documents` — uploaded file refs, S3 key, parsed status, source type
- `school_raw_facts` — atomic extracted facts (factor name, value, unit, source
  type [document|web], source_url or document_id, extracted_at, confidence).
  This is the ungrouped extraction layer before rubric synthesis.
- `rubric_factors` — one row per (school, factor) with `value`, `weight`,
  `weight_source` (`stated`/`cross_school_inferred`/`qualitative_inferred`/
  `manual_override`), `confidence`, `reasoning`, `source_urls[]`, `updated_at`
- `rubric_overrides` — admin manual corrections: school_id, factor, old_value,
  new_value, editor, reason, timestamp. Never overwritten, append-only audit log.
- `cross_school_stats` — computed percentile/z-score reference table per factor,
  recomputed as schools are added (this backs Phase 4)
- `scoring_runs` — student_id (reference only, FK lives conceptually in the Node
  CRM — store as external ID, not a hard FK across services), school_id, score,
  per-factor breakdown JSON, reasoning text, created_at
- `jobs` — generic async job tracking (type, status, payload, result, error)

**Definition of Done:**
- Migration applies cleanly to a fresh DB.
- A test seeds one fake school through the full chain (school → raw_facts →
  rubric_factors → scoring_run) and reads it back correctly.
- Write a short `SCHEMA.md` documenting each table and the `weight_source` tiering
  logic in plain language (for a non-engineer admin to understand later).

**STOP. Report results, then wait for confirmation before Phase 2.**

---

## PHASE 2 — Document Extraction Agent

**Goal:** Given an uploaded document (PDF/docx), produce structured
`school_raw_facts` rows, grounded and cited to page/section where possible.

**Tasks:**
- `fetch_client`/file loader for PDFs (text + OCR fallback via pytesseract for
  scanned pages) and docx.
- Chunk long documents sensibly (don't blindly truncate — split by section/page).
- Pydantic schema (`schemas/school_profile.py`) defining the exact shape of an
  extracted fact: `{factor_key, value, unit, raw_text_snippet, page_number,
  confidence}`.
- `llm_client` call using Claude with **structured output enforced** (JSON schema
  in the prompt + pydantic validation on response, retry once on validation
  failure, else mark extraction failed for that chunk rather than guessing).
- Map extracted facts to the 14-category factor taxonomy (define this taxonomy as
  a static config file — `factor_taxonomy.py` — listing every factor from the
  Academics/DAT/Shadowing/etc. categories the client specified, so the LLM is
  extracting into known slots, not inventing keys).
- Persist to `school_raw_facts`.
- Idempotency: hash the document, skip re-extraction if unchanged.

**Definition of Done:**
- CLI command or API endpoint: `POST /schools/{id}/documents` → upload → job
  enqueued → `school_raw_facts` populated.
- Test with at least 2 real (or realistic synthetic) sample admissions PDFs
  (class profile + mission/values page) checked into `tests/fixtures/`.
- Test asserts specific facts extracted correctly (e.g., "avg GPA 3.7" gets
  parsed to `{factor_key: "avg_gpa", value: 3.7}`).
- Test asserts that a document with no data on a factor does **not** produce a
  fabricated fact for it.
- LLM calls in tests are mocked/recorded — tests must not require live API keys
  to pass in CI.

**STOP. Report results, then wait for confirmation before Phase 3.**

---

## PHASE 3 — Web Research Agent

**Goal:** Given a school's official URL and the gaps left after Phase 2, find and
extract the missing factors from verified sources only.

**Tasks:**
- Source allow-list config: school's own domain(s), ADEA, and a short curated list
  — enforce this in code, not just prompt instruction (reject/flag results from
  off-list domains).
- `search_client` (Tavily) wrapper: given a factor gap + school name, generate a
  targeted query, return candidate URLs.
- `fetch_client`: fetch each candidate (httpx first; `playwright` fallback only if
  content extraction from static fetch is empty/too short).
- `trafilatura` to strip to clean article text before LLM extraction.
- Same structured-extraction approach as Phase 2, writing to `school_raw_facts`
  with `source_type=web` and the real `source_url`.
- Explicit "gap list" step: before researching, compute which taxonomy factors are
  still missing/low-confidence after Phase 2, and only search for those — don't
  blindly re-research everything.
- Cache fetched pages by URL hash with a TTL (config value, default e.g. 30 days).

**Definition of Done:**
- API endpoint: `POST /schools/{id}/research` → job enqueued → gap analysis →
  targeted search+fetch+extract → `school_raw_facts` updated, each row citing a
  real `source_url`.
- Test with mocked Tavily + httpx responses (fixtures) proving: off-allow-list
  domains are rejected, gap analysis correctly identifies only missing factors,
  and extracted facts carry correct source URLs.
- Test proving caching works (second call for same URL doesn't re-fetch).

**STOP. Report results, then wait for confirmation before Phase 4.**

---

## PHASE 4 — Cross-School Normalization Layer

**Goal:** Pure computation (no LLM) that turns raw numeric facts across all
ingested schools into percentile/z-score reference stats, which later phases use
to ground weight inference instead of having the LLM guess blind.

**Tasks:**
- `normalization_service.py`: for each numeric factor (avg GPA, avg DAT AA, etc.),
  compute distribution stats (min/max/mean/percentile rank of each school within
  the full set) across all schools currently in `school_raw_facts`.
- Write results to `cross_school_stats`, recomputed on a trigger (new school
  added, or via scheduled Celery task).
- Handle the specific min/avg/max weighting scheme described by the client
  explicitly: build a small documented function implementing "min GPA ~5% weight,
  avg GPA highest, max GPA ~95%" as a configurable default curve, overridable per
  factor, used only when a school has no explicit stated weighting.

**Definition of Done:**
- Given a seeded set of ≥5 fake schools with varying GPA/DAT stats, test asserts
  correct percentile ranks and that the min/avg/max weighting curve produces
  sane, documented outputs.
- Function is pure/deterministic — no LLM calls in this phase at all. This must
  be testable with plain unit tests, no mocks needed.

**STOP. Report results, then wait for confirmation before Phase 5.**

---

## PHASE 5 — Rubric Inference Agent

**Goal:** Synthesize `school_raw_facts` + `cross_school_stats` into the final
`rubric_factors` table — the school's estimated rubric, every field tiered and
cited as specified in `SCHEMA.md`.

**Tasks:**
- Implement the three-tier logic explicitly (not just via prompt — as real branch
  logic):
  1. `stated` — if a raw fact explicitly states a weight/ranking, use it directly.
  2. `cross_school_inferred` — for purely numeric factors with no stated weight,
     use Phase 4's normalization output plus the documented default curve.
  3. `qualitative_inferred` — for non-numeric factors (School Fit, Mission
     alignment, etc.), one Sonnet call per factor that reads the relevant raw
     facts/snippets (mission statement, "what we look for" text) and outputs a
     `{weight_bucket: low/medium/high, reasoning, source_urls}` — mapped to a
     numeric range in code, not by the LLM.
- Every `rubric_factors` row must carry `weight_source`, `confidence`,
  `reasoning`, `source_urls` — enforce via pydantic, reject writes missing them.
- Normalize final weights so they sum sensibly within/across the 14 categories
  (document the normalization approach in `SCHEMA.md`).

**Definition of Done:**
- API endpoint: `POST /schools/{id}/rubric/generate` → produces a full
  `rubric_factors` set for the school.
- Test asserts: a factor with an explicit stated weight in fixture data comes out
  tier `stated` with that exact weight; a numeric factor with no stated weight
  comes out `cross_school_inferred` and matches Phase 4's math; a qualitative
  factor comes out `qualitative_inferred` with non-empty `reasoning` and
  `source_urls`.
- Test asserts no `rubric_factors` row is ever written with `confidence` missing
  or `source_urls` empty when `weight_source != manual_override`.

**STOP. Report results, then wait for confirmation before Phase 6.**

---

## PHASE 6 — Admin Review / Override Layer

**Goal:** Let a human correct/confirm a generated rubric before it's used for
live scoring, and log corrections for future use.

**Tasks:**
- API endpoints: `GET /schools/{id}/rubric` (with tiers/confidence/citations
  visible), `PATCH /schools/{id}/rubric/{factor}` to override a value/weight
  (writes to `rubric_overrides` audit table AND updates `rubric_factors` with
  `weight_source=manual_override`), `POST /schools/{id}/rubric/approve` to mark
  a rubric version as "live" (add a `status` field: draft/approved).
- Only `approved` rubrics are usable by the Scoring Agent (Phase 7) — enforce
  this, don't let draft rubrics silently get used.

**Definition of Done:**
- Test: generate a draft rubric, attempt to score against it → rejected (not
  approved). Approve it → scoring now allowed. Override a factor → audit row
  created, `rubric_factors` reflects new value, original preserved in
  `rubric_overrides`.

**STOP. Report results, then wait for confirmation before Phase 7.**

---

## PHASE 7 — Scoring Agent

**Goal:** Given a student profile payload (passed in from Node/CRM — this service
does not own student data) and an approved school rubric, compute a probability
score with a factor-by-factor reasoning breakdown.

**Tasks:**
- Define the student profile input schema (`schemas/scoring.py`) — Node will send
  this; you don't need the CRM's actual DB, just agree on the JSON shape and
  document it (GPA, sGPA, DAT scores, shadowing hours, etc. — mirroring the 14
  categories).
- Deterministic weighted-sum calculation in code (not LLM) using
  `rubric_factors.weight` × normalized student-value-vs-school-expectation per
  factor.
- One Sonnet call to generate the human-readable reasoning narrative from the
  computed breakdown (explicitly: LLM explains the math, it does not do the
  math).
- Explicit handling for missing student data on a factor (skip with a note, don't
  silently zero it out and don't fabricate a value).
- Persist to `scoring_runs`.

**Definition of Done:**
- API endpoint: `POST /schools/{id}/score` with a student payload → returns score
  + full per-factor breakdown + narrative + which factors were skipped/why.
- Test with fixture student profiles against a fixture approved rubric, asserting
  the weighted math is correct by hand-computed expected values (not just "score
  is a number between 0-100").
- Test asserts a missing student factor doesn't crash and is reported as skipped.

**STOP. Report results, then wait for confirmation before Phase 8.**

---

## PHASE 8 — Node ↔ Python Integration

**Goal:** Thin, well-documented internal API contract so the existing Node/Express
CRM can trigger jobs and pull results.

**Tasks:**
- Document every Python service endpoint (OpenAPI/Swagger — FastAPI gives this
  free, just make sure summaries/descriptions are filled in).
- Add simple internal-auth (shared secret header or JWT) between Node and Python
  — not public-facing.
- Write a minimal Node-side client module (`services/schoolAiClient.js` or
  similar) with functions matching the Python endpoints, plus job-status
  polling helper.
- Webhook or polling pattern for job completion — pick one, document the choice.

**Definition of Done:**
- A documented Postman/curl walkthrough: upload doc → generate rubric → approve
  → score a student → get result, all from the Node side.
- Integration test (can be a script, not necessarily CI) proving the round trip
  works end to end against a running local stack.

**STOP. Report results, then wait for confirmation before Phase 9.**

---

## PHASE 9 — Hardening & Ops

**Goal:** Make this safe to run unattended in production.

**Tasks:**
- Rate limiting / cost guardrails on LLM and search calls (per-school daily cap,
  configurable).
- Structured logging (cost, tokens, latency per call) aggregated so you can see
  $ spent per school ingestion.
- Error alerting path (at minimum: failed jobs are queryable via
  `GET /jobs?status=failed`).
- Basic load/cost test: ingest N schools, report total tokens/cost/time.
- Final `README.md` pass: setup, env vars, how to add a new school end-to-end,
  how to review/approve a rubric, how to interpret confidence/weight_source.

**Definition of Done:**
- Full test suite (`pytest`) passes.
- README is sufficient for a new engineer to run this without asking you
  questions.
- You produce a short cost report: approx $ per school ingested, $ per student
  scored, based on real token counts from test runs.

**STOP. Final report.**

---

## Reminders the agent should re-read before every phase

- Did I check the Definition of Done against actual test output, not my own
  claim that it works?
- Did every inferred value get a `source_urls` + `confidence`?
- Did I avoid hardcoding secrets?
- Did I stop and ask instead of guessing on anything ambiguous?
- Am I about to start the next phase without explicit confirmation? If yes, stop.