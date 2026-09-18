"""Pure Phase 4 normalization tests — no LLM, no hosted DB required."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.normalization_service import (
    DEFAULT_FAMILY_WEIGHTS,
    SchoolValue,
    compute_distribution,
    default_family_weight,
    distributions_from_facts,
    percentile_rank,
    score_along_min_avg_max,
    z_score,
)
from app.factor_taxonomy import FactorTaxonomy
from app.main import create_app
from app.tests.test_api import dependency


@pytest.fixture
def taxonomy():
    return FactorTaxonomy.model_validate({
        "version": "synthetic-normalization",
        "categories": ["Academics", "DAT"],
        "factors": [
            {"key": "avg_gpa", "category": "Academics", "description": "Average GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "min_gpa", "category": "Academics", "description": "Minimum GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "max_gpa", "category": "Academics", "description": "Maximum GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "avg_dat_aa", "category": "DAT", "description": "Average DAT AA",
             "value_type": "number", "unit": "points"},
            {"key": "mission_alignment", "category": "Academics", "description": "text only",
             "value_type": "text", "unit": None},
        ],
    })


def five_school_gpa_facts():
    """Five schools with avg_gpa 3.2, 3.4, 3.5, 3.6, 3.8 — hand-checkable ranks."""
    values = [Decimal("3.2"), Decimal("3.4"), Decimal("3.5"), Decimal("3.6"), Decimal("3.8")]
    schools = [uuid4() for _ in values]
    facts = []
    for school_id, value in zip(schools, values):
        facts.append({
            "id": uuid4(), "school_id": school_id, "factor_key": "avg_gpa", "value": float(value),
            "unit": "gpa", "confidence": 0.9,
            "extracted_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        })
    return schools, values, facts


def test_percentile_ranks_for_five_schools(taxonomy):
    schools, values, facts = five_school_gpa_facts()
    dist = distributions_from_facts(facts, taxonomy)[0]
    assert dist.factor_key == "avg_gpa"
    assert dist.sample_size == 5
    assert dist.min_value == Decimal("3.2")
    assert dist.max_value == Decimal("3.8")
    assert dist.mean_value == Decimal("3.5")
    # Average-rank percentiles: for sorted unique values, rank i → (i+0.5)/n * 100
    expected = {
        str(schools[0]): 10.0,   # (0+0.5)/5*100
        str(schools[1]): 30.0,
        str(schools[2]): 50.0,
        str(schools[3]): 70.0,
        str(schools[4]): 90.0,
    }
    for school_id, rank in expected.items():
        assert dist.percentiles["by_school"][school_id]["percentile_rank"] == pytest.approx(rank)
    mid = dist.percentiles["by_school"][str(schools[2])]
    assert mid["z_score"] == pytest.approx(0.0)
    assert float(percentile_rank(Decimal("3.5"), values)) == pytest.approx(50.0)


def test_dat_distribution_and_z_scores(taxonomy):
    schools = [uuid4() for _ in range(5)]
    dat_values = [18, 19, 20, 21, 22]
    facts = [
        {"id": uuid4(), "school_id": school_id, "factor_key": "avg_dat_aa", "value": value,
         "unit": "points", "confidence": 0.8,
         "extracted_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        for school_id, value in zip(schools, dat_values)
    ]
    dist = distributions_from_facts(facts, taxonomy)[0]
    assert dist.mean_value == Decimal("20")
    assert dist.stddev_value > 0
    top = dist.percentiles["by_school"][str(schools[-1])]
    assert top["percentile_rank"] == pytest.approx(90.0)
    assert top["z_score"] == pytest.approx(float(z_score(Decimal("22"), Decimal("20"), dist.stddev_value)))


def test_text_factors_are_ignored(taxonomy):
    facts = [{
        "id": uuid4(), "school_id": uuid4(), "factor_key": "mission_alignment",
        "value": "service", "unit": None, "confidence": 0.9,
        "extracted_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }]
    assert distributions_from_facts(facts, taxonomy) == []


def test_min_avg_max_family_weights_are_documented_defaults():
    assert default_family_weight("min_gpa") == DEFAULT_FAMILY_WEIGHTS["min"] == Decimal("0.05")
    assert default_family_weight("avg_gpa") == DEFAULT_FAMILY_WEIGHTS["avg"] == Decimal("0.90")
    assert default_family_weight("max_gpa") == DEFAULT_FAMILY_WEIGHTS["max"] == Decimal("0.05")
    assert default_family_weight("avg_gpa") + default_family_weight("min_gpa") + default_family_weight("max_gpa") == 1
    assert default_family_weight("mission_alignment") is None
    assert default_family_weight("avg_gpa", overrides={"avg": Decimal("0.8")}) == Decimal("0.8")


def test_position_curve_anchors_min_avg_max():
    # Hand-checked anchors from the documented curve.
    assert score_along_min_avg_max(3.0, 3.0, 3.5, 4.0) == Decimal("0.05")
    assert score_along_min_avg_max(3.5, 3.0, 3.5, 4.0) == Decimal("1.00")
    assert score_along_min_avg_max(4.0, 3.0, 3.5, 4.0) == Decimal("0.95")
    # Midway min→avg: 3.25 → 0.05 + 0.5*(1-0.05) = 0.525
    assert score_along_min_avg_max(3.25, 3.0, 3.5, 4.0) == Decimal("0.525")
    # Midway avg→max: 3.75 → 1.0 + 0.5*(0.95-1) = 0.975
    assert score_along_min_avg_max(3.75, 3.0, 3.5, 4.0) == Decimal("0.975")
    assert score_along_min_avg_max(2.5, 3.0, 3.5, 4.0) == Decimal("0.05")
    assert score_along_min_avg_max(4.5, 3.0, 3.5, 4.0) == Decimal("0.95")


def test_compute_distribution_rejects_empty():
    with pytest.raises(ValueError):
        compute_distribution("avg_gpa", "gpa", [])


def test_best_fact_per_school_prefers_higher_confidence(taxonomy):
    school_id = uuid4()
    facts = [
        {"id": uuid4(), "school_id": school_id, "factor_key": "avg_gpa", "value": 3.1,
         "unit": "gpa", "confidence": 0.4, "extracted_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"id": uuid4(), "school_id": school_id, "factor_key": "avg_gpa", "value": 3.7,
         "unit": "gpa", "confidence": 0.95, "extracted_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ]
    # Need ≥1 school; add four more so sample is fine
    for value in (3.2, 3.3, 3.4, 3.5):
        facts.append({
            "id": uuid4(), "school_id": uuid4(), "factor_key": "avg_gpa", "value": value,
            "unit": "gpa", "confidence": 0.9, "extracted_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        })
    dist = distributions_from_facts(facts, taxonomy)[0]
    assert dist.percentiles["by_school"][str(school_id)]["value"] == pytest.approx(3.7)


def test_normalization_api_recompute(settings):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    service = SimpleNamespace(recompute=AsyncMock(return_value={
        "method": "phase4_empirical_v1", "factor_count": 1,
        "factors": [{"factor_key": "avg_gpa", "unit": "gpa", "sample_size": 5,
                     "min_value": 3.2, "max_value": 3.8, "mean_value": 3.5, "stddev_value": 0.2}],
    }))
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=service)
    with TestClient(app) as client:
        assert "/normalization/recompute" in client.get("/openapi.json").json()["paths"]
        response = client.post("/normalization/recompute")
    assert response.status_code == 200
    assert response.json()["factor_count"] == 1
