"""School web research jobs: gap analysis → allow-listed search/fetch → raw facts."""
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.web_research_agent import WebResearchAgent, compute_gaps
from app.clients.llm_client import LLMClient
from app.clients.research_queue import ResearchQueue, ResearchTask
from app.clients.search_client import SearchClient
from app.clients.web_fetch_client import WebFetchClient
from app.db.models import Job, School, SchoolRawFact
from app.factor_taxonomy import get_taxonomy

SCHOOLS = School.__table__
FACTS = SchoolRawFact.__table__
JOBS = Job.__table__


class ResearchNotFound(ValueError):
    pass


class LostOwnership(RuntimeError):
    pass


class ResearchService:
    def __init__(self, settings, database, *, taxonomy=None, queue=None, search=None, fetch=None, llm=None):
        self.settings, self.database = settings, database
        self.taxonomy = taxonomy or get_taxonomy()
        self.queue = queue or ResearchQueue(settings, database)
        self.search = search or SearchClient(settings)
        self.fetch = fetch or WebFetchClient(settings, database)
        self.llm = llm or LLMClient(settings)

    async def enqueue(self, school_id: UUID, *, force_refresh: bool = False):
        async def find(connection):
            school = (await connection.execute(select(SCHOOLS).where(SCHOOLS.c.id == school_id))).mappings().first()
            if school is None:
                raise ResearchNotFound("School does not exist")
            job = (await connection.execute(select(JOBS).where(
                JOBS.c.school_id == school_id, JOBS.c.type == "research_school",
            ).order_by(JOBS.c.created_at.desc(), JOBS.c.id.desc()).limit(1))).mappings().first()
            return dict(school), dict(job) if job else None

        school, job = await self.database.transaction("find_research_job", find)
        if job and job["status"] in {"pending", "running"} and not force_refresh:
            return {"job_id": job["id"], "status": job["status"], "cached": True}

        async def enqueue(connection):
            await connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                     {"key": f"research-enqueue:{school_id}"})
            _, current = await find(connection)
            if current and current["status"] in {"pending", "running"} and not force_refresh:
                return {"job_id": current["id"], "status": current["status"], "cached": True}
            job_id = uuid4()
            await connection.execute(insert(JOBS).values(
                id=job_id, school_id=school_id, type="research_school", created_at=func.clock_timestamp(),
                payload={
                    "school_id": str(school_id), "official_url": school["official_url"],
                    "school_name": school["name"], "force_refresh": force_refresh,
                    "taxonomy_hash": self.taxonomy.content_hash,
                    "confidence_floor": self.settings.research_confidence_floor,
                },
            ))
            await self.queue.send(connection, ResearchTask(job_id=job_id, school_id=school_id))
            return {"job_id": job_id, "status": "pending", "cached": False}

        return await self.database.transaction("enqueue_research", enqueue)

    async def job(self, job_id: UUID):
        async def read(connection):
            row = (await connection.execute(select(JOBS).where(JOBS.c.id == job_id))).mappings().first()
            if row is None or row["type"] != "research_school":
                raise ResearchNotFound("Research job does not exist")
            return dict(row)
        return await self.database.transaction("read_research_job", read)

    async def claim(self, task: ResearchTask, token):
        async def claim(connection):
            job = (await connection.execute(select(JOBS).where(JOBS.c.id == task.job_id).with_for_update())).mappings().first()
            if not job or job["type"] != "research_school" or job["school_id"] != task.school_id:
                raise ResearchNotFound("Task does not match a research job")
            school = (await connection.execute(select(SCHOOLS).where(SCHOOLS.c.id == task.school_id))).mappings().first()
            if school is None:
                raise ResearchNotFound("School does not exist")
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                return dict(job), dict(school), False
            payload = dict(job["payload"], worker_token=str(token))
            await connection.execute(update(JOBS).where(JOBS.c.id == task.job_id).values(
                status="running", payload=payload, attempts=JOBS.c.attempts + 1,
                started_at=job["started_at"] or datetime.now(timezone.utc), error=None,
            ))
            return dict(job, payload=payload, status="running"), dict(school), True
        return await self.database.transaction("claim_research_job", claim)

    @staticmethod
    def _owned(job_id, token):
        return (JOBS.c.id == job_id) & (JOBS.c.status == "running") & (JOBS.c.payload["worker_token"].astext == str(token))

    async def load_facts(self, school_id: UUID) -> list[dict]:
        async def read(connection):
            rows = (await connection.execute(select(FACTS).where(FACTS.c.school_id == school_id))).mappings().all()
            return [dict(row) for row in rows]
        return await self.database.transaction("read_school_facts", read)

    async def persist_writes(self, task: ResearchTask, token, writes, result):
        async def save(connection):
            owned = await connection.scalar(select(JOBS.c.id).where(self._owned(task.job_id, token)).with_for_update())
            if owned is None:
                raise LostOwnership("Research worker ownership changed")
            ids = []
            for index, write in enumerate(writes):
                signature = json.dumps({
                    "factor_key": write.factor_key, "value": write.value, "unit": write.unit,
                    "source_url": write.source_url, "raw_text_snippet": write.raw_text_snippet,
                    "confidence": write.confidence,
                }, sort_keys=True)
                fact_id = uuid5(task.job_id, f"{index}:{signature}")
                await connection.execute(pg_insert(FACTS).values(
                    id=fact_id, school_id=task.school_id, factor_key=write.factor_key,
                    value=write.value, unit=write.unit, source_type="web",
                    source_url=write.source_url, document_id=None,
                    confidence=write.confidence, page_number=write.page_number,
                    section=write.section, raw_text_snippet=write.raw_text_snippet,
                ).on_conflict_do_nothing(index_elements=[FACTS.c.id]))
                ids.append(str(fact_id))
            result["fact_ids"] = ids
            await connection.execute(update(JOBS).where(self._owned(task.job_id, token)).values(result=result))
        await self.database.transaction("persist_research_facts", save)

    async def finish(self, task: ResearchTask, token, message_id, result, *, error=None):
        async def complete(connection):
            changed = await connection.scalar(update(JOBS).where(self._owned(task.job_id, token)).values(
                status="failed" if error else "succeeded", result=result, error=error,
                completed_at=datetime.now(timezone.utc),
            ).returning(JOBS.c.id))
            if changed is None:
                raise LostOwnership("Research worker ownership changed")
            await self.queue.archive(connection, message_id)
        await self.database.transaction("finish_research_job", complete)

    async def archive_terminal(self, message_id):
        async def archive(connection):
            await self.queue.archive(connection, message_id)
        await self.database.transaction("archive_terminal_research", archive)

    async def run_research(self, school: dict, *, force_refresh: bool = False) -> dict:
        facts = await self.load_facts(school["id"])
        gaps = compute_gaps(self.taxonomy, facts, confidence_floor=self.settings.research_confidence_floor)
        agent = WebResearchAgent(self.settings, self.search, self.fetch, self.llm, self.taxonomy)
        outcome = await agent.research_gaps(
            school_name=school["name"], official_url=school["official_url"],
            gaps=gaps, force_refresh=force_refresh,
        )
        # Strip write objects for JSON job result; persistence uses the dataclass list.
        serializable = {
            "gaps": outcome["gaps"],
            "remaining_gaps": outcome["remaining_gaps"],
            "rejected_urls": outcome["rejected_urls"],
            "outcomes": outcome["outcomes"],
            "write_count": len(outcome["writes"]),
        }
        return serializable, outcome["writes"]

    async def close(self):
        await self.search.close()
        await self.fetch.close()
        await self.llm.close()
