"""The API layer stays transport-only (CIS §3, §3.1, §6.5): separate DTOs, no review logic."""

import inspect

from fastapi.routing import APIRoute
from pydantic import BaseModel

from backend.api import dtos
from backend.api.routes import router
from shared.domain.models import DomainModel
from tests.architecture.modules import production_modules
from tests.architecture.test_import_layering import imports, under

# What a route handler must never reach for: analyzers, AI infrastructure, review algorithms,
# idempotency internals, processes and HTTP clients (§3).
FORBIDDEN_FOR_API = (
    "analysis",
    "ai",
    "backend.review",
    "backend.composition",
    "backend.config",
    "backend.application.orchestrator",
    "backend.application.job_store",
    "subprocess",
    "hashlib",
    "httpx",
)


def api_modules() -> list[str]:
    return [m.name for m in production_modules() if under(m.name, "backend.api")]


def test_the_api_package_exists_and_is_scanned() -> None:
    assert {"backend.api.routes", "backend.api.dtos", "backend.api.middleware"} <= set(
        api_modules()
    )


def test_the_api_imports_no_analysis_ai_or_review_logic() -> None:
    for module in production_modules():
        if under(module.name, "backend.api"):
            reached = [
                name for name in imports(module) for f in FORBIDDEN_FOR_API if under(name, f)
            ]
            assert reached == [], module.name


def test_dtos_are_separate_from_the_domain_models() -> None:
    for _, model in inspect.getmembers(dtos, inspect.isclass):
        if issubclass(model, BaseModel) and model.__module__ == dtos.__name__:
            assert not issubclass(model, DomainModel), model.__name__


def test_every_route_responds_with_api_dtos_only() -> None:
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 5
    for route in routes:
        models = [route.response_model] + [r["model"] for r in route.responses.values()]
        for model in models:
            assert model.__module__ == dtos.__name__, (route.path, model)
