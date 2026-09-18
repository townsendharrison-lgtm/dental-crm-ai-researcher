"""Phase 6 admin review: override audit, approve, scoring gate."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.rubrics import RubricNotApproved
from app.schemas.scoring import ScoringResult
from app.tests.test_api import dependency


@pytest.fixture
def school_id():
    return uuid4()


def test_override_approve_and_score_gate_flow(settings, school_id):
    factor_key = "avg_gpa"
    old_value = {"value": 3.7, "weight": 0.9, "confidence": 0.8, "weight_source": "cross_school_inferred",
                 "reasoning": "auto", "source_urls": ["https://school.edu/a"]}
    new_value = {"value": 3.8, "weight": 0.5, "confidence": 0.95, "weight_source": "manual_override",
                 "reasoning": "admin correction", "source_urls": ["https://school.edu/a"]}

    state = {"status": "draft", "approved": False}

    async def generate(_):
        state["status"] = "draft"
        return {"school_id": school_id, "factor_count": 1, "rubric_status": "draft",
                "factors": [{"factor_key": factor_key, **old_value}]}

    async def list_factors(_):
        return {"school_id": school_id, "rubric_status": state["status"],
                "rubric_approved_at": None, "rubric_approved_by": None,
                "factors": [{"factor_key": factor_key, **(new_value if state.get("overridden") else old_value)}]}

    async def override(*args, **kwargs):
        state["status"] = "draft"
        state["overridden"] = True
        state["approved"] = False
        return {"factor_key": factor_key, "old_value": old_value, "new_value": new_value, "rubric_status": "draft"}

    async def approve(_, *, editor):
        state["status"] = "approved"
        state["approved"] = True
        return {"school_id": school_id, "rubric_status": "approved",
                "rubric_approved_at": datetime.now(timezone.utc), "rubric_approved_by": editor}

    async def score(school, profile):
        if not state["approved"]:
            raise RubricNotApproved("Rubric is not approved for scoring")
        return ScoringResult(
            school_id=school, student_id=profile.student_id, score=88.0,
            per_factor_breakdown=[], skipped=[], weight_mass_used=1.0,
            reasoning="approved fixture", scoring_run_id=uuid4(),
        )

    rubrics = SimpleNamespace(
        generate=AsyncMock(side_effect=generate),
        list_factors=AsyncMock(side_effect=list_factors),
        override_factor=AsyncMock(side_effect=override),
        approve=AsyncMock(side_effect=approve),
        close=AsyncMock(),
    )
    scoring = SimpleNamespace(score=AsyncMock(side_effect=score), close=AsyncMock())
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=dependency(),
                     rubrics=rubrics, scoring=scoring)
    with TestClient(app) as client:
        assert client.post(f"/schools/{school_id}/rubric/generate").status_code == 200
        draft_score = client.post(f"/schools/{school_id}/score", json={"student_id": "stu-1", "attributes": {}})
        assert draft_score.status_code == 409

        patched = client.patch(f"/schools/{school_id}/rubric/{factor_key}", json={
            "value": 3.8, "weight": 0.5, "confidence": 0.95,
            "reasoning": "admin correction", "editor": "fixture-admin", "reason": "fix weight",
        })
        assert patched.status_code == 200
        body = patched.json()
        assert body["old_value"]["weight"] == 0.9
        assert body["new_value"]["weight_source"] == "manual_override"

        approved = client.post(f"/schools/{school_id}/rubric/approve", json={"editor": "fixture-admin"})
        assert approved.status_code == 200
        assert approved.json()["rubric_status"] == "approved"

        allowed = client.post(f"/schools/{school_id}/score", json={"student_id": "stu-1", "attributes": {"gpa": 3.6}})
        assert allowed.status_code == 200
        assert allowed.json()["score"] == 88.0
        assert allowed.json()["score_kind"] == "deterministic_fit_score_v1"


def test_openapi_includes_phase6_routes(settings):
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=dependency(),
                     rubrics=dependency(), scoring=dependency())
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/schools/{school_id}/rubric/{factor_key}" in paths
    assert "/schools/{school_id}/rubric/approve" in paths
    assert "/schools/{school_id}/score" in paths
