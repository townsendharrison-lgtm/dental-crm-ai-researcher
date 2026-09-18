"""Run the document workflow without modifying the existing frontend or Node API."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
from uuid import UUID

from app.clients.llm_client import LLMClient
from app.clients.storage_client import StorageClient
from app.config import get_settings
from app.db.session import Database
from app.documents import DocumentService
from app.factor_taxonomy import get_taxonomy
from app.workers.document_worker import DocumentWorker


async def run(args):
    if args.command == "taxonomy":
        return get_taxonomy().model_dump(mode="json")
    settings = get_settings()
    database, storage, llm = Database(settings), StorageClient(settings), LLMClient(settings)
    service = DocumentService(settings, database, storage)
    try:
        if args.command == "init-queue":
            await service.queue.initialize()
            return {"queue": service.queue.name, "status": "ready"}
        if args.command == "create-school":
            return {"school_id": await service.create_school(args.name, args.url)}
        if args.command == "upload":
            with args.path.open("rb") as file:
                data = file.read(settings.document_max_bytes + 1)
            return await service.upload(args.school_id, data, args.path.name, force_refresh=args.force_refresh)
        if args.command == "job":
            job = await service.job(args.job_id)
            result = job["result"] or {}
            return {"job_id": job["id"], "status": job["status"], "attempts": job["attempts"], "error": job["error"],
                    "chunks": [{key: value for key, value in chunk.items() if key != "text"} for chunk in result.get("chunks", [])]}
        if args.command == "facts":
            return await service.facts(args.document_id)
        worker = DocumentWorker(service, llm)
        while True:
            outcome = await worker.run_once()
            if args.once:
                return outcome or {"status": "idle"}
            if outcome:
                print(json.dumps(outcome, default=str), flush=True)
            await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await asyncio.gather(database.close(), storage.close(), llm.close())


def main():
    parser = argparse.ArgumentParser(description="School document ingestion and extraction")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("taxonomy")
    sub.add_parser("init-queue")
    school = sub.add_parser("create-school")
    school.add_argument("--name", required=True)
    school.add_argument("--url", required=True)
    upload = sub.add_parser("upload")
    upload.add_argument("school_id", type=UUID)
    upload.add_argument("path", type=Path)
    upload.add_argument("--force-refresh", action="store_true")
    job = sub.add_parser("job")
    job.add_argument("job_id", type=UUID)
    facts = sub.add_parser("facts")
    facts.add_argument("document_id", type=UUID)
    worker = sub.add_parser("work")
    worker.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        print(json.dumps(asyncio.run(run(args)), default=str, indent=2))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
