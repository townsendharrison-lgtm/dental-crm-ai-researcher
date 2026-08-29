import json
import re
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, START, END
from agents.state import ComparisonGraphState
from agents.prompts import STUDENT_PREDICTIVE_ANALYSIS_PROMPT
from schemas.prediction_schema import (
    PredictionResult,
    RequirementCheckItem,
    OutcomeProbabilities,
    DiagnosticsExplainability,
    RoiImprovement,
    ProbabilityLift
)
from schemas.comparison_schema import StudentComparisonProfile, StudentCompletedCourse
from schemas.criteria_schema import DentalSchoolProfile, PrerequisiteCourseItem
from core.config import settings
from data.seed_schools import get_school_by_id_or_default

# In-memory document and course extraction cache to guarantee 100% deterministic, instant comparisons
_EXTRACTED_STUDENT_CACHE: Dict[str, Dict[str, Any]] = {}

def load_profiles_node(state: ComparisonGraphState) -> Dict[str, Any]:
    logs = state.get("logs", [])
    student = state.get("student_profile")
    school = state.get("school_profile")
    
    if not student:
        student = StudentComparisonProfile()
    if not school:
        school = get_school_by_id_or_default("sch6")
        
    logs.append(f"[Load Node] Comparing student '{student.name}' against school '{school.name}'")
    return {
        "student_profile": student,
        "school_profile": school,
        "logs": logs
    }

def ingest_student_documents_node(state: ComparisonGraphState) -> Dict[str, Any]:
    """
    Ingests and parses student uploaded documents from Supabase Storage (e.g. Last Year's Complete Application .pdf, Transcripts).
    Uses caching to guarantee exact repeatability across runs.
    """
    logs = state.get("logs", [])
    student: StudentComparisonProfile = state["student_profile"]
    
    attached_docs: List[str] = []
    document_insights: Dict[str, Any] = {}
    
    if student and student.id:
        # Check cache first for 100% deterministic output
        if student.id in _EXTRACTED_STUDENT_CACHE:
            cached = _EXTRACTED_STUDENT_CACHE[student.id]
            attached_docs = cached.get("attached_docs", [])
            document_insights = cached.get("document_insights", {})
            if cached.get("completed_courses") and not student.completed_courses:
                student.completed_courses = [
                    StudentCompletedCourse(**c) for c in cached["completed_courses"]
                ]
            if cached.get("shadowing_hours") and cached["shadowing_hours"] > 0:
                student.shadowing_hours = cached["shadowing_hours"]
            if cached.get("volunteering_hours") and cached["volunteering_hours"] > 0:
                student.volunteering_hours = cached["volunteering_hours"]
            logs.append(f"[Document Node] Loaded cached extracted document profile for '{student.name}' ({len(attached_docs)} docs).")
            return {
                "student_profile": student,
                "attached_documents_analyzed": attached_docs,
                "document_insights": document_insights,
                "logs": logs
            }

        try:
            from core.database import get_student_documents
            docs = get_student_documents(student.id)
            if docs:
                for d in docs:
                    attached_docs.append(f"{d['title']} ({d['total_pages']} pages)")
                
                logs.append(f"[Document Node] Ingested {len(docs)} student application document(s): {', '.join(attached_docs)}")
                
                # Combine document text
                combined_text = "\n\n".join([f"=== DOCUMENT: {d['title']} ({d['type']}) ===\n{d['full_text'][:25000]}" for d in docs])
                
                # Extract structured coursework and experiences if courses not yet filled
                if not student.completed_courses and any(kw in combined_text.upper() for kw in ["COURSEWORK", "TRANSCRIPT", "SHADOWING", "EXPERIENCE", "ACADEMIC HISTORY", "DMD", "DDS"]):
                    try:
                        from openai import OpenAI
                        client = OpenAI(api_key=settings.OPENAI_API_KEY)
                        extract_prompt = f"""
You are an expert dental school admissions officer. Analyze this student's uploaded application document/transcript and extract their completed college coursework and clinical experience.

DOCUMENT CONTENT:
{combined_text[:20000]}

Respond ONLY in valid JSON matching this exact structure:
{{
  "extracted_courses": [
    {{
      "course_name": "General Chemistry I",
      "category": "BCP",
      "grade": "A",
      "credit_hours": 4.0,
      "has_lab": true
    }}
  ],
  "total_shadowing_hours": 120,
  "total_volunteering_hours": 105,
  "personal_statement_highlights": "Brief summary of applicant background."
}}
"""
                        resp = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[{"role": "user", "content": extract_prompt}],
                            temperature=0.0,
                            seed=42,
                            response_format={"type": "json_object"}
                        )
                        parsed_json = json.loads(resp.choices[0].message.content or "{}")
                        document_insights = parsed_json
                        
                        # Populate student completed courses from document
                        if parsed_json.get("extracted_courses"):
                            student_courses = []
                            for c in parsed_json["extracted_courses"]:
                                student_courses.append(StudentCompletedCourse(
                                    course_name=c.get("course_name", "Course"),
                                    category=c.get("category", "BCP"),
                                    grade=c.get("grade", "A"),
                                    credit_hours=float(c.get("credit_hours") or 4.0),
                                    has_lab=bool(c.get("has_lab", False))
                                ))
                            student.completed_courses = student_courses
                            logs.append(f"[Document Node] Extracted {len(student_courses)} verified transcript courses directly from uploaded application PDF!")
                        
                        if parsed_json.get("total_shadowing_hours") and parsed_json["total_shadowing_hours"] > 0:
                            student.shadowing_hours = int(parsed_json["total_shadowing_hours"])
                            
                        if parsed_json.get("total_volunteering_hours") and parsed_json["total_volunteering_hours"] > 0:
                            student.volunteering_hours = int(parsed_json["total_volunteering_hours"])
                            
                        # Save to in-memory cache for deterministic subsequent runs
                        _EXTRACTED_STUDENT_CACHE[student.id] = {
                            "attached_docs": attached_docs,
                            "document_insights": document_insights,
                            "completed_courses": [c.model_dump() for c in student.completed_courses],
                            "shadowing_hours": student.shadowing_hours,
                            "volunteering_hours": student.volunteering_hours
                        }
                    except Exception as llm_err:
                        logs.append(f"[Document Node] Note during document LLM parse: {llm_err}")
        except Exception as e:
            logs.append(f"[Document Node] Error processing student documents: {e}")
            
    return {
        "student_profile": student,
        "attached_documents_analyzed": attached_docs,
        "document_insights": document_insights,
        "logs": logs
    }

