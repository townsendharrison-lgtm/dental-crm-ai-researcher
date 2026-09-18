"""Explicit, repeatable provisioning of the service's private bucket and pgmq queue."""
import argparse
import asyncio
import logging

from app.config import get_settings
from app.clients.storage_client import StorageClient
from app.db.session import Database


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
            await database.execute("create_queue", "SELECT pgmq.create(:name)", {"name": settings.queue_name})
            await database.execute(
                "create_document_queue",
                "SELECT pgmq.create(:name)",
                {"name": settings.document_queue_name},
            )
            await database.execute(
                "create_research_queue",
                "SELECT pgmq.create(:name)",
                {"name": settings.research_queue_name},
            )
            print(
                f"Supabase queues initialized ({settings.queue_name}, "
                f"{settings.document_queue_name}, {settings.research_queue_name}). "
                "No CRM tables modified; no existing rows deleted."
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
        logging.error("Infrastructure bootstrap failed (%s); check settings and resource privileges", type(exc).__name__)
        raise SystemExit(1) from None
