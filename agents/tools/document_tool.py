import os
from typing import Dict, Any, List
from pypdf import PdfReader

def parse_pdf_document(file_bytes: bytes, file_name: str) -> Dict[str, Any]:
    """
    Parses a PDF document into structured pages with line numbering and snippets.
    """
    import io
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages_content: List[Dict[str, Any]] = []
        full_text_list: List[str] = []
        
        for idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            cleaned_page = page_text.strip()
            pages_content.append({
                "page_number": idx + 1,
                "text": cleaned_page
            })
            full_text_list.append(f"--- PAGE {idx + 1} ---\n{cleaned_page}")
            
        full_text = "\n\n".join(full_text_list)
        return {
            "success": True,
            "file_name": file_name,
            "total_pages": len(reader.pages),
            "pages": pages_content,
            "full_text": full_text[:45000]
        }
    except Exception as e:
        return {
            "success": False,
            "file_name": file_name,
            "error": str(e),
            "total_pages": 0,
            "pages": [],
            "full_text": ""
        }

def parse_txt_document(file_bytes: bytes, file_name: str) -> Dict[str, Any]:
    """
    Parses plain text document or manual notes.
    """
    try:
        text = file_bytes.decode("utf-8", errors="replace").strip()
        return {
            "success": True,
            "file_name": file_name,
            "total_pages": 1,
            "pages": [{"page_number": 1, "text": text}],
            "full_text": text[:40000]
        }
    except Exception as e:
        return {
            "success": False,
            "file_name": file_name,
            "error": str(e),
            "total_pages": 0,
            "pages": [],
            "full_text": ""
        }
