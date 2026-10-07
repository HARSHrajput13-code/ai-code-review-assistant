"""Import dependency rules (CIS §3.1), enforced over every production module."""

import ast
import sys

import pytest

from tests.architecture.modules import PACKAGES, Module, production_modules

# Most specific prefix first: a module follows the first rule whose prefix it falls under.
ALLOWED_INTERNAL: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend.composition", PACKAGES),  # wiring only
    ("backend.api", ("backend.api", "backend.application", "shared")),
    ("backend.application", ("backend.application", "backend.review", "shared")),
    ("backend.review", ("backend.review", "shared")),
    ("backend", ("backend", "shared", "analysis", "ai")),
    ("analysis", ("analysis", "shared")),
    ("ai", ("ai", "shared")),
    ("shared", ("shared",)),
)
SHARED_THIRD_PARTY = ("pydantic",)


def under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def imports(module: Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:  # resolve a relative import against the importing module
                parts = module.name.split(".")
                keep = len(parts) - node.level + (1 if module.is_package else 0)
                base = ".".join(parts[:keep] + ([node.module] if node.module else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def violations(module: Module) -> list[str]:
    allowed = next(rule for prefix, rule in ALLOWED_INTERNAL if under(module.name, prefix))
    problems = []
    for name in sorted(imports(module)):
        top = name.split(".")[0]
        if top in PACKAGES:
            if not any(under(name, prefix) for prefix in allowed):
                problems.append(f"{module.name} imports {name}")
        elif under(module.name, "shared") and top not in sys.stdlib_module_names:
            if top not in SHARED_THIRD_PARTY:
                problems.append(f"{module.name} imports third-party {name}")
    return problems


def synthetic(name: str, source: str, is_package: bool = False) -> Module:
    return Module(name, is_package, ast.parse(source))


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("shared.domain.models", "import fastapi"),
        ("shared.domain.models", "import backend.config"),
        ("shared.domain.models", "from analysis import registry"),
        ("analysis.python.adapter", "import ai.schemas"),
        ("analysis.python.adapter", "from backend.config import Settings"),
        ("ai.validation", "from analysis.registry import LanguageRegistry"),
        ("ai.validation", "import backend"),
        ("backend.api.routes", "import analysis"),
        ("backend.api.routes", "from ai import fake"),
        ("backend.api.routes", "from backend.review import scoring"),
        ("backend.api.routes", "from ..review import scoring"),
        ("backend.application.orchestrator", "from ai.ollama import provider"),
        ("backend.application.orchestrator", "from analysis import process"),
        ("backend.application.orchestrator", "from backend.api import routes"),
        ("backend.review.scoring", "from backend.application import service"),
        ("backend.review.scoring", "import analysis.python.rules"),
        ("backend.review", "from ..config import Settings"),
    ],
)
def test_forbidden_import_is_detected(name: str, source: str) -> None:
    assert violations(synthetic(name, source, is_package=name == "backend.review"))


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("shared.domain.models", "from pydantic import BaseModel\nimport enum, typing"),
        ("shared.domain.models", "from __future__ import annotations"),
        ("shared.domain.models", "from shared.domain import errors"),
        ("analysis.python.adapter", "from shared.domain import FindingCandidate"),
        ("ai.validation", "from shared.domain import Issue\nimport httpx"),
        ("backend.api.routes", "from backend.application import ReviewJobService"),
        ("backend.api.routes", "from ..application import service"),
        ("backend.application.orchestrator", "from backend.review import scoring"),
        ("backend.review.scoring", "from shared.domain import Category"),
        ("backend.composition", "from ai.ollama import provider\nfrom analysis import registry"),
        ("backend.main", "from backend.composition import build\nfrom backend.api import router"),
    ],
)
def test_allowed_import_is_accepted(name: str, source: str) -> None:
    assert violations(synthetic(name, source)) == []


def test_every_package_root_is_scanned() -> None:
    assert set(PACKAGES) <= {module.name for module in production_modules()}


def test_production_modules_follow_the_layering() -> None:
    problems = [p for module in production_modules() for p in violations(module)]
    assert problems == []
