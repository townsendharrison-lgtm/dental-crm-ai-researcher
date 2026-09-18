# Phase 9 — complete (hardening & ops)

Production data was not deleted, truncated, or cleaned.

## Delivered

- **Cost/rate guardrails:** per-school UTC-day caps for OpenAI and Tavily
  (`OPENAI_DAILY_*`, `TAVILY_DAILY_*`). `BudgetExceeded` → HTTP 429 on sync
  routes; failed job on document/research workers.
- **Usage metering:** in-memory counters + append-only
  `provider_usage_events` (migration `0005_provider_usage`). Structured
  `external_call` logs retain tokens/cost estimates.
- **Failed-job alerting path:** `GET /jobs?status=failed` (+ optional
  `school_id`, `limit`). Node client: `listJobs`.
- **Usage snapshot:** `GET /usage`.
- **Cost report:** `uv run python -m scripts.cost_report`
- **README** rewritten for setup, e2e school flow, rubric review, confidence /
  `weight_source`, and ops caps.

## Cost report (fixture-based estimates)

```text
~ $0.1741 per school ingested
~ $0.0042 per student scored
(GPT-4o standard text rates checked 2026-09-17; not an invoice)
```

## Actual test results

```text
uv run pytest -q --tb=short
136 passed, 27 skipped, 2 warnings in 7.99s

uv run python -m scripts.cost_report
Summary: ~$0.1741/school ingest, ~$0.0042/student score
```

## STOP — Final report

Phase 9 Definition of Done is satisfied. Phases **0–9** are complete for the
OpenAI + Supabase stack.
