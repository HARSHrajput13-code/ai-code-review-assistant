"""Static-analysis boundaries (CIS §3, §5.0, §8.6, §20.3)."""

import inspect
import typing

import pytest
from pydantic import BaseModel

from shared.domain import interfaces, models, review
from tests.architecture.modules import Module, production_modules
from tests.architecture.test_import_layering import imports, synthetic, under

# Tool-native parsing and the rule catalogue stay inside analysis/.
ANALYSIS_INTERNALS = (
    "analysis.process",
    "analysis.python.pylint_tool",
    "analysis.python.bandit_tool",
    "analysis.python.candidates",
    "analysis.python.rules",
)
# The composition root wires the runner and adapter (PR-04); nothing else outside analysis may.
WIRING = "backend.composition"


def leaks(module: Module) -> list[str]:
    if under(module.name, "analysis") or under(module.name, WIRING):
        return []
    return [
        f"{module.name} imports {name}"
        for name in sorted(imports(module))
        if any(under(name, internal) for internal in ANALYSIS_INTERNALS)
    ]


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("backend.api.routes", "from analysis.python import pylint_tool"),
        ("backend.application.orchestrator", "from analysis.process import SafeProcessRunner"),
        ("backend.review.scoring", "from analysis.python.rules import CATALOGUE"),
        ("ai.validation", "import analysis.python.bandit_tool"),
    ],
)
def test_reaching_into_analysis_internals_is_detected(name: str, source: str) -> None:
    assert leaks(synthetic(name, source))


def test_analysis_internals_are_not_imported_outside_analysis() -> None:
    assert [problem for module in production_modules() for problem in leaks(module)] == []


def collection_origins(annotation: object) -> set[object]:
    found = {typing.get_origin(annotation) or annotation}
    for argument in typing.get_args(annotation):
        found |= collection_origins(argument)
    return found


DOMAIN_MODELS = [
    model
    for module in (models, review, interfaces)
    for _, model in inspect.getmembers(module, inspect.isclass)
    if issubclass(model, BaseModel) and model.__module__ == module.__name__
]


@pytest.mark.parametrize("model", DOMAIN_MODELS, ids=lambda m: m.__name__)
def test_domain_models_hold_no_mutable_collections(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert not collection_origins(field.annotation) & {dict, list, set}, (
            f"{model.__name__}.{name}"
        )


def test_domain_models_are_frozen_and_closed() -> None:
    assert len(DOMAIN_MODELS) >= 30
    for model in DOMAIN_MODELS:
        assert model.model_config.get("frozen") is True, model.__name__
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_shared_domain_imports_no_tool_or_infrastructure_library() -> None:
    for module in production_modules():
        if under(module.name, "shared"):
            tops = {name.split(".")[0] for name in imports(module)}
            assert tops.isdisjoint({"pylint", "bandit", "subprocess", "httpx", "fastapi"}), (
                module.name
            )
