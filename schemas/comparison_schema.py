from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from schemas.prediction_schema import PredictionResult

class StudentCompletedCourse(BaseModel):
    course_name: str
    category: str                    # BCP, Biological, Nonscience, Other Science
    grade: str = "A"
    credit_hours: float = 4.0
    term_type: str = "Semester"      # Semester, Quarter
    has_lab: bool = True
    institution: Optional[str] = None
    is_cc_or_online: bool = False

class StudentComparisonProfile(BaseModel):
    id: Optional[str] = None
    name: str = "Student"
    email: Optional[str] = None
    cgpa: float = 3.65
    sgpa: float = 3.58
    bcp_gpa: Optional[float] = 3.55
    dat_aa: int = 21
    dat_ts: int = 21
    dat_pat: int = 20
    dat_bio: Optional[int] = 21
    dat_gc: Optional[int] = 21
    dat_oc: Optional[int] = 20
    dat_rc: Optional[int] = 22
    dat_qr: Optional[int] = 20
    dat_type: str = "AMERICAN"       # AMERICAN, CANADIAN
    shadowing_hours: int = 80
    specialist_shadowing_hours: int = 20
    volunteering_hours: int = 120
    dental_experience_hours: int = 150
    research_hours: int = 100
    state: str = "Massachusetts"
    country: str = "United States"
    applicant_type: str = "FIRST_TIME"  # FIRST_TIME, REAPPLICANT
    is_reapplicant: bool = False
    undergrad_institution: Optional[str] = "Boston University"
    major: Optional[str] = "Biology"
    completed_courses: List[StudentCompletedCourse] = []
    lor_science_faculty_count: int = 2
    lor_dentist_count: int = 1
    lor_committee_letter: bool = True
    total_lor_count: int = 4

class CompareStudentSchoolRequest(BaseModel):
    student_id: Optional[str] = None
    school_id: str
    custom_student_profile: Optional[StudentComparisonProfile] = None
    cycle: str = "2025-2026"
    include_ai_reasoning: bool = True

class BatchCompareRequest(BaseModel):
    student_id: Optional[str] = None
    custom_student_profile: Optional[StudentComparisonProfile] = None
    school_ids: Optional[List[str]] = None
    cycle: str = "2025-2026"

class BatchCompareResult(BaseModel):
    student_id: Optional[str] = None
    student_name: str
    comparisons: List[PredictionResult]
    top_matches: List[str] = []
    target_matches: List[str] = []
    reach_matches: List[str] = []
    safety_matches: List[str] = []
