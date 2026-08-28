import os
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from pydantic import BaseModel
from agents.research_graph import research_graph_app
from agents.tools.document_tool import parse_pdf_document, parse_txt_document
from agents.tools.vision_tool import extract_image_text_with_openai_vision
from schemas.criteria_schema import (
    DentalSchoolProfile,
    GeneralInformation,
    EnrolleeStatistics,
    PrerequisiteCourseItem,
    AcademicStandards,
    TuitionAndFinancials,
    SectionCompleteness,
    FieldCompletenessSummary
)
from schemas.evidence_schema import EvidenceCitation, VerificationStatus, EvidenceVerifyPayload
from data.seed_schools import get_seed_dental_schools, get_school_by_id_or_default

router = APIRouter(prefix="/api/research", tags=["Research Engine"])

def get_all_research_schools() -> Dict[str, DentalSchoolProfile]:
    schools = get_seed_dental_schools()
    for s in schools:
        if s.id not in _SCHOOLS_STORE:
            _SCHOOLS_STORE[s.id] = s
    return _SCHOOLS_STORE

# In-memory storage for mutations
_SCHOOLS_STORE: Dict[str, DentalSchoolProfile] = {}
_REVIEW_QUEUE: List[Dict[str, Any]] = []

class CrawlPayload(BaseModel):
    url: str
    school_id: Optional[str] = "sch6"
    school_name: Optional[str] = "Boston University Henry M. Goldman School of Dental Medicine"
    cycle: Optional[str] = "2025-2026"

class ManualNotePayload(BaseModel):
    school_id: str
    school_name: str
    content: str
    cycle: Optional[str] = "2025-2026"

class CreateSchoolProfilePayload(BaseModel):
    id: Optional[str] = None
    name: str
    location: str
    website_url: Optional[str] = None
    avg_cgpa: Optional[float] = 3.5
    avg_dat_aa: Optional[float] = 20.0
    overall_acceptance_rate: Optional[float] = 10.0
    is_acceptance_rate: Optional[float] = 12.0
    oos_acceptance_rate: Optional[float] = 8.0
    cycle: Optional[str] = "2025-2026"
    crawl_now: Optional[bool] = False

@router.get("/schools", response_model=List[DentalSchoolProfile])
async def list_schools(search: Optional[str] = None):
    store = get_all_research_schools()
    results = list(store.values())
    if search:
        s_lower = search.lower()
        results = [s for s in results if s_lower in s.name.lower() or s_lower in s.location.lower()]
    return results

@router.post("/schools", response_model=DentalSchoolProfile)
async def create_school(payload: CreateSchoolProfilePayload):
    import uuid
    school_id = payload.id or f"sch-{uuid.uuid4().hex[:8]}"
    state_loc = payload.location.split(",")[-1].strip() if "," in payload.location else payload.location
    
    new_profile = DentalSchoolProfile(
        id=school_id,
        name=payload.name,
        cycle=payload.cycle or "2025-2026",
        location=payload.location,
        completeness=FieldCompletenessSummary(
            total_fields_extracted=0,
            reviewed_percentage=0.0,
            verified_percentage=0.0,
            verified_count=0,
            found_unverified_count=0,
            inferred_count=0,
            conflicting_count=0,
            not_found_count=0
        ),
        general_information=GeneralInformation(
            university_affiliation=payload.name,
            state=state_loc,
            country="United States",
            website_url=payload.website_url
        ),
        enrollee_statistics=EnrolleeStatistics(
            baccalaureate_count=0,
            masters_or_beyond_count=0
        ),
        prerequisites=[],
        academic_standards=AcademicStandards(
            avg_cgpa=payload.avg_cgpa or 3.5,
            avg_sgpa=(payload.avg_cgpa or 3.5) - 0.05,
            avg_dat_aa=payload.avg_dat_aa or 20.0,
            avg_dat_ts=payload.avg_dat_aa or 20.0,
            avg_dat_pat=payload.avg_dat_aa or 20.0
        ),
        financials=TuitionAndFinancials(
            in_state_acceptance_rate=payload.is_acceptance_rate or 12.0,
            out_of_state_acceptance_rate=payload.oos_acceptance_rate or 8.0,
            overall_acceptance_rate=payload.overall_acceptance_rate or 10.0
        ),
        evidence_citations=[]
    )
    
    _SCHOOLS_STORE[school_id] = new_profile
    
    # Save to Supabase if client exists
    try:
        from core.database import get_supabase_client
        client = get_supabase_client()
        if client:
            client.table("schools").insert({
                "id": school_id,
                "name": payload.name,
                "location": payload.location,
                "avg_gpa": payload.avg_cgpa,
                "dat_avg": payload.avg_dat_aa,
                "acceptance_rate": payload.overall_acceptance_rate,
                "is_acceptance_rate": payload.is_acceptance_rate,
                "oos_acceptance_rate": payload.oos_acceptance_rate
            }).execute()
    except Exception as e:
        print(f"[Create School] Note inserting to DB: {e}")
        
    # Trigger initial crawl if requested
    if payload.crawl_now and payload.website_url:
        try:
            state_input = {
                "school_id": school_id,
                "school_name": payload.name,
                "cycle": payload.cycle or "2025-2026",
                "source_url": payload.website_url,
                "source_type": "URL",
                "logs": []
            }
            res = await research_graph_app.ainvoke(state_input)
            if res.get("profile"):
                new_profile = res["profile"]
                _SCHOOLS_STORE[school_id] = new_profile
        except Exception as crawl_err:
            print(f"[Create School] Auto-crawl error: {crawl_err}")
            
    return new_profile

