"""HTTP page fetch + trafilatura extraction with optional Playwright fallback and TTL cache."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import hashlib
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import trafilatura
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.clients.operations import external_call
from app.config import Settings
from app.db.models import PageFetchCache
from app.source_allowlist import assert_allowed_url, hostname_of, is_allowed_url

CACHE = PageFetchCache.__table__

# Prefer admissions-related paths when expanding a site crawl.
_PRIORITY_PATH_TOKENS = (
    "admission", "apply", "requirement", "prerequisite", "dds", "dmd",
    "dental", "academ", "gpa", "dat", "tuition", "cost", "aid", "curriculum",
    "mission", "about", "faq", "class-profile", "incoming", "how-to-apply",
)


class WebFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str | None
    text: str
    content_hash: str
    fetch_method: str
    cached: bool
    links: tuple[str, ...] = field(default_factory=tuple)


class _HrefCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value.strip())


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def extract_same_host_links(html: str, base_url: str, allowed_hosts: frozenset[str]) -> list[str]:
    """Collect same-host http(s) links from page HTML for deep crawl expansion."""
    collector = _HrefCollector()
    try:
        collector.feed(html or "")
    except Exception:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for href in collector.hrefs:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"}:
            continue
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        try:
            if not is_allowed_url(clean, allowed_hosts):
                continue
        except Exception:
            continue
        try:
            if hostname_of(clean) != hostname_of(base_url) and hostname_of(clean) not in allowed_hosts:
                continue
        except Exception:
            continue
        normalized = normalize_url(clean)
        if normalized in seen:
            continue
        path_lower = urlsplit(normalized).path.lower()
        if path_lower.endswith((
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".zip", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
            ".mp4", ".mp3", ".css", ".js",
        )):
            continue
        seen.add(normalized)
        found.append(normalized)
    return found


def prioritize_crawl_urls(urls: list[str]) -> list[str]:
    def rank(url: str) -> tuple:
        path = urlsplit(url).path.lower()
        # Stronger boost for admissions/apply paths than generic "about".
        if any(token in path for token in (
            "admission", "apply", "requirement", "prerequisite", "how-to-apply",
            "class-profile", "incoming",
        )):
            priority = 0
        elif any(token in path for token in _PRIORITY_PATH_TOKENS):
            priority = 1
        else:
            priority = 2
        return (priority, len(path), url)

    return sorted(urls, key=rank)


class WebFetchClient:
    def __init__(self, settings: Settings, database, *, client: httpx.AsyncClient | None = None, playwright_fetch=None):
        self.settings = settings
        self.database = database
        self._client = client
        self._owns_client = client is None
        self._playwright_fetch = playwright_fetch

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.external_timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": "school-ai-service/0.3 (+research; allow-listed sources only)"},
            )
        return self._client

    @staticmethod
    def retryable(exc: Exception) -> bool:
        if isinstance(exc, httpx.TransportError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return False

    async def get_cached(self, url: str) -> FetchedPage | None:
        digest = url_hash(url)

        async def read(connection):
            row = (await connection.execute(select(CACHE).where(CACHE.c.url_hash == digest))).mappings().first()
            return dict(row) if row else None

        row = await self.database.transaction("read_page_cache", read)
        if row is None:
            return None
        age = datetime.now(timezone.utc) - row["fetched_at"]
        if age > timedelta(days=self.settings.page_cache_ttl_days):
            return None
        return FetchedPage(
            url=row["url"], title=row["title"], text=row["content_text"],
            content_hash=row["content_hash"], fetch_method=row["fetch_method"], cached=True,
            links=(),
        )

    async def put_cache(self, page: FetchedPage):
        digest = url_hash(page.url)

        async def write(connection):
            await connection.execute(pg_insert(CACHE).values(
                url_hash=digest, url=normalize_url(page.url), title=page.title,
                content_text=page.text, content_hash=page.content_hash,
                fetch_method=page.fetch_method, fetched_at=datetime.now(timezone.utc),
                byte_size=len(page.text.encode()),
            ).on_conflict_do_update(
                index_elements=[CACHE.c.url_hash],
                set_={
                    "url": normalize_url(page.url), "title": page.title,
                    "content_text": page.text, "content_hash": page.content_hash,
                    "fetch_method": page.fetch_method, "fetched_at": datetime.now(timezone.utc),
                    "byte_size": len(page.text.encode()),
                },
            ))

        await self.database.transaction("write_page_cache", write)

    def _extract_text(self, html: str, url: str) -> tuple[str | None, str | None]:
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
        meta = trafilatura.extract_metadata(html, default_url=url)
        title = meta.title if meta else None
        return (text.strip() if text else None), title

    async def _httpx_html(self, url: str) -> str:
        async def request():
            response = await self.client.get(url)
            response.raise_for_status()
            return response.text

        try:
            return await external_call(
                "httpx", "fetch_page", request,
                attempts=self.settings.external_max_attempts,
                backoff=self.settings.external_backoff_seconds,
                retryable=self.retryable,
            )
        except httpx.HTTPError:
            raise WebFetchError("Static page fetch failed") from None

    async def _playwright_html(self, url: str) -> str | None:
        if self._playwright_fetch is not None:
            return await self._playwright_fetch(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None

        async def request():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=int(self.settings.external_timeout_seconds * 1000))
                    return await page.content()
                finally:
                    await browser.close()

        try:
            return await external_call(
                "playwright", "fetch_page", request,
                attempts=1, backoff=self.settings.external_backoff_seconds,
            )
        except Exception:
            raise WebFetchError("Playwright page fetch failed") from None

    async def fetch(
        self, url: str, *, allowed_hosts: frozenset[str], force_refresh: bool = False,
        collect_links: bool = False,
    ) -> FetchedPage:
        assert_allowed_url(url, allowed_hosts)
        if not force_refresh and not collect_links:
            cached = await self.get_cached(url)
            if cached is not None:
                return cached

        html = await self._httpx_html(url)
        text, title = self._extract_text(html, url)
        method = "httpx"
        if not text or len(text) < self.settings.research_min_page_chars:
            rendered = await self._playwright_html(url)
            if rendered:
                html = rendered
                text, title = self._extract_text(rendered, url)
                method = "playwright"
        if not text or len(text) < self.settings.research_min_page_chars:
            raise WebFetchError("Extracted page text is empty or too short")

        links = tuple(extract_same_host_links(html, url, allowed_hosts)) if collect_links else ()
        page = FetchedPage(
            url=normalize_url(url), title=title, text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            fetch_method=method, cached=False, links=links,
        )
        await self.put_cache(page)
        return page

    async def close(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
