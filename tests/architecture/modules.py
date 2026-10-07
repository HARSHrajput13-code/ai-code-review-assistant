"""Discovery of the production Python modules checked by the architecture tests."""

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("shared", "analysis", "ai", "backend")


@dataclass(frozen=True)
class Module:
    name: str  # dotted, e.g. "backend.api.routes"
    is_package: bool  # an __init__.py
    tree: ast.Module


def module_name(path: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def production_modules() -> Iterator[Module]:
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            name, is_package = module_name(path)
            yield Module(name, is_package, ast.parse(path.read_text(encoding="utf-8"), str(path)))
