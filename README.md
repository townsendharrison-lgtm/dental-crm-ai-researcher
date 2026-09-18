# School AI Service

FastAPI microservice that researches dental-school admissions factors, builds
reviewable rubrics, and scores CRM students with a **deterministic fit score**
(not an acceptance probability). Stack: **OpenAI `gpt-4o`**, **Supabase**
(Postgres `school_ai` schema, Storage, pgmq queues). No Redis/Celery/S3/Docker.

Phases 0–9 are complete for this stack. See [SCHEMA.md](SCHEMA.md),
[BUILD_PLAN.md](BUILD_PLAN.md), and per-phase `PHASE*_STATUS.md` notes.

## Setup

From `school-ai-service`:

```powershell
uv sync --locked
Copy-Item .env.example .env   # only if .env does not already exist
```

Fill `.env` (gitignored). Never paste secrets into logs or chat.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Supabase direct or **session** pooler Postgres URL (not port 6543). Percent-encode password specials. |
| `DATABASE_SSL` / `DATABASE_CA_FILE` | TLS verify; this repo uses `certs/prod-ca-2021.crt`. |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Hosted project; server-only key. |
| `SUPABASE_STORAGE_BUCKET` | Private bucket (default `school-ai-documents`). |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | `gpt-4o` for extraction, qualitative weights, score narration. |
| `TAVILY_API_KEY` | Allow-listed web search. |
| `INTERNAL_API_SECRET` | Shared secret; Node sends `X-School-AI-Key`. Empty = local/offline only. |
| `OPENAI_DAILY_*` / `TAVILY_DAILY_*` | Per-school UTC-day call/cost caps (Phase 9). |
| `DB_SCHEMA` / queue names | Defaults `school_ai`, `school_ai_phase0`, `school_ai_documents`, `school_ai_research`. |

```powershell
uv run python -m app.bootstrap
uv run alembic upgrade head
uv run alembic current
```

Apply through revision **`0005_provider_usage`**. Downgrades that destroy data are
disabled. **Never `DELETE` / `TRUNCATE` / clean production rows.**

## Run

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# workers (separate terminals):
uv run python -m app.document_cli work
uv run python -m app.research_cli work
```

- Health: `GET /health` (public). Docs: `/docs`.
- All other routes require `X-School-AI-Key` when `INTERNAL_API_SECRET` is set.
- Node client: `backend/src/services/schoolAiClient.ts` (polling `waitForJob`).
- Frontend binding: browser → Node `/api/school-ai/*` → Python (see `PHASE8_INTEGRATION.md`).
  Admin sidebar **Admission Research** (`/admin/admission-research`).

## End-to-end: add a school

1. **Create school** — `POST /schools` `{ "name", "official_url" }`.
2. **Upload PDF/DOCX** — `POST /schools/{id}/documents` (multipart). Poll
   `GET /jobs/{job_id}` until `succeeded`/`failed`. Facts:
   `GET /documents/{document_id}/facts`.
3. **Web research gaps** — `POST /schools/{id}/research`, same job polling.
4. **Normalize** (optional, fleet-wide) — `POST /normalization/recompute`.
5. **Generate rubric** — `POST /schools/{id}/rubric/generate`.
6. **Review** — `GET /schools/{id}/rubric`. Override with
   `PATCH /schools/{id}/rubric/{factor_key}` (audit row + `manual_override`).
7. **Approve** — `POST /schools/{id}/rubric/approve` `{ "editor": "..." }`.
8. **Score** — `POST /schools/{id}/score` with CRM `student_id` + attributes.

Failed jobs for alerting: `GET /jobs?status=failed`. Process usage snapshot:
`GET /usage`.

### Interpreting confidence and `weight_source`

| `weight_source` | Meaning |
| --- | --- |
| `stated` | School published an explicit weight; highest trust. |
| `cross_school_inferred` | Numeric value from facts + Phase 4 default min/avg/max curve. |
| `qualitative_inferred` | GPT-4o bucket (`low`/`medium`/`high`) mapped in code; requires `source_urls`. |
| `manual_override` | Admin PATCH; preserved across regenerate. |

`confidence` is 0–1 evidence strength for that row (0 + null value = inspected but
unknown). Scoring skips missing student attributes; the LLM only **explains** the
already-computed fit score.

## Ops / cost guardrails

- Per-school daily caps block further OpenAI/Tavily calls (`BudgetExceeded` →
  HTTP 429 on sync routes; failed job on workers).
- Structured `external_call` logs include tokens/cost estimates for OpenAI.
- Durable append-only `provider_usage_events` (migration 0005).
- Cost estimate script (fixture-based, not an invoice):

```powershell
uv run python -m scripts.cost_report
```

## Tests

```powershell
uv run pytest -q
# Hosted opt-in: RUN_INTEGRATION_TESTS=true
```

Offline tests mock providers and roll back synthetic schemas. They do not clean
production data.

## Phase status

Phases **0–9 complete**. Final ops report: [PHASE9_STATUS.md](PHASE9_STATUS.md).
Integration walkthrough: [PHASE8_INTEGRATION.md](PHASE8_INTEGRATION.md).
