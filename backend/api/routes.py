"""The /api/v1 endpoints (CIS §6.2). Each handler delegates to an application service and maps the
result; no business branching happens here.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from backend.api.dtos import (
    Capabilities,
    ComponentDTO,
    ComponentsDTO,
    ErrorBody,
    Health,
    LanguageDTO,
    LimitsDTO,
    Readiness,
    ReviewCreateRequest,
    ReviewResource,
)
from backend.api.mapping import review_resource
from backend.api.middleware import IDEMPOTENCY_KEY_PATTERN, REVIEWS_PATH
from backend.application.job_service import ReviewJobService
from backend.application.readiness import ReadinessService
from shared.domain.enums import Language

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiContext:
    """What the endpoints need, built once by the composition root."""

    jobs: ReviewJobService
    readiness: ReadinessService
    languages: tuple[tuple[Language, str], ...]  # (id, display name)
    max_source_bytes: int
    max_source_lines: int
    review_timeout_seconds: int
    improvement_enabled: bool
    version: str
    close: Callable[[], Awaitable[None]] | None = None  # releases the shared HTTP client


def context(request: Request) -> ApiContext:
    found: ApiContext = request.app.state.context
    return found


Context = Annotated[ApiContext, Depends(context)]


def _errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Every non-2xx response is an ErrorBody (§6.6); `default` replaces FastAPI's own 422."""
    documented: dict[int | str, dict[str, Any]] = {s: {"model": ErrorBody} for s in statuses}
    return {**documented, "default": {"model": ErrorBody, "description": "Error"}}


router = APIRouter(prefix="/api/v1")


@router.post(
    "/reviews",
    status_code=202,
    responses={200: {"model": ReviewResource}, **_errors(400, 413, 415, 422, 429, 500)},
)
async def create_review(
    body: ReviewCreateRequest,
    response: Response,
    ctx: Context,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_KEY_PATTERN)
    ] = None,
) -> ReviewResource:
    """Submit code: 202 creates a review; 200 replays the review for a known key (§6.3)."""
    review, created = await ctx.jobs.submit(body.language, body.source_code, idempotency_key)
    response.status_code = 202 if created else 200
    response.headers["Location"] = f"{REVIEWS_PATH}/{review.review_id}"
    logger.info(
        "review.accepted", extra={"review_id": str(review.review_id), "replayed": not created}
    )
    return review_resource(review)


@router.get("/reviews/{review_id}", responses=_errors(404, 500))
async def get_review(review_id: UUID, ctx: Context) -> ReviewResource:
    """The current state of a review (§6.4)."""
    return review_resource(await ctx.jobs.get(review_id))


@router.get("/capabilities", responses=_errors(500))
async def capabilities(ctx: Context) -> Capabilities:
    """Languages and input limits, for client-side validation (D-31)."""
    return Capabilities(
        languages=[
            # The Monaco language ID equals the language ID for the V1 language (python).
            LanguageDTO(id=language, display_name=name, monaco_language=language.value)
            for language, name in ctx.languages
        ],
        limits=LimitsDTO(
            max_source_bytes=ctx.max_source_bytes, max_source_lines=ctx.max_source_lines
        ),
        review_timeout_seconds=ctx.review_timeout_seconds,
        improvement_enabled=ctx.improvement_enabled,
    )


@router.get("/health", responses=_errors(500))
async def health(ctx: Context) -> Health:
    """Liveness only: no dependency is checked."""
    return Health(status="ok", version=ctx.version)


@router.get("/health/ready", responses={503: {"model": Readiness}, **_errors(500)})
async def ready(response: Response, ctx: Context) -> Readiness:
    """Readiness of the AI provider, the static tools and persistence (D-54)."""
    found = await ctx.readiness.check()
    response.status_code = 200 if found.ready else 503
    return Readiness(
        status="ready" if found.ready else "degraded",
        components=ComponentsDTO(
            ai_provider=ComponentDTO.model_validate(found.ai_provider),
            static_tools=ComponentDTO.model_validate(found.static_tools),
            persistence=ComponentDTO.model_validate(found.persistence),
        ),
    )
