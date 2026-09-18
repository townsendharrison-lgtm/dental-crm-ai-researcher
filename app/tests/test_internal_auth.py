"""Phase 8 internal auth tests."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.tests.test_api import dependency


def test_health_remains_public_when_secret_configured(settings):
    settings = settings.model_copy(update={"internal_api_secret": type(settings.internal_api_secret)("phase8-secret")})
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=dependency(),
                     rubrics=dependency(), scoring=dependency())
    with TestClient(app) as client:
        assert client.get("/health").status_code in {200, 503}


def test_protected_route_requires_matching_key(settings):
    settings = settings.model_copy(update={"internal_api_secret": type(settings.internal_api_secret)("phase8-secret")})
    rubrics = SimpleNamespace(
        generate=AsyncMock(return_value={
            "school_id": uuid4(), "factor_count": 0, "rubric_status": "draft", "factors": [],
        }),
        close=AsyncMock(),
    )
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=dependency(),
                     rubrics=rubrics, scoring=dependency())
    school_id = uuid4()
    with TestClient(app) as client:
        denied = client.post(f"/schools/{school_id}/rubric/generate")
        assert denied.status_code == 401
        wrong = client.post(f"/schools/{school_id}/rubric/generate", headers={"X-School-AI-Key": "nope"})
        assert wrong.status_code == 401
        ok = client.post(f"/schools/{school_id}/rubric/generate", headers={"X-School-AI-Key": "phase8-secret"})
        assert ok.status_code == 200
