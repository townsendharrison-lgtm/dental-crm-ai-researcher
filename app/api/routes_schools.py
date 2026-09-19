"""School document ingestion and web research endpoints."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.clients.fetch_client import DocumentError
from app.documents import DocumentNotFound
from app.research import ResearchNotFound


class CreateSchoolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=500)
    official_url: HttpUrl


class SchoolCreated(BaseModel):
    school_id: UUID


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    status: str
    cached: bool


class ResearchEnqueueResponse(BaseModel):
    job_id: UUID
    status: str
    cached: bool


class JobResponse(BaseModel):
    job_id: UUID
    type: str
    status: str
    attempts: int
    error: dict | None
    document_id: UUID | None = None
    school_id: UUID | None = None
    chunks: list[dict] = []
    result: dict | None = None


class DocumentFactsResponse(BaseModel):
    job_id: UUID
    status: str
    facts: list[dict]


class CrawlUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl


class SchoolFactsResponse(BaseModel):
    school_id: UUID
    fact_count: int
    facts: list[dict]


class SchoolDocumentItem(BaseModel):
    document_id: UUID
    filename: str
    source_type: str
    source_url: str | None = None
    parsed_status: str
    byte_size: int | None = None
    created_at: object | None = None
    job_id: UUID | None = None
    job_status: str | None = None


class SchoolDocumentsResponse(BaseModel):
    school_id: UUID
    documents: list[SchoolDocumentItem]
    count: int


class WebSourceItem(BaseModel):
    url: str
    fact_count: int
    last_seen_at: object | None = None


class SchoolSourcesResponse(BaseModel):
    school_id: UUID
    documents: list[SchoolDocumentItem]
    web_sources: list[WebSourceItem]
    document_count: int
    web_source_count: int


def _documents(request: Request):
    service = getattr(request.app.state, "documents", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Document service is not configured")
    return service


def _research(request: Request):
    service = getattr(request.app.state, "research", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Research service is not configured")
    return service


def build_school_router() -> APIRouter:
    router = APIRouter(tags=["schools"])

    @router.post(
        "/schools",
        response_model=SchoolCreated,
        summary="Create a school record for document ingestion",
        description="Creates a school row used as the parent for uploaded admissions documents. Does not research or score.",
    )
    async def create_school(payload: CreateSchoolRequest, request: Request):
        school_id = await _documents(request).create_school(payload.name.strip(), str(payload.official_url))
        return SchoolCreated(school_id=school_id)

    @router.delete(
        "/schools/{school_id}",
        summary="Delete a school and all of its research data",
        description=(
            "Removes rubric factors, raw facts, documents, jobs, and scoring runs for this school. "
            "Provider usage events are retained with school_id cleared. CRM comparisons must be "
            "deleted via the CRM school row (school_ai_scores CASCADE)."
        ),
    )
    async def delete_school(school_id: UUID, request: Request):
        try:
            return await _documents(request).delete_school(school_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @router.post(
        "/schools/{school_id}/documents",
        response_model=DocumentUploadResponse,
        summary="Upload a school document for extraction",
        description="Accepts a PDF or DOCX upload, stores it in private Supabase Storage, enqueues an extract_document job, and returns identifiers for polling. Identical content is not re-extracted unless force_refresh is true.",
    )
    async def upload_document(
        school_id: UUID,
        request: Request,
        file: Annotated[UploadFile, File(description="PDF or DOCX admissions document")],
        force_refresh: Annotated[bool, Query()] = False,
        filename: Annotated[str | None, Form()] = None,
    ):
        service = _documents(request)
        name = (filename or file.filename or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="A filename with .pdf or .docx is required")
        data = await file.read(service.settings.document_max_bytes + 1)
        try:
            result = await service.upload(school_id, data, name, force_refresh=force_refresh)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except DocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return DocumentUploadResponse(**result)

    @router.post(
        "/schools/{school_id}/research",
        response_model=ResearchEnqueueResponse,
        summary="Enqueue allow-listed web research for factor gaps",
        description="Computes missing/low-confidence taxonomy gaps after document extraction, then enqueues a research_school job that searches only allow-listed domains, fetches pages with TTL caching, and writes grounded school_raw_facts with source_url.",
    )
    async def enqueue_research(
        school_id: UUID,
        request: Request,
        force_refresh: Annotated[bool, Query()] = False,
    ):
        try:
            result = await _research(request).enqueue(school_id, force_refresh=force_refresh)
        except ResearchNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return ResearchEnqueueResponse(**result)

    @router.get(
        "/jobs/{job_id}",
        response_model=JobResponse,
        summary="Read a document extraction or web research job",
        description="Returns durable job status for extract_document or research_school jobs. Document chunk source text is omitted.",
    )
    async def get_job(job_id: UUID, request: Request):
        documents = getattr(request.app.state, "documents", None)
        research = getattr(request.app.state, "research", None)
        if documents is not None:
            try:
                job = await documents.job(job_id)
                result = job.get("result") or {}
                payload = job.get("payload") or {}
                return JobResponse(
                    job_id=job["id"], type=job["type"], status=job["status"], attempts=job["attempts"],
                    error=job["error"], school_id=job.get("school_id"),
                    document_id=UUID(payload["document_id"]) if payload.get("document_id") else None,
                    chunks=[{key: value for key, value in chunk.items() if key != "text"}
                            for chunk in result.get("chunks", [])],
                )
            except DocumentNotFound:
                pass
        if research is not None:
            try:
                job = await research.job(job_id)
                result = job.get("result") or {}
                return JobResponse(
                    job_id=job["id"], type=job["type"], status=job["status"], attempts=job["attempts"],
                    error=job["error"], school_id=job.get("school_id"), result=result,
                )
            except ResearchNotFound:
                pass
        raise HTTPException(status_code=404, detail="Job does not exist")

    @router.post(
        "/schools/{school_id}/crawl-url",
        response_model=ResearchEnqueueResponse,
        summary="Fetch and extract facts from one admin-provided URL",
        description="Enqueues a research job that fetches a specific URL (its host is trusted) and extracts taxonomy facts from it, writing grounded school_raw_facts with source_url. Use for a school's official page or another admin-chosen source.",
    )
    async def crawl_url(
        school_id: UUID,
        payload: CrawlUrlRequest,
        request: Request,
        force_refresh: Annotated[bool, Query()] = False,
    ):
        try:
            result = await _research(request).enqueue(
                school_id, force_refresh=force_refresh, target_url=str(payload.url),
            )
        except ResearchNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return ResearchEnqueueResponse(**result)

    @router.get(
        "/schools/{school_id}/facts",
        response_model=SchoolFactsResponse,
        summary="Read all extracted raw facts for a school",
        description="Returns every school_raw_facts row for the school (from document ingest, targeted URL crawl and web research), including source_type and source_url provenance.",
    )
    async def get_school_facts(school_id: UUID, request: Request):
        facts = await _research(request).load_facts(school_id)
        return SchoolFactsResponse(school_id=school_id, fact_count=len(facts), facts=facts)

    @router.get(
        "/schools/{school_id}/documents",
        response_model=SchoolDocumentsResponse,
        summary="List uploaded documents for a school",
        description="Returns school_documents rows with the latest extract_document job status for each.",
    )
    async def list_school_documents(school_id: UUID, request: Request):
        try:
            documents = await _documents(request).list_documents(school_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return SchoolDocumentsResponse(
            school_id=school_id,
            documents=[SchoolDocumentItem(**doc) for doc in documents],
            count=len(documents),
        )

    @router.get(
        "/schools/{school_id}/sources",
        response_model=SchoolSourcesResponse,
        summary="List data sources (documents + crawled web URLs) for a school",
        description="Aggregates uploaded documents and distinct web source URLs that produced raw facts.",
    )
    async def list_school_sources(school_id: UUID, request: Request):
        try:
            documents = await _documents(request).list_documents(school_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        try:
            web_sources = await _research(request).list_web_sources(school_id)
        except ResearchNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return SchoolSourcesResponse(
            school_id=school_id,
            documents=[SchoolDocumentItem(**doc) for doc in documents],
            web_sources=[WebSourceItem(**src) for src in web_sources],
            document_count=len(documents),
            web_source_count=len(web_sources),
        )

    @router.get(
        "/documents/{document_id}/facts",
        response_model=DocumentFactsResponse,
        summary="Read extracted raw facts for a document",
        description="Returns school_raw_facts written by the latest extract_document job for this document, including taxonomy category metadata.",
    )
    async def get_facts(document_id: UUID, request: Request):
        try:
            result = await _documents(request).facts(document_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return DocumentFactsResponse(**result)

    return router
