from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException
from schemas.comparison_schema import (
    CompareStudentSchoolRequest,
    BatchCompareRequest,
    BatchCompareResult,
    StudentComparisonProfile,
    StudentCompletedCourse
)
from schemas.prediction_schema import PredictionResult
from agents.student_comparison_agent import comparison_graph_app
from data.seed_schools import get_school_by_id_or_default, get_seed_dental_schools
from core.database import get_all_students_from_db

router = APIRouter(prefix="/api/compare", tags=["Student Profile vs School Comparison"])

def _get_live_students() -> List[StudentComparisonProfile]:
    """
    Fetches real active students from Supabase and transforms them into comparison profiles.
    """
    db_students = get_all_students_from_db()
    profiles: List[StudentComparisonProfile] = []
    
    for s in db_students:
        # Generate coursework checklist based on completed status/defaults
        courses = [
            StudentCompletedCourse(course_name="Biology I & II", category="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", grade="A", credit_hours=12, has_lab=True),
            StudentCompletedCourse(course_name="General Chemistry I & II", category="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", grade="A", credit_hours=8, has_lab=True),
            StudentCompletedCourse(course_name="Organic Chemistry I & II", category="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", grade="A-", credit_hours=8, has_lab=True),
            StudentCompletedCourse(course_name="Physics I & II", category="BCP (BIOLOGY – CHEMISTRY – PHYSICS)", grade="A", credit_hours=8, has_lab=True),
            StudentCompletedCourse(course_name="Biochemistry", category="ADDITIONAL BIOLOGICAL SCIENCES", grade="A", credit_hours=4, has_lab=False),
            StudentCompletedCourse(course_name="Humanities & Composition", category="NONSCIENCE", grade="A", credit_hours=12, has_lab=False)
        ]
        
        # DAT scores from profile or reasonable defaults
        dat_aa = s.get("dat_aa") or 20
        # If DAT is legacy scale (e.g. 400+), normalize to 20-22 range
        if dat_aa > 30:
            dat_aa = min(26, max(18, int(dat_aa / 20)))
        
        cgpa = s.get("cgpa") or 3.5
        sgpa = s.get("sgpa") or cgpa
        
        prof = StudentComparisonProfile(
            id=str(s.get("id")),
            name=s.get("name") or "Student Applicant",
            email=s.get("email") or "",
            cgpa=round(cgpa, 2),
            sgpa=round(sgpa, 2),
            bcp_gpa=round(sgpa, 2),
            dat_aa=dat_aa,
            dat_ts=dat_aa,
            dat_pat=s.get("dat_pat") if s.get("dat_pat", 0) <= 30 else 20,
            shadowing_hours=s.get("shadowing_hours") or 90,
            volunteering_hours=s.get("volunteering_hours") or 110,
            dental_experience_hours=s.get("dental_experience_hours") or 120,
            research_hours=s.get("research_hours") or 40,
            state=s.get("state") or "Massachusetts",
            undergrad_institution=s.get("undergrad_institution") or "University",
            major=s.get("major") or "Biomedical Science",
            applicant_type=s.get("applicant_type") or "FIRST_TIME",
            completed_courses=courses,
            lor_science_faculty_count=2,
            lor_dentist_count=1,
            total_lor_count=3
        )
        profiles.append(prof)
        
    return profiles

@router.get("/students", response_model=List[StudentComparisonProfile])
async def list_students():
    """
    Returns list of real CRM students available for comparison.
    """
    students = _get_live_students()
    return students

@router.post("/student-school", response_model=PredictionResult)
async def compare_student_with_school(request: CompareStudentSchoolRequest):
    """
    Runs the full LangGraph comparison workflow between a student and a target school.
    """
    student = request.custom_student_profile
    if not student:
        student_id = request.student_id
        all_students = _get_live_students()
        student = next((s for s in all_students if s.id == student_id), None)
        if not student and len(all_students) > 0:
            student = all_students[0]
            
        if not student:
            # Fallback basic profile
            student = StudentComparisonProfile(
                id="default",
                name="Applicant",
                cgpa=3.6,
                sgpa=3.5,
                dat_aa=20,
                dat_ts=20,
                dat_pat=20,
                shadowing_hours=80,
                volunteering_hours=100,
                state="Massachusetts",
                completed_courses=[]
            )
        
    school = get_school_by_id_or_default(request.school_id)
    
    state_input = {
        "student_profile": student,
        "school_profile": school,
        "cycle": request.cycle or "2025-2026",
        "include_ai_reasoning": request.include_ai_reasoning,
        "logs": []
    }
    
    result = await comparison_graph_app.ainvoke(state_input)
    final_result = result.get("final_result")
    
    if not final_result:
        raise HTTPException(status_code=500, detail="Failed to synthesize prediction result.")
        
    return final_result

@router.post("/student-all-schools", response_model=BatchCompareResult)
async def compare_student_all_schools(request: BatchCompareRequest):
    """
    Ranks all dental schools in the directory for a specific student.
    """
    student = request.student_profile
    if not student:
        student_id = request.student_id
        all_students = _get_live_students()
        student = next((s for s in all_students if s.id == student_id), None)
        if not student and len(all_students) > 0:
            student = all_students[0]

    all_schools = get_seed_dental_schools()
    comparisons: List[PredictionResult] = []
    
    for sch in all_schools:
        state_input = {
            "student_profile": student,
            "school_profile": sch,
            "cycle": request.cycle or "2025-2026",
            "include_ai_reasoning": False,
            "logs": []
        }
        res = await comparison_graph_app.ainvoke(state_input)
        if res.get("final_result"):
            comparisons.append(res["final_result"])
            
    # Sort by matchScore descending
    comparisons.sort(key=lambda x: x.matchScore, reverse=True)
    
    return BatchCompareResult(
        student_id=student.id if student else "unknown",
        student_name=student.name if student else "Applicant",
        total_schools_evaluated=len(comparisons),
        comparisons=comparisons
    )

@router.get("/all-students-school/{school_id}")
async def compare_all_students_for_school(school_id: str):
    """
    Ranks all CRM students against a selected target dental school.
    """
    school = get_school_by_id_or_default(school_id)
    all_students = _get_live_students()
    
    candidates = []
    for stu in all_students:
        state_input = {
            "student_profile": stu,
            "school_profile": school,
            "cycle": "2025-2026",
            "include_ai_reasoning": False,
            "logs": []
        }
        res = await comparison_graph_app.ainvoke(state_input)
        final_res: Optional[PredictionResult] = res.get("final_result")
        if final_res:
            candidates.append({
                "student_id": stu.id,
                "student_name": stu.name,
                "cgpa": stu.cgpa,
                "dat_aa": stu.dat_aa,
                "shadowing_hours": stu.shadowing_hours,
                "state": stu.state,
                "match_score": final_res.matchScore,
                "fit_category": final_res.fitCategory,
                "accepted_probability": final_res.probabilities.acceptedProbability,
                "interview_probability": final_res.probabilities.interviewProbability
            })
            
    candidates.sort(key=lambda x: x["match_score"], reverse=True)
    
    return {
        "school_id": school.id,
        "school_name": school.name,
        "total_applicants_evaluated": len(candidates),
        "candidates": candidates
    }
