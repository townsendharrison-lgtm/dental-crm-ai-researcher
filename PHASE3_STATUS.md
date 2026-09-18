# Phase 3 — complete (web research)

Phase 3 was authorized after Phase 2. Production rows were not deleted, truncated,
or cleaned. Cache rows are upserted only.

## Implemented and tested

- Domain allow-list (`app/source_allowlist.py`): school official host (+ suffix),
  ADEA, and a short curated dental-education list — enforced in code.
- Tavily `SearchClient` via httpx with logging/retries; off-list URLs dropped.
- `WebFetchClient`: httpx fetch → trafilatura extract → optional Playwright
  fallback when text is too short; TTL page cache table `page_fetch_cache`.
- Gap analysis: only missing/low-confidence taxonomy factors are researched.
- Durable workflow: `POST /schools/{id}/research` → `research_school` job →
  pgmq → worker → grounded `school_raw_facts` with `source_type=web` + `source_url`.
- CLI: `python -m app.research_cli init-queue|enqueue|job|work`
- Migration `0003_page_fetch_cache` (additive; downgrade disabled).

## Actual results

```text
uv run pytest -q --tb=short
108 passed, 27 skipped, 2 warnings in 6.73s
```

Offline tests mock Tavily, httpx, and OpenAI. No paid provider calls.

## Operator note

Apply the additive migration before live research:

```powershell
uv run alembic upgrade head
uv run python -m app.bootstrap --queue-only
```

Playwright is optional. If it is not installed, short static extracts fail that
URL instead of inventing content.

## STOP

Phase 3 Definition of Done is satisfied. Confirm before Phase 4 (normalization).
