import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents.normalization_service import NormalizationService
from app.agents.scoring_agent import ScoringService
from app.api.internal_auth import InternalAuthMiddleware
from app.api.routes_jobs import build_jobs_router
from app.api.routes_normalization import build_normalization_router
from app.api.routes_rubrics import build_rubric_router
from app.api.routes_schools import build_school_router
from app.clients.queue_client import QueueClient
from app.clients.storage_client import StorageClient
from app.clients.usage_guard import get_usage_guard
from app.config import Settings, get_settings
from app.db.session import ConfigurationMissing, Database
from app.documents import DocumentService
from app.factor_taxonomy import TaxonomyNotConfigured
from app.jobs import JobQueryService
from app.research import ResearchService
from app.rubrics import RubricService


class HealthReport(BaseModel):
    status: str
    checks: dict[str, bool]
    api_keys: dict[str, bool]
    errors: dict[str, str]


def create_app(settings: Settings | None = None, *, database=None, queue=None, storage=None,
               documents=None, research=None, normalization=None, rubrics=None, scoring=None,
               jobs=None) -> FastAPI:
    settings = settings or get_settings()
    database = database if database is not None else Database(settings)
    queue = queue if queue is not None else QueueClient(settings, database)
    storage = storage if storage is not None else StorageClient(settings)
    get_usage_guard(settings, database)

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=settings.log_level)
        yield
        closers = [database.close(), storage.close()]
        for name in ("research", "rubrics", "scoring"):
            service = getattr(app.state, name, None)
            if service is not None and hasattr(service, "close"):
                closers.append(service.close())
        await asyncio.gather(*closers)

    app = FastAPI(
        title="School AI Service",
        version="0.9.0",
        lifespan=lifespan,
        description=(
            "Internal AI school research and rubric engine for the Dental CRM. "
            "Protected by X-School-AI-Key when INTERNAL_API_SECRET is set. "
            "Job completion uses client-side polling of GET /jobs/{id}. "
            "Failed jobs are listed via GET /jobs?status=failed."
        ),
    )
    app.add_middleware(InternalAuthMiddleware, settings=settings)
    app.state.settings = settings
    app.state.database = database
    app.state.queue = queue
    app.state.storage = storage
    app.state.jobs = jobs if jobs is not None else JobQueryService(database)
    if documents is not None:
        app.state.documents = documents
    else:
        try:
            app.state.documents = DocumentService(settings, database, storage)
        except TaxonomyNotConfigured:
            app.state.documents = None
    if research is not None:
        app.state.research = research
    else:
        try:
            app.state.research = ResearchService(settings, database)
        except TaxonomyNotConfigured:
            app.state.research = None
    if normalization is not None:
        app.state.normalization = normalization
    else:
        try:
            app.state.normalization = NormalizationService(settings, database)
        except TaxonomyNotConfigured:
            app.state.normalization = None
    if rubrics is not None:
        app.state.rubrics = rubrics
    else:
        try:
            app.state.rubrics = RubricService(settings, database)
        except TaxonomyNotConfigured:
            app.state.rubrics = None
    if scoring is not None:
        app.state.scoring = scoring
    elif app.state.rubrics is not None:
        try:
            app.state.scoring = ScoringService(settings, database, app.state.rubrics)
        except TaxonomyNotConfigured:
            app.state.scoring = None
    else:
        app.state.scoring = None

    @app.get("/health", response_model=HealthReport, responses={503: {"model": HealthReport}},
             summary="Check hosted infrastructure and API key presence",
             description="Checks Supabase PostgreSQL, pgmq queue, and native private Storage bucket access. OpenAI and Tavily keys are checked for presence only, without provider calls.")
    async def health():
        async def check(name, function):
            try:
                await asyncio.wait_for(function(), timeout=settings.health_timeout_seconds)
                return name, True, None
            except ConfigurationMissing:
                return name, False, "not_configured"
            except TimeoutError:
                return name, False, "timeout"
            except Exception:
                # Never expose connection strings, SDK exception text, or credentials.
                return name, False, "unavailable"

        results = await asyncio.gather(check("database", database.health), check("queue", queue.health), check("storage", storage.health))
        checks = {name: ok for name, ok, _ in results}
        api_keys = {"openai": bool(settings.openai_api_key.get_secret_value()),
                    "tavily": bool(settings.tavily_api_key.get_secret_value())}
        ready = all(checks.values()) and all(api_keys.values())
        report = HealthReport(status="ok" if ready else "degraded", checks=checks, api_keys=api_keys,
                              errors={name: error for name, _, error in results if error})
        return JSONResponse(report.model_dump(), status_code=200 if ready else 503)

    app.include_router(build_school_router())
    app.include_router(build_normalization_router())
    app.include_router(build_rubric_router())
    app.include_router(build_jobs_router())
    return app


app = create_app()
