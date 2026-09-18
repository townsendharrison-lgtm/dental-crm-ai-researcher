import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def dependency():
    return SimpleNamespace(health=AsyncMock(), close=AsyncMock())


def test_health_all_green_and_cleanup(settings):
    database, queue, storage = dependency(), dependency(), dependency()
    app = create_app(settings, database=database, queue=queue, storage=storage)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": True, "queue": True, "storage": True},
                               "api_keys": {"openai": True, "tavily": True}, "errors": {}}
    database.close.assert_awaited_once()
    storage.close.assert_awaited_once()
    assert "fixture-not-a-real-key" not in response.text


@pytest.mark.parametrize("failing", ["database", "queue", "storage"])
def test_dependency_failure_reports_503_without_secrets(settings, failing):
    dependencies = {name: dependency() for name in ("database", "queue", "storage")}
    dependencies[failing].health.side_effect = RuntimeError("password=VERY_SECRET")
    with TestClient(create_app(settings, **dependencies)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"][failing] is False
    assert response.json()["errors"][failing] == "unavailable"
    assert "VERY_SECRET" not in response.text


def test_missing_keys_are_not_reported_as_healthy(settings):
    settings = settings.model_copy(update={"openai_api_key": type(settings.openai_api_key)("")})
    with TestClient(create_app(settings, database=dependency(), queue=dependency(), storage=dependency())) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["api_keys"] == {"openai": False, "tavily": True}


def test_missing_configuration_starts_but_is_not_ready(settings):
    settings = settings.model_copy(update={"database_url": type(settings.database_url)(""), "supabase_url": ""})
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["errors"] == {"database": "not_configured", "queue": "not_configured", "storage": "not_configured"}


def test_health_checks_are_bounded(settings):
    async def slow():
        await asyncio.sleep(1)
    database = dependency()
    database.health = slow
    settings = settings.model_copy(update={"health_timeout_seconds": 0.01})
    with TestClient(create_app(settings, database=database, queue=dependency(), storage=dependency())) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["errors"]["database"] == "timeout"


def test_document_routes_are_exposed(settings):
    with TestClient(create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                               documents=dependency(), research=dependency(),
                               normalization=dependency(), rubrics=dependency())) as client:
        paths = set(client.get("/openapi.json").json()["paths"])
    assert "/health" in paths
    assert "/schools/{school_id}/documents" in paths
    assert "/schools/{school_id}/research" in paths
    assert "/schools/{school_id}/rubric/generate" in paths
    assert "/normalization/recompute" in paths
    assert "/jobs/{job_id}" in paths
    assert "/jobs" in paths
    assert "/usage" in paths
    assert "/documents/{document_id}/facts" in paths
