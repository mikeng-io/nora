"""Test-side construction seams for the config tree.

`nora.config`'s tree is pydantic (frozen `BaseModel`s), not dataclasses. Every
direct use of `model_copy` or a `nora.config` constructor in the suite is a site
that would break on a future config change; concentrating them here means that
change edits this file, not ten others. Nothing outside this module may
construct or copy a config object.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from nora.config import (
    Config,
    LimitsConfig,
    LoggingConfig,
    TracingConfig,
)
from nora.config import load as _load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _with(obj: Any, path: str, value: Any) -> Any:
    head, _, rest = path.partition(".")
    if rest:
        value = _with(getattr(obj, head), rest, value)
    return obj.model_copy(update={head: value})


def override_config(base: Any, changes: dict[str, Any]) -> Any:
    """Return a copy of `base` with dotted-path fields replaced.

        override_config(cfg, {"store.data_dir": p, "engagement.trigger.min_interval_seconds": 0})

    Format-agnostic by contract: a future config change only touches `_with`,
    never a caller.
    """
    for path, value in changes.items():
        base = _with(base, path, value)
    return base


def make_limits(**kw: Any) -> LimitsConfig:
    return LimitsConfig(**{"turns": 4, "total_tokens": 64000, "output_tokens": 12000, **kw})



def make_logging_config(**kw: Any) -> LoggingConfig:
    # No `path`: the log goes to stdout, so there is nothing to resolve against
    # a base directory. The context argument stays because `model_validate` is how the
    # rest of these builders construct config models.
    data = {"level": "INFO", "format": "json", **kw}
    return LoggingConfig.model_validate(data, context={"base": REPO_ROOT})


def make_tracing_config(**kw: Any) -> TracingConfig:
    return TracingConfig(**{
        "enabled": False, "exporter": "file", "endpoint": "", "headers_env": "", **kw,
    })


def copy_example_config(tmp_path: Path) -> Path:
    """Copy the committed template into `tmp_path` and return the copy's path.

    R-B (`docs/maintainers/conventions-rationale.md` «Test structure»): this is the ONE
    place in the suite that knows the template's name. A test reaches it
    through the `example_config_path` / `example_config` fixtures in
    `tests/conftest.py` — never by spelling `config.example.toml` itself — and
    `tests/architecture/test_config_comes_from_the_template.py` is the gate.
    The only tests that read the REAL `config.toml` are the ones whose subject
    is the deployment (the F11 drift gate, the R-24 budgets), pinned there.
    """
    cfg_path = tmp_path / "config.toml"
    shutil.copy(REPO_ROOT / "config.example.toml", cfg_path)
    return cfg_path


def load_example_config(tmp_path: Path) -> Config:
    """`copy_example_config`, loaded. For helpers that cannot take a fixture
    (the integration harness, a module-level driver); a test function takes
    the `example_config` fixture instead."""
    return _load_config(copy_example_config(tmp_path))
