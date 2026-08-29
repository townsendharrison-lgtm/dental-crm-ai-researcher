from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class RequirementCheckItem(BaseModel):
    id: str
    name: str
    category: str = "Prerequisites"  # BCP, Biological, Nonscience, Other Science, DAT, GPA, Shadowing, LOR
    status: str = "MET"              # MET, WARNING, UNMET, RECOMMENDED_MISSING, UNKNOWN
    studentValue: Any
    schoolRequirement: Any
    details: str
    isHardRequirement: bool = True
    citationId: Optional[str] = None

class ProbabilityLift(BaseModel):
    interviewLift: float = 0.0
    acceptanceLift: float = 0.0

class RoiImprovement(BaseModel):
    id: str
    actionTitle: str
    description: str
    category: str                    # DAT, GPA, SHADOWING, VOLUNTEERING, RESEARCH, PREREQUISITES, LOR
    currentMetric: Any
    targetMetric: Any
    probabilityLift: ProbabilityLift
    impactLevel: str = "HIGH"        # HIGH, MEDIUM, MODERATE

class DiagnosticsExplainability(BaseModel):
    mostLikelyReason: str
    mostLimitingFactor: str
    highestRoiImprovements: List[RoiImprovement] = []
    strategicSummary: Optional[str] = None
    actionSteps: List[str] = []

class OutcomeProbabilities(BaseModel):
    interviewProbability: float      # 0 to 100
    acceptedProbability: float       # 0 to 100
    waitlistProbability: float       # 0 to 100
    rejectionProbability: float      # 0 to 100

class PredictionResult(BaseModel):
    schoolId: str
    schoolName: str
    location: str
    fitCategory: str                 # Strong Fit, Target, Reach, Safety, High Risk / Unqualified
    matchScore: float                # 0 to 100
    requirementsStatus: str          # MEETS_ALL, WARNINGS, FAILS_REQUIREMENTS
    requirementsPassedCount: int
    requirementsTotalCount: int
    requirements: List[RequirementCheckItem] = []
    probabilities: OutcomeProbabilities
    diagnostics: DiagnosticsExplainability
    attached_documents_analyzed: List[str] = []
    document_insights: Optional[Dict[str, Any]] = None
