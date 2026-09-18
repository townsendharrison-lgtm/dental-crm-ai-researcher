# School research database

Domain tables live in the separate `school_ai` schema in the existing Supabase
project. They do not replace CRM tables. Student IDs are references to the CRM,
so creating a research score does not create or change a CRM student.

## What each table stores

| Table | Plain-language purpose | Important details |
| --- | --- | --- |
| `schools` | One school and its official website | Name, metadata, ingestion status and timestamps. Python calls the metadata field `details`. Rubric approval lives here too: `rubric_status` (`draft`/`approved`), `rubric_approved_at`, `rubric_approved_by`. |
| `school_documents` | A reference to an uploaded or downloaded school document | Private Supabase bucket/key, filename, media type, SHA-256 content hash, source and parsing status. The same content cannot be registered twice for one school. Files themselves stay in Storage. |
| `school_raw_facts` | Individual facts found in documents or websites | Factor key, JSON value, unit, confidence, source URL or document ID, excerpt and optional page/section. Several sources may supply facts for the same factor. A document reference must belong to the same school. |
| `rubric_factors` | The current value and weight assigned to each school factor | Exactly one row per school/factor, with weight origin, confidence, explanation, source links and update time. |
| `rubric_overrides` | An append-only history of administrator corrections | School/factor, before/after JSON snapshots, editor, reason and time. Database triggers reject changes, deletion and truncation. Another correction adds another history entry. |
| `cross_school_stats` | Numeric reference data for comparing schools | One current row per factor/unit: sample size, min/max/mean/standard deviation, percentiles (including per-school percentile rank and z-score), method, evidence identifiers and computation time. Recomputed by Phase 4 upserts (never deleted). |
| `scoring_runs` | Persisted student fit scores | Snapshot of rubric + breakdown + narrative; not an acceptance probability. |
| `jobs` | Durable async work | Document extraction and research job status; query `GET /jobs?status=failed` for ops. |
| `page_fetch_cache` | TTL cache of allow-listed page text | Upserted by URL hash; never deleted by the service. |
| `provider_usage_events` | Append-only OpenAI/Tavily metering | Per-call tokens/cost estimates scoped by school and UTC day (Phase 9). |

## Phase 4 normalization rules

Phase 4 is pure arithmetic. It does not call an LLM and does not invent missing
school values.

### Building a factor distribution

1. Consider only taxonomy factors with `value_type=number`.
2. For each school, keep the single best numeric fact (highest confidence, then
   latest `extracted_at`).
3. Across those school values compute min, max, mean, population standard
   deviation, distribution percentiles (p10–p90), and each school's average-rank
   percentile (0–100) and z-score.
4. Upsert into `cross_school_stats` keyed by `(factor_key, unit)`. Existing rows
   are updated in place; the service never `DELETE`s or truncates stats.

### Default min / avg / max weighting curve

Used later only when a school has **no explicit stated weight** for a
`min_*` / `avg_*` / `max_*` family (for example `min_gpa`, `avg_gpa`, `max_gpa`):

| Role | Default relative weight | Meaning |
| --- | --- | --- |
| `min_*` | **0.05** (~5%) | Floor / threshold signal |
| `avg_*` | **0.90** (highest) | Primary class-profile signal |
| `max_*` | **0.05** (~5%) | Ceiling / outlier signal |

These three relative weights sum to 1.0 inside the family. Per-factor overrides
are allowed in code; category-level renormalization remains a Phase 5 concern.

### Position curve (min → avg → max)

When mapping a numeric value against a school's published min/avg/max (for later
scoring or inference helpers), a piecewise-linear curve is used:

- value at **min** → **0.05**
- value at **avg** → **1.00** (peak)
- value at **max** → **0.95**

Values outside `[min, max]` clamp to the nearer endpoint. This matches the client
guidance “min ~5%, average highest, max ~95%” as a score shape, distinct from the
family weight table above.

## Phase 5 rubric inference and weight normalization

Generation uses explicit branch logic (not prompt-only tiering):

1. **`stated`** — parse explicit factor weights from `*_stated_weights` raw facts
   (for example `avg_gpa: 25%`). Those weights are kept as absolute fractions.
2. **`cross_school_inferred`** — numeric factors with grounded values and no stated
   weight receive Phase 4 family defaults (`min_*` 0.05 / `avg_*` 0.90 / `max_*`
   0.05, else provisional 0.10) plus reasoning that cites cross-school stats when
   available.
3. **`qualitative_inferred`** — text factors with evidence get a GPT-4o
   `low|medium|high` bucket; code maps buckets to 0.05 / 0.15 / 0.30. The model
   never outputs the numeric weight. `source_urls` must be a subset of evidence
   URLs supplied to the call.

### Category weight reconciliation

Within each of the fourteen categories independently:

- Sum stated weights. If they exceed 1.0, scale them down to 1.0 and leave no
  remaining mass for inferred factors.
- Otherwise remaining mass `1 − stated_sum` is distributed across inferred
  factors in that category in proportion to their provisional weights (or equally
  if provisionals are zero).
