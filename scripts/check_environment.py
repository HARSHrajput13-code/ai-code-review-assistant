"""Verify the pinned environment, or write the environment record (CIS §20.10, §21.3, §21.4).

    uv run python scripts/check_environment.py            # verify; exit 1 on any mismatch
    uv run python scripts/check_environment.py --record   # write the record

The record is docs/evaluation/environment-record.json.

The pins come from .python-version, frontend/.nvmrc and pyproject.toml. npm, uv and Ollama are
pinned by the record itself. Ollama is a separate service: when it is not reachable, that is
reported as an unavailable environment capability, not as a failure. This script never pulls,
selects or freezes a model. No serial numbers, device IDs or product IDs are collected.
"""

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "docs" / "evaluation" / "environment-record.json"
OLLAMA_VERSION_URL = "http://127.0.0.1:11434/api/version"
PINNED = ("python", "node", "pylint", "bandit")  # exact project baselines
RECORDED = ("npm", "uv", "ollama")  # pinned by the environment record
OPTIONAL = ("ollama",)  # a separate local service (CIS §21.3)


@dataclass(frozen=True)
class Check:
    name: str
    expected: str | None
    actual: str | None
    status: str  # ok | mismatch | missing | unavailable | unrecorded

    @property
    def failed(self) -> bool:
        return self.status in ("mismatch", "missing")


def pinned_versions() -> dict[str, str]:
    pins = {
        "python": (ROOT / ".python-version").read_text(encoding="utf-8").strip(),
        "node": (ROOT / "frontend" / ".nvmrc").read_text(encoding="utf-8").strip(),
    }
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for requirement in pyproject["project"]["dependencies"]:
        name, _, version = requirement.partition("==")
        if name in ("pylint", "bandit"):
            pins[name] = version
    return pins


def run(*argv: str) -> str | None:
    executable = shutil.which(argv[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, *argv[1:]], capture_output=True, text=True, timeout=60, check=True
        )
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout.strip() or None


def ollama_version() -> str | None:
    try:
        # Fixed loopback URL; no user input reaches it.
        with urllib.request.urlopen(OLLAMA_VERSION_URL, timeout=3) as response:  # noqa: S310
            version = json.load(response).get("version")
    except OSError, ValueError:
        return None
    return version if isinstance(version, str) else None


def installed_versions() -> dict[str, str | None]:
    node = run("node", "--version")
    uv = run("uv", "--version")  # "uv 0.12.23 (46b84fd0b 2026-10-03 ...)"
    return {
        "python": platform.python_version(),
        "node": node.removeprefix("v") if node else None,
        "npm": run("npm", "--version"),
        "uv": uv.split()[1] if uv else None,
        "ollama": ollama_version(),
        "pylint": metadata.version("pylint"),
        "bandit": metadata.version("bandit"),
    }


def compare(name: str, expected: str | None, actual: str | None) -> Check:
    if expected is None:
        status = "unavailable" if actual is None else "unrecorded"
    elif actual is None:
        status = "unavailable" if name in OPTIONAL else "missing"
    else:
        status = "ok" if actual == expected else "mismatch"
    return Check(name, expected, actual, status)


def evaluate(
    pins: dict[str, str], installed: dict[str, str | None], record: dict[str, Any] | None
) -> list[Check]:
    checks = [compare(name, pins[name], installed[name]) for name in PINNED]
    recorded = record["versions"] if record else {}
    for name in RECORDED:
        if record is None and name not in OPTIONAL:
            status = "missing" if installed[name] is None else "unrecorded"
            checks.append(Check(name, None, installed[name], status))
        else:
            checks.append(compare(name, recorded.get(name), installed[name]))
    return checks


def windows_hardware() -> dict[str, Any]:
    script = (
        "$p = Get-CimInstance Win32_Processor | Select-Object -First 1;"
        "$c = Get-CimInstance Win32_ComputerSystem;"
        "$o = Get-CimInstance Win32_OperatingSystem;"
        "$g = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name });"
        "@{ cpu = $p.Name.Trim(); ram = [string]$c.TotalPhysicalMemory;"
        " os = $o.Caption; build = $o.BuildNumber; gpus = $g } | ConvertTo-Json -Compress"
    )
    raw = run("powershell", "-NoProfile", "-NonInteractive", "-Command", script)
    facts = json.loads(raw) if raw else {}
    vram = {}  # NVIDIA reports dedicated memory exactly; other adapters are recorded by name only
    for line in (
        run("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits") or ""
    ).splitlines():
        name, _, mib = line.rpartition(",")
        vram[name.strip()] = int(mib)
    return {
        "hardware": {
            "cpu": facts.get("cpu"),
            "ram_bytes": int(facts["ram"]) if facts.get("ram") else None,
            "gpus": [{"name": n, "vram_mib": vram.get(n)} for n in facts.get("gpus", [])],
        },
        "os": {
            "edition": facts.get("os"),
            "build": facts.get("build"),
            "architecture": platform.machine(),
        },
    }


def other_hardware() -> dict[str, Any]:
    return {
        "hardware": {"cpu": platform.processor() or None, "ram_bytes": None, "gpus": []},
        "os": {
            "edition": platform.platform(),
            "build": platform.version(),
            "architecture": platform.machine(),
        },
    }


def build_record(installed: dict[str, str | None]) -> dict[str, Any]:
    machine = windows_hardware() if sys.platform == "win32" else other_hardware()
    return {
        **machine,
        "versions": installed,
        "git_commit": run("git", "-C", str(ROOT), "rev-parse", "HEAD"),
    }


def load_record() -> dict[str, Any] | None:
    if not RECORD.exists():
        return None
    record: dict[str, Any] = json.loads(RECORD.read_text(encoding="utf-8"))
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="write the environment record")
    args = parser.parse_args(argv)

    installed = installed_versions()
    checks = evaluate(pinned_versions(), installed, None if args.record else load_record())
    for c in checks:
        print(f"{c.status.upper():<12} {c.name:<8} expected={c.expected} actual={c.actual}")
    if any(c.name == "ollama" and c.actual is None for c in checks):
        print("note: Ollama is not reachable: an environment capability, not an M0 failure.")
        print("      Its exact version MUST be recorded before PR-06 (CIS section 22.1).")

    failed = [c for c in checks if c.failed]
    if failed:
        print(f"FAILED: {', '.join(c.name for c in failed)}")
        return 1
    if args.record:
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps(build_record(installed), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {RECORD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
