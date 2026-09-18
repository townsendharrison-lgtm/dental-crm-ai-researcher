"""Tavily search wrapper: logged, retried, never scatters raw SDK calls."""
from dataclasses import dataclass

import httpx

from app.clients.operations import external_call
from app.clients.usage_guard import get_usage_guard
from app.config import Settings
from app.db.session import ConfigurationMissing
from app.source_allowlist import is_allowed_url


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    content: str


class SearchClient:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.external_timeout_seconds)
        return self._client

    @staticmethod
    def retryable(exc: Exception) -> bool:
        if isinstance(exc, httpx.TransportError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return False

    def build_query(self, school_name: str, factor_key: str, description: str) -> str:
        return f"{school_name} dental school admissions {factor_key.replace('_', ' ')} {description}"

    async def search(self, query: str, *, allowed_hosts: frozenset[str], max_results: int = 5) -> list[SearchHit]:
        if not self.settings.tavily_api_key.get_secret_value():
            raise ConfigurationMissing("TAVILY_API_KEY is missing")

        async def request():
            response = await self.client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.settings.tavily_api_key.get_secret_value(),
                    "query": query,
                    "max_results": max_results,
                    "include_domains": sorted(allowed_hosts)[:20],
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            return response.json()

        guard = get_usage_guard(self.settings)
        await guard.authorize("tavily")
        try:
            payload = await external_call(
                "tavily", "search", request,
                attempts=self.settings.external_max_attempts,
                backoff=self.settings.external_backoff_seconds,
                retryable=self.retryable,
            )
        except httpx.HTTPError as exc:
            raise SearchError(type(exc).__name__) from None
        await guard.record(
            "tavily", "search",
            cost_usd=self.settings.tavily_estimated_cost_per_search_usd,
        )

        hits = []
        for item in payload.get("results") or []:
            url = (item.get("url") or "").strip()
            if not url or not is_allowed_url(url, allowed_hosts):
                continue
            hits.append(SearchHit(url=url, title=(item.get("title") or "").strip(),
                                  content=(item.get("content") or "").strip()))
        return hits

    async def close(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
