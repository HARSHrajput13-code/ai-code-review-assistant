"""Forbidden calls and imports in production code (CIS §3, §8.6, §16.1, §18)."""

import ast

import pytest

from tests.architecture.modules import Module, production_modules

PROCESS_MODULE = "analysis.process"  # the only subprocess call site (§8.6)
CONFIG_MODULE = "backend.config"  # the only reader of the environment (§16.1)

FORBIDDEN_BUILTINS = {"exec", "eval", "compile", "__import__"}
FORBIDDEN_IMPORTS = {"runpy", "pickle", "marshal"}
FORBIDDEN_CALLS = {
    "builtins.exec",
    "builtins.eval",
    "builtins.compile",
    "builtins.__import__",
    "importlib.import_module",
    "importlib.__import__",
    "os.system",
    "os.popen",
    "os.startfile",
}
FORBIDDEN_CALL_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn")
SUBPROCESS_CALLS = {"asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
ENVIRONMENT = {"os.environ", "os.environb", "os.getenv", "os.getenvb", "os.putenv", "os.unsetenv"}


def aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to the dotted names they were imported as."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def dotted(node: ast.expr, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = dotted(node.value, names)
        return f"{base}.{node.attr}" if base else None
    return None


def imported(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level or not node.module:
        return []
    return [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]


def violations(module: Module) -> list[str]:
    names = aliases(module.tree)
    problems: list[str] = []
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for name in imported(node):
                top = name.split(".")[0]
                if top in FORBIDDEN_IMPORTS:
                    problems.append(f"imports {name}")
                if top == "subprocess" and module.name != PROCESS_MODULE:
                    problems.append(f"imports {name} outside {PROCESS_MODULE}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
                if node.func.id not in names:  # a builtin, not an imported name
                    problems.append(f"calls {node.func.id}")
            target = dotted(node.func, names) or ""
            if target in FORBIDDEN_CALLS or target.startswith(FORBIDDEN_CALL_PREFIXES):
                problems.append(f"calls {target}")
            if target in SUBPROCESS_CALLS and module.name != PROCESS_MODULE:
                problems.append(f"calls {target} outside {PROCESS_MODULE}")
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    problems.append("passes shell= other than False")
        elif isinstance(node, ast.Attribute | ast.Name) and module.name != CONFIG_MODULE:
            if dotted(node, names) in ENVIRONMENT:
                problems.append(f"reads the environment outside {CONFIG_MODULE}")
    return problems


def synthetic(name: str, source: str) -> Module:
    return Module(name, False, ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "exec(code)",
        "eval(text)",
        "compile(text, '<s>', 'exec')",
        "__import__(name)",
        "import builtins\nbuiltins.eval(text)",
        "import importlib\nimportlib.import_module(name)",
        "from importlib import import_module\nimport_module(name)",
        "import runpy",
        "import pickle",
        "from pickle import loads",
        "import marshal",
        "import os\nos.system(cmd)",
        "from os import system\nsystem(cmd)",
        "import os as o\no.popen(cmd)",
        "import os\nos.execv(path, args)",
        "import os\nos.spawnl(mode, path)",
        "import os\nos.posix_spawn(path, argv, env)",
        "run(argv, shell=True)",
        "run(argv, shell=flag)",
        "import subprocess",
        "from subprocess import run",
        "import asyncio\nasyncio.create_subprocess_shell(cmd)",
        "import asyncio\nasyncio.create_subprocess_exec(*argv)",
        "import os\nos.environ['KEY']",
        "import os\nos.getenv('KEY')",
        "from os import environ\nenviron.get('KEY')",
        "from os import getenv\ngetenv('KEY')",
    ],
)
def test_forbidden_pattern_is_detected(source: str) -> None:
    assert violations(synthetic("backend.application.service", source))


@pytest.mark.parametrize(
    "source",
    [
        "import re\nre.compile(pattern)",
        "import ast\nast.literal_eval(text)",
        "from importlib.metadata import version\nversion('pylint')",
        "run(argv, shell=False)",
        "evaluate(x)",
        "import os\nos.path.join(a, b)",
    ],
)
def test_allowed_pattern_is_accepted(source: str) -> None:
    assert violations(synthetic("backend.application.service", source)) == []


def test_process_module_may_use_subprocess() -> None:
    source = "import subprocess\nsubprocess.run(argv, shell=False)"
    assert violations(synthetic(PROCESS_MODULE, source)) == []


def test_config_module_may_read_the_environment() -> None:
    assert violations(synthetic(CONFIG_MODULE, "import os\nos.environ.get('APP_ENV')")) == []


def test_production_modules_contain_no_forbidden_calls() -> None:
    modules = list(production_modules())
    assert modules
    problems = [f"{m.name}: {p}" for m in modules for p in violations(m)]
    assert problems == []
