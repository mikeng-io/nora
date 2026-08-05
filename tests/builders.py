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
    LLMConfig,
    LLMTierConfig,
    LLMTiersConfig,
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

        override_config(cfg, {"store.db_path": p, "engagement.trigger.min_interval_seconds": 0})

    Format-agnostic by contract: a future config change only touches `_with`,
    never a caller.
    """
    for path, value in changes.items():
        base = _with(base, path, value)
    return base


def make_limits(**kw: Any) -> LimitsConfig:
    return LimitsConfig(**{"turns": 4, "total_tokens": 24000, **kw})


def make_llm_tier(**kw: Any) -> LLMTierConfig:
    return LLMTierConfig(**{
        "model": "deepseek-v4-flash", "max_tokens": 400, "temperature": 0.7,
        "timeout_seconds": 30, "limits": make_limits(),
        **kw,
    })


def make_llm_tiers(**kw: Any) -> LLMTiersConfig:
    defaults: dict[str, Any] = {
        "flash": make_llm_tier(),
        "deep": make_llm_tier(model="deepseek-v4-pro", max_tokens=1200, temperature=0.5),
    }
    return LLMTiersConfig(**{**defaults, **kw})


def make_llm_config(**kw: Any) -> LLMConfig:
    defaults: dict[str, Any] = {
        "base_url": "http://localhost:20128/v1",
        "api_key_env": "OMNIROUTE_API_KEY",
        "tiers": make_llm_tiers(),
    }
    return LLMConfig(**{**defaults, **kw})


def make_logging_config(**kw: Any) -> LoggingConfig:
    # LoggingConfig resolves `path` against a base-directory context (nora/config.py);
    # constructing it with no context is a startup-safety error by design (FIX 3), so
    # this builder — like `load_example_config` below — must supply one.
    data = {"level": "INFO", "path": "nora.log", "format": "json", **kw}
    return LoggingConfig.model_validate(data, context={"base": REPO_ROOT})


def make_tracing_config(**kw: Any) -> TracingConfig:
    return TracingConfig(**{
        "enabled": False, "exporter": "file", "endpoint": "", "headers_env": "", **kw,
    })


def load_example_config(tmp_path: Path) -> Config:
    """Copy the committed example config into `tmp_path` and load it.

    The single place that knows the config file's NAME and FORMAT for the
    integration harness — `tests/test_config.py`, `tests/test_main.py`, and
    `tests/test_env_template.py` also name config.example.toml directly, each
    for its own narrower purpose (schema tests, startup tests, template
    contract tests); this is the one the integration harness (and anything
    built on top of it) goes through.
    """
    cfg_path = tmp_path / "config.toml"
    shutil.copy(REPO_ROOT / "config.example.toml", cfg_path)
    return _load_config(cfg_path)
