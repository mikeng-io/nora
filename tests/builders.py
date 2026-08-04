"""Test-side construction seams for the config tree.

Phase 1 replaces frozen dataclasses with pydantic-settings. Every direct use of
`dataclasses.replace` or a `nora.config` constructor in the suite is a site that
would break; concentrating them here means phase 1 edits this file, not ten
others. Nothing outside this module may construct or copy a config object.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import Any

from nora.config import (
    Config,
    LimitsConfig,
    LLMConfig,
    LLMTierConfig,
    LoggingConfig,
    TracingConfig,
)
from nora.config import load as _load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _with(obj: Any, path: str, value: Any) -> Any:
    head, _, rest = path.partition(".")
    if rest:
        value = _with(getattr(obj, head), rest, value)
    return dataclasses.replace(obj, **{head: value})


def override_config(base: Any, changes: dict[str, Any]) -> Any:
    """Return a copy of `base` with dotted-path fields replaced.

        override_config(cfg, {"buffer.db_path": p, "trigger.min_interval_seconds": 0})

    Format-agnostic by contract: when the config tree becomes pydantic, only
    `_with` changes (to `model_copy(update=...)`), and no caller moves.
    """
    for path, value in changes.items():
        base = _with(base, path, value)
    return base


def make_llm_tier(**kw: Any) -> LLMTierConfig:
    return LLMTierConfig(**{"model": "deepseek-v4-flash", "max_tokens": 400,
                            "temperature": 0.7, **kw})


def make_limits(**kw: Any) -> LimitsConfig:
    return LimitsConfig(**{"turns": 4, "total_tokens": 24000, **kw})


def make_llm_config(**kw: Any) -> LLMConfig:
    defaults: dict[str, Any] = {
        "base_url": "http://localhost:20128/v1",
        "api_key_env": "OMNIROUTE_API_KEY",
        "default": make_llm_tier(),
        "deep": make_llm_tier(model="deepseek-v4-pro", max_tokens=1200, temperature=0.5),
        "limits": make_limits(),
    }
    return LLMConfig(**{**defaults, **kw})


def make_logging_config(**kw: Any) -> LoggingConfig:
    return LoggingConfig(**{"level": "INFO", "path": "nora.log", "format": "json", **kw})


def make_tracing_config(**kw: Any) -> TracingConfig:
    return TracingConfig(**{"enabled": False, "exporter": "file", "endpoint": "", **kw})


def load_example_config(tmp_path: Path) -> Config:
    """Copy the committed example config into `tmp_path` and load it.

    The single place that knows the config file's NAME and FORMAT. Phase 1
    renames config.example.yaml -> config.example.toml and swaps the loader;
    only this function moves.
    """
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_ROOT / "config.example.yaml", cfg_path)
    return _load_config(cfg_path)
