"""Write the generated contracts (CIS §6.9, §11.4). Never edit the outputs by hand.

    uv run python -m scripts.export_contracts           # write the snapshots
    uv run python -m scripts.export_contracts --check   # exit 1 if a snapshot differs

shared/openapi/openapi.json comes from the live FastAPI app (create_app under test settings);
shared/schemas/*.schema.json come from the Pydantic AI output models (model_json_schema()).
Output is deterministic: sorted keys, 2-space indentation, a trailing newline.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ai.schemas import AIImprovementOutput, AIReviewOutput
from backend.config import AIProviderName, AppEnv, Settings
from backend.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_settings() -> Settings:
    """Fixed settings, so the contract never depends on the local environment or .env."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None, app_env=AppEnv.TEST, ai_provider=AIProviderName.FAKE
    )


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def contracts() -> dict[Path, str]:
    return {
        ROOT / "shared/openapi/openapi.json": render(create_app(test_settings()).openapi()),
        ROOT / "shared/schemas/ai_review_output.schema.json": render(
            AIReviewOutput.model_json_schema()
        ),
        ROOT / "shared/schemas/ai_improvement_output.schema.json": render(
            AIImprovementOutput.model_json_schema()
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    check = parser.parse_args(argv).check
    stale = []
    for path, text in contracts().items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if check:
            if current != text:
                stale.append(path.relative_to(ROOT).as_posix())
        elif current != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    for name in stale:
        print(f"stale contract: {name}", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
