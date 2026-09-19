"""Phase 3 web research: allow-list, gaps, mocked Tavily/httpx, page cache."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.web_research_agent import WebResearchAgent, compute_gaps, subset_taxonomy
from app.clients.search_client import SearchClient
from app.clients.web_fetch_client import FetchedPage, WebFetchClient, normalize_url, url_hash
from app.factor_taxonomy import FactorTaxonomy
from app.main import create_app
from app.source_allowlist import DomainRejected, allowed_hosts_for_school, assert_allowed_url, is_allowed_url
from app.tests.test_api import dependency
from app.tests.test_document_extraction import fact, make_llm, response


@pytest.fixture
def taxonomy():
    return FactorTaxonomy.model_validate({
        "version": "synthetic-research",
        "categories": ["Academics", "School Fit"],
        "factors": [
            {"key": "avg_gpa", "category": "Academics", "description": "Average GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "mission_alignment", "category": "School Fit",
             "description": "Mission alignment", "value_type": "text", "unit": None},
        ],
    })


def test_allow_list_accepts_school_and_curated_rejects_others():
    allowed = allowed_hosts_for_school("https://admissions.example.edu/dds")
    assert is_allowed_url("https://admissions.example.edu/requirements", allowed)
    assert is_allowed_url("https://www.adea.org/godental", allowed)
    assert not is_allowed_url("https://random-blog.example/post", allowed)
    with pytest.raises(DomainRejected):
        assert_allowed_url("https://evil.example/page", allowed)


def test_gap_analysis_only_lists_missing_and_low_confidence(taxonomy):
    facts = [
        {"factor_key": "avg_gpa", "confidence": 0.95},
        {"factor_key": "mission_alignment", "confidence": 0.2},
    ]
    gaps = compute_gaps(taxonomy, facts, confidence_floor=0.5)
    assert [g.key for g in gaps] == ["mission_alignment"]
    assert gaps[0].reason == "low_confidence"
    empty = compute_gaps(taxonomy, [], confidence_floor=0.5)
    assert {g.key for g in empty} == {"avg_gpa", "mission_alignment"}


async def test_search_filters_off_allow_list_domains(settings):
    allowed = frozenset({"adea.org", "school.edu"})
    payload = {
        "results": [
            {"url": "https://adea.org/ok", "title": "ADEA", "content": "ok"},
            {"url": "https://spam.invalid/nope", "title": "Spam", "content": "no"},
            {"url": "https://school.edu/admissions", "title": "School", "content": "yes"},
        ]
    }

    async def handler(request: httpx.Request):
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    search = SearchClient(settings, client=client)
    try:
        hits = await search.search("fixture query", allowed_hosts=allowed, max_results=5)
    finally:
        await search.close()
    assert [hit.url for hit in hits] == ["https://adea.org/ok", "https://school.edu/admissions"]


async def test_page_cache_skips_second_http_fetch(settings):
    url = "https://school.edu/admissions"
    text = "Average overall GPA 3.7. " * 40
    calls = {"get": 0}
    cache = {}

    async def handler(request: httpx.Request):
        calls["get"] += 1
        return httpx.Response(200, text=f"<html><body>{text}</body></html>")

    class FakeDB:
        async def transaction(self, operation, callback):
            connection = SimpleNamespace(execute=AsyncMock())
            if operation == "read_page_cache":
                row = cache.get(url_hash(url))

                class Result:
                    def mappings(self):
                        return self

                    def first(self):
                        return row

                connection.execute = AsyncMock(return_value=Result())
                return await callback(connection)
            if operation == "write_page_cache":
                async def execute(_statement):
                    cache[url_hash(url)] = {
                        "url_hash": url_hash(url), "url": normalize_url(url), "title": "Admissions",
                        "content_text": text, "content_hash": "b" * 64, "fetch_method": "httpx",
                        "fetched_at": datetime.now(timezone.utc), "byte_size": len(text),
                    }
                connection.execute = execute
                return await callback(connection)
            raise AssertionError(operation)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetch = WebFetchClient(settings.model_copy(update={"research_min_page_chars": 50}), FakeDB(),
                           client=http, playwright_fetch=None)
    fetch._extract_text = lambda html, page_url: (text, "Admissions")
    try:
        first = await fetch.fetch(url, allowed_hosts=frozenset({"school.edu"}))
        second = await fetch.fetch(url, allowed_hosts=frozenset({"school.edu"}))
    finally:
        await fetch.close()
    assert first.cached is False and second.cached is True
    assert calls["get"] == 1


async def test_agent_writes_facts_with_real_source_urls(settings, taxonomy):
    settings = settings.model_copy(update={"research_max_gaps": 2, "research_max_urls_per_gap": 1,
                                           "research_min_page_chars": 20, "research_confidence_floor": 0.5})
    school_url = "https://school.edu/admissions"
    page_text = "Average overall GPA 3.7. Our mission is service to underserved communities."

    class FakeSearch:
        def build_query(self, school_name, factor_key, description):
            return f"{school_name} {factor_key}"

        async def search(self, query, *, allowed_hosts, max_results=5):
            from app.clients.search_client import SearchHit
            return [
                SearchHit(url="https://spam.invalid/x", title="bad", content=""),
                SearchHit(url=school_url, title="Admissions", content="gpa"),
            ]

    class FakeFetch:
        async def fetch(self, url, *, allowed_hosts, force_refresh=False, collect_links=False):
            assert url == school_url
            return FetchedPage(
                url=school_url, title="Admissions", text=page_text,
                content_hash="c" * 64, fetch_method="httpx", cached=False,
                links=(),
            )

    llm, _ = make_llm(settings, [response([
        fact(raw_text_snippet="Average overall GPA 3.7", page_number=None),
    ])])
    gaps = compute_gaps(taxonomy, [], confidence_floor=0.5)
    agent = WebResearchAgent(settings, FakeSearch(), FakeFetch(), llm, taxonomy)
    try:
        outcome = await agent.research_gaps(
            school_name="Fixture Dental", official_url=school_url,
            gaps=[g for g in gaps if g.key == "avg_gpa"],
        )
    finally:
        await llm.close()

    # Official URL is fetched first and fills the gap — search may not run.
    assert len(outcome["writes"]) == 1
    write = outcome["writes"][0]
    assert write.factor_key == "avg_gpa" and write.value == 3.7
    assert write.source_url == school_url
    assert outcome.get("budget", {}).get("extract_calls", 0) >= 1


def test_research_endpoint_enqueues_job(settings):
    research = SimpleNamespace(enqueue=AsyncMock(return_value={
        "job_id": uuid4(), "status": "pending", "cached": False,
    }))
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=research)
    school_id = uuid4()
    with TestClient(app) as client:
        response = client.post(f"/schools/{school_id}/research")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    research.enqueue.assert_awaited_once()


def test_extract_same_host_links_prefers_admissions_paths():
    from app.clients.web_fetch_client import extract_same_host_links, prioritize_crawl_urls
    html = """
    <html><body>
      <a href="/about">About</a>
      <a href="/admissions/requirements">Requirements</a>
      <a href="https://other.edu/x">Other</a>
      <a href="/brochure.pdf">PDF</a>
    </body></html>
    """
    allowed = frozenset({"school.edu"})
    links = extract_same_host_links(html, "https://school.edu/", allowed)
    assert "https://school.edu/admissions/requirements" in links
    assert "https://school.edu/about" in links
    assert all("other.edu" not in u for u in links)
    assert all(not u.endswith(".pdf") for u in links)
    ordered = prioritize_crawl_urls(links + ["https://school.edu/random-page"])
    assert ordered[0].endswith("/admissions/requirements") or "admissions" in ordered[0]
    assert ordered[-1].endswith("/random-page")


def test_subset_taxonomy_keeps_only_requested_keys(taxonomy):
    subset = subset_taxonomy(taxonomy, {"mission_alignment"})
    assert list(subset.by_key) == ["mission_alignment"]
