"""pgmq operations are parameterized and confined to the configured queue."""
import json

from sqlalchemy import text

from app.config import Settings
from app.db.session import Database
from app.workers.tasks import CompletedTask, PingTask, run_task


class QueueClient:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        # Settings validates this identifier before any dynamic table SQL is built.
        self.name = settings.queue_name

    async def health(self):
        rows = await self.database.execute(
            "queue_health", "SELECT queue_name FROM pgmq.list_queues() WHERE queue_name = :name",
            {"name": self.name}, read_only=True,
        )
        if not rows:
            raise RuntimeError("Configured queue does not exist; run infrastructure bootstrap")

    async def enqueue(self, task: PingTask) -> int:
        async def send(connection):
            # Same task ID = same message, even after archival. Lock serializes concurrent submissions.
            await connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                     {"key": f"{self.name}:{task.task_id}"})
            existing = await connection.execute(text(
                f'SELECT msg_id FROM pgmq."q_{self.name}" WHERE message->>\'task_id\' = :id '
                f'UNION ALL SELECT msg_id FROM pgmq."a_{self.name}" WHERE message->>\'task_id\' = :id LIMIT 1'
            ), {"id": str(task.task_id)})
            message_id = existing.scalar_one_or_none()
            if message_id is not None:
                return message_id
            result = await connection.execute(text("SELECT pgmq.send(:name, CAST(:message AS jsonb)) AS id"),
                                              {"name": self.name, "message": task.model_dump_json()})
            return result.scalar_one()
        return await self.database.transaction("queue_enqueue", send)

    async def run_once(self) -> CompletedTask | None:
        async def consume(connection):
            result = await connection.execute(text("SELECT * FROM pgmq.read(:name, :vt, 1)"),
                                             {"name": self.name, "vt": self.settings.queue_visibility_seconds})
            row = result.mappings().first()
            if row is None:
                return None
            payload = row["message"]
            task = PingTask.model_validate_json(payload if isinstance(payload, str) else json.dumps(payload))
            completed = CompletedTask(message_id=row["msg_id"], task_id=task.task_id, result=run_task(task))
            # Commit the trivial task's result and acknowledgement atomically. No external work here.
            await connection.execute(text(
                f'UPDATE pgmq."q_{self.name}" SET message = message || CAST(:result AS jsonb) WHERE msg_id = :id'
            ), {"result": json.dumps({"result": completed.result}), "id": completed.message_id})
            # pgmq overloads archive for bigint and bigint[]; select the scalar form.
            archived = await connection.execute(text("SELECT pgmq.archive(CAST(:name AS text), CAST(:id AS bigint))"),
                                                {"name": self.name, "id": completed.message_id})
            if archived.scalar_one() is not True:
                raise RuntimeError("Queue acknowledgement failed")
            return completed
        return await self.database.transaction("queue_process", consume)

    async def completed_result(self, message_id: int) -> str | None:
        rows = await self.database.execute(
            "queue_result", f'SELECT message->>\'result\' AS result FROM pgmq."a_{self.name}" WHERE msg_id = :id',
            {"id": message_id}, read_only=True,
        )
        return rows[0]["result"] if rows else None