def _match_student_course(req_name: str, req_group: str, courses: List[StudentCompletedCourse]) -> Optional[StudentCompletedCourse]:
    """
    Deterministic canonical matching helper between school requirement and student completed transcript courses.
    """
    r_clean = req_name.lower().strip()
    
    # 1. Exact or Substring match
    for c in courses:
        c_clean = c.course_name.lower().strip()
        if r_clean == c_clean or r_clean in c_clean or c_clean in r_clean:
            return c
            
    # 2. Canonical Subject Keyword Matching
    if "bio" in r_clean and "chem" not in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if any(k in c_name for k in ["biology", "biol", "cell bio", "gen bio", "general bio"]):
                return c
    elif "organic" in r_clean or "orgo" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if "organic" in c_name or "orgo" in c_name or "chem 23" in c_name or "chem 24" in c_name:
                return c
    elif "general" in r_clean or "inorganic" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if any(k in c_name for k in ["general chem", "inorganic", "chem 10", "chem 11", "principles of chem"]):
                return c
    elif "physic" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if "phys" in c_name or "physics" in c_name:
                return c
    elif "biochem" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if "biochem" in c_name:
                return c
    elif "microbio" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if "microbio" in c_name:
                return c
    elif "anatomy" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if "anatomy" in c_name:
                return c
    elif "physiology" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if "physio" in c_name:
                return c
    elif "math" in r_clean or "calculus" in r_clean or "stat" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if any(k in c_name for k in ["calc", "math", "stat"]):
                return c
    elif "english" in r_clean or "writing" in r_clean or "composition" in r_clean:
        for c in courses:
            c_name = c.course_name.lower()
            if any(k in c_name for k in ["engl", "writing", "comp", "literature"]):
                return c

    return None

def prerequisite_audit_node(state: ComparisonGraphState) -> Dict[str, Any]:
    logs = state.get("logs", [])
    student: StudentComparisonProfile = state["student_profile"]
    school: DentalSchoolProfile = state["school_profile"]
    
    prereq_checks: List[RequirementCheckItem] = []
    
    target_prereqs = school.prerequisites
    if not target_prereqs:
        target_prereqs = [
            PrerequisiteCourseItem(course_name="Biology", group="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", required=True, recommended=False, lab_required=True, semester_credits=8.0, quarter_credits=12.0),
            PrerequisiteCourseItem(course_name="Chemistry, General/Inorganic", group="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", required=True, recommended=False, lab_required=True, semester_credits=8.0, quarter_credits=12.0),
            PrerequisiteCourseItem(course_name="Chemistry, Organic", group="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", required=True, recommended=False, lab_required=True, semester_credits=8.0, quarter_credits=12.0),
            PrerequisiteCourseItem(course_name="Physics", group="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", required=True, recommended=False, lab_required=True, semester_credits=8.0, quarter_credits=12.0),
            PrerequisiteCourseItem(course_name="Biochemistry", group="ADDITIONAL BIOLOGICAL SCIENCES", required=True, recommended=False, lab_required=False, semester_credits=4.0, quarter_credits=6.0),
        ]

    for idx, p in enumerate(target_prereqs):
        matched_course = _match_student_course(p.course_name, p.group, student.completed_courses)
        student_val = "Not Taken"
        status = "UNMET"
        details = f"Requires {p.semester_credits} semester credits"
        if p.lab_required:
            details += " with Lab"
            
        if matched_course:
            student_val = f"{matched_course.course_name} ({matched_course.credit_hours} cr, Lab: {'Yes' if matched_course.has_lab else 'No'}, Grade: {matched_course.grade})"
            
            # Check credits and lab
            credits_met = matched_course.credit_hours >= (p.semester_credits * 0.75)
            lab_met = (not p.lab_required) or matched_course.has_lab
            
            if credits_met and lab_met:
                status = "MET"
                details = "Fulfilled with lab" if p.lab_required else "Fulfilled"
            elif not lab_met and p.lab_required:
                status = "WARNING"
                details = "Course completed but missing required Lab component"
            else:
                status = "WARNING"
                details = f"Credits ({matched_course.credit_hours}) below requirement ({p.semester_credits})"
        elif not student.completed_courses:
            # No transcript data available — mark as UNKNOWN instead of assuming MET
            status = "UNKNOWN"
            student_val = "No Transcript Data"
            details = "No coursework data available — cannot verify completion"
                
        if not p.required and status == "UNMET":
            status = "RECOMMENDED_MISSING"
            
        prereq_checks.append(RequirementCheckItem(
            id=f"req-{idx+1}",
            name=p.course_name,
            category=p.group,
            status=status,
            studentValue=student_val,
            schoolRequirement=f"{p.semester_credits} Sem Cr ({p.quarter_credits} Qtr){' + Lab' if p.lab_required else ''}",
            details=details,
            isHardRequirement=p.required
        ))
        
    logs.append(f"[Prereq Node] Evaluated {len(prereq_checks)} prerequisites.")
    return {
        "prerequisite_checks": prereq_checks,
        "logs": logs
    }

