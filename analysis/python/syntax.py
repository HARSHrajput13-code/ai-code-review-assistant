"""Syntax check with the bounded `ast` parse (CIS §8.2). Source is never compiled or executed."""

import ast
import warnings

from analysis.python import rules
from analysis.python.candidates import candidate, location
from shared.domain.enums import Confidence
from shared.domain.models import SourceText, SyntaxCheck

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
