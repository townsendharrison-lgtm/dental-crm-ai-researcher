"""HTTP contract for school document upload, job polling and fact retrieval."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.clients.fetch_client import DocumentError
from app.documents import DocumentNotFound
from app.main import create_app
from app.research import ResearchNotFound

from .test_api import dependency


@pytest.fixture
def documents():
    return SimpleNamespace(
        settings=SimpleNamespace(document_max_bytes=25_000_000),
        create_school=AsyncMock(return_value=uuid4()),
        upload=AsyncMock(),
        job=AsyncMock(),
        facts=AsyncMock(),
    )


@pytest.fixture
def client(settings, documents):
    research = SimpleNamespace(job=AsyncMock(side_effect=ResearchNotFound("Research job does not exist")),
                               enqueue=AsyncMock())
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=documents, research=research)
    with TestClient(app) as test_client:
        yield test_client, documents


def test_openapi_exposes_document_endpoints(client):
    test_client, _ = client
    paths = set(test_client.get("/openapi.json").json()["paths"])
    assert paths >= {
        "/health",
        "/schools",
        "/schools/{school_id}/documents",
        "/schools/{school_id}/research",
        "/schools/{school_id}/crawl-url",
        "/schools/{school_id}/facts",
        "/schools/{school_id}/sources",
        "/jobs",
        "/jobs/{job_id}",
        "/documents/{document_id}/facts",
        "/usage",
    }


def test_create_school_and_upload_document(client):
    test_client, documents = client
    school_id = uuid4()
    documents.create_school.return_value = school_id
    documents.upload.return_value = {
        "document_id": uuid4(), "job_id": uuid4(), "status": "pending", "cached": False,
    }

    created = test_client.post("/schools", json={
        "name": "Fixture Dental School",
        "official_url": "https://fixture.invalid/admissions",
    })
    assert created.status_code == 200
    assert created.json()["school_id"] == str(school_id)

    uploaded = test_client.post(
        f"/schools/{school_id}/documents",
        files={"file": ("class_profile.pdf", b"%PDF-1.4 fixture", "application/pdf")},
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["status"] == "pending"
    assert body["cached"] is False
    documents.upload.assert_awaited_once()
    assert documents.upload.await_args.args[0] == school_id
    assert documents.upload.await_args.args[2] == "class_profile.pdf"


def test_upload_rejects_unknown_school_and_bad_document(client):
    test_client, documents = client
    school_id = uuid4()
    documents.upload.side_effect = DocumentNotFound("School does not exist")
    missing = test_client.post(
        f"/schools/{school_id}/documents",
        files={"file": ("class_profile.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert missing.status_code == 404

    documents.upload.side_effect = DocumentError("Only valid PDF and DOCX documents are supported")
    bad = test_client.post(
        f"/schools/{school_id}/documents",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad.status_code == 400


def test_job_and_facts_endpoints(client):
    test_client, documents = client
    job_id, document_id = uuid4(), uuid4()
    documents.job.return_value = {
        "id": job_id, "type": "extract_document", "status": "succeeded", "attempts": 1, "error": None,
        "school_id": None,
        "payload": {"document_id": str(document_id)},
        "result": {"chunks": [{"index": 0, "text": "secret source", "status": "succeeded", "fact_ids": []}]},
    }
    documents.facts.return_value = {
        "job_id": job_id, "status": "succeeded",
        "facts": [{"factor_key": "avg_gpa", "value": 3.7, "category": "Academics"}],
    }

    job = test_client.get(f"/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["document_id"] == str(document_id)
    assert "text" not in job.json()["chunks"][0]

    facts = test_client.get(f"/documents/{document_id}/facts")
    assert facts.status_code == 200
    assert facts.json()["facts"][0]["factor_key"] == "avg_gpa"

    documents.job.side_effect = DocumentNotFound("Document job does not exist")
    assert test_client.get(f"/jobs/{uuid4()}").status_code == 404
