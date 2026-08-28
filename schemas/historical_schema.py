from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class HistoricalApplicationItem(BaseModel):
    id: Optional[str] = None
    student_name_anonymized: str = "Applicant"
    school_id: str
    school_name: str
    cycle: str = "2024-2025"
    cgpa: float
    sgpa: Optional[float] = None
    dat_aa: int
    dat_ts: int
    dat_pat: Optional[int] = None
    shadowing_hours: int = 0
    volunteering_hours: int = 0
    dental_experience_hours: int = 0
    research_hours: int = 0
    is_in_state: bool = False
    state: Optional[str] = None
    applicant_type: str = "FIRST_TIME"
    outcome: str                      # ACCEPTED, INTERVIEWED, WAITLISTED, REJECTED
    source: str = "CSV_UPLOAD"        # CRM_SYNC, CSV_UPLOAD, MANUAL_ENTRY, RESEARCH_CASE
    notes: Optional[str] = None

class HistoricalUploadPayload(BaseModel):
    school_id: Optional[str] = None
    cycle: str = "2024-2025"
    applications: List[HistoricalApplicationItem]

class StateTrendInsight(BaseModel):
    state: str
    total_applicants: int
    accepted_count: int
    acceptance_rate: float
    is_preferred_state: bool = False

class RubricCalibrationResult(BaseModel):
    school_id: str
    school_name: str
    analyzed_applications_count: int
    calibrated_weights: Dict[str, float]  # gpaWeight, datWeight, shadowingWeight, volunteeringWeight, researchWeight, inStateWeight, lorWeight
    observed_state_trends: List[StateTrendInsight] = []
    top_determining_factors: List[str] = []
    confidence_level: float = 0.92
    calibration_notes: str
    calibrated_at: str
