"""Dedicated durable queue for school web-research jobs; archive on completion, never purge."""
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text


class ResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: UUID
    school_id: UUID


class ResearchQueue:
    def __init__(self, settings, database):
        self.settings, self.database = settings, database
        self.name = settings.research_queue_name

    async def initialize(self):
        await self.database.execute("create_research_queue", "SELECT pgmq.create(:name)", {"name": self.name})

    async def send(self, connection, task: ResearchTask):
        return await connection.scalar(text("SELECT pgmq.send(:name, CAST(:payload AS jsonb))"),
                                       {"name": self.name, "payload": task.model_dump_json()})

    async def read(self):
        rows = await self.database.execute("read_research_task", "SELECT * FROM pgmq.read(:name, :vt, 1)",
                                           {"name": self.name, "vt": self.settings.queue_visibility_seconds})
        if not rows:
            return None
        row = rows[0]
        raw = row["message"]
        task = ResearchTask.model_validate_json(raw if isinstance(raw, str) else json.dumps(raw))
        return row["msg_id"], task

    async def renew(self, message_id):
        rows = await self.database.execute("renew_research_task", "SELECT * FROM pgmq.set_vt(:name, CAST(:id AS bigint), :vt)",
                                           {"name": self.name, "id": message_id, "vt": self.settings.queue_visibility_seconds})
        if not rows:
            raise RuntimeError("Research queue lease was lost")

    async def archive(self, connection, message_id):
        result = await connection.scalar(text("SELECT pgmq.archive(:name, CAST(:id AS bigint))"),
                                         {"name": self.name, "id": message_id})
        if result is not True:
            raise RuntimeError("Research queue acknowledgement failed")
