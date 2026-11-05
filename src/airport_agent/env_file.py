"""Read a `.env` file into the environment, so a plain `uvicorn` start finds the model key."""

import os
from collections.abc import MutableMapping
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
EXPORT_PREFIX = "export "
QUOTES = "\"'"


def _parse_line(line: str) -> tuple[str, str] | None:
    text = line.strip().removeprefix(EXPORT_PREFIX).strip()

    if not text or text.startswith("#") or "=" not in text:
        return None

    name, _, value = text.partition("=")
    name = name.strip()

    if not name:
        return None

    return name, value.strip().strip(QUOTES)


def load_env_file(
    path: Path = DEFAULT_ENV_PATH, environ: MutableMapping[str, str] = os.environ
) -> list[str]:
    """Names set from the file. A variable already set wins; values are never logged."""
    if not path.is_file():
        return []

    loaded = []

    for line in path.read_text(encoding="utf-8").splitlines():
        pair = _parse_line(line)

        if pair is None or pair[0] in environ:
            continue

        environ[pair[0]] = pair[1]
        loaded.append(pair[0])

    return loaded
