"""Document upload, durable extraction checkpoints and append-only raw facts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePath
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.clients.document_queue import DocumentQueue, DocumentTask
from app.clients.fetch_client import DocumentParser
from app.db.models import School, SchoolDocument, SchoolRawFact, Job
from app.factor_taxonomy import FactorTaxonomy, get_taxonomy

SCHOOLS = School.__table__
DOCUMENTS = SchoolDocument.__table__
FACTS = SchoolRawFact.__table__
JOBS = Job.__table__


class DocumentNotFound(ValueError):
    pass


class LostOwnership(RuntimeError):
    pass


class DocumentService:
    def __init__(self, settings, database, storage, *, taxonomy=None, queue=None):
        self.settings, self.database, self.storage = settings, database, storage
        self.taxonomy = taxonomy or get_taxonomy()
        self.parser = DocumentParser(settings)
        self.queue = queue or DocumentQueue(settings, database)

    async def create_school(self, name, official_url):
        school_id = uuid4()
        async def create(connection):
            await connection.execute(insert(SCHOOLS).values(id=school_id, name=name, official_url=official_url))
            return school_id
        return await self.database.transaction("create_school", create)

    @staticmethod
    async def _latest(connection, document_id):
        return (await connection.execute(select(JOBS).where(
            JOBS.c.type == "extract_document", JOBS.c.payload["document_id"].astext == str(document_id),
        ).order_by(JOBS.c.created_at.desc(), JOBS.c.id.desc()).limit(1))).mappings().first()

    async def upload(self, school_id: UUID, data: bytes, filename: str, *, force_refresh=False):
        media_type = self.parser.validate(data, filename)
        digest = hashlib.sha256(data).hexdigest()

        async def find(connection):
            if not await connection.scalar(select(SCHOOLS.c.id).where(SCHOOLS.c.id == school_id)):
                raise DocumentNotFound("School does not exist")
            doc = (await connection.execute(select(DOCUMENTS).where(
                DOCUMENTS.c.school_id == school_id, DOCUMENTS.c.content_hash == digest,
            ))).mappings().first()
            job = await self._latest(connection, doc["id"]) if doc else None
            return doc, job

        doc, job = await self.database.transaction("find_document", find)
        # Reuse only in-flight (pending/running) or successful extractions. A prior
        # failed/cancelled job must NOT be treated as a cache hit — re-enqueue it.
        if job and (job["status"] in {"pending", "running"}
                    or (job["status"] == "succeeded" and not force_refresh)):
            return {"document_id": doc["id"], "job_id": job["id"], "status": job["status"], "cached": True}
        # Store bytes before the DB transaction. A failed commit leaves a reusable
        # content-addressed object, never a dangling DB record or destructive cleanup.
        key = doc["storage_key"] if doc else await self.storage.upload(data)

        async def enqueue(connection):
            await connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                     {"key": f"document-upload:{school_id}:{digest}"})
            current_doc, current_job = await find(connection)
            if current_job and (current_job["status"] in {"pending", "running"}
                                or (current_job["status"] == "succeeded" and not force_refresh)):
                return {"document_id": current_doc["id"], "job_id": current_job["id"], "status": current_job["status"], "cached": True}
            document_id = current_doc["id"] if current_doc else uuid4()
            if current_doc is None:
                await connection.execute(insert(DOCUMENTS).values(
                    id=document_id, school_id=school_id, filename=PurePath(filename).name,
                    media_type=media_type, storage_bucket=self.settings.supabase_storage_bucket,
                    storage_key=key, content_hash=digest, byte_size=len(data), source_type="upload",
                ))
            else:
                await connection.execute(update(DOCUMENTS).where(DOCUMENTS.c.id == document_id).values(
                    parsed_status="pending", parse_error=None,
                ))
            job_id = uuid4()
            await connection.execute(insert(JOBS).values(
                id=job_id, school_id=school_id, type="extract_document", created_at=func.clock_timestamp(), payload={
                    "document_id": str(document_id), "content_hash": digest,
                    "taxonomy": self.taxonomy.model_dump(mode="json"),
                    "taxonomy_hash": self.taxonomy.content_hash, "force_refresh": force_refresh,
                    "parser_version": "pdf-docx-v1",
                },
            ))
            await self.queue.send(connection, DocumentTask(job_id=job_id, document_id=document_id))
            return {"document_id": document_id, "job_id": job_id, "status": "pending", "cached": False}
        return await self.database.transaction("enqueue_document", enqueue)

    async def job(self, job_id):
        async def read(connection):
            row = (await connection.execute(select(JOBS).where(JOBS.c.id == job_id))).mappings().first()
            if row is None or row["type"] != "extract_document":
                raise DocumentNotFound("Document job does not exist")
            return dict(row)
        return await self.database.transaction("read_document_job", read)

    async def claim(self, task, token):
        async def claim(connection):
            job = (await connection.execute(select(JOBS).where(JOBS.c.id == task.job_id).with_for_update())).mappings().first()
            if not job or job["type"] != "extract_document" or job["payload"]["document_id"] != str(task.document_id):
                raise DocumentNotFound("Task does not match a document job")
            doc = (await connection.execute(select(DOCUMENTS).where(DOCUMENTS.c.id == task.document_id))).mappings().first()
            if not doc or doc["school_id"] != job["school_id"]:
                raise DocumentNotFound("Task document does not match its school")
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                return dict(job), dict(doc), False
            payload = dict(job["payload"], worker_token=str(token))
            await connection.execute(update(JOBS).where(JOBS.c.id == task.job_id).values(
                status="running", payload=payload, attempts=JOBS.c.attempts + 1,
                started_at=job["started_at"] or datetime.now(timezone.utc), error=None,
            ))
            await connection.execute(update(DOCUMENTS).where(DOCUMENTS.c.id == task.document_id).values(parsed_status="processing"))
            return dict(job, payload=payload, status="running"), dict(doc), True
        return await self.database.transaction("claim_document_job", claim)

    @staticmethod
    def _owned(job_id, token):
        return (JOBS.c.id == job_id) & (JOBS.c.status == "running") & (JOBS.c.payload["worker_token"].astext == str(token))

    async def checkpoint(self, task, token, result, *, chunk_index=None, facts=()):
        async def save(connection):
            owned = await connection.scalar(select(JOBS.c.id).where(self._owned(task.job_id, token)).with_for_update())
            if owned is None:
                raise LostOwnership("Document worker ownership changed")
            if chunk_index is not None:
                ids = []
                job = (await connection.execute(select(JOBS).where(JOBS.c.id == task.job_id))).mappings().one()
                chunk = result["chunks"][chunk_index]
                for fact in facts:
                    signature = json.dumps(fact.model_dump(mode="json"), sort_keys=True)
                    fact_id = uuid5(task.job_id, f"{chunk_index}:{signature}")
                    await connection.execute(pg_insert(FACTS).values(
                        id=fact_id, school_id=job["school_id"], document_id=task.document_id,
                        factor_key=fact.factor_key, value=fact.value, unit=fact.unit,
                        source_type="document", confidence=fact.confidence,
                        page_number=fact.page_number, section=chunk["section"],
                        raw_text_snippet=fact.raw_text_snippet,
                    ).on_conflict_do_nothing(index_elements=[FACTS.c.id]))
                    ids.append(str(fact_id))
                chunk["fact_ids"] = list(dict.fromkeys(ids))
            await connection.execute(update(JOBS).where(self._owned(task.job_id, token)).values(result=result))
        await self.database.transaction("checkpoint_document_chunk", save)

    async def finish(self, task, token, message_id, result, *, error=None):
        async def complete(connection):
            changed = await connection.scalar(update(JOBS).where(self._owned(task.job_id, token)).values(
                status="failed" if error else "succeeded", result=result, error=error,
                completed_at=datetime.now(timezone.utc),
            ).returning(JOBS.c.id))
            if changed is None:
                raise LostOwnership("Document worker ownership changed")
            await connection.execute(update(DOCUMENTS).where(DOCUMENTS.c.id == task.document_id).values(
                parsed_status="failed" if error else "complete", parse_error=error,
                parsed_at=datetime.now(timezone.utc),
            ))
            await self.queue.archive(connection, message_id)
        await self.database.transaction("finish_document_job", complete)

    async def archive_terminal(self, message_id):
        async def archive(connection):
            await self.queue.archive(connection, message_id)
        await self.database.transaction("archive_terminal_document", archive)

    async def facts(self, document_id):
        async def read(connection):
            job = await self._latest(connection, document_id)
            if job is None:
                raise DocumentNotFound("No document job exists")
            result = job["result"] or {}
            ids = [UUID(value) for chunk in result.get("chunks", []) for value in chunk.get("fact_ids", [])]
            rows = (await connection.execute(select(FACTS).where(FACTS.c.id.in_(ids))
                                             .order_by(FACTS.c.page_number, FACTS.c.factor_key))).mappings().all() if ids else []
            taxonomy = FactorTaxonomy.model_validate(job["payload"]["taxonomy"])
            return {"job_id": job["id"], "status": job["status"], "facts": [
                dict(row, category=taxonomy.by_key[row["factor_key"]].category,
                     scoring_eligible=taxonomy.by_key[row["factor_key"]].scoring_eligible) for row in rows
            ]}
        return await self.database.transaction("read_document_facts", read)
