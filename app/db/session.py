import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clients.operations import external_call
from app.config import ROOT, Settings


class ConfigurationMissing(RuntimeError):
    pass


def ca_file_candidates(settings: Settings) -> list[Path]:
    """Extra CA files to trust in addition to certifi (Supabase direct + custom)."""
    paths: list[Path] = []
    configured = (settings.database_ca_file or "").strip()
    if configured:
        path = Path(configured)
        paths.append(path if path.is_absolute() else ROOT / path)
    paths.append(ROOT / "certs" / "prod-ca-2021.crt")
    # De-dupe while preserving order; skip missing files.
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def resolve_database_ca_file(settings: Settings) -> str | None:
    """Backward-compatible helper: first usable CA path (explicit, bundled, or certifi)."""
    for path in ca_file_candidates(settings):
        return str(path)
    try:
        import certifi
        return certifi.where()
    except Exception:
        return None


def make_ssl_context(settings: Settings):
    """TLS for asyncpg.

    Supabase *session pooler* hosts present a publicly trusted cert (needs certifi /
    system CAs). Supabase *direct* DB hosts may need their published prod CA.
    Loading only prod-ca-2021.crt breaks pooler verification on some platforms
    (e.g. Render) with SSLCertVerificationError — so we always start from certifi
    and then add the Supabase / configured CA files.
    """
    if not settings.database_ssl:
        return False
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    for path in ca_file_candidates(settings):
        context.load_verify_locations(cafile=str(path))
    return context


def make_engine(settings: Settings):
    if not settings.database_url.get_secret_value():
        raise ConfigurationMissing("DATABASE_URL is missing")
    url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql+asyncpg")
    # asyncpg uses an SSL context rather than libpq's sslmode query option.
    url = url.difference_update_query(["sslmode"])
    return create_async_engine(
        url, pool_pre_ping=True, pool_size=3, max_overflow=2, hide_parameters=True,
        connect_args={"ssl": make_ssl_context(settings), "timeout": settings.external_timeout_seconds,
                      "command_timeout": settings.external_timeout_seconds},
    )


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = make_engine(self.settings)
        return self._engine

    @property
    def sessions(self):
        return async_sessionmaker(self.engine, expire_on_commit=False)

    async def execute(self, operation: str, sql: str, params: dict[str, Any] | None = None, *, read_only: bool = False):
        async def run():
            async with self.engine.begin() as connection:
                result = await connection.execute(text(sql), params or {})
                return [dict(row) for row in result.mappings().all()] if result.returns_rows else []
        return await external_call(
            "postgres", operation, run,
            attempts=self.settings.external_max_attempts if read_only else 1,
            backoff=self.settings.external_backoff_seconds,
            retryable=lambda exc: isinstance(exc, (OperationalError, OSError, TimeoutError)),
        )

    async def transaction(self, operation, callback):
        async def run():
            async with self.engine.begin() as connection:
                return await callback(connection)
        # Don't retry a write whose commit outcome may be unknown.
        return await external_call("postgres", operation, run)

    async def health(self) -> None:
        rows = await self.execute("health", "SELECT 1 AS ok", read_only=True)
        if rows != [{"ok": 1}]:
            raise RuntimeError("Database health check failed")

    @asynccontextmanager
    async def session_lock(self, key: str):
        """Hold one session advisory lock without keeping a long transaction open.

        Requires the already-enforced direct/session pooler connection. If release
        fails, discard the connection so no locked session returns to the pool.
        """
        async with self.engine.connect() as connection:
            async def acquire():
                acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": key})
                await connection.commit()
                return acquired
            acquired = False
            try:
                acquired = await external_call("postgres", "document_lock", acquire)
                yield connection if acquired else None
            finally:
                if acquired:
                    async def release():
                        if connection.invalidated:
                            raise RuntimeError("Document lock connection was lost")
                        await connection.rollback()
                        await connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": key})
                        await connection.commit()
                    try:
                        await external_call("postgres", "document_unlock", release)
                    except BaseException:
                        await connection.invalidate()
                        raise
                else:
                    # An interrupted acquisition may have succeeded at the server.
                    await connection.invalidate()

    async def close(self):
        if self._engine is not None:
            await self._engine.dispose()
