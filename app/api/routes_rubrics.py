"""Rubric generation, override, approval and scoring-gate endpoints."""
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agents.scoring_agent import ScoringService
from app.clients.usage_guard import BudgetExceeded, current_school_id
from app.rubrics import RubricNotApproved, RubricNotFound
from app.schemas.rubric import RubricWriteRejected
from app.schemas.scoring import ScoringResult, StudentProfile


class RubricGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: UUID
    factor_count: int
    rubric_status: str = "draft"
    factors: list[dict]


class RubricListResponse(BaseModel):
    school_id: UUID
    rubric_status: str
    rubric_approved_at: datetime | None = None
    rubric_approved_by: str | None = None
    factors: list[dict]


class RubricOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any | None = None
    weight: float | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reasoning: str = Field(min_length=1)
    editor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_urls: list[str] | None = None


class RubricOverrideResponse(BaseModel):
    factor_key: str
    old_value: dict
    new_value: dict
    rubric_status: str


class RubricApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    editor: str = Field(min_length=1)


class RubricApproveResponse(BaseModel):
    school_id: UUID
    rubric_status: str
    rubric_approved_at: datetime
    rubric_approved_by: str


def _rubrics(request: Request):
    service = getattr(request.app.state, "rubrics", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Rubric service is not configured")
    return service


def build_rubric_router() -> APIRouter:
    router = APIRouter(tags=["rubrics"])

    @router.post(
        "/schools/{school_id}/rubric/generate",
        response_model=RubricGenerateResponse,
        summary="Generate a school rubric from raw facts and cross-school stats",
        description="Applies stated → cross_school_inferred → qualitative_inferred tiers. Upserts rubric_factors; never deletes rows; preserves manual_override factors. Resets rubric_status to draft.",
    )
    async def generate_rubric(school_id: UUID, request: Request):
        token = current_school_id.set(school_id)
        try:
            result = await _rubrics(request).generate(school_id)
        except RubricNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except RubricWriteRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except BudgetExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None
        finally:
            current_school_id.reset(token)
        return RubricGenerateResponse(**result)

    @router.get(
        "/schools/{school_id}/rubric",
        response_model=RubricListResponse,
        summary="List current rubric factors for a school",
        description="Returns stored rubric_factors including tiers, confidence, citations and approval status.",
    )
    async def get_rubric(school_id: UUID, request: Request):
        try:
            result = await _rubrics(request).list_factors(school_id)
        except RubricNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return RubricListResponse(**result)

    @router.patch(
        "/schools/{school_id}/rubric/{factor_key}",
        response_model=RubricOverrideResponse,
        summary="Manually override a rubric factor",
        description="Appends an immutable rubric_overrides audit row and updates rubric_factors with weight_source=manual_override. Resets approval to draft.",
    )
    async def override_factor(school_id: UUID, factor_key: str, payload: RubricOverrideRequest, request: Request):
        try:
            result = await _rubrics(request).override_factor(
                school_id, factor_key, value=payload.value, weight=payload.weight,
                confidence=payload.confidence, reasoning=payload.reasoning,
                editor=payload.editor, reason=payload.reason, source_urls=payload.source_urls,
            )
        except RubricNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except RubricWriteRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return RubricOverrideResponse(**result)

    @router.post(
        "/schools/{school_id}/rubric/approve",
        response_model=RubricApproveResponse,
        summary="Approve a school rubric for live scoring",
        description="Marks the school's rubric as approved. Only approved rubrics may be used for scoring.",
    )
    async def approve_rubric(school_id: UUID, payload: RubricApproveRequest, request: Request):
        try:
            result = await _rubrics(request).approve(school_id, editor=payload.editor)
        except RubricNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except RubricWriteRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return RubricApproveResponse(**result)

    @router.post(
        "/schools/{school_id}/score",
        response_model=ScoringResult,
        summary="Score a student against an approved school rubric",
        description="Requires rubric_status=approved. Computes a deterministic fit score 0–100 in code (not calibrated admissions odds). Every scoring-eligible rubric factor is considered; missing student evidence counts as not met (0). Returns fit-derived interview/acceptance/waitlist/reject probability estimates. GPT-4o only narrates the breakdown.",
        responses={409: {"description": "Rubric not approved"}},
    )
    async def score_student(school_id: UUID, profile: StudentProfile, request: Request):
        scoring = getattr(request.app.state, "scoring", None)
        if scoring is None:
            scoring = ScoringService(request.app.state.settings, request.app.state.database, _rubrics(request))
        token = current_school_id.set(school_id)
        try:
            return await scoring.score(school_id, profile)
        except RubricNotApproved as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except RubricNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except BudgetExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None
        finally:
            current_school_id.reset(token)

    return router
