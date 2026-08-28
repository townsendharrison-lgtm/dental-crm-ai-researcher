import os
import json
from typing import List
from dotenv import load_dotenv

# Load from .env file
load_dotenv()

class Settings:
    def __init__(self):
        self.PORT: int = int(os.getenv("PORT", "8000"))
        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
        self.OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
        self.SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
        self.SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
        self.SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        
        raw_origins = os.getenv("CORS_ORIGINS", "")
        if not raw_origins:
            self.CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000", "*"]
        else:
            try:
                self.CORS_ORIGINS = json.loads(raw_origins)
            except Exception:
                self.CORS_ORIGINS = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

settings = Settings()
