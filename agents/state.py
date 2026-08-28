from typing import TypedDict, List, Dict, Any, Optional
from schemas.criteria_schema import DentalSchoolProfile, PrerequisiteCourseItem
from schemas.comparison_schema import StudentComparisonProfile
from schemas.prediction_schema import PredictionResult, RequirementCheckItem, OutcomeProbabilities, DiagnosticsExplainability
from schemas.evidence_schema import EvidenceCitation, VerificationStatus

class ResearchGraphState(TypedDict, total=False):
    # Ingestion inputs
    school_id: str
    school_name: str
    cycle: str
    source_url: Optional[str]
    source_file_path: Optional[str]
    source_type: str
    raw_content: str
    
    # Processed data
    parsed_sections: Dict[str, str]
    extracted_criteria: Dict[str, Any]
    citations: List[EvidenceCitation]
    conflict_reports: List[Dict[str, Any]]
    
    # Output Profile
    profile: Optional[DentalSchoolProfile]
    status: str
    logs: List[str]

class ComparisonGraphState(TypedDict, total=False):
    # Input Profiles
    student_profile: StudentComparisonProfile
    school_profile: DentalSchoolProfile
    cycle: str
    include_ai_reasoning: bool
    
    # Analysis steps
    prerequisite_checks: List[RequirementCheckItem]
    gpa_analysis: Dict[str, Any]
    dat_analysis: Dict[str, Any]
    extracurricular_analysis: Dict[str, Any]
    residency_analysis: Dict[str, Any]
    
    # Probabilities and Fit
    match_score: float
    fit_category: str
    probabilities: OutcomeProbabilities
    
    # AI Diagnostics
    diagnostics: DiagnosticsExplainability
    
    # Document Ingestion
    attached_documents_analyzed: List[str]
    document_insights: Dict[str, Any]
    
    # Final Output Result
    final_result: Optional[PredictionResult]
    logs: List[str]

class CalibrationGraphState(TypedDict, total=False):
    school_id: str
    cycle: str
    historical_applications: List[Dict[str, Any]]
    correlations: Dict[str, float]
    state_trends: List[Dict[str, Any]]
    optimized_weights: Dict[str, float]
    calibration_notes: str
    logs: List[str]
