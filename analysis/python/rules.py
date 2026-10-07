"""The rule catalogue: one table keyed by `rule_key` (CIS §8.3-§8.5).

`title`, `impact` and `recommendation` come from here; `summary` comes from the tool (BS §12).
Bandit severities come from the §8.4 matrix at run time, so Bandit entries carry none.
Coverage per tool (§13.2) and escalability (§12.6) are derived from this table, never hard-coded.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from shared.domain.enums import Category, Severity

C, S, P = Category.CORRECTNESS, Category.SECURITY, Category.PERFORMANCE
R, M, B = Category.READABILITY, Category.MAINTAINABILITY, Category.BEST_PRACTICE
HIGH, MEDIUM, LOW, CRITICAL = Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.CRITICAL

PARSER, PYLINT, BANDIT = "python-parser", "pylint", "bandit"
ESCALABLE_CATEGORIES = frozenset({Category.CORRECTNESS, Category.SECURITY})
GENERIC_BANDIT_CATEGORY = Category.SECURITY


@dataclass(frozen=True)
class RuleEntry:
    rule_key: str
    tool: str
    category: Category
    severity: Severity | None  # None: from the Bandit severity matrix (§8.4)
    title: str
    impact: str
    recommendation: str
    symbol: str | None = None  # the Pylint symbol, as enabled in the rcfile

    @property
    def escalable(self) -> bool:
        return self.category in ESCALABLE_CATEGORIES


_SYNTAX_IMPACT = (
    "The code cannot be imported or run until this is fixed, "
    "and further static analysis is not possible."
)

_PARSER = (
    ("syntax-error", "Syntax error", "Correct the syntax at the indicated line."),
    (
        "unparseable",
        "Code too complex to parse",
        "Reduce deeply nested or very large expressions so that the code can be parsed.",
    ),
)

# (message ID, symbol, category, severity, title, impact, recommendation): CIS §8.3.
_PYLINT = (
    ("E0601", "used-before-assignment", C, HIGH, "Variable used before assignment",
     "The code raises an error at runtime when this line runs before the variable is assigned.",
     "Assign the variable on every path before it is used."),
    ("E0602", "undefined-variable", C, HIGH, "Undefined name",
     "Using a name that is not defined raises a NameError at runtime.",
     "Define or import the name, or correct its spelling."),
    ("E0103", "not-in-loop", C, HIGH, "break or continue outside a loop",
     "The code cannot run, because break and continue are only valid inside a loop.",
     "Move the statement into a loop, or restructure the control flow."),
    ("E0104", "return-outside-function", C, HIGH, "return outside a function",
     "The code cannot run, because return is only valid inside a function.",
     "Move the statement into a function, or remove it."),
    ("E0105", "yield-outside-function", C, HIGH, "yield outside a function",
     "The code cannot run, because yield is only valid inside a function.",
     "Move the statement into a function, or remove it."),
    ("E0702", "raising-bad-type", C, HIGH, "Raising a value that is not an exception",
     "Raising something that is not an exception causes a TypeError instead of the intended "
     "error.",
     "Raise an instance or subclass of BaseException."),
    ("E1120", "no-value-for-parameter", C, HIGH, "Missing argument in call",
     "The call fails with a TypeError because a required argument is missing.",
     "Pass every required argument."),
    ("E1121", "too-many-function-args", C, HIGH, "Too many positional arguments",
     "The call fails with a TypeError because it passes more arguments than the function "
     "accepts.",
     "Remove the extra arguments, or change the function's signature."),
    ("E1123", "unexpected-keyword-arg", C, HIGH, "Unexpected keyword argument",
     "The call fails with a TypeError because the function does not accept this keyword.",
     "Use a parameter name that the function defines."),
    ("E0102", "function-redefined", C, MEDIUM, "Function or class redefined",
     "The later definition silently replaces the earlier one, so the earlier code is never "
     "used.",
     "Rename or remove one of the definitions."),
    ("E0711", "notimplemented-raised", C, MEDIUM, "NotImplemented raised",
     "Raising NotImplemented causes a TypeError instead of signalling an unimplemented method.",
     "Raise NotImplementedError instead."),
    ("E1111", "assignment-from-no-return", C, MEDIUM, "Result of a function that returns nothing",
     "The variable always receives None, which is probably not what was intended.",
     "Return a value from the function, or do not use its result."),
    ("E1305", "too-many-format-args", C, MEDIUM, "Too many arguments for format string",
     "Formatting fails at runtime with a TypeError.",
     "Make the arguments match the placeholders."),
    ("E1306", "too-few-format-args", C, MEDIUM, "Not enough arguments for format string",
     "Formatting fails at runtime with a TypeError.",
     "Make the arguments match the placeholders."),
    ("W0102", "dangerous-default-value", C, MEDIUM, "Mutable default argument",
     "The default object is shared between calls, so changes made in one call leak into later "
     "calls.",
     "Use None as the default and create the object inside the function."),
    ("W0150", "lost-exception", C, MEDIUM, "Exception lost in finally block",
     "A return or break in a finally block silently discards any exception being raised.",
     "Do not return or break inside finally."),
    ("W0631", "undefined-loop-variable", C, MEDIUM, "Loop variable used after the loop",
     "If the loop never runs, the variable is undefined and the code raises an error.",
     "Initialise the variable before the loop, or use it only inside the loop."),
    ("W0640", "cell-var-from-loop", C, MEDIUM, "Closure captures a loop variable",
     "Functions defined in the loop all see the variable's final value, not the value from "
     "their own iteration.",
     "Bind the current value explicitly, for example as a default argument."),
    ("W0104", "pointless-statement", C, LOW, "Statement has no effect",
     "The statement does nothing, which often means a call or assignment is missing.",
     "Remove the statement, or complete the intended operation."),
    ("W0106", "expression-not-assigned", C, LOW, "Expression result not used",
     "The result is discarded, which often hides a mistake such as a comparison written "
     "instead of an assignment.",
     "Assign or use the result, or remove the expression."),
    ("W0702", "bare-except", B, MEDIUM, "Bare except clause",
     "Catching everything, including KeyboardInterrupt and SystemExit, hides real errors and "
     "makes the program hard to stop.",
     "Catch only the exceptions you expect."),
    ("W0718", "broad-exception-caught", B, LOW, "Overly broad exception caught",
     "Catching Exception hides unexpected errors and makes failures harder to diagnose.",
     "Catch only the exceptions you expect to handle."),
    ("W0706", "try-except-raise", B, LOW, "Except clause only re-raises",
     "The handler adds nothing and can hide a more specific handler after it.",
     "Remove the handler, or handle the exception."),
    ("W0707", "raise-missing-from", B, LOW, "Exception re-raised without its cause",
     "The original exception is reported as an error during handling, which makes the "
     "traceback misleading.",
     "Use 'raise ... from err', or 'from None' to hide the cause deliberately."),
    ("W1514", "unspecified-encoding", B, LOW, "File opened without an explicit encoding",
     "The file uses a platform-dependent encoding, so the program behaves differently on "
     "different machines.",
     'Pass an explicit encoding, for example encoding="utf-8".'),
    ("R1732", "consider-using-with", B, LOW, "Resource not managed with 'with'",
     "The resource can stay open if an error occurs before it is closed.",
     "Open the resource in a with statement."),
    ("W0622", "redefined-builtin", B, LOW, "Built-in name redefined",
     "Shadowing a built-in makes the original unavailable in this scope and confuses readers.",
     "Rename the variable."),
    ("C0123", "unidiomatic-typecheck", B, LOW, "type() used for a type check",
     "Comparing type() exactly ignores subclasses.",
     "Use isinstance()."),
    ("R0912", "too-many-branches", M, MEDIUM, "Too many branches",
     "Many branches make the function hard to understand and test.",
     "Split the function, or simplify its conditions."),
    ("R0915", "too-many-statements", M, MEDIUM, "Too many statements",
     "Long functions are hard to understand, test and change.",
     "Split the function into smaller ones."),
    ("R1702", "too-many-nested-blocks", M, MEDIUM, "Too deeply nested",
     "Deep nesting makes the control flow hard to follow.",
     "Use early returns, or move nested blocks into separate functions."),
    ("R0911", "too-many-return-statements", M, LOW, "Too many return statements",
     "Many exit points make the function's behaviour hard to follow.",
     "Simplify the logic, or split the function."),
    ("R0913", "too-many-arguments", M, LOW, "Too many arguments",
     "Long parameter lists are hard to call correctly and suggest that the function does too "
     "much.",
     "Group related parameters, or split the function."),
    ("W0101", "unreachable", M, LOW, "Unreachable code",
     "This code can never run, so it is dead or indicates a logic error.",
     "Remove it, or fix the control flow that skips it."),
    ("W0603", "global-statement", M, LOW, "Use of the global statement",
     "Modifying globals creates hidden dependencies between functions.",
     "Pass values as arguments and return results."),
    ("W0611", "unused-import", M, LOW, "Unused import",
     "Unused imports clutter the code and slow down loading.",
     "Remove the import."),
    ("W0612", "unused-variable", M, LOW, "Unused variable",
     "An unused variable may indicate a mistake or leftover code.",
     "Remove the variable, or use it as intended."),
    ("W0613", "unused-argument", M, LOW, "Unused argument",
     "An unused parameter suggests a mistake or an unnecessary part of the interface.",
     "Use or remove the parameter, or prefix its name with _ if it must stay."),
    ("W0621", "redefined-outer-name", M, LOW, "Name from an outer scope redefined",
     "Reusing an outer name hides it inside the function and invites confusion.",
     "Rename the inner variable."),
    ("C0301", "line-too-long", R, LOW, "Line too long",
     "Long lines are hard to read and review.",
     "Break the line so that it is at most 100 characters."),
    ("C0121", "singleton-comparison", R, LOW, "Comparison to None, True or False with ==",
     "Equality can be overridden and is less clear than an identity or truth test.",
     "Use 'is None' or 'is not None', or a plain truth test."),
    ("C0200", "consider-using-enumerate", R, LOW, "Index-based loop",
     "Looping over range(len(...)) is harder to read than iterating directly.",
     "Use enumerate()."),
    ("C0201", "consider-iterating-dictionary", R, LOW, "Iterating over dict.keys()",
     "Calling .keys() is unnecessary when iterating over a dictionary.",
     "Iterate over the dictionary directly."),
    ("C0206", "consider-using-dict-items", R, LOW, "Dictionary looked up inside a key loop",
     "Looking up each key again is slower and harder to read.",
     "Iterate with .items()."),
    ("C0325", "superfluous-parens", R, LOW, "Unnecessary parentheses",
     "Extra parentheses after a keyword add noise.",
     "Remove the parentheses."),
    ("C1802", "use-implicit-booleaness-not-len", R, LOW, "len() used as a condition",
     "Testing len() is less idiomatic than testing the sequence itself.",
     "Test the sequence directly, for example 'if not items:'."),
    ("R1714", "consider-using-in", R, LOW, "Repeated equality comparisons",
     "Several == comparisons joined by 'or' are harder to read than a membership test.",
     "Use a membership test such as 'x in (a, b)'."),
    ("R1728", "consider-using-generator", P, LOW, "List built only to be consumed",
     "Building a temporary list wastes memory when a generator would do.",
     "Pass a generator expression instead."),
    ("R1729", "use-a-generator", P, LOW, "List comprehension inside any() or all()",
     "A list comprehension evaluates every element, so any() and all() cannot stop early.",
     "Use a generator expression."),
)  # fmt: skip

_EXPOSED_IN_SOURCE = "Secrets in source code are exposed to anyone who can read it."
_LOAD_FROM_CONFIGURATION = "Load secrets from configuration or a secret store."
_SHELL_IMPACT = "Running commands through a shell allows command injection."

# (test ID, category, title, impact, recommendation): the explicit entries of CIS §8.5.
_BANDIT = (
    ("B102", S, "Use of exec", "Executing dynamically built code can run attacker-controlled code.",
     "Avoid exec; use explicit logic or a safe parser."),
    ("B105", S, "Hard-coded password string", _EXPOSED_IN_SOURCE, _LOAD_FROM_CONFIGURATION),
    ("B106", S, "Hard-coded password argument", _EXPOSED_IN_SOURCE, _LOAD_FROM_CONFIGURATION),
    ("B107", S, "Hard-coded password default", _EXPOSED_IN_SOURCE, _LOAD_FROM_CONFIGURATION),
    ("B108", S, "Insecure temporary file or directory",
     "Predictable temporary paths can be read or replaced by other users.",
     "Create temporary files and directories with the tempfile module."),
    ("B110", B, "Exception silently ignored (try/except/pass)",
     "Silently ignoring exceptions hides failures.",
     "Handle or log the exception, and catch only specific exceptions."),
    ("B112", B, "Exception silently skipped (try/except/continue)",
     "Silently skipping on exceptions hides failures.",
     "Handle or log the exception, and catch only specific exceptions."),
    ("B201", S, "Flask debug mode enabled",
     "The Flask debugger allows arbitrary code execution when it is reachable.",
     "Never enable debug mode outside local development."),
    ("B301", S, "Unsafe pickle deserialization",
     "Unpickling untrusted data can execute arbitrary code.",
     "Use a safe format such as JSON for untrusted data."),
    ("B303", S, "Insecure hash function",
     "MD5 and SHA1 can be forged and must not protect security-relevant data.",
     "Use SHA-256 or stronger; for passwords, use a dedicated password hash."),
    ("B304", S, "Insecure cipher",
     "Weak or broken ciphers do not protect the confidentiality of data.",
     "Use a modern authenticated cipher such as AES-GCM."),
    ("B307", S, "Use of eval", "eval can execute arbitrary code from its input.",
     "Use ast.literal_eval for literals, or parse the input explicitly."),
    ("B311", S, "Non-cryptographic random generator",
     "The random module is predictable and unsuitable for security purposes.",
     "Use the secrets module for tokens and keys."),
    ("B324", S, "Weak hash algorithm in hashlib",
     "MD4, MD5 and SHA1 are broken for security purposes.",
     "Use SHA-256 or stronger, or pass usedforsecurity=False for non-security uses."),
    ("B501", S, "TLS certificate verification disabled",
     "Without certificate verification, connections can be intercepted.",
     "Keep certificate verification enabled."),
    ("B506", S, "Unsafe YAML load",
     "yaml.load with an unsafe loader can construct arbitrary objects from its input.",
     "Use yaml.safe_load."),
    ("B602", S, "Subprocess call with shell=True", _SHELL_IMPACT,
     "Pass an argument list with shell=False."),
    ("B605", S, "Process started with a shell", _SHELL_IMPACT,
     "Use subprocess with an argument list and shell=False."),
    ("B608", S, "SQL built from strings", "Building SQL from strings allows SQL injection.",
     "Use parameterized queries."),
    ("B701", S, "Jinja2 autoescape disabled",
     "Without autoescaping, rendered templates are vulnerable to cross-site scripting.",
     "Enable autoescaping, for example with select_autoescape()."),
)  # fmt: skip


def _entries() -> Iterable[RuleEntry]:
    for name, title, recommendation in _PARSER:
        yield RuleEntry(
            f"{PARSER}:{name}", PARSER, C, CRITICAL, title, _SYNTAX_IMPACT, recommendation
        )
    for msg_id, symbol, category, severity, title, impact, recommendation in _PYLINT:
        yield RuleEntry(
            f"{PYLINT}:{msg_id}", PYLINT, category, severity, title, impact, recommendation, symbol
        )
    for test_id, category, title, impact, recommendation in _BANDIT:
        yield RuleEntry(
            f"{BANDIT}:{test_id}", BANDIT, category, None, title, impact, recommendation
        )


def build_catalogue(entries: Iterable[RuleEntry]) -> Mapping[str, RuleEntry]:
    table: dict[str, RuleEntry] = {}
    for entry in entries:
        if entry.rule_key in table:
            raise ValueError(f"duplicate rule_key {entry.rule_key}")
        table[entry.rule_key] = entry
    return MappingProxyType(table)


CATALOGUE = build_catalogue(_entries())


def lookup(rule_key: str) -> RuleEntry | None:
    return CATALOGUE.get(rule_key)


def bandit_entry(test_id: str, test_name: str, cwe_id: int | None) -> RuleEntry:
    """The explicit entry, or the generic texts for any other Bandit test (§8.5)."""
    explicit = CATALOGUE.get(f"{BANDIT}:{test_id}")
    if explicit is not None:
        return explicit
    weakness = f" (CWE-{cwe_id})" if cwe_id is not None else ""
    return RuleEntry(
        rule_key=f"{BANDIT}:{test_id}",
        tool=BANDIT,
        category=GENERIC_BANDIT_CATEGORY,
        severity=None,
        title=test_name.replace("_", " ").strip().capitalize() or test_id,
        impact=f"This pattern is commonly associated with a security weakness{weakness}.",
        recommendation="Review this usage and replace it with a safe alternative.",
    )


def covered_categories(tool: str) -> tuple[Category, ...]:
    """Categories a tool covers, in canonical order (§8.5, §13.2)."""
    covered = {e.category for e in CATALOGUE.values() if e.tool == tool}
    if tool == BANDIT:
        covered.add(GENERIC_BANDIT_CATEGORY)
    return tuple(c for c in Category if c in covered)


def is_escalable(rule_key: str) -> bool:
    """A rule is escalable iff its category is CORRECTNESS or SECURITY (§8.5, §12.6)."""
    entry = lookup(rule_key)
    if entry is None and rule_key.startswith(f"{BANDIT}:"):
        return GENERIC_BANDIT_CATEGORY in ESCALABLE_CATEGORIES
    return entry is not None and entry.escalable


def enabled_pylint_symbols() -> tuple[str, ...]:
    return tuple(e.symbol for e in CATALOGUE.values() if e.tool == PYLINT and e.symbol)
