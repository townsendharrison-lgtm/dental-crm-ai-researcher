"""Long-running web research worker with leases and ownership fencing."""
import asyncio
from uuid import uuid4

from sqlalchemy import text

from app.clients.usage_guard import BudgetExceeded, current_school_id
from app.documents import LostOwnership as DocumentLostOwnership
from app.research import LostOwnership


class ResearchWorker:
    def __init__(self, service, *, heartbeat=True):
        self.service = service
        self.heartbeat = heartbeat

    async def _keep_alive(self, connection, message_id):
        while True:
            await asyncio.sleep(self.service.settings.queue_visibility_seconds / 3)

            async def check_lock_session():
                if connection.invalidated or connection.closed:
                    raise LostOwnership("Research lock connection was lost")
                await connection.scalar(text("SELECT 1"))
                await connection.commit()

            from app.clients.operations import external_call
            await external_call("postgres", "research_lock_health", check_lock_session)
            await self.service.queue.renew(message_id)

    async def run_once(self):
        leased = await self.service.queue.read()
        if leased is None:
            return None
        message_id, task = leased
        lock_key = f"research-worker:{task.school_id}"
        async with self.service.database.session_lock(lock_key) as connection:
            if connection is None:
                return {"job_id": task.job_id, "status": "busy"}
            token = uuid4()
            job, school, claimed = await self.service.claim(task, token)
            if not claimed:
                await self.service.archive_terminal(message_id)
                return {"job_id": task.job_id, "status": job["status"]}
            work = asyncio.create_task(self._process(task, token, job, school))
            pulse = asyncio.create_task(self._keep_alive(connection, message_id)) if self.heartbeat else None
            try:
                if pulse:
                    done, _ = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
                    if pulse in done:
                        await pulse
                        raise LostOwnership("Research heartbeat stopped")
                result, error = await work
            finally:
                if not work.done():
                    work.cancel()
                if pulse:
                    pulse.cancel()
                await asyncio.gather(work, *([pulse] if pulse else []), return_exceptions=True)
            await self.service.finish(task, token, message_id, result, error=error)
            return {
                "job_id": task.job_id,
                "status": "failed" if error else "succeeded",
                "fact_count": len((result or {}).get("fact_ids", [])),
                "gap_count": len((result or {}).get("gaps", [])),
            }

    async def _process(self, task, token, job, school):
        force_refresh = bool(job["payload"].get("force_refresh"))
        target_url = job["payload"].get("target_url")
        mode = job["payload"].get("mode")
        school_token = current_school_id.set(job.get("school_id") or task.school_id)
        try:
            result, writes = await self.service.run_research(
                school, force_refresh=force_refresh, target_url=target_url, mode=mode,
            )
            await self.service.persist_writes(task, token, writes, result)
            return result, None
        except BudgetExceeded as exc:
            return {"gaps": [], "outcomes": []}, {"type": "BudgetExceeded", "detail": str(exc)}
        except (LostOwnership, DocumentLostOwnership):
            raise
        except Exception:
            return {"gaps": [], "outcomes": []}, {"type": "ResearchFailed"}
        finally:
            current_school_id.reset(school_token)
