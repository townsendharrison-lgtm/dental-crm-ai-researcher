"""Phase 7 scoring — hand-checked weighted math, skips, approval gate."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.scoring_agent import compute_weighted_score, match_score
from app.factor_taxonomy import FactorTaxonomy
from app.main import create_app
from app.rubrics import RubricNotApproved
from app.schemas.scoring import ScoringResult, StudentProfile
from app.tests.test_api import dependency


@pytest.fixture
def taxonomy():
    return FactorTaxonomy.model_validate({
        "version": "synthetic-scoring",
        "categories": ["Academics", "DAT", "Shadowing"],
        "factors": [
            {"key": "avg_gpa", "category": "Academics", "description": "Average GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "avg_dat_aa", "category": "DAT", "description": "DAT AA",
             "value_type": "number", "unit": "points"},
            {"key": "shadowing_hours", "category": "Shadowing", "description": "Shadowing hours",
             "value_type": "number", "unit": "hours"},
            {"key": "mission_alignment", "category": "Academics", "description": "Mission",
             "value_type": "text", "unit": None},
        ],
    })


def test_hand_computed_weighted_score(taxonomy):
    # weight 0.5 @ GPA: student 3.5 / school 3.5 → 1.0 → contribution 0.5
    # weight 0.5 @ DAT: student 18 / school 20 → 0.9 → contribution 0.45
    # score = 100 * (0.95) / 1.0 = 95
    rubric = [
        {"factor_key": "avg_gpa", "value": 3.5, "weight": Decimal("0.5")},
        {"factor_key": "avg_dat_aa", "value": 20, "weight": Decimal("0.5")},
        {"factor_key": "shadowing_hours", "value": 100, "weight": Decimal("0.2")},
    ]
    score, breakdown, skipped, mass = compute_weighted_score(
        taxonomy=taxonomy, rubric_rows=rubric,
        attributes={"avg_gpa": 3.5, "avg_dat_aa": 18},
    )
    assert score == Decimal("95.0000")
    assert mass == Decimal("1.0")
    assert {item.factor_key for item in breakdown} == {"avg_gpa", "avg_dat_aa"}
    assert any(item.factor_key == "shadowing_hours" and item.reason == "missing_student_value" for item in skipped)
    gpa = next(item for item in breakdown if item.factor_key == "avg_gpa")
    dat = next(item for item in breakdown if item.factor_key == "avg_dat_aa")
    assert gpa.factor_score == pytest.approx(1.0)
    assert dat.factor_score == pytest.approx(0.9)
    assert gpa.contribution == pytest.approx(0.5)
    assert dat.contribution == pytest.approx(0.45)


def test_min_avg_max_curve_method():
    score, method = match_score(
        Decimal("3.5"), Decimal("3.5"),
        family_values={"min": Decimal("3.0"), "avg": Decimal("3.5"), "max": Decimal("4.0")},
    )
    assert method == "min_avg_max_curve"
    assert score == Decimal("1.00")


def test_missing_student_factor_is_skipped_not_zeroed(taxonomy):
    rubric = [{"factor_key": "avg_gpa", "value": 3.5, "weight": Decimal("1.0")}]
    score, breakdown, skipped, _ = compute_weighted_score(
        taxonomy=taxonomy, rubric_rows=rubric, attributes={},
    )
    assert score == Decimal("0")
    assert breakdown == []
    assert skipped[0].reason == "missing_student_value"


def test_score_api_rejects_draft_and_returns_full_result_when_approved(settings, taxonomy):
    school_id = uuid4()
    run_id = uuid4()

    async def require_approved(_):
        raise RubricNotApproved("Rubric is not approved for scoring")

    draft_rubrics = SimpleNamespace(require_approved=AsyncMock(side_effect=require_approved), close=AsyncMock())
    draft_scoring = SimpleNamespace(score=AsyncMock(), close=AsyncMock())

    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=dependency(),
                     rubrics=draft_rubrics, scoring=draft_scoring)
    # Route builds ScoringService if scoring.score not used when require fails first...
    # Our route uses scoring.score which should call require inside — mock score to raise.
    draft_scoring.score = AsyncMock(side_effect=RubricNotApproved("Rubric is not approved for scoring"))
    with TestClient(app) as client:
        rejected = client.post(f"/schools/{school_id}/score", json={"student_id": "s1", "attributes": {"avg_gpa": 3.5}})
    assert rejected.status_code == 409

    result = ScoringResult(
        school_id=school_id, student_id="s1", score=95.0,
        per_factor_breakdown=[], skipped=[], weight_mass_used=1.0,
        reasoning="Fixture narrative", scoring_run_id=run_id,
    )
    scoring = SimpleNamespace(score=AsyncMock(return_value=result), close=AsyncMock())
    rubrics = SimpleNamespace(require_approved=AsyncMock(return_value={"rubric_status": "approved"}), close=AsyncMock())
    app2 = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                      documents=dependency(), research=dependency(), normalization=dependency(),
                      rubrics=rubrics, scoring=scoring)
    with TestClient(app2) as client:
        ok = client.post(f"/schools/{school_id}/score", json={
            "student_id": "s1", "attributes": {"avg_gpa": 3.5, "avg_dat_aa": 18},
        })
    assert ok.status_code == 200
    body = ok.json()
    assert body["score"] == 95.0
    assert body["score_kind"] == "deterministic_fit_score_v1"
    assert body["scoring_run_id"] == str(run_id)