def extracurricular_audit_node(state: ComparisonGraphState) -> Dict[str, Any]:
    """
    Evaluates student's GPA, DAT scores, shadowing, volunteering, research, dental experience,
    and LOR compliance against the school's published standards and requirements.
    """
    logs = state.get("logs", [])
    student: StudentComparisonProfile = state["student_profile"]
    school: DentalSchoolProfile = state["school_profile"]
    
    checks: List[RequirementCheckItem] = []
    check_idx = 0
    
    # ---------- GPA Checks ----------
    def _gpa_status(student_val: float, school_avg: float, school_min: float) -> tuple:
        if student_val >= school_avg:
            return "MET", f"At or above class average ({school_avg:.2f})"
        elif student_val >= school_min:
            return "WARNING", f"Below class average ({school_avg:.2f}) but above minimum cutoff ({school_min:.2f})"
        else:
            return "UNMET", f"Below minimum cutoff ({school_min:.2f})"
    
    # cGPA
    check_idx += 1
    cgpa_status, cgpa_details = _gpa_status(student.cgpa, school.academic_standards.avg_cgpa, school.academic_standards.min_cgpa_cutoff)
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="Cumulative GPA (cGPA)",
        category="GPA",
        status=cgpa_status,
        studentValue=f"{student.cgpa:.2f}",
        schoolRequirement=f"Avg: {school.academic_standards.avg_cgpa:.2f} | Min: {school.academic_standards.min_cgpa_cutoff:.2f}",
        details=cgpa_details,
        isHardRequirement=True
    ))
    
    # sGPA
    check_idx += 1
    sgpa_status, sgpa_details = _gpa_status(student.sgpa, school.academic_standards.avg_sgpa, school.academic_standards.min_sgpa_cutoff)
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="Science GPA (sGPA)",
        category="GPA",
        status=sgpa_status,
        studentValue=f"{student.sgpa:.2f}",
        schoolRequirement=f"Avg: {school.academic_standards.avg_sgpa:.2f} | Min: {school.academic_standards.min_sgpa_cutoff:.2f}",
        details=sgpa_details,
        isHardRequirement=True
    ))
    
    # ---------- DAT Checks ----------
    def _dat_status(student_val: int, school_avg: float, school_min: int) -> tuple:
        if student_val >= school_avg:
            return "MET", f"At or above class average ({school_avg:.1f})"
        elif student_val >= school_min:
            return "WARNING", f"Below class average ({school_avg:.1f}) but above minimum ({school_min})"
        else:
            return "UNMET", f"Below minimum cutoff ({school_min})"
    
    # DAT AA
    check_idx += 1
    dat_aa_status, dat_aa_details = _dat_status(student.dat_aa, school.academic_standards.avg_dat_aa, school.academic_standards.min_dat_aa_cutoff)
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="DAT Academic Average (AA)",
        category="DAT",
        status=dat_aa_status,
        studentValue=str(student.dat_aa),
        schoolRequirement=f"Avg: {school.academic_standards.avg_dat_aa:.1f} | Min: {school.academic_standards.min_dat_aa_cutoff}",
        details=dat_aa_details,
        isHardRequirement=True
    ))
    
    # DAT TS
    check_idx += 1
    dat_ts_status, dat_ts_details = _dat_status(student.dat_ts, school.academic_standards.avg_dat_ts, school.academic_standards.min_dat_ts_cutoff)
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="DAT Total Science (TS)",
        category="DAT",
        status=dat_ts_status,
        studentValue=str(student.dat_ts),
        schoolRequirement=f"Avg: {school.academic_standards.avg_dat_ts:.1f} | Min: {school.academic_standards.min_dat_ts_cutoff}",
        details=dat_ts_details,
        isHardRequirement=True
    ))
    
    # DAT PAT
    check_idx += 1
    dat_pat_status, dat_pat_details = _dat_status(student.dat_pat, school.academic_standards.avg_dat_pat, school.academic_standards.min_dat_pat_cutoff)
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="DAT Perceptual Ability (PAT)",
        category="DAT",
        status=dat_pat_status,
        studentValue=str(student.dat_pat),
        schoolRequirement=f"Avg: {school.academic_standards.avg_dat_pat:.1f} | Min: {school.academic_standards.min_dat_pat_cutoff}",
        details=dat_pat_details,
        isHardRequirement=True
    ))
    
    # ---------- Shadowing ----------
    check_idx += 1
    min_shadow = school.extracurriculars.min_shadowing_hours
    rec_shadow = school.extracurriculars.recommended_shadowing_hours
    if student.shadowing_hours >= rec_shadow:
        shadow_status = "MET"
        shadow_details = f"Exceeds recommended hours ({rec_shadow}h)"
    elif student.shadowing_hours >= min_shadow:
        shadow_status = "WARNING"
        shadow_details = f"Meets minimum ({min_shadow}h) but below recommended ({rec_shadow}h)"
    else:
        shadow_status = "UNMET"
        shadow_details = f"Below minimum requirement ({min_shadow}h)"
    
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="Dental Shadowing Hours",
        category="Shadowing",
        status=shadow_status,
        studentValue=f"{student.shadowing_hours}h",
        schoolRequirement=f"Min: {min_shadow}h | Recommended: {rec_shadow}h",
        details=shadow_details,
        isHardRequirement=True
    ))
    
    # ---------- Volunteering ----------
    check_idx += 1
    min_vol = school.extracurriculars.min_volunteering_hours
    rec_vol = school.extracurriculars.recommended_volunteering_hours
    if student.volunteering_hours >= rec_vol:
        vol_status = "MET"
        vol_details = f"Exceeds recommended hours ({rec_vol}h)"
    elif student.volunteering_hours >= min_vol:
        vol_status = "WARNING"
        vol_details = f"Meets minimum ({min_vol}h) but below recommended ({rec_vol}h)"
    else:
        vol_status = "UNMET"
        vol_details = f"Below minimum requirement ({min_vol}h)"
    
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="Community Volunteering Hours",
        category="Volunteering",
        status=vol_status,
        studentValue=f"{student.volunteering_hours}h",
        schoolRequirement=f"Min: {min_vol}h | Recommended: {rec_vol}h",
        details=vol_details,
        isHardRequirement=False
    ))
    
    # ---------- Research ----------
    check_idx += 1
    research_pref = school.extracurriculars.research_experience_preference
    if research_pref == "REQUIRED":
        if student.research_hours >= 100:
            res_status = "MET"
            res_details = "Meets required research experience"
        elif student.research_hours > 0:
            res_status = "WARNING"
            res_details = "Has some research but may need more for a competitive application"
        else:
            res_status = "UNMET"
            res_details = "Research experience is required by this school"
    elif research_pref == "RECOMMENDED":
        if student.research_hours >= 50:
            res_status = "MET"
            res_details = "Has recommended research experience"
        elif student.research_hours > 0:
            res_status = "WARNING"
            res_details = "Some research but below competitive threshold"
        else:
            res_status = "RECOMMENDED_MISSING"
            res_details = "Research is recommended — lack of research weakens application"
    else:  # OPTIONAL
        res_status = "MET" if student.research_hours > 0 else "RECOMMENDED_MISSING"
        res_details = "Has research experience (optional)" if student.research_hours > 0 else "Research is optional but beneficial"
    
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="Research Experience",
        category="Research",
        status=res_status,
        studentValue=f"{student.research_hours}h",
        schoolRequirement=f"Preference: {research_pref}",
        details=res_details,
        isHardRequirement=(research_pref == "REQUIRED")
    ))
    
    # ---------- Dental Experience ----------
    check_idx += 1
    if student.dental_experience_hours >= 100:
        dent_status = "MET"
        dent_details = "Strong dental clinical experience"
    elif student.dental_experience_hours >= 50:
        dent_status = "WARNING"
        dent_details = "Some dental experience but below competitive threshold"
    elif student.dental_experience_hours > 0:
        dent_status = "WARNING"
        dent_details = "Limited dental experience — consider gaining more clinical exposure"
    else:
        dent_status = "RECOMMENDED_MISSING"
        dent_details = "No recorded dental experience"
    
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="Dental Clinical Experience",
        category="Experience",
        status=dent_status,
        studentValue=f"{student.dental_experience_hours}h",
        schoolRequirement="Competitive: 100h+",
        details=dent_details,
        isHardRequirement=False
    ))
    
    # ---------- LOR Compliance ----------
    lor_reqs = school.letters_of_evaluation
    check_idx += 1
    
    lor_issues = []
    if student.lor_science_faculty_count < lor_reqs.science_faculty_letters_required:
        lor_issues.append(f"Need {lor_reqs.science_faculty_letters_required} science faculty letters (have {student.lor_science_faculty_count})")
    if lor_reqs.practicing_dentist_letter_required and student.lor_dentist_count < 1:
        lor_issues.append("Missing required letter from practicing dentist")
    if student.total_lor_count < lor_reqs.total_letters_required:
        lor_issues.append(f"Need {lor_reqs.total_letters_required} total letters (have {student.total_lor_count})")
    
    if not lor_issues:
        lor_status = "MET"
        lor_details = f"All LOR requirements satisfied ({student.total_lor_count} letters)"
    else:
        lor_status = "UNMET"
        lor_details = "; ".join(lor_issues)
    
    checks.append(RequirementCheckItem(
        id=f"ext-{check_idx}",
        name="Letters of Evaluation",
        category="LOR",
        status=lor_status,
        studentValue=f"{student.total_lor_count} total ({student.lor_science_faculty_count} science, {student.lor_dentist_count} dentist, committee: {'Yes' if student.lor_committee_letter else 'No'})",
        schoolRequirement=f"Required: {lor_reqs.total_letters_required} total, {lor_reqs.science_faculty_letters_required} science, Dentist: {'Yes' if lor_reqs.practicing_dentist_letter_required else 'No'}",
        details=lor_details,
        isHardRequirement=True
    ))
    
    logs.append(f"[Extracurricular Audit] Evaluated {len(checks)} extracurricular and academic benchmarks.")
    return {
        "extracurricular_checks": checks,
        "logs": logs
    }

