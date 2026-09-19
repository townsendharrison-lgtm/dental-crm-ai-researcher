"""Admin-provided URL crawl (deep same-host subpages) → extract grounded facts.

Multi-site Tavily search was removed — inaccurate third-party pages are no longer fetched.
Admins upload documents or crawl an explicit URL (typically the school's official site).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time

from app.clients.fetch_client import DocumentChunk, normalize_text
from app.clients.llm_client import LLMClient, ExtractionFailed
from app.clients.search_client import SearchClient
from app.clients.web_fetch_client import WebFetchClient, WebFetchError, prioritize_crawl_urls
from app.factor_taxonomy import FactorTaxonomy
from app.source_allowlist import DomainRejected, allowed_hosts_for_school, hostname_of


@dataclass(frozen=True)
class GapFactor:
    key: str
    category: str
    description: str
    reason: str
    best_confidence: float | None


@dataclass(frozen=True)
class ResearchFactWrite:
    factor_key: str
    value: float | str | None
    unit: str | None
    confidence: float
    raw_text_snippet: str
    source_url: str
    page_number: int | None = None
    section: str | None = None


def compute_gaps(taxonomy: FactorTaxonomy, facts: list[dict], *, confidence_floor: float) -> list[GapFactor]:
    best: dict[str, float] = {}
    for row in facts:
        key = row["factor_key"]
        conf = float(row["confidence"])
        if key not in best or conf > best[key]:
            best[key] = conf
    gaps = []
    for factor in taxonomy.factors:
        # Meta weight-evidence slots are not useful crawl targets.
        if factor.key.endswith("_stated_weights"):
            continue
        conf = best.get(factor.key)
        if conf is None:
            gaps.append(GapFactor(factor.key, factor.category, factor.description, "missing", None))
        elif conf < confidence_floor:
            gaps.append(GapFactor(factor.key, factor.category, factor.description, "low_confidence", conf))
    return gaps


def prioritize_gaps(gaps: list[GapFactor], taxonomy: FactorTaxonomy, *, limit: int) -> list[GapFactor]:
    """Prefer numeric / scoring-eligible admissions stats over soft policy text."""

    def rank(gap: GapFactor) -> tuple:
        definition = taxonomy.by_key.get(gap.key)
        numeric = 0 if definition and definition.value_type == "number" else 1
        eligible = 0 if definition and definition.scoring_eligible else 1
        missing = 0 if gap.reason == "missing" else 1
        return (numeric, eligible, missing, gap.category, gap.key)

    return sorted(gaps, key=rank)[:limit]


def subset_taxonomy(taxonomy: FactorTaxonomy, keys: set[str]) -> FactorTaxonomy:
    factors = tuple(f for f in taxonomy.factors if f.key in keys)
    categories = tuple(dict.fromkeys(f.category for f in factors))
    return FactorTaxonomy(version=f"{taxonomy.version}-gaps", categories=categories, factors=factors)


def chunk_page(text: str, *, url: str, title: str | None, chunk_chars: int) -> list[DocumentChunk]:
    cleaned = normalize_text(text)
    parts = []
    start = 0
    while start < len(cleaned):
        parts.append(cleaned[start:start + chunk_chars])
        start += chunk_chars
    section = title or url
    return [DocumentChunk(index=i, text=part, page_number=None, section=section, used_ocr=False)
            for i, part in enumerate(parts) if part]


class WebResearchAgent:
    def __init__(self, settings, search: SearchClient, fetch: WebFetchClient, llm: LLMClient,
                 taxonomy: FactorTaxonomy):
        self.settings = settings
        self.search = search  # retained for DI/tests; multi-site search is disabled
        self.fetch = fetch
        self.llm = llm
        self.taxonomy = taxonomy

    async def _extract_page(
        self, page, *, remaining: set[str], writes: list[ResearchFactWrite], outcomes: list[dict],
        extract_calls: list[int], budget_deadline: float, label: str,
    ) -> list[str]:
        if not remaining or extract_calls[0] >= self.settings.research_max_extract_calls:
            return []
        if time.monotonic() >= budget_deadline:
            return []
        gap_taxonomy = subset_taxonomy(self.taxonomy, remaining)
        chunks = chunk_page(
            page.text, url=page.url, title=page.title,
            chunk_chars=self.settings.research_chunk_chars,
        )[: self.settings.research_max_chunks_per_page]
        page_writes: list[str] = []
        for chunk in chunks:
            if extract_calls[0] >= self.settings.research_max_extract_calls:
                break
            if time.monotonic() >= budget_deadline:
                break
            if not remaining:
                break
            try:
                extract_calls[0] += 1
                extraction = await self.llm.extract(chunk, gap_taxonomy)
            except ExtractionFailed as exc:
                outcomes.append({
                    "factor_key": label, "url": page.url, "status": "extract_failed",
                    "error_type": type(exc).__name__, "cached": page.cached,
                })
                continue
            for fact in extraction.result.facts:
                if fact.factor_key not in remaining:
                    continue
                writes.append(ResearchFactWrite(
                    factor_key=fact.factor_key, value=fact.value, unit=fact.unit,
                    confidence=fact.confidence, raw_text_snippet=fact.raw_text_snippet,
                    source_url=page.url, section=chunk.section,
                ))
                page_writes.append(fact.factor_key)
                if fact.value is not None and fact.confidence >= self.settings.research_confidence_floor:
                    remaining.discard(fact.factor_key)
        outcomes.append({
            "factor_key": label, "url": page.url, "status": "fetched",
            "cached": page.cached, "fetch_method": page.fetch_method,
            "extracted_keys": page_writes, "link_count": len(getattr(page, "links", ()) or ()),
        })
        return page_writes

    async def _deep_crawl(
        self, *, seed_url: str, allowed_hosts: frozenset[str], remaining: set[str],
        writes: list[ResearchFactWrite], outcomes: list[dict], extract_calls: list[int],
        budget_deadline: float, force_refresh: bool, label: str,
    ) -> dict:
        """BFS same-host crawl from seed_url until gaps filled or page/LLM/time budgets hit."""
        queue: deque[str] = deque([seed_url])
        seen: set[str] = set()
        pages_fetched = 0
        stopped_reason = None
        max_pages = self.settings.research_max_pages

        while queue and remaining:
            if pages_fetched >= max_pages:
                stopped_reason = stopped_reason or "page_budget"
                break
            if extract_calls[0] >= self.settings.research_max_extract_calls:
                stopped_reason = stopped_reason or "extract_budget"
                break
            if time.monotonic() >= budget_deadline:
                stopped_reason = stopped_reason or "time_budget"
                break

            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            try:
                page = await self.fetch.fetch(
                    url, allowed_hosts=allowed_hosts, force_refresh=force_refresh,
                    collect_links=True,
                )
            except (WebFetchError, DomainRejected) as exc:
                outcomes.append({
                    "factor_key": label, "url": url, "status": "fetch_failed",
                    "error_type": type(exc).__name__,
                })
                continue

            pages_fetched += 1
            await self._extract_page(
                page, remaining=remaining, writes=writes, outcomes=outcomes,
                extract_calls=extract_calls, budget_deadline=budget_deadline, label=label,
            )

            for link in prioritize_crawl_urls(list(page.links or ())):
                if link not in seen:
                    queue.append(link)

        return {
            "pages_fetched": pages_fetched,
            "urls_seen": len(seen),
            "stopped_reason": stopped_reason,
            "queue_remaining": len(queue),
        }

    async def research_gaps(
        self, *, school_name: str, official_url: str, gaps: list[GapFactor], force_refresh: bool = False,
    ) -> dict:
        """Deep-crawl the school's official URL (no multi-site search)."""
        if not official_url or not official_url.startswith("http"):
            return {
                "gaps": [g.__dict__ for g in gaps],
                "remaining_gaps": [g.key for g in gaps],
                "rejected_urls": [{"url": official_url or "", "reason": "missing_official_url"}],
                "outcomes": [],
                "writes": [],
                "budget": {"pages_fetched": 0, "stopped_reason": "missing_official_url"},
            }
        return await self.research_url(
            school_name=school_name, official_url=official_url, url=official_url,
            gaps=gaps, force_refresh=force_refresh,
        )

    async def research_url(
        self, *, school_name: str, official_url: str, url: str, gaps: list[GapFactor],
        force_refresh: bool = False,
    ) -> dict:
        """Deep-crawl an admin-provided URL and its same-host subpages.

        Only gap factors are extracted — never re-writes covered keys (caller filters).
        """
        _ = school_name  # reserved for logging / future query hints
        try:
            target_host = hostname_of(url)
        except DomainRejected:
            return {"gaps": [], "remaining_gaps": [], "rejected_urls": [{"url": url, "reason": "invalid_url"}],
                    "outcomes": [], "writes": []}
        allowed = allowed_hosts_for_school(official_url, extra_domains=(target_host,))
        selected = prioritize_gaps(gaps, self.taxonomy, limit=self.settings.research_max_gaps)
        gap_keys = {gap.key for gap in selected}
        if not gap_keys:
            return {
                "gaps": [],
                "remaining_gaps": [],
                "rejected_urls": [],
                "outcomes": [{"url": url, "status": "skipped", "reason": "no_gaps"}],
                "writes": [],
                "budget": {"pages_fetched": 0, "stopped_reason": "no_gaps"},
            }

        outcomes: list[dict] = []
        writes: list[ResearchFactWrite] = []
        remaining = set(gap_keys)
        extract_calls = [0]
        budget_deadline = time.monotonic() + self.settings.research_job_timeout_seconds
        crawl_stats = await self._deep_crawl(
            seed_url=url, allowed_hosts=allowed, remaining=remaining,
            writes=writes, outcomes=outcomes, extract_calls=extract_calls,
            budget_deadline=budget_deadline, force_refresh=force_refresh, label="crawl_url",
        )
        return {
            "gaps": [g.__dict__ for g in selected],
            "remaining_gaps": sorted(remaining),
            "rejected_urls": [],
            "outcomes": outcomes,
            "writes": writes,
            "budget": {
                "extract_calls": extract_calls[0],
                "timeout_seconds": self.settings.research_job_timeout_seconds,
                **crawl_stats,
            },
        }
