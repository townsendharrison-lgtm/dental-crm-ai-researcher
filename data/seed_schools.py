from typing import List, Dict, Any, Optional
from schemas.criteria_schema import (
    DentalSchoolProfile,
    GeneralInformation,
    EnrolleeStatistics,
    PrerequisiteCourseItem,
    AcademicStandards,
    ExtracurricularRequirements,
    LettersOfEvaluation,
    ApplicationLogistics,
    TuitionAndFinancials,
    FieldCompletenessSummary,
    SectionCompleteness
)
from schemas.evidence_schema import VerificationStatus, EvidenceCitation, SourceType

_cached_seed_schools: Optional[List[DentalSchoolProfile]] = None

def get_seed_dental_schools(force_refresh: bool = False) -> List[DentalSchoolProfile]:
    global _cached_seed_schools
    if _cached_seed_schools is not None and not force_refresh:
        return _cached_seed_schools
    
    schools_list: List[DentalSchoolProfile] = []

    try:
        from core.database import get_all_schools_from_db, get_supabase_client
        db_schools = get_all_schools_from_db()
        
        # Batch fetch all evidence
        client = get_supabase_client()
        evidence_by_school: Dict[str, List[Dict[str, Any]]] = {}
        if client:
            try:
                all_ev = client.table("school_evidence").select("*").execute().data or []
                for e in all_ev:
                    sid = str(e.get("school_id"))
                    evidence_by_school.setdefault(sid, []).append(e)
            except Exception as ev_err:
                print(f"[Seed Schools] Note: school_evidence query: {ev_err}")
        
        for d in db_schools:
            d_id = str(d.get("id"))
            d_name = d.get("name") or "Dental School"
            avg_gpa = float(d.get("avg_gpa") or 0.0)
            dat_avg = float(d.get("dat_avg") or 0.0)
            acc_rate = float(d.get("acceptance_rate") or 0.0)
            is_acc_rate = float(d.get("is_acceptance_rate") or 0.0)
            oos_acc_rate = float(d.get("oos_acceptance_rate") or 0.0)
            location = d.get("location") or ""
            state = location.split(",")[-1].strip() if "," in location else location
            
            # Check real evidence citations from DB
            evidence_rows = evidence_by_school.get(d_id, [])
            
            verified_count = sum(1 for e in evidence_rows if e.get("is_verified") or e.get("confidence_score", 0) >= 0.95)
            unverified_count = sum(1 for e in evidence_rows if not e.get("is_verified") and 0.7 <= e.get("confidence_score", 0) < 0.95)
            conflicting_count = sum(1 for e in evidence_rows if e.get("notes") == "CONFLICTING")
            inferred_count = sum(1 for e in evidence_rows if e.get("notes") == "INFERRED")
            total_found = verified_count + unverified_count + conflicting_count + inferred_count
            
            reviewed_pct = round(((verified_count + unverified_count) / float(total_found)) * 100, 1) if total_found > 0 else 0.0
            verified_pct = round((verified_count / float(total_found)) * 100, 1) if total_found > 0 else 0.0
            
            # Extract citations
            citations: List[EvidenceCitation] = []
            dean_name: Optional[str] = None
            mission_text: Optional[str] = None
            desc_text: Optional[str] = None
            prereqs_list: List[PrerequisiteCourseItem] = []

            for e in evidence_rows:
                f_key = e.get("field_key") or ""
                f_val = e.get("extracted_value")
                cat = e.get("category") or "General Information"
                
                if "dean" in f_key.lower() and isinstance(f_val, str):
                    dean_name = f_val
                elif "mission" in f_key.lower() and isinstance(f_val, str):
                    mission_text = f_val
                elif "description" in f_key.lower() and isinstance(f_val, str):
                    desc_text = f_val
                elif cat.lower() == "prerequisites" and isinstance(f_val, dict):
                    prereqs_list.append(PrerequisiteCourseItem(
                        course_name=e.get("field_label") or f_key.capitalize(),
                        group="BCP (BIOLOGY – CHEMISTRY – PHYSICS)",
                        required=f_val.get("required", True),
                        recommended=f_val.get("recommended", False),
                        lab_required=f_val.get("lab_required", False),
                        semester_credits=float(f_val.get("semester_credits") or 4.0),
                        quarter_credits=float(f_val.get("quarter_credits") or 6.0),
                        status=VerificationStatus.VERIFIED if e.get("is_verified") else VerificationStatus.FOUND_UNVERIFIED
                    ))
                
                citations.append(EvidenceCitation(
                    school_id=d_id,
                    category=cat,
                    field_key=f_key or "field",
                    field_label=e.get("field_label") or "Field",
                    extracted_value=f_val,
                    source_type=SourceType(e.get("source_type", "URL")),
                    source_name=e.get("source_name") or "Source",
                    source_url=e.get("source_url"),
                    page_number=e.get("page_number"),
                    raw_snippet=e.get("raw_snippet") or "",
                    confidence_score=float(e.get("confidence_score") or 0.9),
                    status=VerificationStatus.VERIFIED if e.get("is_verified") else VerificationStatus.FOUND_UNVERIFIED
                ))
            
            school_profile = DentalSchoolProfile(
                id=d_id,
                name=d_name,
                cycle="2025-2026",
                location=location,
                completeness=FieldCompletenessSummary(
                    total_fields_extracted=total_found,
                    reviewed_percentage=reviewed_pct,
                    verified_percentage=verified_pct,
                    verified_count=verified_count,
                    found_unverified_count=unverified_count,
                    inferred_count=inferred_count,
                    conflicting_count=conflicting_count,
                    not_found_count=0
                ),
                general_information=GeneralInformation(
                    university_affiliation=d_name,
                    state=state,
                    country="United States",
                    dean=dean_name,
                    dental_school_description=desc_text,
                    mission=mission_text,
                    vision=None,
                    community_service_mission=None,
                    research_mission=None,
                    core_values=[],
                    admissions_philosophy=None
                ),
                enrollee_statistics=EnrolleeStatistics(
                    baccalaureate_count=0,
                    masters_or_beyond_count=0,
                    additional_preparation_notes=[]
                ),
                prerequisites=prereqs_list,
                academic_standards=AcademicStandards(
                    avg_cgpa=avg_gpa if avg_gpa > 0 else 3.5,
                    avg_sgpa=avg_gpa - 0.05 if avg_gpa > 0 else 3.45,
                    avg_dat_aa=dat_avg if dat_avg > 0 else 20.0,
                    avg_dat_ts=dat_avg if dat_avg > 0 else 20.0,
                    avg_dat_pat=dat_avg if dat_avg > 0 else 20.0
                ),
                financials=TuitionAndFinancials(
                    in_state_acceptance_rate=is_acc_rate,
                    out_of_state_acceptance_rate=oos_acc_rate,
                    overall_acceptance_rate=acc_rate
                ),
                evidence_citations=citations
            )
            schools_list.append(school_profile)
    except Exception as e:
        print(f"[Seed Schools] Error loading DB schools: {e}")

    _cached_seed_schools = schools_list
    return schools_list

def get_school_by_id_or_default(school_id: str) -> DentalSchoolProfile:
    schools = get_seed_dental_schools()
    if not schools:
        return DentalSchoolProfile(
            id=school_id,
            name="Dental School",
            location="United States"
        )
    for s in schools:
        if s.id == school_id or s.id.lower() == school_id.lower() or s.name.lower() == school_id.lower():
            return s
    for s in schools:
        if school_id.lower() in s.name.lower() or s.name.lower() in school_id.lower():
            return s
    return schools[0]
