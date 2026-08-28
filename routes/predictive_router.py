from typing import Dict, Any, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from schemas.prediction_schema import PredictionResult
from schemas.comparison_schema import StudentComparisonProfile
from agents.student_comparison_agent import comparison_graph_app
from data.seed_schools import get_school_by_id_or_default

router = APIRouter(prefix="/api/predict", tags=["Predictive Simulator"])

class WhatIfSimulationPayload(BaseModel):
    school_id: str
    cgpa: float
    dat_aa: int
    shadowing_hours: int
    volunteering_hours: int = 100
    research_hours: int = 50
    state: str = "Massachusetts"
    include_ai_reasoning: bool = False

@router.post("/what-if", response_model=PredictionResult)
async def simulate_what_if_admission(payload: WhatIfSimulationPayload):
    """
    Real-time What-If simulator endpoint for live sliders in the frontend.
    """
    student = StudentComparisonProfile(
        name="Simulated Candidate",
        cgpa=payload.cgpa,
        sgpa=payload.cgpa - 0.05,
        dat_aa=payload.dat_aa,
        dat_ts=payload.dat_aa,
        shadowing_hours=payload.shadowing_hours,
        volunteering_hours=payload.volunteering_hours,
        research_hours=payload.research_hours,
        state=payload.state
    )
    
    school = get_school_by_id_or_default(payload.school_id)
    
    state_input = {
        "student_profile": student,
        "school_profile": school,
        "cycle": "2025-2026",
        "include_ai_reasoning": payload.include_ai_reasoning,
        "logs": []
    }
    
    result = await comparison_graph_app.ainvoke(state_input)
    return result["final_result"]
