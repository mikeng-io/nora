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
    # Most legacy tests exercise effective settings with string surgery. Keep
    # their synthetic literal config; dedicated resolver tests use source refs.
    import tomlkit

    from nora.config import PRIVATE_ENV_FIELDS

    doc = tomlkit.parse(cfg_path.read_text())
    refs = doc["agent"].pop("handles_env")
    doc["agent"]["handles"] = dict.fromkeys(refs, "@synthetic")
    for field in PRIVATE_ENV_FIELDS:
        if field.startswith("agent.handles.") or ".sidecars." in field:
            continue
        parts = field.split(".")
        section = doc
        for part in parts[:-1]:
            section = section[part]
        section.pop(parts[-1] + "_env", None)
        section[parts[-1]] = {
            "account": "", "canary": "SYNTHETIC-CANARY",
            "base_url": "https://example.invalid/pulse/mcp",
            "public_url": "", "handle": "noraexample", "shadow_channel": "",
        }[parts[-1]]
    doc["identity_file"] = "identity.toml"
    doc["policy_file"] = "policy.toml"
    cfg_path.write_text(tomlkit.dumps(doc))
    return cfg_path


def load_example_config(tmp_path: Path) -> Config:
    """`copy_example_config`, loaded. For helpers that cannot take a fixture
    (the integration harness, a module-level driver); a test function takes
    the `example_config` fixture instead."""
    return _load_config(copy_example_config(tmp_path))


# Synthetic env for testing the reference-bearing public example.
PRIVATE_CONFIG_ENV = {
    **{f"NORA_HANDLE_{n.upper()}": f"@synthetic_{n}" for n in
       ("signal", "whatsapp", "x", "threads", "discord")},
    "NORA_SIGNAL_ACCOUNT": "+12025550123",
    "NORA_INSTAGRAM_SUMMONS_HANDLE": "synthetic",
    "NORA_PARTICIPATION_SHADOW_CHANNEL": "10000000000000001/10000000000000002",
    "NORA_RELEVANCE_SHADOW_CHANNEL": "10000000000000003/10000000000000004",
    "NORA_TTS_GUILD_PRIMARY": "10000000000000001",
    "NORA_TTS_GUILD_SECONDARY": "10000000000000002",
    "NORA_PULSE_BASE_URL": "https://example.invalid/pulse/mcp",
    "NORA_DOCUMENTS_PUBLIC_URL": "https://example.invalid",
    "NORA_DOCTRINE_CANARY": "SYNTHETIC-CANARY",
}
