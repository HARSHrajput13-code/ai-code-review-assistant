"""Source snippets submitted to the pinned tools in the contract tests."""

CLEAN = "def add(a, b):\n    return a + b\n"
SYNTAX_INVALID = "def broken(:\n    return 1\n"

# Recorded in tests/fixtures/pylint/json2_findings.json.
PYLINT_FINDINGS = (
    "import os\n\n\ndef collect(item, items=[]):\n    items.append(item)\n    return items\n"
)

# Recorded in tests/fixtures/bandit/json_findings.json.
BANDIT_FINDINGS = (
    "import subprocess\n\n\ndef run(command):\n    return subprocess.call(command, shell=True)\n"
)
