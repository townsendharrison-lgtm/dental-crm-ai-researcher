import base64
from typing import Dict, Any, Optional
from core.config import settings

async def extract_image_text_with_openai_vision(file_bytes: bytes, file_name: str, mime_type: str = "image/png") -> Dict[str, Any]:
    """
    Uses OpenAI GPT-4o Multimodal Vision to extract text, tables, and criteria from image files (PNG/JPG/WEBP).
    """
    if not settings.OPENAI_API_KEY:
        return {
            "success": False,
            "error": "OpenAI API key not configured.",
            "file_name": file_name,
            "extracted_text": "",
            "raw_text": ""
        }
        
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        base64_image = base64.b64encode(file_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{base64_image}"
        
        prompt = (
            "You are an expert OCR and data extractor for dental school admissions documents. "
            "Transcribe all text, tables, course prerequisite matrices, DAT/GPA statistics, "
            "and admission requirements from this image verbatim. Format tables as clear markdown tables."
        )
        
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url}
                        }
                    ]
                }
            ],
            max_tokens=3000,
            temperature=0.0
        )
        
        extracted_text = response.choices[0].message.content or ""
        return {
            "success": True,
            "file_name": file_name,
            "extracted_text": extracted_text,
            "raw_text": extracted_text,
            "total_pages": 1
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "file_name": file_name,
            "extracted_text": "",
            "raw_text": ""
        }
