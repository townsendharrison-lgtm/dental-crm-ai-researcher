"""Long-running document worker with leases, checkpoints and ownership fencing."""
import asyncio
from dataclasses import asdict
import hashlib
from uuid import uuid4

from openai import APIError
from sqlalchemy import text

from app.clients.chunk_retriever import assign_chunks_by_category, taxonomy_for_categories
from app.clients.fetch_client import DocumentChunk, DocumentError
from app.clients.llm_client import ExtractionFailed
from app.clients.operations import external_call
from app.clients.usage_guard import BudgetExceeded, current_school_id
from app.documents import LostOwnership
from app.factor_taxonomy import FactorTaxonomy


class DocumentWorker:
    def __init__(self, service, llm, *, heartbeat=True, retriever=None):
        self.service, self.llm = service, llm
        self.heartbeat = heartbeat
        # Optional override for tests (sync callable with assign_chunks_by_category signature).
        self.retriever = retriever or assign_chunks_by_category

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

    async def _extract_chunk(self, chunk, taxonomy, saved, task, token, result, index):
        try:
            extraction = await self.llm.extract(chunk, taxonomy)
            saved.update(status="succeeded", usage=extraction.usage)
            await self.service.checkpoint(task, token, result, chunk_index=index, facts=extraction.result.facts)
            return None
        except BudgetExceeded as exc:
            saved.update(status="failed", error_type=type(exc).__name__)
            await self.service.checkpoint(task, token, result, chunk_index=index)
            return {"type": "BudgetExceeded", "detail": str(exc)}
        except (ExtractionFailed, APIError) as exc:
            saved.update(status="failed", error_type=type(exc).__name__)
            await self.service.checkpoint(task, token, result, chunk_index=index)
            return None

    async def _process_chunk_mode(self, result, taxonomy, task, token):
        min_chars = self.service.settings.document_min_chunk_chars
        for index, saved in enumerate(result["chunks"]):
            if saved["status"] in {"succeeded", "failed"}:
                continue
            chunk = DocumentChunk(**{key: saved[key] for key in ("index", "text", "page_number", "section", "used_ocr")})
            if len("".join(chunk.text.split())) < min_chars:
                saved.update(status="succeeded", skipped="low_content")
                await self.service.checkpoint(task, token, result, chunk_index=index, facts=[])
                continue
            fatal = await self._extract_chunk(chunk, taxonomy, saved, task, token, result, index)
            if fatal:
                return result, fatal
        failures = [c["index"] for c in result["chunks"] if c["status"] == "failed"]
        return result, ({"type": "ChunkExtractionFailed", "chunk_indexes": failures} if failures else None)

    async def _process_retrieve_mode(self, result, taxonomy, task, token):
        settings = self.service.settings
        min_chars = settings.document_min_chunk_chars
        pending = []
        for index, saved in enumerate(result["chunks"]):
            if saved["status"] in {"succeeded", "failed"}:
                continue
            # Use list position as chunk.index so retrieval assignments match checkpoints.
            chunk = DocumentChunk(
                index=index, text=saved["text"], page_number=saved["page_number"],
                section=saved["section"], used_ocr=saved["used_ocr"],
            )
            if len("".join(chunk.text.split())) < min_chars:
                saved.update(status="succeeded", skipped="low_content")
                await self.service.checkpoint(task, token, result, chunk_index=index, facts=[])
                continue
            pending.append(chunk)

        if not pending:
            failures = [c["index"] for c in result["chunks"] if c["status"] == "failed"]
            return result, ({"type": "ChunkExtractionFailed", "chunk_indexes": failures} if failures else None)

        assignments = await asyncio.to_thread(
            self.retriever,
            pending,
            taxonomy,
            api_key=settings.openai_api_key.get_secret_value(),
            embed_model_name=settings.openai_embed_model,
            top_k=settings.document_retrieve_top_k,
        )
        selected = {item.chunk_index: item.categories for item in assignments}
        result["retrieve"] = {
            "mode": "llamaindex",
            "top_k": settings.document_retrieve_top_k,
            "embed_model": settings.openai_embed_model,
            "selected_chunks": [
                {"chunk_index": index, "categories": list(categories)}
                for index, categories in selected.items()
            ],
        }
        await self.service.checkpoint(task, token, result)

        for chunk in pending:
            saved = result["chunks"][chunk.index]
            if saved["status"] in {"succeeded", "failed"}:
                continue
            categories = selected.get(chunk.index)
            if not categories:
                saved.update(status="succeeded", skipped="not_retrieved")
                await self.service.checkpoint(task, token, result, chunk_index=chunk.index, facts=[])
                continue
            subset = taxonomy_for_categories(taxonomy, categories)
            fatal = await self._extract_chunk(chunk, subset, saved, task, token, result, chunk.index)
            if fatal:
                return result, fatal

        failures = [c["index"] for c in result["chunks"] if c["status"] == "failed"]
        return result, ({"type": "ChunkExtractionFailed", "chunk_indexes": failures} if failures else None)

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
            if self.service.settings.document_extract_mode == "retrieve":
                return await self._process_retrieve_mode(result, taxonomy, task, token)
            return await self._process_chunk_mode(result, taxonomy, task, token)
        except DocumentError as exc:
            return result, {"type": type(exc).__name__}
        finally:
            current_school_id.reset(school_token)
