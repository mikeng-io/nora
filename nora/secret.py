"""Private boot-time credential resolution. See docs/configuration.md."""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path


class SecretError(ValueError):
    pass


def read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text())
        values = data.get("credentials", {})
        if set(data) - {"credentials"} or not isinstance(values, dict):
            raise ValueError
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or not isinstance(v, str)
            for k, v in values.items()
        ):
            raise ValueError
        return values
    except (ValueError, OSError):
        raise SecretError("invalid private credential file; values withheld") from None


def resolve(path: Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    return {**read(path), **(os.environ if environ is None else environ)}


def require(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "")
    if not value:
        raise SecretError(f"required credential {name} is missing or empty")
    return value


def write(path: Path, name: str, value: str) -> None:
    """Atomically replace a bootstrap, preserving other credentials."""
    values = read(path)
    values[name] = value
    body = "[credentials]\n" + "".join(
        f"{k} = {json.dumps(v, ensure_ascii=False)}\n" for k, v in sorted(values.items())
    )
    fd, temporary = tempfile.mkstemp(prefix=".secret-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def credentials() -> dict[str, str]:
    """Host tooling using the current runtime directory."""
    return resolve(Path("secret.toml"))
