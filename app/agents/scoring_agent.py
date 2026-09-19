"""Deterministic school-fit scoring against an approved rubric. LLM explains only."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from app.agents.normalization_service import family_role, score_along_min_avg_max
from app.clients.llm_client import LLMClient, ExtractionFailed
from app.dat_scale import align_dat_pair, is_dat_section_factor, normalize_dat_to_legacy, normalize_dat_to_modern, detect_dat_scale
from app.db.models import RubricFactor, ScoringRun
from app.factor_taxonomy import FactorTaxonomy, get_taxonomy
from app.outcome_probabilities import fit_to_outcome_probabilities
from app.rubrics import RubricNotApproved, RubricService
from app.schemas.scoring import (
    FactorScoreBreakdown,
    OutcomeProbabilities,
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
    """Pure deterministic scoring. No LLM.

    Every scoring-eligible rubric factor with weight > 0 is considered.
    Missing / unusable student values count as not met (factor score 0), not skipped.
    """
    by_key = {row["factor_key"]: row for row in rubric_rows}
    student_dat_scale_hint = attributes.get("dat_score_scale")
    scale_hint = student_dat_scale_hint if isinstance(student_dat_scale_hint, str) else None

    family_expectations: dict[str, dict[str, Decimal]] = {}
    for key, row in by_key.items():
        role = family_role(key)
        number = _as_number(row.get("value"))
        if role is None or number is None:
            continue
        stem = key.split("_", 1)[1]
        family_expectations.setdefault(stem, {})[role] = number

    breakdown: list[FactorScoreBreakdown] = []
    skipped: list[SkippedFactor] = []
    weighted_sum = Decimal("0")
    weight_mass = Decimal("0")

    def _record_not_met(key: str, category: str, weight: Decimal, school_val: Decimal | None, method: str):
        nonlocal weighted_sum, weight_mass
        factor_score = Decimal("0")
        contribution = weight * factor_score
        weighted_sum += contribution
        weight_mass += weight
        breakdown.append(FactorScoreBreakdown(
            factor_key=key, category=category, weight=float(weight),
            student_value=None,
            school_expectation=float(school_val) if school_val is not None else None,
            factor_score=0.0, contribution=float(contribution), method=method,
        ))

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

        school_val = _as_number(row.get("value"))
        raw_attr = attributes.get(key)
        missing = key not in attributes or raw_attr is None or raw_attr == ""

        # Missing student evidence → not met (0), still counts against the score.
        if missing:
            _record_not_met(key, category, weight, school_val, "not_met_missing_student_value")
            continue

        # Qualitative / text factors: presence counts as met (1.0); absence already handled.
        if definition.value_type != "number":
            factor_score = Decimal("1")
            contribution = weight * factor_score
            weighted_sum += contribution
            weight_mass += weight
            breakdown.append(FactorScoreBreakdown(
                factor_key=key, category=category, weight=float(weight),
                student_value=str(raw_attr)[:200],
                school_expectation=None if school_val is None else float(school_val),
                factor_score=1.0, contribution=float(contribution),
                method="qualitative_present_as_met",
            ))
            continue

        student = _as_number(raw_attr)
        if student is None:
            _record_not_met(key, category, weight, school_val, "not_met_non_numeric_student_value")
            continue

        role = family_role(key)
        stem = key.split("_", 1)[1] if role else None
        family_vals = family_expectations.get(stem or "", {})
        method_suffix = ""

        if is_dat_section_factor(key):
            aligned = align_dat_pair(student, school_val, student_scale_hint=scale_hint)
            if aligned is None:
                # Unresolvable scale still counts as not met rather than vanishing.
                _record_not_met(key, category, weight, school_val, "not_met_dat_scale_mismatch")
                continue
            compare_student, compare_school, method_suffix = aligned
            if len(family_vals) == 3 and compare_school is not None:
                school_scale = detect_dat_scale(compare_school)
                converter = normalize_dat_to_legacy if school_scale == "legacy" else normalize_dat_to_modern
                converted = {}
                ok = True
                for fam_role, fam_val in family_vals.items():
                    converted_val = converter(fam_val)
                    if converted_val is None:
                        ok = False
                        break
                    converted[fam_role] = converted_val
                family_for_match = converted if ok else None
            else:
                family_for_match = family_vals if len(family_vals) == 3 else None
            matched = match_score(compare_student, compare_school, family_values=family_for_match)
        else:
            matched = match_score(
                student, school_val,
                family_values=family_vals if len(family_vals) == 3 else None,
            )

        if matched is None:
            # School has no usable expectation — still score 0 so the factor is visible.
            _record_not_met(key, category, weight, school_val, "not_met_missing_school_expectation")
            continue

        factor_score, method = matched
        if method_suffix:
            method = f"{method}+{method_suffix}"
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
            not_met = sum(1 for item in breakdown if item.method.startswith("not_met_"))
            reasoning = (
                f"Deterministic fit score {float(score)} using weight mass {float(weight_mass)}. "
                f"Scored {len(breakdown)} factors ({not_met} counted as not met); "
                f"skipped {len(skipped)} (zero-weight / not eligible). "
                "Narrative generation failed; math above is authoritative."
            )

        probabilities = OutcomeProbabilities(**fit_to_outcome_probabilities(float(score)))
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
                    "probabilities": probabilities.model_dump(),
                },
                rubric_snapshot=snapshot,
                reasoning=reasoning,
            ))
        await self.database.transaction("insert_scoring_run", write)

        return ScoringResult(
            school_id=school_id, student_id=profile.student_id, score=float(score),
            per_factor_breakdown=breakdown, skipped=skipped,
            weight_mass_used=float(weight_mass), reasoning=reasoning, scoring_run_id=run_id,
            probabilities=probabilities,
        )

    async def close(self):
        await self.llm.close()
