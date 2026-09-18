"""Deterministic school-fit scoring against an approved rubric. LLM explains only."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from app.agents.normalization_service import family_role, score_along_min_avg_max
from app.clients.llm_client import LLMClient, ExtractionFailed
from app.db.models import RubricFactor, ScoringRun
from app.factor_taxonomy import FactorTaxonomy, get_taxonomy
from app.rubrics import RubricNotApproved, RubricService
from app.schemas.scoring import (
    FactorScoreBreakdown,
    ScoringResult,
    SkippedFactor,
    StudentProfile,
)

RUBRIC = RubricFactor.__table__
RUNS = ScoringRun.__table__


def _as_number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except Exception:
            return None
    return None


def match_score(
    student: Decimal,
    school_expectation: Decimal | None,
    *,
    family_values: Mapping[str, Decimal] | None = None,
) -> tuple[Decimal, str] | None:
    """Return (0–1 factor score, method) or None if expectation cannot be used."""
    family_values = family_values or {}
    minimum, average, maximum = family_values.get("min"), family_values.get("avg"), family_values.get("max")
    if minimum is not None and average is not None and maximum is not None and minimum <= average <= maximum:
        return score_along_min_avg_max(student, minimum, average, maximum), "min_avg_max_curve"
    if school_expectation is None:
        return None
    if school_expectation <= 0:
        return None
    ratio = student / school_expectation
    if ratio >= 1:
        return Decimal("1"), "ratio_capped"
    if ratio <= 0:
        return Decimal("0"), "ratio_capped"
    return ratio, "ratio_to_expectation"


def compute_weighted_score(
    *,
    taxonomy: FactorTaxonomy,
    rubric_rows: list[dict],
    attributes: Mapping[str, Any],
) -> tuple[Decimal, list[FactorScoreBreakdown], list[SkippedFactor], Decimal]:
    """Pure deterministic scoring. No LLM."""
    by_key = {row["factor_key"]: row for row in rubric_rows}
    family_expectations: dict[str, dict[str, Decimal]] = {}
    for key, row in by_key.items():
        role = family_role(key)
        number = _as_number(row.get("value"))
        if role is None or number is None:
            continue
        # Group by stem after min_/avg_/max_
        stem = key.split("_", 1)[1]
        family_expectations.setdefault(stem, {})[role] = number

    breakdown: list[FactorScoreBreakdown] = []
    skipped: list[SkippedFactor] = []
    weighted_sum = Decimal("0")
    weight_mass = Decimal("0")

    for row in rubric_rows:
        key = row["factor_key"]
        definition = taxonomy.by_key.get(key)
        if definition is None:
            continue
        category = definition.category
        weight = _as_number(row.get("weight"))
        if weight is None or weight == 0:
            skipped.append(SkippedFactor(factor_key=key, category=category, reason="zero_weight"))
            continue
        if not definition.scoring_eligible:
            skipped.append(SkippedFactor(factor_key=key, category=category, reason="not_scoring_eligible"))
            continue
        if key not in attributes or attributes[key] is None or attributes[key] == "":
            skipped.append(SkippedFactor(factor_key=key, category=category, reason="missing_student_value"))
            continue
        if definition.value_type != "number":
            # Qualitative factors need structured student evidence; Phase 7 skips rather than invents.
            skipped.append(SkippedFactor(factor_key=key, category=category, reason="non_numeric_student_value"))
            continue
        student = _as_number(attributes[key])
        if student is None:
            skipped.append(SkippedFactor(factor_key=key, category=category, reason="non_numeric_student_value"))
            continue
        school_val = _as_number(row.get("value"))
        role = family_role(key)
        stem = key.split("_", 1)[1] if role else None
        family_vals = family_expectations.get(stem or "", {})
        matched = match_score(student, school_val, family_values=family_vals if len(family_vals) == 3 else None)
        if matched is None:
            skipped.append(SkippedFactor(factor_key=key, category=category, reason="missing_school_expectation"))
            continue
        factor_score, method = matched
        contribution = weight * factor_score
        weighted_sum += contribution
        weight_mass += weight
        breakdown.append(FactorScoreBreakdown(
            factor_key=key, category=category, weight=float(weight),
            student_value=float(student),
            school_expectation=float(school_val) if school_val is not None else None,
            factor_score=float(factor_score), contribution=float(contribution), method=method,
        ))

    if weight_mass == 0:
        score = Decimal("0")
    else:
        score = (weighted_sum / weight_mass * Decimal(100)).quantize(Decimal("0.0001"))
    return score, breakdown, skipped, weight_mass


class ScoringService:
    def __init__(self, settings, database, rubrics: RubricService, *, taxonomy=None, llm=None):
        self.settings = settings
        self.database = database
        self.rubrics = rubrics
        self.taxonomy = taxonomy or get_taxonomy()
        self.llm = llm or LLMClient(settings)

    async def _load_rubric_rows(self, school_id: UUID) -> list[dict]:
        async def read(connection):
            rows = (await connection.execute(select(RUBRIC).where(RUBRIC.c.school_id == school_id))).mappings().all()
            return [dict(row) for row in rows]
        return await self.database.transaction("read_rubric_for_scoring", read)

    async def score(self, school_id: UUID, profile: StudentProfile) -> ScoringResult:
        await self.rubrics.require_approved(school_id)
        rows = await self._load_rubric_rows(school_id)
        score, breakdown, skipped, weight_mass = compute_weighted_score(
            taxonomy=self.taxonomy, rubric_rows=rows, attributes=profile.attributes,
        )
        snapshot = {
            row["factor_key"]: {
                "value": row.get("value"), "weight": float(row["weight"]) if row.get("weight") is not None else None,
                "weight_source": row.get("weight_source"), "confidence": float(row["confidence"]),
                "source_urls": list(row.get("source_urls") or []),
            }
            for row in rows
        }
        try:
            reasoning = await self.llm.explain_score(
                score=float(score), breakdown=[item.model_dump() for item in breakdown],
                skipped=[item.model_dump() for item in skipped],
            )
        except ExtractionFailed:
            reasoning = (
                f"Deterministic fit score {float(score)} using weight mass {float(weight_mass)}. "
                f"Scored {len(breakdown)} factors; skipped {len(skipped)}. "
                "Narrative generation failed; math above is authoritative."
            )

        run_id = uuid4()

        async def write(connection):
            await connection.execute(insert(RUNS).values(
                id=run_id, school_id=school_id, student_id=profile.student_id,
                score=score,
                per_factor_breakdown={
                    "factors": [item.model_dump() for item in breakdown],
                    "skipped": [item.model_dump() for item in skipped],
                    "weight_mass_used": float(weight_mass),
                    "score_kind": "deterministic_fit_score_v1",
                },
                rubric_snapshot=snapshot,
                reasoning=reasoning,
            ))
        await self.database.transaction("insert_scoring_run", write)

        return ScoringResult(
            school_id=school_id, student_id=profile.student_id, score=float(score),
            per_factor_breakdown=breakdown, skipped=skipped,
            weight_mass_used=float(weight_mass), reasoning=reasoning, scoring_run_id=run_id,
        )

    async def close(self):
        await self.llm.close()
