"""Durable document workflow tests. Offline only — no production schema writes."""
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.clients.document_queue import DocumentTask
from app.clients.fetch_client import DocumentParser
from app.documents import DocumentNotFound, DocumentService, LostOwnership
from app.factor_taxonomy import FactorTaxonomy
from app.workers.document_worker import DocumentWorker

from .test_document_extraction import FIXTURES, fact, make_llm, response


@pytest.fixture
def taxonomy():
    return FactorTaxonomy.model_validate({
        "version": "synthetic-workflow",
        "categories": ["Test numbers"],
        "factors": [
            {"key": "avg_gpa", "category": "Test numbers",
             "description": "Average overall GPA", "value_type": "number", "unit": "gpa"},
            {"key": "avg_science_gpa", "category": "Test numbers",
             "description": "Average science GPA", "value_type": "number", "unit": "gpa"},
        ],
    })


def memory_service(settings, taxonomy, *, storage_key="fixture-key.pdf"):
    """In-memory DocumentService stand-in for worker orchestration tests."""
    school_id = uuid4()
    document_id = uuid4()
    job_id = uuid4()
    data = (FIXTURES / "class_profile.pdf").read_bytes()
    state = {
        "school_id": school_id,
        "document": {
            "id": document_id, "school_id": school_id, "filename": "class_profile.pdf",
            "storage_bucket": settings.supabase_storage_bucket, "storage_key": storage_key,
            "content_hash": hashlib.sha256(data).hexdigest(),
        },
        "job": {
            "id": job_id, "school_id": school_id, "status": "pending", "attempts": 0,
            "started_at": None, "error": None, "result": None,
            "payload": {
                "document_id": str(document_id),
                "taxonomy": taxonomy.model_dump(mode="json"),
                "taxonomy_hash": taxonomy.content_hash,
            },
        },
        "facts": [],
        "messages": [(1, DocumentTask(job_id=job_id, document_id=document_id))],
        "archived": [],
        "data": data,
        "token": None,
    }

    class MemoryQueue:
        name = settings.document_queue_name

        async def read(self):
            return state["messages"].pop(0) if state["messages"] else None

        async def renew(self, message_id):
            return None

        async def archive(self, connection, message_id):
            state["archived"].append(message_id)

    class FakeDatabase:
        @asynccontextmanager
        async def session_lock(self, key: str):
            yield object()

    class Service:
        def __init__(self):
            self.settings = settings
            self.parser = DocumentParser(settings)
            self.queue = MemoryQueue()
            self.database = FakeDatabase()
            self.storage = SimpleNamespace(download=AsyncMock(return_value=data))
            self.state = state

        async def claim(self, task, token):
            job = state["job"]
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                return dict(job), dict(state["document"]), False
            job = dict(job, status="running", payload=dict(job["payload"], worker_token=str(token)),
                       attempts=job["attempts"] + 1)
            state["job"] = job
            state["token"] = token
            return job, dict(state["document"]), True

        async def checkpoint(self, task, token, result, *, chunk_index=None, facts=()):
            if state.get("token") != token:
                raise LostOwnership("Document worker ownership changed")
            if chunk_index is not None:
                chunk = result["chunks"][chunk_index]
                ids = []
                for item in facts:
                    fact_id = uuid4()
                    state["facts"].append({
                        "id": fact_id, "factor_key": item.factor_key, "value": item.value,
                        "unit": item.unit, "confidence": item.confidence,
                        "page_number": item.page_number, "raw_text_snippet": item.raw_text_snippet,
                    })
                    ids.append(str(fact_id))
                chunk["fact_ids"] = ids
            state["job"]["result"] = result

        async def finish(self, task, token, message_id, result, *, error=None):
            if state.get("token") != token:
                raise LostOwnership("Document worker ownership changed")
            state["job"].update(status="failed" if error else "succeeded", result=result, error=error)
            await self.queue.archive(None, message_id)

        async def archive_terminal(self, message_id):
            await self.queue.archive(None, message_id)

    return Service()


async def test_worker_persists_extracted_facts_without_fabrication(settings, taxonomy):
    service = memory_service(settings, taxonomy)
    llm, calls = make_llm(settings, [response([
        fact(),
        fact(factor_key="avg_science_gpa", value=3.6, raw_text_snippet="Average science GPA 3.6"),
    ])])
    try:
        outcome = await DocumentWorker(service, llm, heartbeat=False).run_once()
    finally:
        await llm.close()

    assert outcome["status"] == "succeeded"
    assert outcome["fact_count"] == 2
    assert service.state["job"]["status"] == "succeeded"
    assert service.state["archived"] == [1]
    keys = {row["factor_key"]: row["value"] for row in service.state["facts"]}
    assert keys == {"avg_gpa": 3.7, "avg_science_gpa": 3.6}
    assert "avg_dat" not in keys
    assert calls and "avg_gpa" in str(calls[0])


async def test_worker_marks_chunk_failure_without_guessing(settings, taxonomy):
    service = memory_service(settings, taxonomy)
    llm, _ = make_llm(settings, [response([], raw="{not-json", finish="length")])
    try:
        outcome = await DocumentWorker(service, llm, heartbeat=False).run_once()
    finally:
        await llm.close()
    assert outcome["status"] == "failed"
    assert service.state["facts"] == []
    assert service.state["job"]["error"]["type"] == "ChunkExtractionFailed"


async def test_upload_idempotency_returns_cached_job(settings, taxonomy):
    school_id, document_id, job_id = uuid4(), uuid4(), uuid4()
    data = b"%PDF-cached-content"
    digest = hashlib.sha256(data).hexdigest()
    doc = {"id": document_id, "school_id": school_id, "storage_key": "cached.pdf"}
    job = {"id": job_id, "status": "pending"}

    async def transaction(operation, callback):
        if operation == "find_document":
            return doc, job
        raise AssertionError(f"unexpected write path: {operation}")

    storage = SimpleNamespace(upload=AsyncMock())
    service = DocumentService(settings, SimpleNamespace(transaction=transaction), storage, taxonomy=taxonomy)
    service.parser.validate = Mock(return_value="application/pdf")
    result = await service.upload(school_id, data, "cached.pdf")
    assert result == {"document_id": document_id, "job_id": job_id, "status": "pending", "cached": True}
    storage.upload.assert_not_awaited()
    assert digest == hashlib.sha256(data).hexdigest()


async def test_document_service_rejects_missing_school(settings, taxonomy):
    async def transaction(operation, callback):
        connection = SimpleNamespace(scalar=AsyncMock(return_value=None), execute=AsyncMock())
        return await callback(connection)

    service = DocumentService(settings, SimpleNamespace(transaction=transaction),
                              SimpleNamespace(upload=AsyncMock()), taxonomy=taxonomy)
    service.parser.validate = Mock(return_value="application/pdf")
    with pytest.raises(DocumentNotFound, match="School does not exist"):
        await service.upload(uuid4(), b"%PDF-1.4", "missing.pdf")
