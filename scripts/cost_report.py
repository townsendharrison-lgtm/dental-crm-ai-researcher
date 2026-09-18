"""Offline cost report for Phase 9 — approximate $ per school ingest and per score.

Uses published GPT-4o standard-text rates (checked 2026-09-17) and measured
token counts from the offline extraction/scoring fixtures (mocked OpenAI usage
objects in unit tests). This is an estimate for ops planning, not an invoice.
"""
from __future__ import annotations

import json
from decimal import Decimal

# Official GPT-4o standard text pricing (same constants as app.clients.llm_client).
INPUT_PER_MILLION = Decimal("2.50")
OUTPUT_PER_MILLION = Decimal("10.00")
TAVILY_PER_SEARCH = Decimal("0.001")


def cost(input_tokens: int, output_tokens: int) -> Decimal:
    return (Decimal(input_tokens) * INPUT_PER_MILLION + Decimal(output_tokens) * OUTPUT_PER_MILLION) / Decimal(1_000_000)


def report() -> dict:
    # Measured from offline fixture usage objects in document extraction tests
    # (typical chunk: ~1.2k input / ~180 output). Scale to a mid-size PDF.
    chunk_calls = 18
    chunk_in, chunk_out = 1200, 180
    extract_tokens_in = chunk_calls * chunk_in
    extract_tokens_out = chunk_calls * chunk_out
    extract_cost = cost(extract_tokens_in, extract_tokens_out)

    # Web research: gap searches + page extracts (allow-listed only).
    tavily_searches = 12
    research_extract_calls = 10
    research_in, research_out = 1400, 200
    research_cost = (
        Decimal(tavily_searches) * TAVILY_PER_SEARCH
        + cost(research_extract_calls * research_in, research_extract_calls * research_out)
    )

    # Qualitative weight inference for uncovered factors.
    qualitative_calls = 6
    qual_in, qual_out = 900, 120
    qualitative_cost = cost(qualitative_calls * qual_in, qualitative_calls * qual_out)

    # Deterministic scoring + one explanation call.
    explain_in, explain_out = 800, 220
    score_cost = cost(explain_in, explain_out)

    per_school = extract_cost + research_cost + qualitative_cost
    return {
        "price_basis": {
            "model": "gpt-4o",
            "input_usd_per_million": float(INPUT_PER_MILLION),
            "output_usd_per_million": float(OUTPUT_PER_MILLION),
            "tavily_usd_per_search": float(TAVILY_PER_SEARCH),
            "price_checked_on": "2026-09-17",
            "note": "Estimates from fixture token counts; not invoice totals.",
        },
        "assumptions": {
            "document_chunks": chunk_calls,
            "tavily_searches": tavily_searches,
            "research_extract_calls": research_extract_calls,
            "qualitative_calls": qualitative_calls,
        },
        "approx_usd_per_school_ingested": float(round(per_school, 4)),
        "approx_usd_per_student_scored": float(round(score_cost, 4)),
        "breakdown_usd": {
            "document_extraction": float(round(extract_cost, 4)),
            "web_research": float(round(research_cost, 4)),
            "qualitative_inference": float(round(qualitative_cost, 4)),
            "score_explanation": float(round(score_cost, 4)),
        },
        "approx_tokens_per_school": {
            "input": extract_tokens_in + research_extract_calls * research_in + qualitative_calls * qual_in,
            "output": extract_tokens_out + research_extract_calls * research_out + qualitative_calls * qual_out,
        },
    }


def main():
    payload = report()
    print(json.dumps(payload, indent=2))
    print(
        f"\nSummary: ~${payload['approx_usd_per_school_ingested']:.4f}/school ingest, "
        f"~${payload['approx_usd_per_student_scored']:.4f}/student score"
    )


if __name__ == "__main__":
    main()
