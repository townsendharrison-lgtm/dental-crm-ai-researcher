"""Approved taxonomy contract; production factors must come from the client."""
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaxonomyNotConfigured(RuntimeError):
    pass


class FactorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    value_type: Literal["number", "text"]
    unit: str | None
    scoring_eligible: bool = True


class FactorTaxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(min_length=1)
    categories: tuple[str, ...]
    factors: tuple[FactorDefinition, ...]

    @model_validator(mode="after")
    def validate_members(self):
        if not self.categories or len(set(self.categories)) != len(self.categories):
            raise ValueError("Categories must be nonempty and unique")
        if not self.factors or len({f.key for f in self.factors}) != len(self.factors):
            raise ValueError("Factor keys must be nonempty and unique")
        if {f.category for f in self.factors} != set(self.categories):
            raise ValueError("Every category needs factors and every factor needs a known category")
        return self

    @property
    def by_key(self) -> dict[str, FactorDefinition]:
        return {factor.key: factor for factor in self.factors}

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


# Derived from the original client criteria supplied in this conversation.
# These are extraction slots, not inferred school requirements or weights.
CATEGORIES = (
    "Academics", "DAT", "Shadowing", "Dental Experience", "Service", "Leadership",
    "Research", "Extracurriculars", "Application Quality", "School Fit",
    "Application Strategy", "Context / Holistic Factors", "Red Flags", "Skills",
)


