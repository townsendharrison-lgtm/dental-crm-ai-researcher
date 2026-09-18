"""Run web research jobs without modifying the React or Node codebases."""
import argparse
import asyncio
import json
import logging
from uuid import UUID

from app.clients.llm_client import LLMClient
from app.clients.search_client import SearchClient
from app.clients.web_fetch_client import WebFetchClient
from app.config import get_settings
from app.db.session import Database
from app.research import ResearchService
from app.workers.research_worker import ResearchWorker


async def run(args):
    settings = get_settings()
    database = Database(settings)
    search, fetch, llm = SearchClient(settings), WebFetchClient(settings, database), LLMClient(settings)
    service = ResearchService(settings, database, search=search, fetch=fetch, llm=llm)
    try:
        if args.command == "init-queue":
            await service.queue.initialize()
            return {"queue": service.queue.name, "status": "ready"}
        if args.command == "enqueue":
            return await service.enqueue(args.school_id, force_refresh=args.force_refresh)
        if args.command == "job":
            job = await service.job(args.job_id)
            return {"job_id": job["id"], "status": job["status"], "attempts": job["attempts"],
                    "error": job["error"], "result": job["result"]}
        worker = ResearchWorker(service)
        while True:
            outcome = await worker.run_once()
            if args.once:
                return outcome or {"status": "idle"}
            if outcome:
                print(json.dumps(outcome, default=str), flush=True)
            await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await service.close()
        await database.close()


def main():
    parser = argparse.ArgumentParser(description="School web research")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-queue")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("school_id", type=UUID)
    enqueue.add_argument("--force-refresh", action="store_true")
    job = sub.add_parser("job")
    job.add_argument("job_id", type=UUID)
    work = sub.add_parser("work")
    work.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        print(json.dumps(asyncio.run(run(args)), default=str, indent=2))
    except KeyboardInterrupt:
        pass
    except Exception:
        print(json.dumps({"error_type": "ResearchCliError"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
