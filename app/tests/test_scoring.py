"""Phase 7 scoring — hand-checked weighted math, not-met policy, approval gate."""
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
    # shadowing missing → not met (0) but still in weight mass (0.2)
    # score = 100 * (0.5+0.45+0) / 1.2 = 79.1667
    rubric = [
        {"factor_key": "avg_gpa", "value": 3.5, "weight": Decimal("0.5")},
        {"factor_key": "avg_dat_aa", "value": 20, "weight": Decimal("0.5")},
        {"factor_key": "shadowing_hours", "value": 100, "weight": Decimal("0.2")},
    ]
    score, breakdown, skipped, mass = compute_weighted_score(
        taxonomy=taxonomy, rubric_rows=rubric,
        attributes={"avg_gpa": 3.5, "avg_dat_aa": 18},
    )
    assert mass == Decimal("1.2")
    assert score == Decimal("79.1667")
    assert {item.factor_key for item in breakdown} == {"avg_gpa", "avg_dat_aa", "shadowing_hours"}
    assert skipped == []
    gpa = next(item for item in breakdown if item.factor_key == "avg_gpa")
    dat = next(item for item in breakdown if item.factor_key == "avg_dat_aa")
    shadow = next(item for item in breakdown if item.factor_key == "shadowing_hours")
    assert gpa.factor_score == pytest.approx(1.0)
    assert dat.factor_score == pytest.approx(0.9)
    assert shadow.factor_score == 0.0
    assert shadow.method == "not_met_missing_student_value"
    assert gpa.contribution == pytest.approx(0.5)
    assert dat.contribution == pytest.approx(0.45)


def test_dat_modern_student_vs_legacy_school_is_aligned(taxonomy):
    """Student 430 (200–600) must not be ratio'd raw against school 20.1 (1–30)."""
    from app.dat_scale import normalize_dat_to_legacy

    rubric = [
        {"factor_key": "avg_dat_aa", "value": 20.1, "weight": Decimal("1.0")},
    ]
    score, breakdown, skipped, _ = compute_weighted_score(
        taxonomy=taxonomy, rubric_rows=rubric,
        attributes={"avg_dat_aa": 430, "dat_score_scale": "IRT_200_600"},
    )
    assert skipped == []
    dat = breakdown[0]
    assert dat.student_value == 430.0
    assert dat.school_expectation == 20.1
    aligned = normalize_dat_to_legacy(430)
    assert aligned == Decimal("20")
    assert dat.factor_score == pytest.approx(float(aligned / Decimal("20.1")))
    assert dat.factor_score < 1.0
    assert "dat_student_modern_to_legacy" in dat.method


def test_dat_same_legacy_scale_unchanged(taxonomy):
    rubric = [{"factor_key": "avg_dat_aa", "value": 20, "weight": Decimal("1.0")}]
    _, breakdown, _, _ = compute_weighted_score(
        taxonomy=taxonomy, rubric_rows=rubric, attributes={"avg_dat_aa": 18},
    )
    assert breakdown[0].factor_score == pytest.approx(0.9)
    assert "dat_same_legacy" in breakdown[0].method


def test_min_avg_max_curve_method():
    score, method = match_score(
        Decimal("3.5"), Decimal("3.5"),
        family_values={"min": Decimal("3.0"), "avg": Decimal("3.5"), "max": Decimal("4.0")},
    )
    assert method == "min_avg_max_curve"
    assert score == Decimal("1.00")


def test_missing_student_factor_counts_as_not_met(taxonomy):
    rubric = [{"factor_key": "avg_gpa", "value": 3.5, "weight": Decimal("1.0")}]
    score, breakdown, skipped, mass = compute_weighted_score(
        taxonomy=taxonomy, rubric_rows=rubric, attributes={},
    )
    assert score == Decimal("0.0000")
    assert mass == Decimal("1.0")
    assert skipped == []
    assert breakdown[0].factor_score == 0.0
    assert breakdown[0].method == "not_met_missing_student_value"
    assert breakdown[0].student_value is None


def test_qualitative_present_counts_as_met(taxonomy):
    rubric = [{"factor_key": "mission_alignment", "value": None, "weight": Decimal("1.0")}]
    score, breakdown, skipped, _ = compute_weighted_score(
        taxonomy=taxonomy, rubric_rows=rubric,
        attributes={"mission_alignment": "Strong community focus"},
    )
    assert skipped == []
    assert breakdown[0].factor_score == 1.0
    assert breakdown[0].method == "qualitative_present_as_met"
    assert score == Decimal("100.0000")


def test_outcome_probabilities_increase_with_fit():
    from app.outcome_probabilities import fit_to_outcome_probabilities

    low = fit_to_outcome_probabilities(30)
    high = fit_to_outcome_probabilities(85)
    assert high["interview_probability"] > low["interview_probability"]
    assert high["acceptance_probability"] > low["acceptance_probability"]
    assert high["reject_probability"] < low["reject_probability"]
    assert low["probability_kind"] == "fit_score_derived_v1"


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
