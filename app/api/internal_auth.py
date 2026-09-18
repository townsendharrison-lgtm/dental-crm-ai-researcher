"""Internal shared-secret auth between the Node CRM and this service."""
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class InternalAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)
        expected = self.settings.internal_api_secret.get_secret_value()
        if not expected:
            # Unset secret keeps local/offline tests open; production must set INTERNAL_API_SECRET.
            return await call_next(request)
        provided = request.headers.get("x-school-ai-key") or ""
        if not provided or not secrets.compare_digest(provided, expected):
            return JSONResponse({"detail": "Unauthorized internal client"}, status_code=401)
        return await call_next(request)
