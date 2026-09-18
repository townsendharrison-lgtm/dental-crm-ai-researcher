"""Phase 0 has only a trivial, strictly validated echo task."""
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class PingTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task: Literal["ping"] = "ping"
    task_id: UUID = Field(default_factory=uuid4)
    value: str = Field(default="phase0", max_length=256)


class CompletedTask(BaseModel):
    message_id: int
    task_id: UUID
    result: str


def run_task(task: PingTask) -> str:
    return task.value
