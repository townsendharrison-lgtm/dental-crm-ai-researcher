from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from schemas.evidence_schema import VerificationStatus, EvidenceCitation

class PrerequisiteCourseItem(BaseModel):
    course_name: str
    group: str                               # BCP, ADDITIONAL BIOLOGICAL SCIENCES, NONSCIENCE, OTHER SCIENCE
    required: bool = True
    recommended: bool = False
    lab_required: bool = True
    semester_credits: float = 8.0
    quarter_credits: float = 12.0
    min_grade: str = "C"
    notes: Optional[str] = None
    status: VerificationStatus = VerificationStatus.VERIFIED
    citation_id: Optional[str] = None

class GeneralInformation(BaseModel):
    university_affiliation: str = ""
    state: str = ""
    country: str = "United States"
    dean: Optional[str] = None
    dental_school_description: Optional[str] = None
    mission: Optional[str] = None
    vision: Optional[str] = None
    community_service_mission: Optional[str] = None
    research_mission: Optional[str] = None
    core_values: List[str] = []
    admissions_philosophy: Optional[str] = None
    website_url: Optional[str] = None
    admissions_email: Optional[str] = None
    phone: Optional[str] = None
    accreditation_status: Optional[str] = "CODA Accredited"

class EnrolleeStatistics(BaseModel):
    baccalaureate_count: Optional[int] = None
    masters_or_beyond_count: Optional[int] = None
    four_years_predental_count: Optional[int] = None
    three_years_predental_count: Optional[int] = None
    two_years_predental_count: Optional[int] = None
    total_class_size: Optional[int] = None
    male_percentage: Optional[float] = None
    female_percentage: Optional[float] = None
    in_state_percentage: Optional[float] = None
    out_of_state_percentage: Optional[float] = None
    average_age: Optional[int] = None
    underrepresented_minority_percentage: Optional[float] = None
    additional_preparation_notes: List[str] = []

class AcademicStandards(BaseModel):
    avg_cgpa: float = 3.65
    avg_sgpa: float = 3.58
    avg_bcp_gpa: float = 3.55
    min_cgpa_cutoff: float = 3.0
    min_sgpa_cutoff: float = 3.0
    cgpa_5th_percentile: float = 3.30
    cgpa_95th_percentile: float = 3.95
    
    avg_dat_aa: float = 21.0
    avg_dat_ts: float = 21.0
    avg_dat_pat: float = 20.5
    avg_dat_bio: float = 21.0
    avg_dat_gc: float = 21.0
    avg_dat_oc: float = 21.0
    avg_dat_rc: float = 22.0
    avg_dat_qr: float = 20.0
    min_dat_aa_cutoff: int = 18
    min_dat_ts_cutoff: int = 18
    min_dat_pat_cutoff: int = 17
    dat_5th_percentile: int = 19
    dat_95th_percentile: int = 25
    canadian_dat_accepted: bool = True

class ExtracurricularRequirements(BaseModel):
    min_shadowing_hours: int = 50
    recommended_shadowing_hours: int = 100
    general_dentist_hours_required: int = 50
    specialist_shadowing_accepted: bool = True
    min_volunteering_hours: int = 50
    recommended_volunteering_hours: int = 100
    research_experience_preference: str = "RECOMMENDED"  # REQUIRED, RECOMMENDED, OPTIONAL
    manual_dexterity_assessed: bool = True

class LettersOfEvaluation(BaseModel):
    total_letters_required: int = 3
    total_letters_max: int = 4
    science_faculty_letters_required: int = 2
    non_science_faculty_letters_required: int = 0
    practicing_dentist_letter_required: bool = True
    committee_letter_accepted: bool = True

class ApplicationLogistics(BaseModel):
    aadsas_deadline: str = "December 1"
    secondary_application_required: bool = True
    secondary_fee: float = 75.0
    casper_required: bool = False
    kira_talent_required: bool = False
    seat_deposit_amount: float = 2000.0
    interview_format: str = "Traditional 1-on-1 and Group Simulation"

class TuitionAndFinancials(BaseModel):
    in_state_tuition_annual: float = 82000.0
    out_of_state_tuition_annual: float = 82000.0
    four_year_total_estimated_cost: float = 420000.0
    in_state_acceptance_rate: float = 13.5
    out_of_state_acceptance_rate: float = 10.1
    overall_acceptance_rate: float = 11.2
    in_state_preference_multiplier: float = 1.15
    international_students_accepted: bool = True

class SectionCompleteness(BaseModel):
    section_name: str
    total_fields: int
    verified_count: int
    found_unverified_count: int
    inferred_count: int
    conflicting_count: int
    not_found_count: int
    completion_percentage: float

class FieldCompletenessSummary(BaseModel):
    total_fields_extracted: int = 0
    verified_count: int = 0
    found_unverified_count: int = 0
    inferred_count: int = 0
    conflicting_count: int = 0
    not_found_count: int = 0
    verified_percentage: float = 0.0
    reviewed_percentage: float = 0.0
    section_breakdown: List[SectionCompleteness] = []

class DentalSchoolProfile(BaseModel):
    id: str
    name: str
    cycle: str = "2025-2026"
    location: str
    completeness: FieldCompletenessSummary = Field(default_factory=FieldCompletenessSummary)
    general_information: GeneralInformation = Field(default_factory=GeneralInformation)
    enrollee_statistics: EnrolleeStatistics = Field(default_factory=EnrolleeStatistics)
    prerequisites: List[PrerequisiteCourseItem] = []
    academic_standards: AcademicStandards = Field(default_factory=AcademicStandards)
    extracurriculars: ExtracurricularRequirements = Field(default_factory=ExtracurricularRequirements)
    letters_of_evaluation: LettersOfEvaluation = Field(default_factory=LettersOfEvaluation)
    logistics: ApplicationLogistics = Field(default_factory=ApplicationLogistics)
    financials: TuitionAndFinancials = Field(default_factory=TuitionAndFinancials)
    evidence_citations: List[EvidenceCitation] = []
    last_updated: Optional[str] = None
