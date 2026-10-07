"""The rule sets fixed by CIS §8.3-§8.5, as test data.

The production rule catalogue (`analysis/python/rules.py`) is PR-02 work. These tables are
what the M0 contract tests verify against the pinned tools, and what that catalogue must cover.
"""

# CIS §8.3: (message ID, symbol, category) for every enabled Pylint message.
PYLINT_RULES: tuple[tuple[str, str, str], ...] = (
    ("E0601", "used-before-assignment", "CORRECTNESS"),
    ("E0602", "undefined-variable", "CORRECTNESS"),
    ("E0103", "not-in-loop", "CORRECTNESS"),
    ("E0104", "return-outside-function", "CORRECTNESS"),
    ("E0105", "yield-outside-function", "CORRECTNESS"),
    ("E0702", "raising-bad-type", "CORRECTNESS"),
    ("E1120", "no-value-for-parameter", "CORRECTNESS"),
    ("E1121", "too-many-function-args", "CORRECTNESS"),
    ("E1123", "unexpected-keyword-arg", "CORRECTNESS"),
    ("E0102", "function-redefined", "CORRECTNESS"),
    ("E0711", "notimplemented-raised", "CORRECTNESS"),
    ("E1111", "assignment-from-no-return", "CORRECTNESS"),
    ("E1305", "too-many-format-args", "CORRECTNESS"),
    ("E1306", "too-few-format-args", "CORRECTNESS"),
    ("W0102", "dangerous-default-value", "CORRECTNESS"),
    ("W0150", "lost-exception", "CORRECTNESS"),
    ("W0631", "undefined-loop-variable", "CORRECTNESS"),
    ("W0640", "cell-var-from-loop", "CORRECTNESS"),
    ("W0104", "pointless-statement", "CORRECTNESS"),
    ("W0106", "expression-not-assigned", "CORRECTNESS"),
    ("W0702", "bare-except", "BEST_PRACTICE"),
    ("W0718", "broad-exception-caught", "BEST_PRACTICE"),
    ("W0706", "try-except-raise", "BEST_PRACTICE"),
    ("W0707", "raise-missing-from", "BEST_PRACTICE"),
    ("W1514", "unspecified-encoding", "BEST_PRACTICE"),
    ("R1732", "consider-using-with", "BEST_PRACTICE"),
    ("W0622", "redefined-builtin", "BEST_PRACTICE"),
    ("C0123", "unidiomatic-typecheck", "BEST_PRACTICE"),
    ("R0912", "too-many-branches", "MAINTAINABILITY"),
    ("R0915", "too-many-statements", "MAINTAINABILITY"),
    ("R1702", "too-many-nested-blocks", "MAINTAINABILITY"),
    ("R0911", "too-many-return-statements", "MAINTAINABILITY"),
    ("R0913", "too-many-arguments", "MAINTAINABILITY"),
    ("W0101", "unreachable", "MAINTAINABILITY"),
    ("W0603", "global-statement", "MAINTAINABILITY"),
    ("W0611", "unused-import", "MAINTAINABILITY"),
    ("W0612", "unused-variable", "MAINTAINABILITY"),
    ("W0613", "unused-argument", "MAINTAINABILITY"),
    ("W0621", "redefined-outer-name", "MAINTAINABILITY"),
    ("C0301", "line-too-long", "READABILITY"),
    ("C0121", "singleton-comparison", "READABILITY"),
    ("C0200", "consider-using-enumerate", "READABILITY"),
    ("C0201", "consider-iterating-dictionary", "READABILITY"),
    ("C0206", "consider-using-dict-items", "READABILITY"),
    ("C0325", "superfluous-parens", "READABILITY"),
    ("C1802", "use-implicit-booleaness-not-len", "READABILITY"),
    ("R1714", "consider-using-in", "READABILITY"),
    ("R1728", "consider-using-generator", "PERFORMANCE"),
    ("R1729", "use-a-generator", "PERFORMANCE"),
)

# CIS §8.3: deliberately excluded.
PYLINT_EXCLUDED: frozenset[str] = frozenset({"E1101", "C0103", "C0114", "C0115", "C0116", "R0801"})

# Messages Pylint keeps enabled regardless of the rcfile (CIS §8.7).
PYLINT_SYSTEM_MESSAGES: frozenset[str] = frozenset(
    {
        "E0001",
        "E0011",
        "E0013",
        "E0014",
        "E0015",
        "F0001",
        "F0002",
        "F0010",
        "F0011",
        "R0022",
        "W0012",
    }
)

# Pylint checks that duplicate a Bandit security test. Bandit owns security (CIS §8.1).
PYLINT_SECURITY_OVERLAP: frozenset[str] = frozenset({"W0122", "W0123"})  # exec-used, eval-used

# CIS §8.4.
BANDIT_SKIPS: tuple[str, ...] = ("B101", "B404", "B603")

# CIS §8.5: Bandit tests with explicit catalogue entries.
BANDIT_EXPLICIT: frozenset[str] = frozenset(
    {
        "B102",
        "B105",
        "B106",
        "B107",
        "B108",
        "B110",
        "B112",
        "B201",
        "B301",
        "B303",
        "B304",
        "B307",
        "B311",
        "B324",
        "B501",
        "B506",
        "B602",
        "B605",
        "B608",
        "B701",
    }
)

CATEGORIES: frozenset[str] = frozenset(
    {"CORRECTNESS", "SECURITY", "BEST_PRACTICE", "MAINTAINABILITY", "READABILITY", "PERFORMANCE"}
)

# CIS §8.3: the severity column, by message ID.
PYLINT_SEVERITIES: dict[str, str] = {
    **dict.fromkeys(("E0601", "E0602", "E0103", "E0104", "E0105", "E0702"), "HIGH"),
    **dict.fromkeys(("E1120", "E1121", "E1123"), "HIGH"),
    **dict.fromkeys(("E0102", "E0711", "E1111", "E1305", "E1306"), "MEDIUM"),
    **dict.fromkeys(("W0102", "W0150", "W0631", "W0640", "W0702"), "MEDIUM"),
    **dict.fromkeys(("R0912", "R0915", "R1702"), "MEDIUM"),
    **dict.fromkeys(("W0104", "W0106", "W0718", "W0706", "W0707", "W1514", "R1732"), "LOW"),
    **dict.fromkeys(("W0622", "C0123", "R0911", "R0913", "W0101", "W0603", "W0611"), "LOW"),
    **dict.fromkeys(("W0612", "W0613", "W0621", "C0301", "C0121", "C0200", "C0201"), "LOW"),
    **dict.fromkeys(("C0206", "C0325", "C1802", "R1714", "R1728", "R1729"), "LOW"),
}