def benchmark_and_probability_node(state: ComparisonGraphState) -> Dict[str, Any]:
    """
    Multi-factor weighted scoring model that evaluates student fit across all dimensions.
    
    Weight Distribution:
    - cGPA comparison:        20%
    - sGPA/BCP GPA comparison: 15%
    - DAT AA comparison:       20%
    - DAT subsections (TS, PAT): 10%
    - Prerequisite completion: 15%
    - Extracurriculars:        10%
    - LOR compliance:          5%
    - In-state residency:      5%
    """
    logs = state.get("logs", [])
    student: StudentComparisonProfile = state["student_profile"]
    school: DentalSchoolProfile = state["school_profile"]
    prereqs = state.get("prerequisite_checks", [])
    extracurricular_checks = state.get("extracurricular_checks", [])
    
    # ============ Factor Scoring (each factor scores 0-100) ============
    
    def _percentile_score(student_val: float, school_avg: float, school_min: float, spread: float = 0.5) -> float:
        """Score 0-100 based on where student falls relative to school avg.
        50 = at average, 100 = significantly above, 0 = significantly below minimum."""
        if student_val >= school_avg:
            # Above average: score 50-100
            diff = student_val - school_avg
            return min(100.0, 50.0 + (diff / max(0.01, spread)) * 50.0)
        elif student_val >= school_min:
            # Between min and average: score 20-50
            range_size = max(0.01, school_avg - school_min)
            ratio = (student_val - school_min) / range_size
            return 20.0 + ratio * 30.0
        else:
            # Below minimum: score 0-20
            deficit = school_min - student_val
            return max(0.0, 20.0 - (deficit / max(0.01, spread)) * 20.0)
    
    # 1. cGPA Score (weight: 20%)
    cgpa_score = _percentile_score(
        student.cgpa, school.academic_standards.avg_cgpa,
        school.academic_standards.min_cgpa_cutoff, spread=0.4
    )
    
    # 2. sGPA Score (weight: 15%)
    sgpa_score = _percentile_score(
        student.sgpa, school.academic_standards.avg_sgpa,
        school.academic_standards.min_sgpa_cutoff, spread=0.4
    )
    
    # 3. DAT AA Score (weight: 20%)
    dat_aa_score = _percentile_score(
        float(student.dat_aa), school.academic_standards.avg_dat_aa,
        float(school.academic_standards.min_dat_aa_cutoff), spread=3.0
    )
    
    # 4. DAT Subsections Score (weight: 10%) - average of TS and PAT
    dat_ts_score = _percentile_score(
        float(student.dat_ts), school.academic_standards.avg_dat_ts,
        float(school.academic_standards.min_dat_ts_cutoff), spread=3.0
    )
    dat_pat_score = _percentile_score(
        float(student.dat_pat), school.academic_standards.avg_dat_pat,
        float(school.academic_standards.min_dat_pat_cutoff), spread=3.0
    )
    dat_sub_score = (dat_ts_score + dat_pat_score) / 2.0
    
    # 5. Prerequisite Compliance Score (weight: 15%)
    met_count = sum(1 for r in prereqs if r.status == "MET")
    unknown_count = sum(1 for r in prereqs if r.status == "UNKNOWN")
    unmet_count = sum(1 for r in prereqs if r.status == "UNMET")
    warning_count = sum(1 for r in prereqs if r.status == "WARNING")
    total_reqs = len(prereqs)
    
    if total_reqs > 0:
        # MET = full credit, WARNING = 60% credit, UNKNOWN = 30% credit, UNMET = 0
        prereq_score = ((met_count * 1.0 + warning_count * 0.6 + unknown_count * 0.3) / total_reqs) * 100.0
    else:
        prereq_score = 50.0  # No prereqs to check — neutral
    
    # 6. Extracurricular Score (weight: 10%) - from extracurricular checks
    ext_met = sum(1 for r in extracurricular_checks if r.status == "MET" and r.category not in ("GPA", "DAT"))
    ext_warn = sum(1 for r in extracurricular_checks if r.status == "WARNING" and r.category not in ("GPA", "DAT"))
    ext_unmet = sum(1 for r in extracurricular_checks if r.status in ("UNMET", "RECOMMENDED_MISSING") and r.category not in ("GPA", "DAT"))
    ext_total = ext_met + ext_warn + ext_unmet
    
    if ext_total > 0:
        extra_score = ((ext_met * 1.0 + ext_warn * 0.5) / ext_total) * 100.0
    else:
        extra_score = 50.0
    
    # 7. LOR Compliance Score (weight: 5%)
    lor_checks = [r for r in extracurricular_checks if r.category == "LOR"]
    lor_score = 100.0 if (lor_checks and lor_checks[0].status == "MET") else (30.0 if lor_checks else 50.0)
    
    # 8. In-State Residency Score (weight: 5%)
    is_in_state = (
        student.state and school.general_information.state and 
        student.state.lower().strip() in school.general_information.state.lower().strip()
    )
    in_state_multiplier = school.financials.in_state_preference_multiplier or 1.0
    if is_in_state:
        residency_score = min(100.0, 70.0 + (in_state_multiplier - 1.0) * 200.0)
    else:
        # Out of state — not a penalty per se, but no bonus
        residency_score = 40.0
    
    # ============ Weighted Composite Score ============
    raw_match = (
        cgpa_score * 0.20 +
        sgpa_score * 0.15 +
        dat_aa_score * 0.20 +
        dat_sub_score * 0.10 +
        prereq_score * 0.15 +
        extra_score * 0.10 +
        lor_score * 0.05 +
        residency_score * 0.05
    )
    
    # Hard cutoff penalties
    below_min_gpa = student.cgpa < school.academic_standards.min_cgpa_cutoff
    below_min_dat = student.dat_aa < school.academic_standards.min_dat_aa_cutoff
    
    if below_min_gpa:
        raw_match -= 15.0
        logs.append(f"[Scoring] PENALTY: cGPA {student.cgpa} below minimum cutoff {school.academic_standards.min_cgpa_cutoff}")
    if below_min_dat:
        raw_match -= 15.0
        logs.append(f"[Scoring] PENALTY: DAT AA {student.dat_aa} below minimum cutoff {school.academic_standards.min_dat_aa_cutoff}")
    
    # Reapplicant slight penalty
    if student.is_reapplicant:
        raw_match -= 3.0
    
    # Hard UNMET prerequisite penalty
    hard_unmet = sum(1 for r in prereqs if r.status == "UNMET" and r.isHardRequirement)
    if hard_unmet > 0:
        raw_match -= hard_unmet * 5.0
        logs.append(f"[Scoring] PENALTY: {hard_unmet} hard prerequisite(s) UNMET")
    
    match_score = max(5.0, min(98.0, raw_match))
    
    # ============ Fit Category ============
    if match_score >= 80.0:
        fit_category = "Strong Fit"
    elif match_score >= 60.0:
        fit_category = "Target"
    elif match_score >= 40.0:
        fit_category = "Reach"
    elif match_score >= 25.0:
        fit_category = "High Reach"
    else:
        fit_category = "High Risk / Unqualified"
    
    # Override to "High Risk" if below hard minimums
    if below_min_gpa or below_min_dat:
        if match_score < 40.0:
            fit_category = "High Risk / Unqualified"
    
    # ============ Calibrated Probabilities ============
    base_acceptance_rate = school.financials.overall_acceptance_rate or 8.5
    is_rate = school.financials.in_state_acceptance_rate or base_acceptance_rate
    oos_rate = school.financials.out_of_state_acceptance_rate or base_acceptance_rate
    effective_rate = is_rate if is_in_state else oos_rate
    
    # Interview probability based on match score bands
    if match_score >= 80:
        interview_prob = min(92.0, 60.0 + (match_score - 80) * 1.6)
    elif match_score >= 60:
        interview_prob = 30.0 + (match_score - 60) * 1.5
    elif match_score >= 40:
        interview_prob = 10.0 + (match_score - 40) * 1.0
    else:
        interview_prob = max(3.0, match_score * 0.25)
    
    # Acceptance probability — anchored to school's actual rate, modulated by match score
    score_multiplier = match_score / 50.0  # 1.0 at match_score=50 (average)
    accepted_prob = max(2.0, min(55.0, effective_rate * score_multiplier * 1.2))
    
    # Waitlist — higher for "borderline" students (match 40-70)
    if 40 <= match_score <= 70:
        waitlist_prob = max(10.0, min(30.0, 25.0 - abs(match_score - 55) * 0.5))
    elif match_score > 70:
        waitlist_prob = max(5.0, 15.0 - (match_score - 70) * 0.3)
    else:
        waitlist_prob = max(3.0, match_score * 0.2)
    
    # Rejection — remainder, ensuring all three outcome probs sum to ~100
    rejection_prob = max(3.0, 100.0 - accepted_prob - waitlist_prob)
    
    # Normalize outcome probabilities to sum to exactly 100
    outcome_total = accepted_prob + waitlist_prob + rejection_prob
    accepted_prob = round(accepted_prob / outcome_total * 100.0, 1)
    waitlist_prob = round(waitlist_prob / outcome_total * 100.0, 1)
    rejection_prob = round(100.0 - accepted_prob - waitlist_prob, 1)
    
    probabilities = OutcomeProbabilities(
        interviewProbability=round(interview_prob, 1),
        acceptedProbability=accepted_prob,
        waitlistProbability=waitlist_prob,
        rejectionProbability=rejection_prob
    )
    
    logs.append(
        f"[Scoring] cGPA:{cgpa_score:.0f} sGPA:{sgpa_score:.0f} DAT:{dat_aa_score:.0f} "
        f"DATsub:{dat_sub_score:.0f} Prereq:{prereq_score:.0f} Extra:{extra_score:.0f} "
        f"LOR:{lor_score:.0f} Res:{residency_score:.0f} => Match: {match_score:.1f}% | Fit: {fit_category}"
    )
    
    return {
        "match_score": round(match_score, 1),
        "fit_category": fit_category,
        "probabilities": probabilities,
        "logs": logs
    }

