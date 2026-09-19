from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: SecretStr = SecretStr("")
    database_ssl: bool = True
    # Verify the DB server certificate. Keep true. Set false ONLY as a last resort
    # (still encrypted) when a platform's CA store cannot validate Supabase's pooler
    # cert and you accept the reduced trust for that internal connection.
    database_ssl_verify: bool = True
    database_ca_file: str | None = None
    db_schema: str = Field(default="school_ai", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_storage_bucket: str = Field(default="school-ai-documents", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")
    queue_name: str = Field(default="school_ai_phase0", pattern=r"^[a-z][a-z0-9_]{0,46}$")
    document_queue_name: str = Field(default="school_ai_documents", pattern=r"^[a-z][a-z0-9_]{0,46}$")
    research_queue_name: str = Field(default="school_ai_research", pattern=r"^[a-z][a-z0-9_]{0,46}$")
    queue_visibility_seconds: int = Field(default=60, ge=10, le=3600)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    # Run the document + research workers inside the web process (no separate
    # worker service needed). Suitable for low-volume single-admin deployments.
    run_inprocess_workers: bool = False
    # DB connection pool. Headroom matters when in-process workers run alongside
    # request handling (each active job can hold a lock connection + transactions).
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    page_cache_ttl_days: int = Field(default=30, ge=1, le=365)
    research_confidence_floor: float = Field(default=0.5, ge=0, le=1)
    # Admin crawl (docs + explicit URL only — no multi-site search).
    research_max_gaps: int = Field(default=130, ge=1, le=200)
    research_max_urls_per_gap: int = Field(default=1, ge=1, le=10)
    # Deprecated: Tavily multi-site search removed; kept for env compat (unused).
    research_max_searches: int = Field(default=0, ge=0, le=30)
    research_max_extract_calls: int = Field(default=40, ge=1, le=200)
    research_max_chunks_per_page: int = Field(default=3, ge=1, le=20)
    research_max_pages: int = Field(default=30, ge=1, le=100)
    research_job_timeout_seconds: float = Field(default=600, gt=30, le=1800)
    research_min_page_chars: int = Field(default=400, ge=50, le=5000)
    research_chunk_chars: int = Field(default=16000, ge=500, le=20000)
    # Discover trusted sources: search only official domain (+ ADEA), then deep-crawl seeds.
    research_discover_max_queries: int = Field(default=8, ge=1, le=20)
    research_discover_max_results_per_query: int = Field(default=5, ge=1, le=10)
    research_discover_max_seeds: int = Field(default=15, ge=1, le=40)
    research_discover_max_pages: int = Field(default=50, ge=1, le=120)
    research_discover_max_extract_calls: int = Field(default=100, ge=1, le=300)
    research_discover_timeout_seconds: float = Field(default=900, gt=60, le=1800)
    research_discover_categories_per_page: int = Field(default=4, ge=1, le=14)
    openai_api_key: SecretStr = SecretStr("")
    openai_model: Literal["gpt-4o"] = "gpt-4o"
    openai_timeout_seconds: float = Field(default=60, gt=0, le=180)
    # SDK-level retries honor OpenAI's Retry-After header (correct waits for
    # per-minute TPM/RPM rate limits, which our short external_call backoff can't).
    openai_max_retries: int = Field(default=4, ge=0, le=8)
    openai_max_output_tokens: int = Field(default=4000, ge=256, le=16384)
    document_max_bytes: int = Field(default=25_000_000, ge=1024, le=100_000_000)
    document_max_pages: int = Field(default=300, ge=1, le=1000)
    document_max_chars: int = Field(default=2_000_000, ge=1000, le=10_000_000)
    # Larger chunks => fewer OpenAI calls (each call also carries the full taxonomy,
    # so fewer chunks cuts repeated overhead and rate-limit pressure significantly).
    document_chunk_chars: int = Field(default=16000, ge=500, le=20000)
    # Skip chunks with less than this many non-whitespace chars (boilerplate,
    # page numbers, headers) — they rarely contain facts and waste an LLM call.
    document_min_chunk_chars: int = Field(default=180, ge=0, le=2000)
    document_max_chunks: int = Field(default=1000, ge=1, le=5000)
    # "retrieve" (LlamaIndex): embed chunks, pull top-k per taxonomy category, then
    # extract only those chunks against a category subset — far fewer/cheaper GPT calls.
    # "chunk": legacy path — every eligible chunk gets the full taxonomy.
    document_extract_mode: Literal["retrieve", "chunk"] = "retrieve"
    document_retrieve_top_k: int = Field(default=3, ge=1, le=10)
    openai_embed_model: str = Field(default="text-embedding-3-small", min_length=1)
    ocr_timeout_seconds: float = Field(default=45, gt=0, le=120)
    tesseract_cmd: str = "tesseract"
    tavily_api_key: SecretStr = SecretStr("")
    external_timeout_seconds: float = Field(default=10, gt=0, le=60)
    external_max_attempts: int = Field(default=3, ge=1, le=5)
    external_backoff_seconds: float = Field(default=0.25, ge=0, le=5)
    health_timeout_seconds: float = Field(default=5, gt=0, le=30)
    log_level: str = "INFO"
    run_integration_tests: bool = False
    # Shared secret for Node ↔ Python internal calls. Empty disables auth (local only).
    internal_api_secret: SecretStr = SecretStr("")
    # Phase 9 cost/rate guardrails (per school per UTC day).
    openai_daily_calls_per_school: int = Field(default=200, ge=1, le=100_000)
    openai_daily_cost_usd_per_school: float = Field(default=5.0, ge=0.01, le=10_000)
    tavily_daily_calls_per_school: int = Field(default=100, ge=1, le=100_000)
    tavily_daily_cost_usd_per_school: float = Field(default=2.0, ge=0.01, le=10_000)
    # Approximate Tavily basic-search unit cost for metering estimates (not an invoice).
    tavily_estimated_cost_per_search_usd: float = Field(default=0.001, ge=0, le=1)

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Use the hosted HTTPS Supabase project URL without credentials or a path")
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            return value
        try:
            url = make_url(raw)
            if url.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
                raise ValueError
            if not url.host or not url.database or not url.username or not url.password:
                raise ValueError
            if str(url.port) == "6543":
                raise ValueError
        except (ValueError, TypeError, ArgumentError):
            raise ValueError("Use a complete PostgreSQL direct/session connection URL (not port 6543)") from None
        return value

    @field_validator("db_schema")
    @classmethod
    def private_schema(cls, value: str) -> str:
        if value in {"public", "auth", "storage", "pgmq", "extensions", "information_schema"} or value.startswith("pg_"):
            raise ValueError("Use a dedicated application schema")
        return value

    @property
    def storage_configured(self) -> bool:
        return all((self.supabase_url, self.supabase_storage_bucket,
                    self.supabase_service_role_key.get_secret_value()))


@lru_cache
def get_settings() -> Settings:
    return Settings()
