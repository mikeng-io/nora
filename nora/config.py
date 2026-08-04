"""Frozen config tree (spec D.1), pydantic-backed. `load()` is the only reader of config.toml.

Strictness is deliberate: a typo in `trigger.text` would silently disable Nora, so
`extra="forbid"` is on every model here — unknown or missing keys are a startup
failure, not a default.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, model_validator


class ConfigError(Exception):
    """Any condition that must stop the process at startup."""


def _resolve_path(value: Path, base: Path) -> Path:
    expanded = Path(str(value)).expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


def _resolve_context_field(instance: BaseModel, field: str, info: ValidationInfo) -> None:
    """Resolve a Path field against the config file's directory, in place.

    `instance` is frozen, so a plain `setattr` would raise; `object.__setattr__`
    bypasses pydantic's frozen guard the same way it bypasses a dataclass's.
    """
    base = (info.context or {}).get("base") if info.context is not None else None
    if base is None:
        return
    value = getattr(instance, field)
    object.__setattr__(instance, field, _resolve_path(value, base))


class SignalCliConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    account: str
    nora_sender_id: str


class HonchoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    workspace_id: str


class LLMTierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    max_tokens: int
    temperature: float


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int
    total_tokens: int


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    api_key_env: str
    default: LLMTierConfig
    deep: LLMTierConfig
    limits: LimitsConfig


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    min_interval_seconds: float
    invocation_timeout_seconds: float


class BufferConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    db_path: Path
    max_per_conversation: int
    context_window: int

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> BufferConfig:
        _resolve_context_field(self, "db_path", info)
        return self


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dir: Path
    reload: Literal["mtime", "startup"]
    strict: bool
    chinese_script: Literal["traditional", "simplified"]
    canary: str
    display_name: str

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> PersonaConfig:
        _resolve_context_field(self, "dir", info)
        return self


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str
    path: Path
    format: str

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> LoggingConfig:
        _resolve_context_field(self, "path", info)
        return self


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    port: int


class TracingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    exporter: Literal["console", "otlp", "file"]
    endpoint: str

    @model_validator(mode="after")
    def _check_otlp_endpoint(self) -> TracingConfig:
        if self.exporter == "otlp" and not self.endpoint:
            raise ValueError(
                "observability.tracing.endpoint is required when exporter is 'otlp'"
            )
        return self


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tracing: TracingConfig


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> Config:
        _resolve_context_field(self, "persons_file", info)
        return self


def load(path: Path) -> Config:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    try:
        cfg = Config.model_validate(data, context={"base": path.parent})
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
    if not os.environ.get(cfg.llm.api_key_env):
        raise ConfigError(
            f"environment variable {cfg.llm.api_key_env} is not set — refusing to start"
        )
    return cfg
