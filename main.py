import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from core.config import settings

# Import routers
from routes.research_router import router as research_router
from routes.comparison_router import router as comparison_router
from routes.predictive_router import router as predictive_router
from routes.calibration_router import router as calibration_router

app = FastAPI(
    title="Dental School Intelligence & Predictive Admission AI Server",
    description="Python LangGraph Agent Server for Dental School Research, Standardized Criteria Extraction, Evidence Verification, and Student Profile vs School Comparison with OpenAI.",
    version="2.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS if settings.CORS_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(research_router)
app.include_router(comparison_router)
app.include_router(predictive_router)
app.include_router(calibration_router)

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "DSG Research Engine & LangGraph Agent Server",
        "version": "2.0.0",
        "model": "OpenAI GPT-4o",
        "openai_configured": bool(settings.OPENAI_API_KEY),
        "docs_url": "/docs"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "supabase_connected": bool(settings.SUPABASE_URL and settings.SUPABASE_ANON_KEY)
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
