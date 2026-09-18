"""Phase 9 usage guardrails and failed-job listing."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.clients.usage_guard import BudgetExceeded, UsageGuard, current_school_id, get_usage_guard, reset_usage_guard_for_tests
from app.main import create_app
from app.tests.test_api import dependency


@pytest.fixture(autouse=True)
def _reset_guard():
    reset_usage_guard_for_tests()
    yield
    reset_usage_guard_for_tests()
    current_school_id.set(None)


@pytest.mark.asyncio
async def test_usage_guard_blocks_over_call_cap(settings):
    settings = settings.model_copy(update={"openai_daily_calls_per_school": 2, "openai_daily_cost_usd_per_school": 100})
    guard = UsageGuard(settings, database=None)
    school = uuid4()
    await guard.record("openai", "extract_document_chunk", school_id=school, tokens=10, cost_usd=0.01)
    await guard.record("openai", "extract_document_chunk", school_id=school, tokens=10, cost_usd=0.01)
    with pytest.raises(BudgetExceeded):
        await guard.authorize("openai", school_id=school)
    # Other schools are independent.
    await guard.authorize("openai", school_id=uuid4())


@pytest.mark.asyncio
async def test_usage_guard_blocks_over_cost_cap(settings):
    settings = settings.model_copy(update={"openai_daily_calls_per_school": 1000, "openai_daily_cost_usd_per_school": 0.05})
    guard = UsageGuard(settings, database=None)
    school = uuid4()
    await guard.record("openai", "explain_score", school_id=school, tokens=100, cost_usd=0.05)
    with pytest.raises(BudgetExceeded):
        await guard.authorize("openai", school_id=school)


def test_list_failed_jobs_and_usage_endpoint(settings):
    job_id = uuid4()
    school_id = uuid4()
    jobs = SimpleNamespace(list_jobs=AsyncMock(return_value=[{
        "id": job_id, "type": "extract_document", "status": "failed", "attempts": 1,
        "school_id": school_id, "error": {"type": "ChunkExtractionFailed"},
        "created_at": datetime(2026, 9, 17, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 9, 17, 1, tzinfo=timezone.utc),
    }]))
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), jobs=jobs)
    with TestClient(app) as client:
        paths = set(client.get("/openapi.json").json()["paths"])
        assert "/jobs" in paths
        assert "/usage" in paths
        listed = client.get("/jobs?status=failed")
        assert listed.status_code == 200
        body = listed.json()
        assert body["count"] == 1
        assert body["jobs"][0]["job_id"] == str(job_id)
        assert body["jobs"][0]["status"] == "failed"
        jobs.list_jobs.assert_awaited_once()
        assert jobs.list_jobs.await_args.kwargs["status"] == "failed"

        bad = client.get("/jobs?status=nope")
        assert bad.status_code == 400

        usage = client.get("/usage")
        assert usage.status_code == 200
        assert "rows" in usage.json()


def test_get_usage_guard_singleton_uses_settings(settings):
    reset_usage_guard_for_tests()
    first = get_usage_guard(settings)
    second = get_usage_guard()
    assert first is second
