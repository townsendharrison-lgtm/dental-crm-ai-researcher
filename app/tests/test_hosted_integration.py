"""Real infrastructure checks. Skips never count as Phase 0 completion."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.smoke import smoke


@pytest.fixture
def hosted_settings():
    settings = Settings()
    if not settings.run_integration_tests:
        pytest.skip("Set RUN_INTEGRATION_TESTS=true after configuring dedicated Supabase resources")
    if not settings.database_url.get_secret_value() or not settings.storage_configured:
        pytest.fail("Integration tests explicitly enabled but DB/Supabase Storage configuration is missing")
    return settings


@pytest.mark.integration
def test_hosted_health_is_green(hosted_settings):
    with TestClient(create_app(hosted_settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200, response.json()


@pytest.mark.integration
async def test_hosted_queue_and_storage_round_trip(hosted_settings, monkeypatch):
    monkeypatch.setattr("app.smoke.get_settings", lambda: hosted_settings)
    await smoke()


@pytest.mark.integration
async def test_hosted_storage_round_trip(monkeypatch):
    settings = Settings()
    if not settings.run_integration_tests:
        pytest.skip("Set RUN_INTEGRATION_TESTS=true to test hosted Storage")
    if not settings.storage_configured:
        pytest.fail("Hosted Storage tests enabled but Storage configuration is missing")
    monkeypatch.setattr("app.smoke.get_settings", lambda: settings)
    result = await smoke(storage_only=True)
    assert result == {"storage_round_trip": "passed", "storage_cache": "passed", "storage_cleanup": "passed"}
