"""The FastAPI application factory (CIS §6, §21.3). Run with `uvicorn backend.main:app`.

`app` is created on first access, so importing this module reads no configuration.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import errors
from backend.api.middleware import RequestGuard
from backend.api.routes import ApiContext, router
from backend.composition import build_context
from backend.config import AppEnv, Settings, load_settings
from backend.logging_setup import configure_logging, request_context
from shared.domain.errors import RequestRejected

logger = logging.getLogger(__name__)

REQUEST_ENVELOPE_BYTES = 16_384  # MAX_REQUEST_BYTES = 2 × MAX_SOURCE_BYTES + 16384 (§6.1)


def create_app(settings: Settings | None = None, context: ApiContext | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings)
    built = context or build_context(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        readiness = await built.readiness.check()  # probed once; Ollama may start later (§9.5)
        if not readiness.ready:
            logger.warning("startup.readiness_degraded")
        yield
        await built.jobs.shutdown()

    docs = settings.app_env is not AppEnv.PRODUCTION
    app = FastAPI(
        title="AI Code Review Assistant",
        version=built.version,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=lifespan,
    )
    app.state.context = built
    app.include_router(router)
    app.add_exception_handler(RequestRejected, errors.rejected)
    app.add_exception_handler(RequestValidationError, errors.invalid)
    app.add_exception_handler(StarletteHTTPException, errors.http_error)
    app.add_middleware(
        RequestGuard,
        max_request_bytes=2 * settings.max_source_bytes + REQUEST_ENVELOPE_BYTES,
        max_source_bytes=settings.max_source_bytes,
        max_source_lines=settings.max_source_lines,
        request_context=request_context,
    )
    return app


def __getattr__(name: str) -> FastAPI:
    if name == "app":
        return create_app()
    raise AttributeError(name)
