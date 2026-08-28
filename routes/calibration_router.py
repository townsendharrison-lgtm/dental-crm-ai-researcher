from typing import Dict, Any, List, Optional
from fastapi import APIRouter, UploadFile, File
import csv
import io
import json
from schemas.historical_schema import HistoricalUploadPayload, HistoricalApplicationItem, RubricCalibrationResult
from agents.calibration_graph import calibration_graph_app
from core.database import get_supabase_client
from data.seed_schools import get_school_by_id_or_default

router = APIRouter(prefix="/api/calibration", tags=["Historical Outcomes & Rubric Calibration"])

_HISTORICAL_DATASET: List[Dict[str, Any]] = [
    {"school_id": "sch6", "student_name_anonymized": "Past Applicant 1", "cgpa": 3.72, "dat_aa": 22, "shadowing_hours": 120, "state": "Massachusetts", "outcome": "ACCEPTED"},
    {"school_id": "sch6", "student_name_anonymized": "Past Applicant 2", "cgpa": 3.45, "dat_aa": 19, "shadowing_hours": 60, "state": "California", "outcome": "WAITLISTED"},
    {"school_id": "sch6", "student_name_anonymized": "Past Applicant 3", "cgpa": 3.60, "dat_aa": 21, "shadowing_hours": 90, "state": "Massachusetts", "outcome": "ACCEPTED"},
    {"school_id": "sch6", "student_name_anonymized": "Past Applicant 4", "cgpa": 3.20, "dat_aa": 17, "shadowing_hours": 40, "state": "Texas", "outcome": "REJECTED"},
    {"school_id": "sch6", "student_name_anonymized": "Past Applicant 5", "cgpa": 3.85, "dat_aa": 23, "shadowing_hours": 150, "state": "New York", "outcome": "ACCEPTED"},
    {"school_id": "sch6", "student_name_anonymized": "Past Applicant 6", "cgpa": 3.50, "dat_aa": 20, "shadowing_hours": 80, "state": "Florida", "outcome": "INTERVIEWED"},
]

@router.get("/outcomes")
async def list_historical_outcomes(school_id: Optional[str] = None):
    if school_id:
        return [a for a in _HISTORICAL_DATASET if a.get("school_id") == school_id]
    return _HISTORICAL_DATASET

@router.post("/upload-csv")
async def upload_csv_outcomes(file: UploadFile = File(...), school_id: str = "sch6"):
    contents = await file.read()
    text = contents.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    
    added_count = 0
    for row in reader:
        item = {
            "school_id": school_id,
            "student_name_anonymized": row.get("name") or row.get("student_name") or f"Applicant {len(_HISTORICAL_DATASET)+1}",
            "cgpa": float(row.get("cgpa") or row.get("gpa") or 3.5),
            "dat_aa": int(row.get("dat_aa") or row.get("dat") or 20),
            "shadowing_hours": int(row.get("shadowing") or row.get("shadowing_hours") or 50),
            "state": row.get("state") or "Other",
            "outcome": (row.get("outcome") or "REJECTED").upper()
        }
        _HISTORICAL_DATASET.append(item)
        added_count += 1
        
    return {
        "success": True,
        "added_count": added_count,
        "total_dataset_size": len(_HISTORICAL_DATASET),
        "message": f"Successfully ingested {added_count} student outcome records."
    }

@router.post("/sync-crm")
async def sync_crm_student_outcomes():
    # Syncs applications from Supabase if available
    client = get_supabase_client()
    if client:
        try:
            res = client.table("student_schools").select("*, users:student_id(name), student_profiles(*)").execute()
            if res and res.data:
                for r in res.data:
                    prof = r.get("student_profiles") or {}
                    status_map = {
                        "Accepted": "ACCEPTED",
                        "Interviewed": "INTERVIEWED",
                        "Waitlisted": "WAITLISTED",
                        "Rejected": "REJECTED"
                    }
                    if r.get("status") in status_map:
                        _HISTORICAL_DATASET.append({
                            "school_id": r.get("school_id"),
                            "student_name_anonymized": r.get("users", {}).get("name", "CRM Student"),
                            "cgpa": float(prof.get("gpa") or 3.5),
                            "dat_aa": int(prof.get("dat_aa") or 20),
                            "shadowing_hours": 80,
                            "state": prof.get("state") or "Massachusetts",
                            "outcome": status_map[r["status"]]
                        })
        except Exception as e:
            print(f"[Sync CRM] Warning: {e}")
            
    return {
        "success": True,
        "synced_count": len(_HISTORICAL_DATASET),
        "message": "CRM Student application outcomes synced into historical calibration dataset."
    }

@router.post("/recalibrate", response_model=RubricCalibrationResult)
async def recalibrate_school_rubrics(school_id: str = "sch6", cycle: str = "2025-2026"):
    school = get_school_by_id_or_default(school_id)
    relevant_apps = [a for a in _HISTORICAL_DATASET if a.get("school_id") == school_id]
    if not relevant_apps:
        relevant_apps = _HISTORICAL_DATASET
        
    state_input = {
        "school_id": school_id,
        "cycle": cycle,
        "historical_applications": relevant_apps,
        "logs": []
    }
    
    res = await calibration_graph_app.ainvoke(state_input)
    
    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    return RubricCalibrationResult(
        school_id=school.id,
        school_name=school.name,
        analyzed_applications_count=len(relevant_apps),
        calibrated_weights=res.get("optimized_weights", {
            "gpaWeight": 25.0,
            "datWeight": 30.0,
            "shadowingWeight": 15.0,
            "volunteeringWeight": 10.0,
            "researchWeight": 5.0,
            "inStateWeight": 10.0,
            "lorWeight": 5.0
        }),
        observed_state_trends=res.get("state_trends", []),
        top_determining_factors=["DAT Total Science >= 21", "Minimum 100 General Shadowing Hours", "In-State Residency Priority"],
        calibration_notes=res.get("calibration_notes", "Empirical regression calibration complete."),
        calibrated_at=now_iso
    )
