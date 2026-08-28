import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from schemas.comparison_schema import StudentComparisonProfile, StudentCompletedCourse
from schemas.criteria_schema import DentalSchoolProfile
from data.seed_schools import get_school_by_id_or_default
from agents.student_comparison_agent import comparison_graph_app
from agents.research_graph import research_graph_app

async def test_student_comparison_graph():
    student = StudentComparisonProfile(
        name="Sarah Jenkins",
        cgpa=3.74,
        sgpa=3.68,
        dat_aa=22,
        dat_ts=22,
        dat_pat=21,
        shadowing_hours=115,
        state="Massachusetts",
        completed_courses=[
            StudentCompletedCourse(course_name="Biology", category="BCP", grade="A", credit_hours=12, has_lab=True),
            StudentCompletedCourse(course_name="General Chemistry", category="BCP", grade="A", credit_hours=8, has_lab=True),
            StudentCompletedCourse(course_name="Organic Chemistry", category="BCP", grade="A-", credit_hours=8, has_lab=True),
            StudentCompletedCourse(course_name="Physics", category="BCP", grade="A", credit_hours=8, has_lab=True),
            StudentCompletedCourse(course_name="Biochemistry", category="ADDITIONAL BIOLOGICAL SCIENCES", grade="A", credit_hours=4, has_lab=False)
        ]
    )
    school = get_school_by_id_or_default("sch6")  # Boston University GSDM
    
    state_input = {
        "student_profile": student,
        "school_profile": school,
        "cycle": "2025-2026",
        "include_ai_reasoning": False,
        "logs": []
    }
    
    result = await comparison_graph_app.ainvoke(state_input)
    assert result is not None
    assert "final_result" in result
    pred = result["final_result"]
    assert pred.matchScore > 50
    assert pred.probabilities.interviewProbability > 0
    assert pred.probabilities.acceptedProbability > 0
    assert len(pred.requirements) > 0
async def test_student_document_ingestion():
    # Test real student with uploaded application PDF (Camille Cambre)
    student = StudentComparisonProfile(
        id="00f7830a-af9b-4a98-ab67-e70c3f2e45be",
        name="Camille Cambre",
        cgpa=3.65,
        sgpa=3.60,
        dat_aa=21,
        dat_ts=21,
        dat_pat=20,
        shadowing_hours=80,
        state="Louisiana",
        completed_courses=[]
    )
    school = get_school_by_id_or_default("sch-01bc7acd") # UT Health San Antonio
    
    state_input = {
        "student_profile": student,
        "school_profile": school,
        "cycle": "2025-2026",
        "include_ai_reasoning": False,
        "logs": []
    }
    
    # Run 1
    result1 = await comparison_graph_app.ainvoke(state_input)
    pred1 = result1["final_result"]
    
    # Run 2 (Immediate repeat with same inputs)
    result2 = await comparison_graph_app.ainvoke(state_input)
    pred2 = result2["final_result"]
    
    # Assert 100% Deterministic Equality
    assert pred1.matchScore == pred2.matchScore, f"Match score mismatch: {pred1.matchScore} vs {pred2.matchScore}"
    assert pred1.probabilities.interviewProbability == pred2.probabilities.interviewProbability
    assert pred1.probabilities.acceptedProbability == pred2.probabilities.acceptedProbability
    assert pred1.requirementsPassedCount == pred2.requirementsPassedCount
    
    print("SUCCESS! Idempotency & Repeatability Verified: 100% Identical Results on Both Runs!")
    print(f"Run 1 Match: {pred1.matchScore}% | Run 2 Match: {pred2.matchScore}%")
    print(f"Run 1 Accept: {pred1.probabilities.acceptedProbability}% | Run 2 Accept: {pred2.probabilities.acceptedProbability}%")
    print(f"Requirements: {pred1.requirementsPassedCount}/{pred1.requirementsTotalCount}")

if __name__ == "__main__":
    asyncio.run(test_student_comparison_graph())
    asyncio.run(test_student_document_ingestion())
