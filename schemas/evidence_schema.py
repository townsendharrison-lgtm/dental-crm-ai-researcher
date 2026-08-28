from typing import Optional, Any, Dict, List
from enum import Enum
from pydantic import BaseModel, Field

class SourceType(str, Enum):
    URL = "URL"
    PDF = "PDF"
    TXT = "TXT"
    IMAGE = "IMAGE"
    MANUAL = "MANUAL"
    ADEA_GUIDE = "ADEA_GUIDE"

class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"                       # Green
    FOUND_UNVERIFIED = "FOUND_UNVERIFIED"       # Blue
    INFERRED = "INFERRED"                       # Teal
    CONFLICTING = "CONFLICTING"                 # Red
    NOT_FOUND = "NOT_FOUND"                     # Grey

class EvidenceCitation(BaseModel):
    id: Optional[str] = None
    school_id: Optional[str] = None
    cycle: str = "2025-2026"
    category: str = "General Information"
    field_key: str = "general"
    field_label: str = "Field"
    extracted_value: Any = None
    source_type: SourceType = SourceType.URL
    source_name: str = "Admissions Source"
    source_url: Optional[str] = None
    page_number: Optional[int] = None
    raw_snippet: str = ""
    confidence_score: float = Field(default=0.95, ge=0.0, le=1.0)
    status: VerificationStatus = VerificationStatus.FOUND_UNVERIFIED
    is_verified: bool = False
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    conflicting_snippets: Optional[List[Dict[str, Any]]] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class EvidenceVerifyPayload(BaseModel):
    is_verified: bool = True
    status: VerificationStatus = VerificationStatus.VERIFIED
    corrected_value: Optional[Any] = None
    notes: Optional[str] = None
