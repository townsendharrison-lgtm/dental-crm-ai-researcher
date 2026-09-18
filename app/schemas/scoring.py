"""Student profile and scoring result contracts for Node ↔ Python scoring."""
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StudentProfile(BaseModel):
    """CRM-owned student snapshot. Keys under `attributes` mirror taxonomy factor keys.

    Common examples: avg_gpa / science_gpa aliases via attributes["avg_gpa"],
    attributes["avg_science_gpa"], attributes["avg_dat_aa"], attributes["shadowing_hours"], etc.
    Missing keys are skipped during scoring — never fabricated.
    """
    model_config = ConfigDict(extra="forbid")
    student_id: str = Field(min_length=1, max_length=200)
    attributes: dict[str, Any] = Field(default_factory=dict)


class FactorScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor_key: str
    category: str
    weight: float
    student_value: float | str | None
    school_expectation: float | str | None
    factor_score: float
    contribution: float
    method: str


class SkippedFactor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor_key: str
    category: str
    reason: Literal[
        "missing_student_value",
        "missing_school_expectation",
        "non_numeric_student_value",
        "zero_weight",
        "not_scoring_eligible",
    ]


class ScoringResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: UUID
    student_id: str
    score: float = Field(description="Deterministic fit score 0–100; not an acceptance probability")
    score_kind: Literal["deterministic_fit_score_v1"] = "deterministic_fit_score_v1"
    per_factor_breakdown: list[FactorScoreBreakdown]
    skipped: list[SkippedFactor]
    weight_mass_used: float
    reasoning: str
    scoring_run_id: UUID | None = None


# Kept for older Phase 6 gate tests / docs references.
class ScoringGateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str
    student_id: str
    rubric_status: str
    scoring_allowed: bool
    detail: str
