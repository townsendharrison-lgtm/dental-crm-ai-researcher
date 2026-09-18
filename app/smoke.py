"""Hosted smoke checks; --storage-only works before PostgreSQL credentials are ready."""
import argparse
import asyncio
import hashlib
import json
from uuid import uuid4

from app.clients.queue_client import QueueClient
from app.clients.storage_client import StorageClient
from app.config import get_settings
from app.db.session import Database
from app.workers.tasks import PingTask


async def check_storage(storage):
    await storage.health()
    content = f"school-ai Phase 0 smoke {uuid4()}".encode()
    key = f"phase0-smoke/{hashlib.sha256(content).hexdigest()}"
    try:
        if await storage.upload(content, prefix="phase0-smoke") != key:
            raise RuntimeError("Storage object key mismatch")
        if await storage.download(key) != content:
            raise RuntimeError("Storage round trip mismatch")
        if await storage.upload(content, prefix="phase0-smoke") != key:
            raise RuntimeError("Storage cache identity mismatch")
    finally:
        await storage.delete(key)
    if await storage.exists(key):
        raise RuntimeError("Storage test object cleanup failed")
    return {"storage_round_trip": "passed", "storage_cache": "passed", "storage_cleanup": "passed"}


async def smoke(*, storage_only: bool = False):
    settings = get_settings()
    database = Database(settings)
    queue = QueueClient(settings, database)
    storage = StorageClient(settings)
    try:
        if storage_only:
            results = await check_storage(storage)
        else:
            await database.health()
            await queue.health()
            results = await check_storage(storage)
            task = PingTask(value=f"smoke-{uuid4()}")
            message_id = await queue.enqueue(task)
            if await queue.enqueue(task) != message_id:
                raise RuntimeError("Queue idempotency check failed")
            # A separate running worker may consume first; poll the durable result.
            for _ in range(20):
                await queue.run_once()
                if await queue.completed_result(message_id) == task.value:
                    break
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError("Smoke task did not complete")
            results.update(database="passed", queue_task="passed", enqueue_idempotency="passed")
        print(json.dumps(results))
        return results
    finally:
        await storage.close()
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage-only", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(smoke(storage_only=args.storage_only))
    except Exception as exc:
        print(f"Hosted smoke test failed ({type(exc).__name__}); check configuration/resources.")
        raise SystemExit(1) from None
