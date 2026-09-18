"""List durable jobs for ops/alerting (failed-job queries)."""
from uuid import UUID

from sqlalchemy import select

from app.db.models import Job

JOBS = Job.__table__
ALLOWED_STATUSES = frozenset({"pending", "running", "succeeded", "failed", "cancelled"})


class JobQueryService:
    def __init__(self, database):
        self.database = database

    async def list_jobs(self, *, status: str | None = None, school_id: UUID | None = None, limit: int = 50):
        if status is not None and status not in ALLOWED_STATUSES:
            raise ValueError(f"Unsupported job status: {status}")
        limit = max(1, min(int(limit), 200))

        async def read(connection):
            query = select(JOBS).order_by(JOBS.c.created_at.desc()).limit(limit)
            if status is not None:
                query = query.where(JOBS.c.status == status)
            if school_id is not None:
                query = query.where(JOBS.c.school_id == school_id)
            return [dict(row) for row in (await connection.execute(query)).mappings()]

        return await self.database.transaction("list_jobs", read)
