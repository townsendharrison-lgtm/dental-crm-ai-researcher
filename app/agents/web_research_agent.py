"""Gap analysis and web research orchestration (search → allow-list → fetch → extract)."""
from dataclasses import dataclass

from app.clients.fetch_client import DocumentChunk, normalize_text
from app.clients.llm_client import LLMClient, ExtractionFailed
from app.clients.search_client import SearchClient
from app.clients.web_fetch_client import WebFetchClient, WebFetchError
from app.factor_taxonomy import FactorDefinition, FactorTaxonomy
from app.source_allowlist import DomainRejected, allowed_hosts_for_school, is_allowed_url


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
        conf = best.get(factor.key)
        if conf is None:
            gaps.append(GapFactor(factor.key, factor.category, factor.description, "missing", None))
        elif conf < confidence_floor:
            gaps.append(GapFactor(factor.key, factor.category, factor.description, "low_confidence", conf))
    return gaps


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
        self.search = search
        self.fetch = fetch
        self.llm = llm
        self.taxonomy = taxonomy

    async def research_gaps(
        self, *, school_name: str, official_url: str, gaps: list[GapFactor], force_refresh: bool = False,
    ) -> dict:
        allowed = allowed_hosts_for_school(official_url)
        remaining = {gap.key for gap in gaps[: self.settings.research_max_gaps]}
        outcomes = []
        writes: list[ResearchFactWrite] = []
        rejected = []

        for gap in gaps[: self.settings.research_max_gaps]:
            if gap.key not in remaining:
                continue
            factor: FactorDefinition = self.taxonomy.by_key[gap.key]
            query = self.search.build_query(school_name, factor.key, factor.description)
            hits = await self.search.search(
                query, allowed_hosts=allowed, max_results=self.settings.research_max_urls_per_gap + 2,
            )
            used_urls = []
            for hit in hits:
                if not is_allowed_url(hit.url, allowed):
                    rejected.append({"url": hit.url, "reason": "off_allow_list"})
                    continue
                if len(used_urls) >= self.settings.research_max_urls_per_gap:
                    break
                try:
                    page = await self.fetch.fetch(hit.url, allowed_hosts=allowed, force_refresh=force_refresh)
                except (WebFetchError, DomainRejected) as exc:
                    outcomes.append({"factor_key": gap.key, "url": hit.url, "status": "fetch_failed",
                                     "error_type": type(exc).__name__})
                    continue
                used_urls.append(page.url)
                if not remaining:
                    break
                gap_taxonomy = subset_taxonomy(self.taxonomy, remaining)
                page_writes = []
                for chunk in chunk_page(page.text, url=page.url, title=page.title,
                                        chunk_chars=self.settings.research_chunk_chars):
                    try:
                        extraction = await self.llm.extract(chunk, gap_taxonomy)
                    except ExtractionFailed as exc:
                        outcomes.append({"factor_key": gap.key, "url": page.url, "status": "extract_failed",
                                         "error_type": type(exc).__name__, "cached": page.cached})
                        continue
                    for fact in extraction.result.facts:
                        if fact.factor_key not in remaining:
                            continue
                        write = ResearchFactWrite(
                            factor_key=fact.factor_key, value=fact.value, unit=fact.unit,
                            confidence=fact.confidence, raw_text_snippet=fact.raw_text_snippet,
                            source_url=page.url, section=chunk.section,
                        )
                        writes.append(write)
                        page_writes.append(fact.factor_key)
                        if fact.value is not None and fact.confidence >= self.settings.research_confidence_floor:
                            remaining.discard(fact.factor_key)
                outcomes.append({
                    "factor_key": gap.key, "url": page.url, "status": "fetched",
                    "cached": page.cached, "fetch_method": page.fetch_method,
                    "extracted_keys": page_writes,
                })
                if gap.key not in remaining:
                    break

        return {
            "gaps": [gap.__dict__ for gap in gaps[: self.settings.research_max_gaps]],
            "remaining_gaps": sorted(remaining),
            "rejected_urls": rejected,
            "outcomes": outcomes,
            "writes": writes,
        }
