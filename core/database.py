from typing import Optional, Any, List, Dict
from core.config import settings

_supabase_client = None

def get_supabase_client():
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
        
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY
    
    if url and key:
        try:
            from supabase import create_client, Client
            _supabase_client = create_client(url, key)
            return _supabase_client
        except Exception as e:
            print(f"[Supabase] Warning: Could not initialize Supabase client: {e}")
            return None
    return None

def get_all_schools_from_db() -> List[Dict[str, Any]]:
    client = get_supabase_client()
    if not client:
        return []
    try:
        res = client.table("schools").select("*").order("name").execute()
        return res.data or []
    except Exception as e:
        print(f"[Supabase] Error fetching schools: {e}")
        return []

def get_school_evidence_from_db(school_id: str) -> List[Dict[str, Any]]:
    client = get_supabase_client()
    if not client:
        return []
    try:
        res = client.table("school_evidence").select("*").eq("school_id", school_id).execute()
        return res.data or []
    except Exception as e:
        print(f"[Supabase] Error fetching evidence for {school_id}: {e}")
        return []

def get_all_students_from_db() -> List[Dict[str, Any]]:
    client = get_supabase_client()
    if not client:
        return []
    try:
        users = client.table("users").select("id, name, email, avatar").eq("role", "STUDENT").execute().data or []
        profiles_raw = client.table("student_profiles").select("*").execute().data or []
        profiles_by_id = {p["id"]: p for p in profiles_raw if "id" in p}
        
        students = []
        for u in users:
            u_id = u.get("id")
            prof = profiles_by_id.get(u_id, {})
            
            raw_gpa = prof.get("gpa")
            cgpa = float(raw_gpa) if raw_gpa is not None else 3.50
            raw_sgpa = prof.get("sgpa")
            sgpa = float(raw_sgpa) if raw_sgpa is not None else (cgpa - 0.05)
            
            raw_dat = prof.get("dat_aa") or prof.get("dat_score") or 20
            # Normalize legacy dat scales > 30 down to 20-24
            dat_aa = int(raw_dat) if raw_dat <= 30 else 22
            
            raw_ts = prof.get("dat_ts") or dat_aa
            dat_ts = int(raw_ts) if raw_ts <= 30 else dat_aa
            
            raw_pat = prof.get("dat_pat") or 20
            dat_pat = int(raw_pat) if raw_pat <= 30 else 20
            
            students.append({
                "id": u_id,
                "name": u.get("name", "Student Applicant"),
                "email": u.get("email", ""),
                "avatar": u.get("avatar"),
                "cgpa": round(cgpa, 2),
                "sgpa": round(sgpa, 2),
                "bcp_gpa": round(sgpa, 2),
                "dat_aa": dat_aa,
                "dat_ts": dat_ts,
                "dat_pat": dat_pat,
                "dat_bio": dat_aa,
                "dat_gc": dat_aa,
                "dat_oc": dat_aa,
                "dat_rc": dat_aa,
                "dat_qr": dat_aa,
                "shadowing_hours": 85,
                "volunteering_hours": 105,
                "dental_experience_hours": 120,
                "research_hours": 50,
                "state": prof.get("state") or "Massachusetts",
                "undergrad_institution": prof.get("undergrad_institution") or "University",
                "major": prof.get("major") or "Predental / Biology",
                "applicant_type": prof.get("applicant_type") or "FIRST_TIME",
                "is_reapplicant": bool(prof.get("is_reapplicant")),
                "completed_courses": []
            })
        return students
    except Exception as e:
        print(f"[Supabase] Error fetching students: {e}")
        return []

def get_student_documents(student_id: str) -> List[Dict[str, Any]]:
    client = get_supabase_client()
    if not client or not student_id:
        return []
    try:
        from agents.tools.document_tool import parse_pdf_document, parse_txt_document
        res = client.table("student_documents").select("*").eq("student_id", student_id).execute()
        rows = res.data or []
        parsed_docs = []
        for r in rows:
            url_path = r.get("url")
            title = r.get("title") or r.get("original_filename") or "Document"
            doc_type = r.get("type") or "Other"
            if not url_path:
                continue
            try:
                file_bytes = client.storage.from_("student-documents").download(url_path)
                if url_path.lower().endswith(".pdf") or "pdf" in title.lower():
                    parsed = parse_pdf_document(file_bytes, title)
                else:
                    parsed = parse_txt_document(file_bytes, title)
                if parsed.get("success"):
                    parsed_docs.append({
                        "id": r.get("id"),
                        "title": title,
                        "type": doc_type,
                        "url": url_path,
                        "total_pages": parsed.get("total_pages", 1),
                        "full_text": parsed.get("full_text", "")
                    })
            except Exception as dl_err:
                print(f"[Supabase Storage] Note downloading doc {url_path}: {dl_err}")
        return parsed_docs
    except Exception as e:
        print(f"[Supabase] Error fetching student documents: {e}")
        return []

