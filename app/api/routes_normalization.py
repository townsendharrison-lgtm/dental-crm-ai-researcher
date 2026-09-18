"""Phase 4 normalization endpoints — recompute and read cross-school stats."""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict


class NormalizationRecomputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str
    factor_count: int
    factors: list[dict]


class CrossSchoolStatResponse(BaseModel):
    factor_key: str
    unit: str
    sample_size: int
    min_value: float
    max_value: float
    mean_value: float
    stddev_value: float
    percentiles: dict
    method: str
    provenance: dict


def _normalization(request: Request):
    service = getattr(request.app.state, "normalization", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Normalization service is not configured")
    return service


def build_normalization_router() -> APIRouter:
    router = APIRouter(tags=["normalization"])

    @router.post(
        "/normalization/recompute",
        response_model=NormalizationRecomputeResponse,
        summary="Recompute cross-school numeric reference stats",
        description="Pure deterministic recompute of cross_school_stats from numeric school_raw_facts. Upserts only; never deletes existing rows. No LLM calls.",
    )
    async def recompute(request: Request):
        return NormalizationRecomputeResponse(**(await _normalization(request).recompute()))

    @router.get(
        "/normalization/stats/{factor_key}",
        response_model=CrossSchoolStatResponse,
        summary="Read cross-school stats for one factor",
        description="Returns the latest upserted distribution for a factor/unit pair.",
    )
    async def get_stat(factor_key: str, request: Request, unit: str = Query(default="")):
        row = await _normalization(request).get_stat(factor_key, unit)
        if row is None:
            raise HTTPException(status_code=404, detail="No cross-school stats for this factor")
        return CrossSchoolStatResponse(
            factor_key=row["factor_key"],
            unit=row["unit"],
            sample_size=row["sample_size"],
            min_value=float(row["min_value"]),
            max_value=float(row["max_value"]),
            mean_value=float(row["mean_value"]),
            stddev_value=float(row["stddev_value"]),
            percentiles=row["percentiles"],
            method=row["method"],
            provenance=row["provenance"],
        )

    return router