def _definitions():
    factors = []

    def number(category, key, description, unit):
        factors.append(FactorDefinition(key=key, category=category, description=description,
                                        value_type="number", unit=unit))

    def words(category, key, description, *, scoring=True):
        factors.append(FactorDefinition(key=key, category=category, description=description,
                                        value_type="text", unit=None, scoring_eligible=scoring))

    for key, label in (("gpa", "overall GPA"), ("science_gpa", "science GPA")):
        for statistic, meaning in (("min", "minimum"), ("avg", "average"), ("max", "maximum")):
            number("Academics", f"{statistic}_{key}", f"Explicitly stated {meaning} {label}; do not infer percentiles or weights", "gpa")
    for key, description in (
        ("post_baccalaureate_policy", "Post-baccalaureate coursework, completion or eligibility policy"),
        ("masters_policy", "Master's degree coursework, completion or eligibility policy"),
        ("online_classes_policy", "Policy on online classes"),
        ("community_college_policy", "Policy on community-college classes"),
        ("gpa_trend_policy", "Academic GPA trend or trajectory considered by the school"),
        ("prerequisite_grades", "Required or preferred grades in prerequisite courses"),
        ("withdrawals_failures_policy", "Withdrawals (W grades) and failed classes"),
        ("repeated_courses_policy", "Repeated courses and grade replacement policy"),
        ("academic_red_flags_policy", "Other academic concerns explicitly identified by the school"),
        ("course_load_policy", "Evidence of handling a heavy academic course load"),
    ):
        words("Academics", key, description)
    number("Academics", "post_baccalaureate_gpa", "Explicitly stated post-baccalaureate GPA criterion", "gpa")
    number("Academics", "masters_gpa", "Explicitly stated master's GPA criterion", "gpa")
    number("Academics", "credits_per_semester", "Explicitly stated number of credits per semester", "credits")

    for section, label in (("aa", "Academic Average"), ("total_science", "Total Science"),
                          ("pat", "Perceptual Ability"), ("biology", "Biology"),
                          ("general_chemistry", "General Chemistry"), ("organic_chemistry", "Organic Chemistry"),
                          ("reading_comprehension", "Reading Comprehension"),
                          ("quantitative_reasoning", "Quantitative Reasoning")):
        for statistic, meaning in (("min", "minimum"), ("avg", "average"), ("max", "maximum")):
            number("DAT", f"{statistic}_dat_{section}", f"Explicitly stated {meaning} DAT {label}, on the source's original scale", "points")
    number("DAT", "dat_attempts", "Explicit numeric number or limit of DAT attempts", "attempts")
    words("DAT", "dat_attempts_policy", "How the school considers multiple DAT attempts")
    words("DAT", "dat_type_policy", "Canadian versus American DAT acceptance or preference")
    words("DAT", "dat_score_scale", "Explicit score scale, scoring version or applicable testing dates; do not convert")
    words("DAT", "dat_section_priority", "Explicit priority given to particular DAT sections")

    for key, description, unit in (
        ("shadowing_hours", "Total dental shadowing hours", "hours"),
        ("general_dentistry_shadowing_hours", "General-dentistry shadowing hours", "hours"),
        ("specialty_shadowing_hours", "Specialty shadowing hours", "hours"),
        ("dentists_shadowed", "Number of dentists shadowed", "dentists"),
        ("shadowing_practices", "Number of dental practices shadowed", "practices"),
        ("shadowing_weeks", "Number of weeks spent shadowing", "weeks"),
    ):
        number("Shadowing", key, description, unit)
    words("Shadowing", "shadowing_consistency", "Consistency, length and continuity of shadowing involvement")

    for category, items in {
        "Dental Experience": [
            ("dental_employment", "Dental employment: hygienist, dental assistant, lab technician, receptionist or other stated role"),
            ("clinical_employment", "Clinical employment experience"),
            ("hands_on_dental_experience", "Hands-on dental experience"),
            ("dental_experience_depth", "Length, depth and responsibilities of dental experience"),
        ],
        "Service": [
            ("non_dental_service", "Non-dental volunteering or service"),
            ("underserved_community_service", "Service to underserved populations and communities"),
            ("service_consistency", "Consistency and longitudinal commitment to service"),
            ("service_impact", "Service depth, strength, opportunity quality and demonstrated impact"),
        ],
        "Leadership": [
            ("leadership_positions", "Leadership positions held"),
            ("leadership_duration", "Duration of leadership involvement"),
            ("leadership_responsibility", "Level of leadership responsibility"),
            ("leadership_impact", "Demonstrated leadership impact"),
        ],
        "Research": [
            ("research_duration", "Duration of research involvement"),
            ("research_outputs", "Publications, posters and presentations"),
            ("research_relevance_depth", "Relevance and depth of research"),
        ],
        "Extracurriculars": [
            ("clubs_organizations", "Clubs and organizations"), ("athletics", "Athletic involvement"),
            ("hobbies", "Hobbies"), ("employment", "Employment experience"),
            ("unique_experiences", "Unique extracurricular experiences"),
            ("sustained_involvement", "Sustained extracurricular involvement"),
        ],
        "Application Quality": [
            ("personal_statement", "Personal statement expectations and strength"),
            ("experience_descriptions", "Quality of experience descriptions"),
            ("letters_of_recommendation", "Recommendation letter expectations and quality"),
            ("supplemental_essays", "School-specific supplemental essay expectations"),
        ],
        "School Fit": [
            ("mission_alignment", "Alignment with the school's mission"),
            ("residency_preference", "In-state or residency preference"),
            ("geographic_connection", "Geographic ties or connections"),
            ("underserved_rural_interest", "Interest in underserved or rural care"),
            ("research_alignment", "Alignment with school research interests"),
            ("community_service_alignment", "Alignment with community-service priorities"),
            ("demonstrated_interest", "Demonstrated interest in the school"),
        ],
        "Application Strategy": [
            ("submission_date", "Application submission date, deadline or timing expectations"),
            ("supplemental_completion_date", "Supplemental completion date or timing"),
            ("prerequisites_satisfied", "Completion of required prerequisites"),
            ("application_completeness", "Application completeness requirements"),
        ],
        "Context / Holistic Factors": [
            ("upward_academic_trend", "Holistic consideration of an upward academic trend"),
            ("significant_life_experiences", "Consideration of significant life experiences"),
            ("disadvantaged_background_policy", "Explicit school policy on considering disadvantaged background; never infer applicant background"),
            ("first_generation_policy", "Explicit school policy on first-generation status; never infer applicant status"),
            ("nontraditional_applicant_policy", "Career changer and nontraditional-applicant considerations"),
        ],
        "Red Flags": [
            ("academic_misconduct_policy", "Academic misconduct disclosure or review policy"),
            ("institutional_action_policy", "Institutional action disclosure or review policy"),
            ("criminal_disclosure_policy", "Criminal disclosures where applicable; quote policy only"),
            ("unexplained_academic_decline", "Unexplained academic decline concerns"),
            ("low_dat_subsection_policy", "Concern about extremely low DAT subsection scores"),
            ("repeated_dat_attempts_concern", "Multiple DAT attempts explicitly treated as a concern"),
            ("weak_dental_exposure", "Weak or absent dental exposure"),
            ("missing_prerequisites", "Missing prerequisite concerns"),
            ("weak_recommendations", "Poor recommendation-letter concerns"),
        ],
        "Skills": [
            ("resilience", "Resilience demonstrated in experiences"),
            ("leadership_skill", "Leadership as a demonstrated skill or core value"),
            ("organization", "Organizational skills"),
            ("multilingual_skills", "Speaking multiple languages"),
            ("teamwork", "Teamwork demonstrated in experiences"),
            ("other_school_core_values", "Other explicitly stated school core values and evidence of demonstrating them"),
            ("diversity_core_value", "School-published diversity or inclusion values, not inferred applicant demographics"),
        ],
    }.items():
        for key, description in items:
            words(category, key, description)
    number("Service", "service_hours", "Total volunteering or service hours", "hours")
    number("Service", "service_weeks", "Number of weeks of service involvement", "weeks")
    number("Research", "research_hours", "Total research hours", "hours")
    for key, description in (
        ("gender_policy_context", "Cited school-published gender policy/context only; never individual applicant scoring or demographic inference"),
        ("ethnicity_policy_context", "Cited school-published ethnicity policy/context only; never individual applicant scoring or demographic inference"),
    ):
        words("School Fit", key, description, scoring=False)
    # Capture exact published weights/priorities as evidence; never manufacture weights.
    for category in CATEGORIES:
        slug = category.lower().replace(" / ", "_").replace(" ", "_")
        words(category, slug + "_stated_weights", f"Exact school-published weights, percentages or priorities for {category}; include factor labels with figures")
    return tuple(factors)


APPROVED_TAXONOMY = FactorTaxonomy(version="client-criteria-v1", categories=CATEGORIES, factors=_definitions())


def get_taxonomy() -> FactorTaxonomy:
    if APPROVED_TAXONOMY is None:
        raise TaxonomyNotConfigured("The approved fourteen-category factor list is required")
    if len(APPROVED_TAXONOMY.categories) != 14:
        raise TaxonomyNotConfigured("The production taxonomy must contain fourteen categories")
    return APPROVED_TAXONOMY
