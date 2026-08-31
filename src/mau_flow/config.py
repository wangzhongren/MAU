"""Environment loading without copying secrets into the project."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_environment(project_dir: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load project `.env`, then its optional ``MAU_FLOW_ENV_FILE`` target.

    Existing process variables win unless ``override`` is explicitly enabled.
    Returns the external file path when one was loaded, otherwise the local path.
    """

    root = Path(project_dir or Path.cwd()).resolve()
    local_file = root / ".env"
    local_values = _parse_env(local_file) if local_file.is_file() else {}
    for key, value in local_values.items():
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)

    external_value = os.environ.get("MAU_FLOW_ENV_FILE") or local_values.get("MAU_FLOW_ENV_FILE")
    if not external_value:
        return local_file if local_file.is_file() else None
    external_file = Path(external_value).expanduser().resolve()
    if not external_file.is_file():
        raise FileNotFoundError(f"MAU_FLOW_ENV_FILE does not exist: {external_file}")
    for key, value in _parse_env(external_file).items():
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    return external_file


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str = field(repr=False)
    base_url: str | None
    model: str
    max_output_tokens: int = 32768

    @classmethod
    def from_environment(cls, project_dir: str | Path | None = None) -> OpenAISettings:
        load_environment(project_dir)
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        max_output_tokens = int(os.environ.get("MAX_OUTPUT_TOKENS", "32768"))
        if max_output_tokens < 1:
            raise ValueError("MAX_OUTPUT_TOKENS must be at least 1")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            model=os.environ.get("MODEL_NAME", "gpt-5"),
            max_output_tokens=max_output_tokens,
        )
