"""Offline Phase 8 round-trip script: auth + generate → approve → score via TestClient.

Does not touch production data. Run:

  uv run python -m scripts.phase8_roundtrip
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.scoring import ScoringResult


SECRET = "phase8-roundtrip-secret"


def dependency():
    return SimpleNamespace(health=AsyncMock(), close=AsyncMock())


def main():
    school_id = uuid4()
    settings_kw = dict(
        _env_file=None,
        database_url="postgresql://fixture:fixture@localhost/fixture",
        supabase_url="https://fixture.supabase.invalid",
        supabase_service_role_key="fixture",
        supabase_storage_bucket="fixture-bucket",
        openai_api_key="fixture", tavily_api_key="fixture",
        queue_name="school_ai_test", db_schema="school_ai_test",
        internal_api_secret=SECRET,
    )
    from app.config import Settings
    settings = Settings(**settings_kw)

    rubrics = SimpleNamespace(
        generate=AsyncMock(return_value={
            "school_id": school_id, "factor_count": 1, "rubric_status": "draft",
            "factors": [{"factor_key": "avg_gpa", "weight": 1, "weight_source": "stated"}],
        }),
        approve=AsyncMock(return_value={
            "school_id": school_id, "rubric_status": "approved",
            "rubric_approved_at": "2026-01-01T00:00:00Z", "rubric_approved_by": "phase8",
        }),
        close=AsyncMock(),
    )
    scoring = SimpleNamespace(
        score=AsyncMock(return_value=ScoringResult(
            school_id=school_id, student_id="crm-student-1", score=91.5,
            per_factor_breakdown=[], skipped=[], weight_mass_used=1.0,
            reasoning="Fixture Phase 8 narrative", scoring_run_id=uuid4(),
        )),
        close=AsyncMock(),
    )
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=dependency(),
                     rubrics=rubrics, scoring=scoring)
    headers = {"X-School-AI-Key": SECRET}
    with TestClient(app) as client:
        assert client.get("/health").status_code in {200, 503}
        assert client.post(f"/schools/{school_id}/rubric/generate").status_code == 401
        gen = client.post(f"/schools/{school_id}/rubric/generate", headers=headers)
        assert gen.status_code == 200
        appr = client.post(f"/schools/{school_id}/rubric/approve", headers=headers, json={"editor": "phase8"})
        assert appr.status_code == 200
        score = client.post(
            f"/schools/{school_id}/score", headers=headers,
            json={"student_id": "crm-student-1", "attributes": {"avg_gpa": 3.7}},
        )
        assert score.status_code == 200
        assert score.json()["score"] == 91.5
    print("phase8_roundtrip: ok (auth + generate + approve + score)")


if __name__ == "__main__":
    main()