- Categories are **not** re-balanced against each other. `manual_override` rows
  are preserved and never overwritten by generation.

Automated rows always require nonempty `source_urls` and a confidence score.
Ungrounded factors are omitted rather than fabricated.

## Phase 6 admin review and approval

- Generated rubrics start as **`draft`**. Regeneration or a manual override resets
  approval back to draft.
- `PATCH /schools/{id}/rubric/{factor}` writes an append-only `rubric_overrides`
  audit row (old/new snapshots, editor, reason) and sets
  `weight_source=manual_override` on `rubric_factors`. Override history is never
  updated or deleted.
- `POST /schools/{id}/rubric/approve` marks the school rubric **`approved`**.
- Scoring is refused until approval (`POST /schools/{id}/score` returns HTTP 409
  for drafts).

## Phase 7 scoring semantics

`POST /schools/{id}/score` accepts:

```json
{"student_id": "crm-external-id", "attributes": {"avg_gpa": 3.6, "avg_dat_aa": 20}}
```

Attribute keys should match taxonomy factor keys. Missing keys are **skipped**
(listed with a reason), never fabricated or silently treated as zero.

The numeric result is a **deterministic fit score** (`deterministic_fit_score_v1`)
on a 0–100 scale:

`score = 100 × Σ(weightᵢ × factor_scoreᵢ) / Σ(weightᵢ)` over non-skipped factors.

`factor_score` is 0–1 from either the Phase 4 min/avg/max position curve (when the
school has min+avg+max values for that stem) or `min(1, student/school_expectation)`.
This is **not** a calibrated acceptance probability. GPT-4o only writes the
narrative explaining the already-computed breakdown.
| `scoring_runs` | A saved scoring result for an external student and school | Numeric score, factor breakdown, rubric snapshot, explanation and timestamp. Snapshots preserve the inputs used even if the current rubric later changes. This phase does not define score meaning or calculate scores. |
| `jobs` | Background-work tracking | Type, optional school, status, payload, result/error, attempt count and timestamps. Used by document extraction and web research workers. |
| `page_fetch_cache` | Cached allow-listed web page text | URL hash primary key, normalized URL, extracted text, content hash, fetch method (`httpx`/`playwright`), fetch time. Upserted on refresh; the service never deletes cache rows. TTL is enforced in application code (`PAGE_CACHE_TTL_DAYS`). |

All IDs are UUIDs generated by the Python models. Timestamps include time zones.
Database triggers refresh `updated_at` on mutable records, including plain SQL
updates. Parent references use `RESTRICT`: removing a parent must never silently
erase linked documents, facts, corrections or scoring history.

## Where a rubric weight comes from

| `weight_source` | Meaning |
| --- | --- |
| `stated` | A source explicitly states the weight or priority. Preserve what the source actually says. |
| `cross_school_inferred` | A later deterministic calculation uses grounded numeric facts and cross-school statistics. This is an inference, not a school-published rule. |
| `qualitative_inferred` | A later model reasons from cited qualitative evidence, such as mission fit. Its explanation and confidence must be retained. |
| `manual_override` | An identified administrator corrects a value or weight. The review workflow must record the before/after snapshot and reason in `rubric_overrides`. |

The automated order is stated evidence first, numeric inference when applicable,
then grounded qualitative inference. An administrator correction is explicitly
labelled; it must not be presented as something the school published.

## Missing information and evidence

- Confidence is required and ranges from 0 to 1; it is not an admission probability.
- Unknown raw values use SQL `NULL` and confidence 0, never an invented substitute.
- Raw document facts require a document reference; web facts require an HTTP(S)
  URL. A document excerpt/page can support later review.
- Automated rubric rows require at least one nonblank source link and a reason.
  An inspected source can support a row with unknown value/weight and confidence
  0. With no source at all, do not create an automated rubric row.
- Weights are finite, nonnegative decimal numbers, or `NULL` when unknown. Their
  scale and normalization are Phase 4/5 decisions; this schema does not invent
  an admission formula. Scoring values likewise are not labelled probabilities.
- The schema requires evidence fields but cannot prove the contents of a URL.
  Extraction and inference validation in later phases must verify grounding.

## Safety and migration scope

Revision `0002_domain_models` adds the eight domain tables. Revision
`0003_page_fetch_cache` adds the page cache table used by web research. Revision
`0004_rubric_status` adds draft/approved rubric columns on `schools`. Revision
`0005_provider_usage` adds append-only `provider_usage_events`. These migrations
do not modify or delete existing CRM records.

The migration does not modify or delete existing CRM records. Downgrade is
deliberately disabled because it would destroy research data and audit history;
future changes must use forward migrations. The extension is shared and is never
removed by this migration.

Hosted tests create a unique empty `school_ai_test_<uuid>` schema inside a single
transaction, run the migration, and exercise synthetic records there. The outer
transaction is rolled back and the schema's absence verified afterward. No
`DROP`, `DELETE` or `TRUNCATE` cleanup statements target existing data. This tests
a fresh application schema in the same hosted database, not a separate server.

**Never delete, truncate, or clean production `school_ai` rows from this service.**
