"""Run with: uv run python -m app.workers.worker [--once]."""
import argparse
import asyncio
import json
import logging

from app.clients.queue_client import QueueClient
from app.config import get_settings
from app.db.session import Database


async def work(once: bool = False):
    settings = get_settings()
    database = Database(settings)
    queue = QueueClient(settings, database)
    try:
        await queue.health()
        while True:
            result = await queue.run_once()
            if result:
                logging.getLogger("school_ai.worker").info(json.dumps({
                    "event": "task_completed", "message_id": result.message_id, "task_id": str(result.task_id),
                }))
            if once:
                return result
            await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level)
    try:
        asyncio.run(work(args.once))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.error("Worker failed (%s); check configuration and infrastructure", type(exc).__name__)
        raise SystemExit(1) from None
