"""Run the pinned tools the way CIS §8.6 specifies, for the M0 contract tests.

This is test support, not the production runner (`analysis/process.py`, PR-02): fixed argv,
an isolated interpreter, source on stdin only, an empty working directory and an
allow-listed environment.
"""

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYLINTRC = ROOT / "analysis" / "python" / "config" / "pylintrc"
BANDIT_CONFIG = ROOT / "analysis" / "python" / "config" / "bandit.yaml"
FIXTURES = ROOT / "tests" / "fixtures"


@dataclass(frozen=True)
class ToolRun:
    exit_code: int
    stdout: str


def run_module(module_args: list[str], source: str = "") -> ToolRun:
    with tempfile.TemporaryDirectory() as tmp:
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if k in os.environ}
        env["PYLINTHOME"] = tmp
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-m", *module_args],
            input=source.encode("utf-8"),
            capture_output=True,
            shell=False,
            check=False,
            timeout=120,
            cwd=tmp,
            env=env,
        )
    # Normalise Windows line endings so the output parses the same on every platform.
    return ToolRun(proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n"))


def pylint(source: str, rcfile: Path = PYLINTRC) -> ToolRun:
    return run_module(
        ["pylint", f"--rcfile={rcfile}", "--output-format=json2", "--from-stdin", "submission.py"],
        source,
    )


def bandit(source: str) -> ToolRun:
    return run_module(
        ["bandit", "-c", str(BANDIT_CONFIG), "-f", "json", "-q", "--ignore-nosec", "-"], source
    )
