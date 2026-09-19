"""Long-running document worker with leases, checkpoints and ownership fencing."""
import asyncio
from dataclasses import asdict
import hashlib
from uuid import uuid4

from openai import APIError
from sqlalchemy import text

from app.clients.fetch_client import DocumentChunk, DocumentError
from app.clients.llm_client import ExtractionFailed
from app.clients.operations import external_call
from app.clients.usage_guard import BudgetExceeded, current_school_id
from app.documents import LostOwnership
from app.factor_taxonomy import FactorTaxonomy


class DocumentWorker:
    def __init__(self, service, llm, *, heartbeat=True):
        self.service, self.llm = service, llm
        self.heartbeat = heartbeat

    async def _keep_alive(self, connection, message_id):
        while True:
            await asyncio.sleep(self.service.settings.queue_visibility_seconds / 3)
            async def check_lock_session():
                # Never reconnect and pretend to still own a lost session lock.
                if connection.invalidated or connection.closed:
                    raise LostOwnership("Document lock connection was lost")
                await connection.scalar(text("SELECT 1"))
                await connection.commit()
            await external_call("postgres", "document_lock_health", check_lock_session)
            await self.service.queue.renew(message_id)

    async def run_once(self):
        leased = await self.service.queue.read()
        if leased is None:
            return None
        message_id, task = leased
        lock_key = f"document-worker:{task.document_id}"
        async with self.service.database.session_lock(lock_key) as connection:
            if connection is None:
                return {"job_id": task.job_id, "status": "busy"}
            token = uuid4()
            job, document, claimed = await self.service.claim(task, token)
            if not claimed:
                await self.service.archive_terminal(message_id)
                return {"job_id": task.job_id, "status": job["status"]}
            work = asyncio.create_task(self._process(task, token, job, document))
            pulse = asyncio.create_task(self._keep_alive(connection, message_id)) if self.heartbeat else None
            try:
                if pulse:
                    done, _ = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
                    if pulse in done:
                        await pulse  # Propagate lease/session failure; unfinished work is cancelled below.
                        raise LostOwnership("Document heartbeat stopped")
                result, error = await work
            finally:
                if not work.done():
                    work.cancel()
                if pulse:
                    pulse.cancel()
                await asyncio.gather(work, *([pulse] if pulse else []), return_exceptions=True)
            await self.service.finish(task, token, message_id, result, error=error)
            return {"job_id": task.job_id, "status": "failed" if error else "succeeded",
                    "fact_count": sum(len(c.get("fact_ids", [])) for c in result.get("chunks", []))}

    async def _process(self, task, token, job, document):
        result = job["result"] or {}
        school_token = current_school_id.set(job.get("school_id"))
        try:
            taxonomy = FactorTaxonomy.model_validate(job["payload"]["taxonomy"])
            if taxonomy.content_hash != job["payload"]["taxonomy_hash"]:
                raise DocumentError("Taxonomy snapshot does not match its hash")
            if "chunks" not in result:
                if document["storage_bucket"] != self.service.settings.supabase_storage_bucket:
                    raise DocumentError("Document bucket differs from configured bucket")
                data = await self.service.storage.download(document["storage_key"])
                if hashlib.sha256(data).hexdigest() != document["content_hash"]:
                    raise DocumentError("Stored document content hash mismatch")
                chunks = await asyncio.to_thread(self.service.parser.parse, data, document["filename"])
                result = {"taxonomy_hash": taxonomy.content_hash, "chunks": [
                    dict(asdict(chunk), status="pending", fact_ids=[]) for chunk in chunks
                ]}
                # Save parsed text before any model request. Restart uses this checkpoint.
                await self.service.checkpoint(task, token, result)
            min_chars = self.service.settings.document_min_chunk_chars
            for index, saved in enumerate(result["chunks"]):
                if saved["status"] in {"succeeded", "failed"}:
                    continue
                chunk = DocumentChunk(**{key: saved[key] for key in ("index", "text", "page_number", "section", "used_ocr")})
                # Skip near-empty chunks (boilerplate/page numbers) without an LLM call.
                if len("".join(chunk.text.split())) < min_chars:
                    saved.update(status="succeeded", skipped="low_content")
                    await self.service.checkpoint(task, token, result, chunk_index=index, facts=[])
                    continue
                try:
                    extraction = await self.llm.extract(chunk, taxonomy)
                    saved.update(status="succeeded", usage=extraction.usage)
                    await self.service.checkpoint(task, token, result, chunk_index=index, facts=extraction.result.facts)
                except BudgetExceeded as exc:
                    saved.update(status="failed", error_type=type(exc).__name__)
                    await self.service.checkpoint(task, token, result, chunk_index=index)
                    return result, {"type": "BudgetExceeded", "detail": str(exc)}
                except (ExtractionFailed, APIError) as exc:
                    saved.update(status="failed", error_type=type(exc).__name__)
                    await self.service.checkpoint(task, token, result, chunk_index=index)
            failures = [c["index"] for c in result["chunks"] if c["status"] == "failed"]
            return result, ({"type": "ChunkExtractionFailed", "chunk_indexes": failures} if failures else None)
        except DocumentError as exc:
            return result, {"type": type(exc).__name__}
        finally:
            current_school_id.reset(school_token)
