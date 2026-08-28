from typing import Dict, Any, List, Optional
from core.database import get_supabase_client
from schemas.evidence_schema import EvidenceCitation, VerificationStatus

def save_evidence_citations(citations: List[Dict[str, Any]]) -> bool:
    """
    Saves or updates evidence citations in the public.school_evidence table.
    """
    client = get_supabase_client()
    if not client or not citations:
        return False
    try:
        # Prepare records for Supabase schema
        records = []
        for c in citations:
            rec = {
                "school_id": c.get("school_id"),
                "category": c.get("category", "General Information"),
                "field_key": c.get("field_key"),
                "field_label": c.get("field_label", c.get("field_key")),
                "extracted_value": c.get("extracted_value", {}),
                "source_type": c.get("source_type", "URL"),
                "source_name": c.get("source_name", "Admissions Source"),
                "source_url": c.get("source_url"),
                "page_number": c.get("page_number"),
                "raw_snippet": c.get("raw_snippet", ""),
                "confidence_score": c.get("confidence_score", 0.95),
                "is_verified": c.get("is_verified", False),
                "notes": c.get("notes")
            }
            records.append(rec)
            
        client.table("school_evidence").insert(records).execute()
        return True
    except Exception as e:
        print(f"[Supabase Tool] Error saving evidence: {e}")
        return False

def save_school_rubric(school_id: str, weights: Dict[str, Any], cutoffs: Dict[str, Any], prerequisites: List[Any], holistic_factors: Dict[str, Any]) -> bool:
    """
    Upserts school scoring rubric and weights into public.school_scoring_rubrics.
    """
    client = get_supabase_client()
    if not client:
        return False
    try:
        data = {
            "school_id": school_id,
            "weights": weights,
            "cutoffs": cutoffs,
            "prerequisites": prerequisites,
            "holistic_factors": holistic_factors,
            "updated_at": "now()"
        }
        client.table("school_scoring_rubrics").upsert(data).execute()
        return True
    except Exception as e:
        print(f"[Supabase Tool] Error upserting rubric: {e}")
        return False

def fetch_school_by_id(school_id: str) -> Optional[Dict[str, Any]]:
    client = get_supabase_client()
    if not client:
        return None
    try:
        res = client.table("schools").select("*").eq("id", school_id).single().execute()
        return res.data if res else None
    except Exception as e:
        print(f"[Supabase Tool] Error fetching school {school_id}: {e}")
        return None