def openai_diagnostics_node(state: ComparisonGraphState) -> Dict[str, Any]:
    logs = state.get("logs", [])
    student: StudentComparisonProfile = state["student_profile"]
    school: DentalSchoolProfile = state["school_profile"]
    probabilities = state.get("probabilities")
    fit_cat = state.get("fit_category", "Target")
    match_score = state.get("match_score", 50.0)
    prereqs = state.get("prerequisite_checks", [])
    extracurricular_checks = state.get("extracurricular_checks", [])
    
    school_gpa = school.academic_standards.avg_cgpa or 3.55
    school_sgpa = school.academic_standards.avg_sgpa or 3.45
    school_dat = school.academic_standards.avg_dat_aa or 20.2
    
    # ============ Generate Dynamic ROI Improvements ============
    roi_items: List[RoiImprovement] = []
    roi_idx = 0
    
    # Analyze actual gaps and generate targeted suggestions
    gpa_diff = student.cgpa - school_gpa
    dat_diff = student.dat_aa - school_dat
    
    # DAT improvement — only if below average
    if dat_diff < 0:
        target_dat = max(student.dat_aa + 2, int(school_dat) + 1)
        lift_interview = min(20.0, abs(dat_diff) * 5.0)
        lift_accept = min(12.0, abs(dat_diff) * 3.0)
        roi_idx += 1
        roi_items.append(RoiImprovement(
            id=f"roi-{roi_idx}",
            actionTitle=f"Raise DAT AA from {student.dat_aa} to {target_dat}+",
            description=f"Your DAT Academic Average ({student.dat_aa}) is {abs(dat_diff):.1f} points below {school.name}'s class average ({school_dat:.1f}). Retaking the DAT with focused study can significantly improve competitiveness.",
            category="DAT",
            currentMetric=student.dat_aa,
            targetMetric=target_dat,
            probabilityLift=ProbabilityLift(interviewLift=round(lift_interview, 1), acceptanceLift=round(lift_accept, 1)),
            impactLevel="HIGH"
        ))
    
    # GPA improvement — only if below average
    if gpa_diff < 0:
        target_gpa = round(min(student.cgpa + 0.15, school_gpa), 2)
        lift_interview = min(18.0, abs(gpa_diff) * 25.0)
        lift_accept = min(10.0, abs(gpa_diff) * 15.0)
        roi_idx += 1
        roi_items.append(RoiImprovement(
            id=f"roi-{roi_idx}",
            actionTitle=f"Boost cGPA from {student.cgpa:.2f} toward {target_gpa:.2f}+",
            description=f"Your cGPA ({student.cgpa:.2f}) is {abs(gpa_diff):.2f} below {school.name}'s class average ({school_gpa:.2f}). Focus on acing remaining coursework, especially upper-level sciences.",
            category="GPA",
            currentMetric=f"{student.cgpa:.2f}",
            targetMetric=f"{target_gpa:.2f}",
            probabilityLift=ProbabilityLift(interviewLift=round(lift_interview, 1), acceptanceLift=round(lift_accept, 1)),
            impactLevel="HIGH"
        ))
    
    # Shadowing — only if below recommended
    rec_shadow = school.extracurriculars.recommended_shadowing_hours
    if student.shadowing_hours < rec_shadow:
        roi_idx += 1
        gap = rec_shadow - student.shadowing_hours
        roi_items.append(RoiImprovement(
            id=f"roi-{roi_idx}",
            actionTitle=f"Increase shadowing from {student.shadowing_hours}h to {rec_shadow}h+",
            description=f"You need {gap} more shadowing hours to meet {school.name}'s recommended threshold. Focus on general dentist shadowing with diverse case exposure.",
            category="SHADOWING",
            currentMetric=f"{student.shadowing_hours}h",
            targetMetric=f"{rec_shadow}h",
            probabilityLift=ProbabilityLift(interviewLift=round(min(10.0, gap * 0.1), 1), acceptanceLift=round(min(5.0, gap * 0.05), 1)),
            impactLevel="MEDIUM"
        ))
    
    # Research — if school recommends/requires and student is low
    if school.extracurriculars.research_experience_preference in ("REQUIRED", "RECOMMENDED") and student.research_hours < 50:
        roi_idx += 1
        roi_items.append(RoiImprovement(
            id=f"roi-{roi_idx}",
            actionTitle=f"Gain research experience ({student.research_hours}h → 100h+)",
            description=f"Research is {school.extracurriculars.research_experience_preference.lower()} at {school.name}. Consider joining a lab or independent research project.",
            category="RESEARCH",
            currentMetric=f"{student.research_hours}h",
            targetMetric="100h",
            probabilityLift=ProbabilityLift(interviewLift=7.0, acceptanceLift=4.0),
            impactLevel="MEDIUM"
        ))
    
    # Volunteering — if below minimum
    if student.volunteering_hours < school.extracurriculars.min_volunteering_hours:
        roi_idx += 1
        target_vol = school.extracurriculars.recommended_volunteering_hours
        roi_items.append(RoiImprovement(
            id=f"roi-{roi_idx}",
            actionTitle=f"Increase volunteering from {student.volunteering_hours}h to {target_vol}h+",
            description=f"Community volunteering hours ({student.volunteering_hours}h) are below {school.name}'s minimum ({school.extracurriculars.min_volunteering_hours}h).",
            category="VOLUNTEERING",
            currentMetric=f"{student.volunteering_hours}h",
            targetMetric=f"{target_vol}h",
            probabilityLift=ProbabilityLift(interviewLift=5.0, acceptanceLift=3.0),
            impactLevel="MEDIUM"
        ))
    
    # LOR gap
    lor_checks_list = [r for r in extracurricular_checks if r.category == "LOR"]
    if lor_checks_list and lor_checks_list[0].status != "MET":
        roi_idx += 1
        roi_items.append(RoiImprovement(
            id=f"roi-{roi_idx}",
            actionTitle="Complete LOR requirements",
            description=lor_checks_list[0].details,
            category="LOR",
            currentMetric=f"{student.total_lor_count} letters",
            targetMetric=f"{school.letters_of_evaluation.total_letters_required}+ letters",
            probabilityLift=ProbabilityLift(interviewLift=8.0, acceptanceLift=4.0),
            impactLevel="HIGH"
        ))
    
    # Early submission — always good advice if no other high-impact items
    if len(roi_items) < 3:
        roi_idx += 1
        roi_items.append(RoiImprovement(
            id=f"roi-{roi_idx}",
            actionTitle="Submit AADSAS application early (June 1-15)",
            description="Early submission during the first weeks of the cycle maximizes rolling admission slot availability before interview spots fill.",
            category="LOGISTICS",
            currentMetric="Standard Timing",
            targetMetric="June 1-15 Submission",
            probabilityLift=ProbabilityLift(interviewLift=10.0, acceptanceLift=5.0),
            impactLevel="HIGH"
        ))
    
    # Keep top 3 ROI items sorted by total lift
    roi_items.sort(key=lambda x: x.probabilityLift.interviewLift + x.probabilityLift.acceptanceLift, reverse=True)
    highest_roi = roi_items[:3]
    
    # ============ Default Diagnostics (before LLM enrichment) ============
    most_likely_reason = f"Applicant metrics (cGPA: {student.cgpa:.2f}, DAT AA: {student.dat_aa}) {'align competitively with' if gpa_diff >= 0 and dat_diff >= 0 else 'fall below key averages at'} {school.name} (avg cGPA: {school_gpa:.2f}, avg DAT AA: {school_dat:.1f})."
    most_limiting_factor = highest_roi[0].description if highest_roi else "Review overall application strength."
    action_steps = [roi.actionTitle for roi in highest_roi]
    
    # ============ Enrich with OpenAI if available ============
    if settings.OPENAI_API_KEY and state.get("include_ai_reasoning", True):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            
            # Build completed courses summary
            if student.completed_courses:
                courses_summary = ", ".join([f"{c.course_name} ({c.grade})" for c in student.completed_courses[:10]])
            else:
                courses_summary = "No transcript data available"
            
            # LOR summary
            lor_summary = f"{student.total_lor_count} total ({student.lor_science_faculty_count} science faculty, {student.lor_dentist_count} dentist, committee letter: {'Yes' if student.lor_committee_letter else 'No'})"
            
            # Prerequisite summary
            prereq_met = sum(1 for r in prereqs if r.status == "MET")
            prereq_unknown = sum(1 for r in prereqs if r.status == "UNKNOWN")
            prereq_unmet = sum(1 for r in prereqs if r.status in ("UNMET", "WARNING"))
            prereq_total = len(prereqs)
            
            # Extracurricular summary
            ext_items = [r for r in extracurricular_checks if r.category not in ("GPA", "DAT")]
            ext_summary_parts = []
            for ec in ext_items:
                ext_summary_parts.append(f"{ec.name}: {ec.status} ({ec.studentValue} vs {ec.schoolRequirement})")
            extracurricular_summary = "; ".join(ext_summary_parts) if ext_summary_parts else "No extracurricular data"
            
            # School LOR summary
            lor_reqs = school.letters_of_evaluation
            school_lor_summary = f"{lor_reqs.total_letters_required} total, {lor_reqs.science_faculty_letters_required} science, dentist: {'Required' if lor_reqs.practicing_dentist_letter_required else 'Optional'}"
            
            prompt = STUDENT_PREDICTIVE_ANALYSIS_PROMPT.format(
                student_name=student.name,
                cgpa=student.cgpa,
                sgpa=student.sgpa,
                bcp_gpa=student.bcp_gpa or student.sgpa,
                dat_aa=student.dat_aa,
                dat_ts=student.dat_ts,
                dat_pat=student.dat_pat,
                dat_bio=student.dat_bio or student.dat_aa,
                dat_gc=student.dat_gc or student.dat_aa,
                dat_oc=student.dat_oc or student.dat_aa,
                dat_rc=student.dat_rc or student.dat_aa,
                dat_qr=student.dat_qr or student.dat_aa,
                shadowing_hours=student.shadowing_hours,
                general_shadowing_hours=student.shadowing_hours - student.specialist_shadowing_hours,
                volunteering_hours=student.volunteering_hours,
                dental_exp_hours=student.dental_experience_hours,
                research_hours=student.research_hours,
                student_state=student.state or "Unknown",
                applicant_type=student.applicant_type or "FIRST_TIME",
                completed_courses_summary=courses_summary,
                lor_summary=lor_summary,
                school_name=school.name,
                school_location=school.location,
                school_avg_cgpa=school_gpa,
                school_avg_sgpa=school_sgpa,
                school_avg_dat_aa=school_dat,
                school_avg_dat_ts=school.academic_standards.avg_dat_ts,
                school_min_cgpa=school.academic_standards.min_cgpa_cutoff,
                school_min_dat=school.academic_standards.min_dat_aa_cutoff,
                school_is_rate=school.financials.in_state_acceptance_rate or school.financials.overall_acceptance_rate,
                school_oos_rate=school.financials.out_of_state_acceptance_rate or school.financials.overall_acceptance_rate,
                school_prereq_summary=f"{prereq_total} prerequisites checked",
                school_min_shadowing=school.extracurriculars.min_shadowing_hours,
                school_rec_shadowing=school.extracurriculars.recommended_shadowing_hours,
                school_min_volunteering=school.extracurriculars.min_volunteering_hours,
                school_research_pref=school.extracurriculars.research_experience_preference,
                school_lor_summary=school_lor_summary,
                match_score=match_score,
                fit_category=fit_cat,
                interview_prob=probabilities.interviewProbability if probabilities else 30,
                accepted_prob=probabilities.acceptedProbability if probabilities else 15,
                prereq_met_count=prereq_met,
                prereq_total_count=prereq_total,
                prereq_unknown_count=prereq_unknown,
                prereq_unmet_count=prereq_unmet,
                extracurricular_summary=extracurricular_summary
            )
            
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                seed=42,
                response_format={"type": "json_object"}
            )
            
            ai_data = json.loads(resp.choices[0].message.content or "{}")
            if "mostLikelyReason" in ai_data and isinstance(ai_data["mostLikelyReason"], str):
                most_likely_reason = ai_data["mostLikelyReason"]
            if "mostLimitingFactor" in ai_data and isinstance(ai_data["mostLimitingFactor"], str):
                most_limiting_factor = ai_data["mostLimitingFactor"]
            if "actionSteps" in ai_data and isinstance(ai_data["actionSteps"], list):
                action_steps = ai_data["actionSteps"]
                
            logs.append("[OpenAI Diagnostics] Successfully generated tailored admissions intelligence.")
        except Exception as e:
            logs.append(f"[OpenAI Diagnostics] Note during OpenAI diagnostics: {e}")
            
    diagnostics = DiagnosticsExplainability(
        mostLikelyReason=most_likely_reason,
        mostLimitingFactor=most_limiting_factor,
        highestRoiImprovements=highest_roi,
        actionSteps=action_steps
    )
    
    return {
        "diagnostics": diagnostics,
        "logs": logs
    }

