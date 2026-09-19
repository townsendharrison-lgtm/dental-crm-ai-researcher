"""Trusted-source research: discover official pages → deep crawl → extract taxonomy gaps.

Search is used only to *find* URLs on the school's official domain (and ADEA).
Facts are extracted only from those trusted pages — never from blogs/rankers.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import time

from app.clients.fetch_client import DocumentChunk, normalize_text
from app.clients.llm_client import LLMClient, ExtractionFailed
from app.clients.search_client import SearchClient, SearchError
from app.clients.web_fetch_client import WebFetchClient, WebFetchError, normalize_url, prioritize_crawl_urls
from app.db.session import ConfigurationMissing
from app.factor_taxonomy import FactorTaxonomy
from app.source_allowlist import (
    DomainRejected,
    allowed_hosts_for_school,
    hostname_of,
    is_allowed_url,
    registrable_suffix,
    trusted_discovery_hosts,
)

# Topic queries used only to discover official admissions-related pages.
DISCOVERY_TOPICS = (
    "admissions requirements DDS DMD",
    "how to apply dental school",
    "class profile entering class GPA DAT statistics",
    "prerequisites coursework requirements",
    "tuition fees cost of attendance",
    "mission vision values",
    "curriculum dental education",
    "shadowing clinical experience requirements",
)

# Map URL path tokens → taxonomy categories to prioritize on that page.
_URL_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("tuition", "School Fit"),
    ("aid", "School Fit"),
    ("cost", "School Fit"),
    ("mission", "School Fit"),
    ("about", "School Fit"),
    ("curriculum", "School Fit"),
    ("prerequisite", "Academics"),
    ("requirement", "Academics"),
    ("gpa", "Academics"),
    ("dat", "DAT"),
    ("admission", "Academics"),
    ("apply", "Application Strategy"),
    ("class-profile", "Academics"),
    ("profile", "Academics"),
    ("shadow", "Shadowing"),
    ("experience", "Dental Experience"),
    ("service", "Service"),
    ("volunteer", "Service"),
    ("leadership", "Leadership"),
    ("research", "Research"),
    ("extracurricular", "Extracurriculars"),
    ("interview", "Application Quality"),
    ("essay", "Application Quality"),
    ("holistic", "Context / Holistic Factors"),
)


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


def categories_for_page(url: str, remaining: set[str], taxonomy: FactorTaxonomy, *, limit: int) -> list[str]:
    """Pick which taxonomy categories to extract from this page."""
    by_cat: dict[str, int] = defaultdict(int)
    for key in remaining:
        definition = taxonomy.by_key.get(key)
        if definition:
            by_cat[definition.category] += 1
    if not by_cat:
        return []
    # Match path segments (avoid 'aid' matching inside 'admissions').
    path = url.lower()
    segments = {part for part in path.replace("?", "/").replace("&", "/").replace("=", "/").split("/") if part}
    hinted = []
    for token, category in _URL_CATEGORY_HINTS:
        if category not in by_cat or category in hinted:
            continue
        if token in segments or any(token in seg for seg in segments if len(token) >= 5):
            hinted.append(category)
    rest = sorted(
        (c for c in by_cat if c not in hinted),
        key=lambda c: (-by_cat[c], c),
    )
    return (hinted + rest)[:limit]


class WebResearchAgent:
    def __init__(self, settings, search: SearchClient, fetch: WebFetchClient, llm: LLMClient,
                 taxonomy: FactorTaxonomy):
        self.settings = settings
        self.search = search
        self.fetch = fetch
        self.llm = llm
        self.taxonomy = taxonomy

    async def _extract_page(
        self, page, *, remaining: set[str], writes: list[ResearchFactWrite], outcomes: list[dict],
        extract_calls: list[int], budget_deadline: float, label: str,
        max_extract_calls: int | None = None,
        categories_per_page: int | None = None,
    ) -> list[str]:
        extract_cap = max_extract_calls or self.settings.research_max_extract_calls
        cat_limit = categories_per_page or self.settings.research_discover_categories_per_page
        if not remaining or extract_calls[0] >= extract_cap:
            return []
        if time.monotonic() >= budget_deadline:
            return []

        chunks = chunk_page(
            page.text, url=page.url, title=page.title,
            chunk_chars=self.settings.research_chunk_chars,
        )[: self.settings.research_max_chunks_per_page]
        page_writes: list[str] = []
        categories = categories_for_page(page.url, remaining, self.taxonomy, limit=cat_limit)

        for category in categories:
            cat_keys = {
                key for key in remaining
                if (self.taxonomy.by_key.get(key) and self.taxonomy.by_key[key].category == category)
            }
            if not cat_keys:
                continue
            gap_taxonomy = subset_taxonomy(self.taxonomy, cat_keys)
            for chunk in chunks:
                if extract_calls[0] >= extract_cap:
                    break
                if time.monotonic() >= budget_deadline:
                    break
                if not remaining & cat_keys:
                    break
                try:
                    extract_calls[0] += 1
                    extraction = await self.llm.extract(chunk, gap_taxonomy)
                except ExtractionFailed as exc:
                    outcomes.append({
                        "factor_key": label, "url": page.url, "status": "extract_failed",
                        "error_type": type(exc).__name__, "cached": page.cached,
                        "category": category,
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
            "extracted_keys": page_writes, "categories": categories,
            "link_count": len(getattr(page, "links", ()) or ()),
        })
        return page_writes

    async def _deep_crawl(
        self, *, seed_urls: list[str], allowed_hosts: frozenset[str], remaining: set[str],
        writes: list[ResearchFactWrite], outcomes: list[dict], extract_calls: list[int],
        budget_deadline: float, force_refresh: bool, label: str,
        max_pages: int | None = None, max_extract_calls: int | None = None,
        categories_per_page: int | None = None,
    ) -> dict:
        """BFS crawl from one or more seeds until gaps filled or budgets hit."""
        queue: deque[str] = deque()
        seen: set[str] = set()
        for seed in seed_urls:
            try:
                normalized = normalize_url(seed)
            except Exception:
                normalized = seed
            if normalized not in seen:
                queue.append(normalized)
                seen.add(normalized)
        # Re-seed seen incorrectly — we want seeds in queue but not marked seen until fetch.
        # Fix: only track visited after pop.
        queue = deque()
        seen = set()
        for seed in seed_urls:
            try:
                normalized = normalize_url(seed)
            except Exception:
                normalized = seed
            if normalized not in queue:
                queue.append(normalized)

        pages_fetched = 0
        stopped_reason = None
        page_cap = max_pages or self.settings.research_max_pages
        extract_cap = max_extract_calls or self.settings.research_max_extract_calls

        while queue and remaining:
            if pages_fetched >= page_cap:
                stopped_reason = stopped_reason or "page_budget"
                break
            if extract_calls[0] >= extract_cap:
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
                max_extract_calls=extract_cap, categories_per_page=categories_per_page,
            )

            for link in prioritize_crawl_urls(list(page.links or ())):
                if link not in seen and link not in queue:
                    queue.append(link)

        return {
            "pages_fetched": pages_fetched,
            "urls_seen": len(seen),
            "stopped_reason": stopped_reason,
            "queue_remaining": len(queue),
            "seeds": seed_urls,
        }

    async def discover_trusted_seeds(
        self, *, school_name: str, official_url: str,
    ) -> dict:
        """Search for admissions pages on the official domain (+ ADEA). Returns seed URLs."""
        discovery_hosts = trusted_discovery_hosts(official_url)
        # Crawl allow-list is broader (full school allow-list) once seeds are chosen.
        crawl_hosts = allowed_hosts_for_school(official_url)
        seeds: list[str] = []
        seen: set[str] = set()
        rejected: list[dict] = []
        queries_run: list[str] = []

        # Always start from the official URL itself.
        if official_url.startswith("http"):
            try:
                seeds.append(normalize_url(official_url))
                seen.add(normalize_url(official_url))
            except Exception:
                seeds.append(official_url)
                seen.add(official_url)

        suffix = registrable_suffix(hostname_of(official_url))
        max_queries = self.settings.research_discover_max_queries
        max_seeds = self.settings.research_discover_max_seeds
        per_query = self.settings.research_discover_max_results_per_query

        for topic in DISCOVERY_TOPICS[:max_queries]:
            if len(seeds) >= max_seeds:
                break
            query = f"{school_name} dental school {topic} site:{suffix}"
            queries_run.append(query)
            try:
                hits = await self.search.search(
                    query, allowed_hosts=discovery_hosts, max_results=per_query,
                )
            except (SearchError, ConfigurationMissing, DomainRejected) as exc:
                rejected.append({"query": query, "reason": type(exc).__name__, "detail": str(exc)[:200]})
                continue
            for hit in hits:
                if not is_allowed_url(hit.url, discovery_hosts):
                    rejected.append({"url": hit.url, "reason": "off_trusted_hosts"})
                    continue
                try:
                    normalized = normalize_url(hit.url)
                except Exception:
                    normalized = hit.url
                if normalized in seen:
                    continue
                seen.add(normalized)
                seeds.append(normalized)
                if len(seeds) >= max_seeds:
                    break

        return {
            "seeds": seeds,
            "queries": queries_run,
            "rejected": rejected,
            "discovery_hosts": sorted(discovery_hosts),
            "crawl_hosts": sorted(crawl_hosts),
        }

    async def research_discover(
        self, *, school_name: str, official_url: str, gaps: list[GapFactor],
        force_refresh: bool = False,
    ) -> dict:
        """Discover trusted official pages, then deep-crawl them for taxonomy gaps."""
        if not official_url or not official_url.startswith("http"):
            return {
                "gaps": [g.__dict__ for g in gaps],
                "remaining_gaps": [g.key for g in gaps],
                "rejected_urls": [{"url": official_url or "", "reason": "missing_official_url"}],
                "outcomes": [],
                "writes": [],
                "discovered_seeds": [],
                "budget": {"stopped_reason": "missing_official_url"},
            }

        discovery = await self.discover_trusted_seeds(school_name=school_name, official_url=official_url)
        seeds = discovery["seeds"]
        if not seeds:
            return {
                "gaps": [g.__dict__ for g in gaps],
                "remaining_gaps": [g.key for g in gaps],
                "rejected_urls": discovery["rejected"],
                "outcomes": [],
                "writes": [],
                "discovered_seeds": [],
                "discovery": discovery,
                "budget": {"stopped_reason": "no_seeds"},
            }

        # Aim at the full gap set (up to taxonomy size).
        selected = prioritize_gaps(gaps, self.taxonomy, limit=self.settings.research_max_gaps)
        remaining = {g.key for g in selected}
        outcomes: list[dict] = []
        writes: list[ResearchFactWrite] = []
        extract_calls = [0]
        timeout = self.settings.research_discover_timeout_seconds
        budget_deadline = time.monotonic() + timeout
        crawl_hosts = allowed_hosts_for_school(official_url)

        crawl_stats = await self._deep_crawl(
            seed_urls=seeds,
            allowed_hosts=crawl_hosts,
            remaining=remaining,
            writes=writes,
            outcomes=outcomes,
            extract_calls=extract_calls,
            budget_deadline=budget_deadline,
            force_refresh=force_refresh,
            label="discover_trusted",
            max_pages=self.settings.research_discover_max_pages,
            max_extract_calls=self.settings.research_discover_max_extract_calls,
            categories_per_page=self.settings.research_discover_categories_per_page,
        )
        return {
            "gaps": [g.__dict__ for g in selected],
            "remaining_gaps": sorted(remaining),
            "rejected_urls": discovery["rejected"],
            "outcomes": outcomes,
            "writes": writes,
            "discovered_seeds": seeds,
            "discovery": {
                "queries": discovery["queries"],
                "discovery_hosts": discovery["discovery_hosts"],
                "seed_count": len(seeds),
            },
            "budget": {
                "extract_calls": extract_calls[0],
                "timeout_seconds": timeout,
                "mode": "discover_trusted",
                **crawl_stats,
            },
        }

    async def research_gaps(
        self, *, school_name: str, official_url: str, gaps: list[GapFactor], force_refresh: bool = False,
    ) -> dict:
        """Deep-crawl the school's official URL (no discovery search)."""
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
        """Deep-crawl an admin-provided URL and its same-host subpages."""
        _ = school_name
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
            seed_urls=[url], allowed_hosts=allowed, remaining=remaining,
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
