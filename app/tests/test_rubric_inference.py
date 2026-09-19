"""Phase 5 rubric inference tests — stated / cross-school / qualitative tiers."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.agents.normalization_service import default_family_weight
from app.agents.rubric_inference_agent import (
    FactView,
    RubricInferenceAgent,
    normalize_category_weights,
    parse_stated_weights,
)
from app.clients.llm_client import LLMClient
from app.factor_taxonomy import FactorTaxonomy
from app.main import create_app
from app.schemas.rubric import QUALITATIVE_BUCKET_WEIGHTS, RubricFactorDraft, RubricWriteRejected
from app.tests.test_api import dependency


@pytest.fixture
def taxonomy():
    return FactorTaxonomy.model_validate({
        "version": "synthetic-rubric",
        "categories": ["Academics", "School Fit"],
        "factors": [
            {"key": "avg_gpa", "category": "Academics", "description": "Average GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "min_gpa", "category": "Academics", "description": "Minimum GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "academics_stated_weights", "category": "Academics",
             "description": "Stated academics weights", "value_type": "text", "unit": None},
            {"key": "mission_alignment", "category": "School Fit",
             "description": "Mission alignment", "value_type": "text", "unit": None},
        ],
    })


def fact(**changes):
    base = dict(
        id=str(uuid4()), factor_key="avg_gpa", value=3.7, unit="gpa", confidence=Decimal("0.9"),
        source_type="web", source_url="https://school.edu/admissions", document_id=None,
        raw_text_snippet="Average overall GPA 3.7",
    )
    base.update(changes)
    return FactView(**base)


def test_parse_stated_weights_exact(taxonomy):
    stated_fact = fact(
        factor_key="academics_stated_weights",
        value="avg_gpa: 25%",
        raw_text_snippet="Published weights avg_gpa: 25%",
        source_url="https://school.edu/weights",
    )
    parsed = parse_stated_weights([stated_fact], taxonomy)
    assert parsed["avg_gpa"][0] == Decimal("0.25")


async def test_stated_tier_preserves_exact_weight(taxonomy):
    school_id = uuid4()
    stated_fact = fact(
        factor_key="academics_stated_weights", value="avg_gpa: 25%",
        raw_text_snippet="avg_gpa: 25%", source_url="https://school.edu/weights",
    )
    gpa = fact(value=3.7, source_url="https://school.edu/admissions")
    agent = RubricInferenceAgent(taxonomy, llm=None)
    drafts = await agent.infer(
        school_id=school_id, official_url="https://school.edu",
        facts=[stated_fact, gpa], stats_by_key={},
    )
    by_key = {d.factor_key: d for d in drafts}
    assert by_key["avg_gpa"].weight_source == "stated"
    assert by_key["avg_gpa"].weight == Decimal("0.25")
    assert by_key["avg_gpa"].source_urls == ["https://school.edu/weights"]


async def test_cross_school_tier_uses_phase4_family_weight(taxonomy):
    school_id = uuid4()
    gpa = fact(value=3.7)
    stats = {
        "avg_gpa": {
            "sample_size": 5,
            "percentiles": {"by_school": {str(school_id): {"percentile_rank": 90.0, "z_score": 1.2}}},
        }
    }
    agent = RubricInferenceAgent(taxonomy, llm=None)
    drafts = await agent.infer(
        school_id=school_id, official_url="https://school.edu",
        facts=[gpa], stats_by_key=stats,
    )
    row = next(d for d in drafts if d.factor_key == "avg_gpa")
    assert row.weight_source == "cross_school_inferred"
    # Only one weighted Academics factor → category norm scales provisional to 1.0
    assert row.weight == Decimal("1.00000000")
    assert "scaled by cross-school distinctiveness" in row.reasoning
    assert "90.0" in row.reasoning
    assert row.source_urls


async def test_cross_school_weights_differ_by_percentile(taxonomy):
    """Two schools with different GPA percentiles must get different provisional weights."""
    from app.agents.normalization_service import school_specific_provisional_weight

    high_id, low_id = uuid4(), uuid4()
    gpa = fact(value=3.7)
    agent = RubricInferenceAgent(taxonomy, llm=None)

    high_stats = {
        "avg_gpa": {
            "sample_size": 10,
            "percentiles": {"by_school": {str(high_id): {"percentile_rank": 95.0}}},
        }
    }
    low_stats = {
        "avg_gpa": {
            "sample_size": 10,
            "percentiles": {"by_school": {str(low_id): {"percentile_rank": 10.0}}},
        }
    }
    high = agent.draft_cross_school(
        best={"avg_gpa": gpa}, stated_keys=set(), stats_by_key=high_stats,
        school_id=high_id, official_url="https://high.edu",
    )[0]
    low = agent.draft_cross_school(
        best={"avg_gpa": gpa}, stated_keys=set(), stats_by_key=low_stats,
        school_id=low_id, official_url="https://low.edu",
    )[0]
    assert high.weight > low.weight
    assert high.weight == school_specific_provisional_weight("avg_gpa", percentile_rank=95.0)
    assert low.weight == school_specific_provisional_weight("avg_gpa", percentile_rank=10.0)


async def test_infer_materializes_full_taxonomy_with_pending_slots():
    """Rubric must cover every client category/sub-factor, not only extracted facts."""
    from app.factor_taxonomy import get_taxonomy

    taxonomy = get_taxonomy()
    school_id = uuid4()
    agent = RubricInferenceAgent(taxonomy, llm=None)
    drafts = await agent.infer(
        school_id=school_id,
        official_url="https://school.edu",
        facts=[fact(value=3.7)],
        stats_by_key={},
    )
    by_key = {d.factor_key: d for d in drafts}
    expected = [f.key for f in taxonomy.factors if not f.key.endswith("_stated_weights")]
    assert len(by_key) == len(expected)
    assert set(by_key) == set(expected)
    categories = {taxonomy.by_key[k].category for k in by_key}
    assert categories == set(taxonomy.categories)
    assert by_key["avg_gpa"].weight_source == "cross_school_inferred"
    assert by_key["avg_gpa"].weight and by_key["avg_gpa"].weight > 0
    pending = [d for d in drafts if d.weight_source == "pending_evidence"]
    assert pending
    assert all(d.weight == Decimal("0") for d in pending)
    assert "mission_alignment" in by_key
    assert by_key["mission_alignment"].weight_source == "pending_evidence"


async def test_cross_school_provisional_matches_phase4_before_norm(taxonomy):
    school_id = uuid4()
    agent = RubricInferenceAgent(taxonomy, llm=None)
    drafts = agent.draft_cross_school(
        best={"avg_gpa": fact(), "min_gpa": fact(factor_key="min_gpa", value=3.2)},
        stated_keys=set(), stats_by_key={}, school_id=school_id,
        official_url="https://school.edu",
    )
    by_key = {d.factor_key: d for d in drafts}
    assert by_key["avg_gpa"].weight == Decimal("0.90")
    assert by_key["min_gpa"].weight == Decimal("0.05")


def make_qual_llm(settings, payload: dict):
    async def handle(request: httpx.Request):
        import json
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "created": 1, "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(payload),
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    sdk = AsyncOpenAI(api_key="synthetic-key", http_client=http_client, max_retries=0)
    return LLMClient(settings, client=sdk)


async def test_qualitative_tier_maps_bucket_in_code(settings, taxonomy):
    school_id = uuid4()
    mission = fact(
        factor_key="mission_alignment", value="service to underserved communities",
        unit=None, raw_text_snippet="Our mission is service to underserved communities.",
        source_url="https://school.edu/mission",
    )
    llm = make_qual_llm(settings, {
        "weight_bucket": "high",
        "reasoning": "Mission text emphasizes underserved communities.",
        "source_urls": ["https://school.edu/mission"],
    })
    agent = RubricInferenceAgent(taxonomy, llm=llm)
    try:
        # Bucket → numeric mapping happens in code before category normalization.
        provisional = await agent.draft_qualitative(
            best={"mission_alignment": mission}, facts=[mission],
            occupied_keys=set(), official_url="https://school.edu",
        )
        drafts = await agent.infer(
            school_id=school_id, official_url="https://school.edu",
            facts=[mission], stats_by_key={},
        )
    finally:
        await llm.close()
    assert provisional[0].weight == QUALITATIVE_BUCKET_WEIGHTS["high"] == Decimal("0.30")
    row = next(d for d in drafts if d.factor_key == "mission_alignment")
    assert row.weight_source == "qualitative_inferred"
    # Sole School Fit factor → category norm scales provisional 0.30 up to 1.0
    assert row.weight == Decimal("1.00000000")
    assert row.reasoning
    assert row.source_urls == ["https://school.edu/mission"]


def test_reject_ungrounded_automated_write():
    with pytest.raises((RubricWriteRejected, Exception), match="source_url"):
        RubricFactorDraft(
            factor_key="avg_gpa", weight=Decimal("0.2"), weight_source="cross_school_inferred",
            confidence=Decimal("0.5"), reasoning="missing urls", source_urls=[],
        )


def test_category_normalization_preserves_stated_and_scales_inferred(taxonomy):
    stated = RubricFactorDraft(
        factor_key="avg_gpa", weight=Decimal("0.25"), weight_source="stated",
        confidence=Decimal("0.9"), reasoning="stated", source_urls=["https://school.edu/w"],
    )
    inferred = RubricFactorDraft(
        factor_key="min_gpa", weight=Decimal("0.05"), weight_source="cross_school_inferred",
        confidence=Decimal("0.8"), reasoning="curve", source_urls=["https://school.edu/a"],
    )
    # min_gpa and avg_gpa both Academics — stated 0.25, inferred fills 0.75
    out = {d.factor_key: d for d in normalize_category_weights([stated, inferred], taxonomy)}
    assert out["avg_gpa"].weight == Decimal("0.25")
    assert out["min_gpa"].weight == Decimal("0.75000000")


def test_generate_endpoint(settings):
    rubrics = SimpleNamespace(generate=AsyncMock(return_value={
        "school_id": uuid4(), "factor_count": 1, "rubric_status": "draft",
        "factors": [{"factor_key": "avg_gpa", "weight_source": "stated", "weight": "0.25",
                     "confidence": "0.9", "reasoning": "x", "source_urls": ["https://school.edu"]}],
    }))
    app = create_app(settings, database=dependency(), queue=dependency(), storage=dependency(),
                     documents=dependency(), research=dependency(), normalization=dependency(),
                     rubrics=rubrics)
    school_id = rubrics.generate.return_value["school_id"]
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/schools/{school_id}/rubric/generate" in paths
        response = client.post(f"/schools/{school_id}/rubric/generate")
    assert response.status_code == 200
    assert response.json()["factor_count"] == 1