@router.get("/schools/{school_id}", response_model=DentalSchoolProfile)
async def get_school_details(school_id: str):
    store = get_all_research_schools()
    if school_id in store:
        return store[school_id]
    return get_school_by_id_or_default(school_id)

@router.post("/crawl")
async def trigger_crawl(payload: CrawlPayload):
    state_input = {
        "school_id": payload.school_id or "sch6",
        "school_name": payload.school_name or "Dental School",
        "cycle": payload.cycle or "2025-2026",
        "source_url": payload.url,
        "source_type": "URL",
        "logs": []
    }
    
    result = await research_graph_app.ainvoke(state_input)
    profile = result.get("profile") or get_school_by_id_or_default(payload.school_id or "sch6")
    _SCHOOLS_STORE[profile.id] = profile
    
    return {
        "success": True,
        "message": f"Successfully crawled and analyzed {payload.url}",
        "profile": profile,
        "extracted_citations_count": len(result.get("citations", [])),
        "conflict_count": len(result.get("conflict_reports", [])),
        "logs": result.get("logs", [])
    }

@router.post("/ingest-file")
async def ingest_file(
    file: UploadFile = File(...),
    school_id: str = Form("sch6"),
    school_name: str = Form("Boston University Henry M. Goldman School of Dental Medicine"),
    cycle: str = Form("2025-2026")
):
    contents = await file.read()
    filename = file.filename or "uploaded_doc"
    ext = filename.split(".")[-1].lower()
    
    parsed_text = ""
    source_type = "PDF"
    
    if ext == "pdf":
        doc_res = parse_pdf_document(contents, filename)
        parsed_text = doc_res.get("full_text", "")
        source_type = "PDF"
    elif ext in ["png", "jpg", "jpeg", "webp"]:
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
        vision_res = await extract_image_text_with_openai_vision(contents, filename, mime)
        parsed_text = vision_res.get("raw_text", "")
        source_type = "IMAGE"
    else:
        txt_res = parse_txt_document(contents, filename)
        parsed_text = txt_res.get("full_text", "")
        source_type = "TXT"
        
    state_input = {
        "school_id": school_id,
        "school_name": school_name,
        "cycle": cycle,
        "source_file_path": filename,
        "source_type": source_type,
        "raw_content": parsed_text,
        "logs": []
    }
    
    result = await research_graph_app.ainvoke(state_input)
    profile = result.get("profile") or get_school_by_id_or_default(school_id)
    _SCHOOLS_STORE[profile.id] = profile
    
    return {
        "success": True,
        "file_name": filename,
        "source_type": source_type,
        "profile": profile,
        "extracted_citations_count": len(result.get("citations", [])),
        "logs": result.get("logs", [])
    }

