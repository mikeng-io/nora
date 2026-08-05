"""Frozen config tree (spec D.1), pydantic-backed. `load()` is the only reader of config.toml.

Strictness is deliberate: a typo in `engagement.trigger.text` would silently disable Nora, so
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
        raise ValueError("base directory context is required to resolve paths")
    value = getattr(instance, field)
    object.__setattr__(instance, field, _resolve_path(value, base))


class SignalPlatformConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    account: str
    nora_sender_id: str
    # NOT YET HONOURED: nothing reads this field today — `__main__.py` constructs
    # SignalAdapter unconditionally regardless of its value. Enable/disable wiring is
    # Phase 4's adapter-registry work (see the architecture design doc under
    # docs/superpowers/specs, "Platforms: registry, resolve_identity()").
    enabled: bool


class PlatformsConfig(BaseModel):
    """A model, not a free-form dict: `extra="forbid"` must keep rejecting an
    unknown platform name. A later phase widens this into a registry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: SignalPlatformConfig


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    workspace_id: str


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int
    total_tokens: int


class LLMTierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    max_tokens: int
    temperature: float
    timeout_seconds: float
    limits: LimitsConfig


class LLMTiersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    flash: LLMTierConfig
    deep: LLMTierConfig


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    api_key_env: str
    tiers: LLMTiersConfig


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    min_interval_seconds: float


class EngagementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: Literal["trigger_text"]
    trigger: TriggerConfig


class StoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    db_path: Path
    max_per_conversation: int
    context_window: int

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> StoreConfig:
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
    exporter: Literal["console", "otlp_http", "file"]
    endpoint: str
    # Names an environment variable holding the OTLP auth header value, in the
    # same `key1=value1,key2=value2` shape OTel's own `OTEL_EXPORTER_OTLP_HEADERS`
    # uses (`nora.obs.tracing._resolve_headers` parses it). `*_env` naming, never
    # the value inlined, and no `${VAR}` interpolation (DEC-8). Empty = no auth
    # header sent — valid for `console`/`file`, and for an `otlp_http` endpoint
    # that doesn't require one.
    headers_env: str = ""

    @model_validator(mode="after")
    def _check_otlp_endpoint(self) -> TracingConfig:
        if self.exporter == "otlp_http" and not self.endpoint:
            raise ValueError(
                "obs.tracing.endpoint is required when exporter is 'otlp_http'"
            )
        return self


class ObsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logging: LoggingConfig
    metrics: MetricsConfig
    tracing: TracingConfig


class LangfuseEvalConfig(BaseModel):
    """Phase 7 / DEC-6: Langfuse as the store for eval results (datasets, dataset
    runs, scores) — `strands-agents-evals` (`evals/run.py`) stays the runner.

    `headers_env` deliberately names the SAME variable `obs.tracing.headers_env`
    already points at (`LANGFUSE_OTLP_HEADERS`), not a second credential: its
    value is already a Basic-auth header for this exact Langfuse project, in
    OTel's `key=value`, URL-encoded shape — the same shape `evals.sink.
    resolve_headers_from_env` decodes for the eval sink's plain HTTP calls.
    Introducing a second `*_env` for the identical secret would be duplication
    with no security benefit, not a stronger boundary (DEC-8: name the var,
    don't inline it — not "one var per consumer").
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    host: str
    headers_env: str = ""


class EvalsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    langfuse: LangfuseEvalConfig


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platforms: PlatformsConfig
    llm: LLMConfig
    memory: MemoryConfig
    store: StoreConfig
    engagement: EngagementConfig
    persona: PersonaConfig
    identity_file: Path
    policy_file: Path
    obs: ObsConfig
    evals: EvalsConfig

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> Config:
        _resolve_context_field(self, "identity_file", info)
        _resolve_context_field(self, "policy_file", info)
        return self


def load(path: Path, *, require_secrets: bool = True) -> Config:
    """Parse and validate config.toml.

    `require_secrets` defaults to True and that is the daemon's behaviour,
    unchanged: a missing LLM key must be a startup failure, because a Nora who
    starts without one is a Nora who is silently unable to answer.

    The read-only operator control plane (`nora groups`, `nora persons
    pending`) passes False, and the reason is the 3am path it exists for: the
    key lives in `.env`, which docker compose injects into the CONTAINER, so an
    operator running these commands from his own shell — typically with the
    container stopped, which is exactly when he needs them — has no such
    variable exported. Neither command constructs an LLM client or reaches the
    provider at all, so refusing to list group ids over a credential that
    nothing on that path uses would be a self-inflicted outage of the tool
    brought in to diagnose an outage.
    """
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
    if require_secrets and not os.environ.get(cfg.llm.api_key_env):
        raise ConfigError(
            f"environment variable {cfg.llm.api_key_env} is not set — refusing to start"
        )
    return cfg
