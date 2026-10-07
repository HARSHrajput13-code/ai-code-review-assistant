# AI Code Review Assistant

A local-first assistant that reviews Python code by combining static analysis (Pylint, Bandit) with a locally hosted language model (Ollama), and returns a score, explained issues and suggested improvements.

The specifications in [`docs/`](docs/) define the product. The [Concrete Implementation Specification](docs/Concrete%20Implementation%20Specification%20—%20AI%20Code%20Review%20Assistant.md) (CIS) is the implementation contract. [CONTRIBUTING.md](CONTRIBUTING.md) describes the branch, commit and pull-request workflow.

## Current status

**M0 bootstrap is implemented.** This provides:
- the repository layout;
- the exact runtime baselines;
- locked Python and frontend dependencies;
- the frontend scaffold;
- lint, type and test configuration;
- the environment check and record;
- the architecture and static-tool contract tests;
- CI.

**M1/M2/M3/M4 application functionality is not yet implemented.** There is no review API, review pipeline, AI integration or review interface yet. The frontend shows only a placeholder page.

No language model has been selected. The model is chosen in M2 through the controlled evaluation procedure in CIS §20.9; M0 does not select, rank or freeze a model.

## Supported development environment

| Component | Version | Pinned in |
|---|---|---|
| Python | 3.14.8 | `.python-version`, `pyproject.toml` (`>=3.14.8,<3.15`) |
| Node.js | 24.21.0 | `frontend/.nvmrc`, `frontend/package.json` (`>=24.21.0 <25`) |
| Pylint | 4.1.2 | `pyproject.toml`, `uv.lock` |
| Bandit | 1.9.4 | `pyproject.toml`, `uv.lock` |
| uv, npm, Ollama | as recorded | [`docs/evaluation/environment-record.json`](docs/evaluation/environment-record.json) |

The reference machine is Windows 11 x64 (CIS §21.1). macOS and Linux are supported but not characterised. Docker is not required.

Ollama is a separate local service that you install and run yourself. M1 and later can be developed without it by using the fake AI provider.

Dependencies are installed only into project-managed environments (`.venv`, `frontend/node_modules`), never globally.

## Setup

Python (with [uv](https://docs.astral.sh/uv/)):

```sh
uv python install 3.14.8
uv sync --locked
```

Frontend (with Node.js 24.21.0):

```sh
cd frontend
npm ci
npm run dev        # http://127.0.0.1:5173
```

## Checks

Python:

```sh
uv run --locked pytest              # architecture and tool contract tests
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked mypy
```

Frontend (inside `frontend/`):

```sh
npm run test
npm run typecheck
npm run lint
npm run build
```

Environment:

```sh
uv run python scripts/check_environment.py            # verify pinned versions against the record
uv run python scripts/check_environment.py --record   # rewrite the environment record
```

The check fails when a pinned version differs. An unreachable Ollama is reported as an unavailable environment capability, not as a failure. Its exact version must be recorded before PR-06, the first Ollama integration PR (CIS §22.1).