def synthesis_node(state: ComparisonGraphState) -> Dict[str, Any]:
    logs = state.get("logs", [])
    school: DentalSchoolProfile = state["school_profile"]
    prereqs = state.get("prerequisite_checks", [])
    extracurricular_checks = state.get("extracurricular_checks", [])
    
    # Combine all requirement checks for the final result
    all_requirements = list(prereqs) + list(extracurricular_checks)
    
    passed_count = sum(1 for r in all_requirements if r.status == "MET")
    total_count = len(all_requirements)
    
    req_status = "MEETS_ALL"
    if any(r.status == "UNMET" and r.isHardRequirement for r in all_requirements):
        req_status = "FAILS_REQUIREMENTS"
    elif any(r.status in ("WARNING", "UNKNOWN") for r in all_requirements):
        req_status = "WARNINGS"
        
    final_result = PredictionResult(
        schoolId=school.id,
        schoolName=school.name,
        location=school.location,
        fitCategory=state.get("fit_category", "Target"),
        matchScore=state.get("match_score", 50.0),
        requirementsStatus=req_status,
        requirementsPassedCount=passed_count,
        requirementsTotalCount=total_count,
        requirements=all_requirements,
        probabilities=state.get("probabilities"),
        diagnostics=state.get("diagnostics"),
        attached_documents_analyzed=state.get("attached_documents_analyzed", []),
        document_insights=state.get("document_insights")
    )
    
    logs.append("[Synthesis Node] Generated complete PredictionResult.")
    return {
        "final_result": final_result,
        "logs": logs
    }

def create_comparison_graph():
    workflow = StateGraph(ComparisonGraphState)
    workflow.add_node("load_profiles", load_profiles_node)
    workflow.add_node("ingest_documents", ingest_student_documents_node)
    workflow.add_node("prerequisite_audit", prerequisite_audit_node)
    workflow.add_node("extracurricular_audit", extracurricular_audit_node)
    workflow.add_node("benchmark_probability", benchmark_and_probability_node)
    workflow.add_node("openai_diagnostics", openai_diagnostics_node)
    workflow.add_node("synthesis", synthesis_node)
    
    workflow.add_edge(START, "load_profiles")
    workflow.add_edge("load_profiles", "ingest_documents")
    workflow.add_edge("ingest_documents", "prerequisite_audit")
    workflow.add_edge("prerequisite_audit", "extracurricular_audit")
    workflow.add_edge("extracurricular_audit", "benchmark_probability")
    workflow.add_edge("benchmark_probability", "openai_diagnostics")
    workflow.add_edge("openai_diagnostics", "synthesis")
    workflow.add_edge("synthesis", END)
    
    return workflow.compile()

comparison_graph_app = create_comparison_graph()
