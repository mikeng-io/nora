"""Frozen config tree (spec D.1). `load()` is the only reader of config.yaml.

Strictness is deliberate: a typo in `trigger.text` would silently disable Nora,
so any unknown or missing key is a startup failure, not a default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml


class ConfigError(Exception):
    """Any condition that must stop the process at startup."""


@dataclass(frozen=True)
class SignalCliConfig:
    base_url: str
    account: str
    nora_sender_id: str


@dataclass(frozen=True)
class HonchoConfig:
    base_url: str
    workspace_id: str


@dataclass(frozen=True)
class LLMTierConfig:
    model: str
    max_tokens: int
    temperature: float


@dataclass(frozen=True)
class LimitsConfig:
    turns: int
    total_tokens: int


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key_env: str
    default: LLMTierConfig
    deep: LLMTierConfig
    limits: LimitsConfig


@dataclass(frozen=True)
class TriggerConfig:
    text: str
    min_interval_seconds: float
    invocation_timeout_seconds: float


@dataclass(frozen=True)
class BufferConfig:
    db_path: Path
    max_per_conversation: int
    context_window: int


@dataclass(frozen=True)
class PersonaConfig:
    dir: Path
    reload: str
    strict: bool
    chinese_script: str
    canary: str
    display_name: str


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    path: Path
    format: str


@dataclass(frozen=True)
class MetricsConfig:
    port: int


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool
    exporter: str
    endpoint: str


@dataclass(frozen=True)
class ObservabilityConfig:
    tracing: TracingConfig


@dataclass(frozen=True)
class Config:
    signal_cli: SignalCliConfig
    honcho: HonchoConfig
    llm: LLMConfig
    trigger: TriggerConfig
    buffer: BufferConfig
    persona: PersonaConfig
    persons_file: Path
    logging: LoggingConfig
    metrics: MetricsConfig
    observability: ObservabilityConfig


# `from __future__ import annotations` makes every dataclass field's `.type`
# a string (e.g. "Path", "LLMTierConfig") rather than the real object, so
# naive `field.type is Path` / `is_dataclass(field.type)` checks at runtime
# are always False. Resolve each class's annotations once, eagerly, via
# `typing.get_type_hints`, and key nested-dataclass / Path dispatch off the
# resolved type instead of the raw (string) annotation.
_CONFIG_CLASSES = (
    SignalCliConfig,
    HonchoConfig,
    LLMTierConfig,
    LimitsConfig,
    LLMConfig,
    TriggerConfig,
    BufferConfig,
    PersonaConfig,
    LoggingConfig,
    MetricsConfig,
    TracingConfig,
    ObservabilityConfig,
    Config,
)
_HINTS: dict[type, dict[str, Any]] = {cls: get_type_hints(cls) for cls in _CONFIG_CLASSES}

_PATH_FIELDS = {"db_path", "path", "dir", "persons_file"}


def _resolve_path(value: str, base: Path) -> Path:
    expanded = Path(str(value)).expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


def _build(cls: type, data: Any, base: Path, trail: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"config key {trail!r} must be a mapping, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    hints = _HINTS[cls]
    for key in data:
        if key not in known:
            raise ConfigError(f"unknown config key: {trail}.{key}".lstrip("."))
    kwargs: dict[str, Any] = {}
    for name in known:
        if name not in data:
            raise ConfigError(f"missing config key: {trail}.{name}".lstrip("."))
        raw = data[name]
        child_trail = f"{trail}.{name}".lstrip(".")
        resolved_type = hints[name]
        if isinstance(resolved_type, type) and is_dataclass(resolved_type):
            kwargs[name] = _build(resolved_type, raw, base, child_trail)
        elif resolved_type is Path or name in _PATH_FIELDS:
            kwargs[name] = _resolve_path(raw, base)
        else:
            kwargs[name] = raw
    return cls(**kwargs)


def _validate(cfg: Config) -> None:
    if cfg.persona.reload not in ("mtime", "startup"):
        raise ConfigError(f"persona.reload must be 'mtime' or 'startup', got {cfg.persona.reload!r}")
    if cfg.persona.chinese_script not in ("traditional", "simplified"):
        raise ConfigError(
            f"persona.chinese_script must be 'traditional' or 'simplified', "
            f"got {cfg.persona.chinese_script!r}"
        )
    if cfg.observability.tracing.exporter not in ("console", "otlp", "file"):
        raise ConfigError(
            f"observability.tracing.exporter must be console|otlp|file, "
            f"got {cfg.observability.tracing.exporter!r}"
        )
    if cfg.observability.tracing.exporter == "otlp" and not cfg.observability.tracing.endpoint:
        raise ConfigError("observability.tracing.endpoint is required when exporter is 'otlp'")
    if not os.environ.get(cfg.llm.api_key_env):
        raise ConfigError(
            f"environment variable {cfg.llm.api_key_env} is not set — refusing to start"
        )


def load(path: Path) -> Config:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    cfg = _build(Config, data, path.parent, "")
    _validate(cfg)
    return cfg
