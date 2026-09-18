"""Explicit, repeatable provisioning of the service's private bucket and pgmq queue."""
import argparse
import asyncio
import logging

from app.config import get_settings
from app.clients.storage_client import StorageClient
from app.db.session import Database


def _db_error_hint(exc: BaseException) -> str:
    """Surface a short driver message for CLI diagnosis; never include connection URLs."""
    orig = getattr(exc, "orig", None) or exc
    text = str(orig).replace("\n", " ").strip()
    for marker in ("postgresql://", "postgres://", "password="):
        if marker in text.lower():
            return type(orig).__name__
    return f"{type(orig).__name__}: {text[:300]}"


async def ensure_queue(database: Database, operation: str, name: str) -> str:
    existing = await database.execute(
        f"check_{operation}",
        "SELECT queue_name FROM pgmq.list_queues() WHERE queue_name = :name",
        {"name": name},
        read_only=True,
    )
    if existing:
        return "exists"
    try:
        # SELECT * FROM works across pgmq versions that return void or a row.
        await database.execute(operation, "SELECT * FROM pgmq.create(:name)", {"name": name})
        return "created"
    except Exception as exc:
        message = str(getattr(exc, "orig", None) or exc).lower()
        if "already exists" in message or "duplicate" in message:
            return "exists"
        raise


async def bootstrap(*, storage_only: bool = False, queue_only: bool = False):
    settings = get_settings()
    database = Database(settings)
    storage = StorageClient(settings)
    try:
        if not queue_only:
            await storage.ensure_bucket()
            print(f"Private Supabase Storage bucket ready: {settings.supabase_storage_bucket}")
        if not storage_only:
            await database.execute("enable_pgmq", "CREATE EXTENSION IF NOT EXISTS pgmq")
            results = []
            for operation, name in (
                ("create_queue", settings.queue_name),
                ("create_document_queue", settings.document_queue_name),
                ("create_research_queue", settings.research_queue_name),
            ):
                status = await ensure_queue(database, operation, name)
                results.append(f"{name}={status}")
            print(
                "Supabase queues initialized ("
                + ", ".join(results)
                + "). No CRM tables modified; no existing rows deleted."
            )
    finally:
        await storage.close()
        await database.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--storage-only", action="store_true", help="Initialize only the private Storage bucket")
    scope.add_argument("--queue-only", action="store_true", help="Initialize only the PostgreSQL queue")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(bootstrap(storage_only=args.storage_only, queue_only=args.queue_only))
    except Exception as exc:
        logging.error(
            "Infrastructure bootstrap failed (%s); check settings and resource privileges",
            _db_error_hint(exc),
        )
        raise SystemExit(1) from None
