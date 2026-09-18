"""Job listing and usage metering endpoints for ops."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.clients.usage_guard import get_usage_guard
from app.jobs import ALLOWED_STATUSES


class JobListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    type: str
    status: str
    attempts: int
    school_id: UUID | None = None
    error: dict | None = None
    created_at: str | None = None
    completed_at: str | None = None


class JobListResponse(BaseModel):
    jobs: list[JobListItem]
    count: int


class UsageSnapshotResponse(BaseModel):
    rows: list[dict]


def build_jobs_router() -> APIRouter:
    router = APIRouter(tags=["jobs"])

    @router.get(
        "/jobs",
        response_model=JobListResponse,
        summary="List jobs filtered by status (alerting path for failures)",
        description="Query durable job rows. Use status=failed for the minimum error-alerting path. Never deletes jobs.",
    )
    async def list_jobs(
        request: Request,
        status: Annotated[str | None, Query(description="pending|running|succeeded|failed|cancelled")] = None,
        school_id: Annotated[UUID | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        if status is not None and status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(ALLOWED_STATUSES)}")
        service = getattr(request.app.state, "jobs", None)
        if service is None:
            raise HTTPException(status_code=503, detail="Job listing is unavailable")
        rows = await service.list_jobs(status=status, school_id=school_id, limit=limit)
        items = [
            JobListItem(
                job_id=row["id"],
                type=row["type"],
                status=row["status"],
                attempts=row["attempts"],
                school_id=row.get("school_id"),
                error=row.get("error"),
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                completed_at=row["completed_at"].isoformat() if row.get("completed_at") else None,
            )
            for row in rows
        ]
        return JobListResponse(jobs=items, count=len(items))

    @router.get(
        "/usage",
        response_model=UsageSnapshotResponse,
        summary="In-memory daily provider usage snapshot",
        description="Returns process-local metering counters (calls/tokens/estimated cost). Durable events live in provider_usage_events after migration 0005.",
    )
    async def usage_snapshot(school_id: Annotated[UUID | None, Query()] = None):
        guard = get_usage_guard()
        return UsageSnapshotResponse(rows=guard.snapshot(school_id))

    return router
