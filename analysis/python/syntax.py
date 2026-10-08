"""The bounded `ast` parse (CIS §8.2): syntax check and generated-code validation (§14.3).

Source and generated code are parsed for inspection only; never compiled or executed.
"""

import ast
import warnings

from analysis.python import rules
from analysis.python.candidates import candidate, location
from shared.domain.enums import Confidence
from shared.domain.models import CodeValidation, SourceText, SyntaxCheck

UNPARSEABLE_SUMMARY = "The code is too deeply nested or too complex to be parsed."


def parse_module(text: str) -> ast.Module:
    """The bounded parse, also used for improved-code validation (§14.3).

    Raises SyntaxError (including IndentationError and TabError), RecursionError, MemoryError or
    ValueError. SyntaxWarnings are suppressed so they never reach stderr or the logs.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(text, filename="<submission>", mode="exec")


def check_syntax(source: SourceText) -> SyntaxCheck:
    try:
        parse_module(source.text)
    except SyntaxError as error:
        entry = rules.CATALOGUE[f"{rules.PARSER}:syntax-error"]
        where = location(error.lineno, error.end_lineno, source.line_count)
        line = f" (line {where.start_line})" if where else ""
        summary = f"{error.msg}{line}"
        found = candidate(entry, rules.CRITICAL, Confidence.HIGH, summary, where)
        return SyntaxCheck(valid=False, candidate=found)
    except RecursionError, MemoryError, ValueError:
        entry = rules.CATALOGUE[f"{rules.PARSER}:unparseable"]
        found = candidate(entry, rules.CRITICAL, Confidence.HIGH, UNPARSEABLE_SUMMARY, None)
        return SyntaxCheck(valid=False, candidate=found)
    return SyntaxCheck(valid=True, candidate=None)


# Generated-code validation (§14.3 steps 5-6, D-60). The tree is inspected only; never executed.
DOES_NOT_PARSE = "DOES_NOT_PARSE"
INTERFACE_CHANGED = "INTERFACE_CHANGED"
DECORATOR_FORMS = frozenset({"staticmethod", "classmethod", "property"})

type _Function = ast.FunctionDef | ast.AsyncFunctionDef
type _Definition = _Function | ast.ClassDef
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _members(body: list[ast.stmt], *, methods: bool = False) -> dict[str, _Definition]:
    """Public top-level definitions, or a class's public methods plus `__init__`.

    A later definition replaces an earlier one with the same name, as it does at runtime.
    """
    kinds = _FUNCTIONS if methods else (*_FUNCTIONS, ast.ClassDef)
    return {
        node.name: node
        for node in body
        if isinstance(node, kinds)
        and (not node.name.startswith("_") or (methods and node.name == "__init__"))
    }


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _forms(function: _Function) -> set[str]:
    return {
        name for d in function.decorator_list if (name := _decorator_name(d)) in DECORATOR_FORMS
    }


def _positional(args: ast.arguments) -> list[tuple[str, bool]]:
    """(name, has_default) for positional-only plus positional-or-keyword parameters."""
    params = args.posonlyargs + args.args
    first_default = len(params) - len(args.defaults)
    return [(param.arg, index >= first_default) for index, param in enumerate(params)]


def _signature_kept(old: ast.arguments, new: ast.arguments) -> bool:
    """Rules (c)-(f): positional names and order, keyword-only names, variadics, defaults."""
    before, after = _positional(old), _positional(new)
    if [name for name, _ in after[: len(before)]] != [name for name, _ in before]:
        return False
    if len(new.posonlyargs) < len(old.posonlyargs):
        return False
    if any(had and not has for (_, had), (_, has) in zip(before, after, strict=False)):
        return False
    if not all(has for _, has in after[len(before) :]):
        return False
    old_kw = {a.arg: d is not None for a, d in zip(old.kwonlyargs, old.kw_defaults, strict=True)}
    new_kw = {a.arg: d is not None for a, d in zip(new.kwonlyargs, new.kw_defaults, strict=True)}
    if any(name not in new_kw or (had and not new_kw[name]) for name, had in old_kw.items()):
        return False
    if not all(has for name, has in new_kw.items() if name not in old_kw):
        return False
    return (new.vararg is not None or old.vararg is None) and (
        new.kwarg is not None or old.kwarg is None
    )


def _same(old: _Definition, new: _Definition) -> bool:
    if isinstance(old, ast.ClassDef):
        return isinstance(new, ast.ClassDef) and _kept(
            _members(old.body, methods=True), _members(new.body, methods=True)
        )
    if isinstance(new, ast.ClassDef) or type(old) is not type(new):  # rule (a)
        return False
    return _forms(old) == _forms(new) and _signature_kept(old.args, new.args)  # (b), (c)-(f)


def _kept(old: dict[str, _Definition], new: dict[str, _Definition]) -> bool:
    return all(name in new and _same(node, new[name]) for name, node in old.items())


def validate_generated_code(original: SourceText, generated: str) -> CodeValidation:
    """The bounded parse, then public-interface preservation if the original parsed (§14.3)."""
    try:
        improved = parse_module(generated)
    except SyntaxError, RecursionError, MemoryError, ValueError:
        return CodeValidation(valid=False, reason=DOES_NOT_PARSE)
    try:
        before = parse_module(original.text)
    except SyntaxError, RecursionError, MemoryError, ValueError:
        return CodeValidation(valid=True, reason=None)  # an unparseable original skips the check
    if not _kept(_members(before.body), _members(improved.body)):
        return CodeValidation(valid=False, reason=INTERFACE_CHANGED)
    return CodeValidation(valid=True, reason=None)