@router.post("/manual-note")
async def ingest_manual_note(payload: ManualNotePayload):
    state_input = {
        "school_id": payload.school_id,
        "school_name": payload.school_name,
        "cycle": payload.cycle or "2025-2026",
        "source_type": "MANUAL",
        "raw_content": payload.content,
        "logs": []
    }
    result = await research_graph_app.ainvoke(state_input)
    return {"success": True, "result": result}

@router.get("/spreadsheet")
async def get_spreadsheet_matrix(category: Optional[str] = None):
    schools = list(_SCHOOLS_STORE.values())
    rows = []
    for s in schools:
        row = {
            "school_id": s.id,
            "school_name": s.name,
            "location": s.location,
            "cycle": s.cycle,
            "dean": s.general_information.dean,
            "avg_cgpa": s.academic_standards.avg_cgpa,
            "avg_dat_aa": s.academic_standards.avg_dat_aa,
            "min_shadowing": s.extracurriculars.min_shadowing_hours,
            "rec_shadowing": s.extracurriculars.recommended_shadowing_hours,
            "biology_credits": next((p.semester_credits for p in s.prerequisites if "bio" in p.course_name.lower()), 8),
            "biochem_required": any(p.required for p in s.prerequisites if "biochem" in p.course_name.lower()),
            "baccalaureate_enrollees": s.enrollee_statistics.baccalaureate_count,
            "masters_enrollees": s.enrollee_statistics.masters_or_beyond_count,
            "completeness_verified": s.completeness.verified_count,
            "completeness_percentage": s.completeness.verified_percentage,
            "status": "VERIFIED" if s.completeness.verified_percentage > 20 else "IN_PROGRESS"
        }
        rows.append(row)
    return {"total": len(rows), "rows": rows}

@router.get("/review-queue")
async def get_review_queue(school_id: Optional[str] = None):
    items = list(_REVIEW_QUEUE)
    try:
        from core.database import get_supabase_client
        client = get_supabase_client()
        if client:
            query = client.table("school_evidence").select("*").eq("status", "CONFLICTING")
            if school_id:
                query = query.eq("school_id", school_id)
            db_conflicts = query.execute().data or []
            for c in db_conflicts:
                items.append({
                    "id": c.get("id"),
                    "school_id": c.get("school_id"),
                    "school_name": c.get("school_name", "Dental School"),
                    "category": c.get("category", "General"),
                    "field_key": c.get("field_key"),
                    "field_label": c.get("field_label") or c.get("field_key"),
                    "issue_type": "CONFLICTING",
                    "confidence_score": c.get("confidence_score") or 0.85,
                    "source_a": {
                        "name": c.get("source_name", "Primary Source"),
                        "snippet": c.get("verbatim_quote", ""),
                        "value": str(c.get("extracted_value", ""))
                    },
                    "source_b": {
                        "name": "Secondary Document",
                        "snippet": "Conflicting requirement found across official school sources.",
                        "value": "Requires Admin Review"
                    },
                    "created_at": c.get("created_at")
                })
    except Exception as e:
        print(f"[Review Queue] Note loading DB conflicts: {e}")
    if school_id:
        return [item for item in items if item.get("school_id") == school_id]
    return items

@router.post("/review-queue/{item_id}/resolve")
async def resolve_review_item(item_id: str, payload: EvidenceVerifyPayload):
    global _REVIEW_QUEUE
    _REVIEW_QUEUE = [item for item in _REVIEW_QUEUE if item.get("id") != item_id]
    try:
        from core.database import get_supabase_client
        client = get_supabase_client()
        if client:
            client.table("school_evidence").update({
                "status": payload.status or "VERIFIED",
                "is_verified": (payload.status != "REJECTED")
            }).eq("id", item_id).execute()
    except Exception as e:
        print(f"[Review Queue] Note resolving in DB: {e}")
    return {"success": True, "message": f"Review item {item_id} marked as {payload.status}"}

@router.get("/sources")
async def list_sources(school_id: Optional[str] = None):
    school = get_school_by_id_or_default(school_id or "sch6")
    return {
        "school_id": school.id,
        "school_name": school.name,
        "sources": school.evidence_citations
    }
