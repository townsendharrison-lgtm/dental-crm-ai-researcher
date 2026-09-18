import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.clients.queue_client import QueueClient
from app.workers.tasks import PingTask


def result(*, scalar=None, row=None):
    value = MagicMock()
    value.scalar_one.return_value = scalar
    value.scalar_one_or_none.return_value = scalar
    value.mappings.return_value.first.return_value = row
    return value


def database_for(connection):
    async def transaction(operation, callback):
        return await callback(connection)
    return SimpleNamespace(transaction=transaction)


async def test_task_is_enqueued_and_validated_worker_persists_result(settings):
    task = PingTask(value="fixture-pong")
    connection = SimpleNamespace(execute=AsyncMock(side_effect=[
        result(), result(), result(scalar=17),
        result(row={"msg_id": 17, "message": json.loads(task.model_dump_json())}), result(), result(scalar=True),
    ]))
    queue = QueueClient(settings, database_for(connection))
    assert await queue.enqueue(task) == 17
    completed = await queue.run_once()
    assert completed.message_id == 17
    assert completed.task_id == task.task_id
    assert completed.result == "fixture-pong"
    calls = connection.execute.await_args_list
    assert json.loads(calls[2].args[1]["message"])["task_id"] == str(task.task_id)
    assert json.loads(calls[4].args[1]["result"]) == {"result": "fixture-pong"}
    assert "pgmq.archive" in str(calls[5].args[0])


async def test_existing_task_id_does_not_send_again(settings):
    connection = SimpleNamespace(execute=AsyncMock(side_effect=[result(), result(scalar=17)]))
    queue = QueueClient(settings, database_for(connection))
    assert await queue.enqueue(PingTask()) == 17
    assert connection.execute.await_count == 2


async def test_empty_queue_has_no_result_or_acknowledgement(settings):
    connection = SimpleNamespace(execute=AsyncMock(return_value=result()))
    assert await QueueClient(settings, database_for(connection)).run_once() is None
    assert connection.execute.await_count == 1


async def test_invalid_payload_is_never_acknowledged(settings):
    connection = SimpleNamespace(execute=AsyncMock(return_value=result(row={"msg_id": 1, "message": {"task": "unknown"}})))
    with pytest.raises(ValidationError):
        await QueueClient(settings, database_for(connection)).run_once()
    assert connection.execute.await_count == 1


async def test_missing_queue_fails_health(settings):
    database = SimpleNamespace(execute=AsyncMock(return_value=[]))
    with pytest.raises(RuntimeError, match="does not exist"):
        await QueueClient(settings, database).health()


async def test_completed_result_reads_archive(settings):
    database = SimpleNamespace(execute=AsyncMock(return_value=[{"result": "pong"}]))
    assert await QueueClient(settings, database).completed_result(17) == "pong"
    assert database.execute.await_args.args[2] == {"id": 17}
