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
    """R-8's three caps, all of which bound the WHOLE agent loop for one tag.

    `output_tokens` is separate from `LLMConfig.max_tokens` on purpose and is
    the reason this class has three fields instead of two: `max_tokens` is the
    provider's per-CALL ceiling, this is the loop's budget across every call
    the turn makes. They used to be the same number — see
    `nora.agent.factory.build_limits` for the measurement that separated them.

    None of the three is optional. A missing `output_tokens` would silently
    mean "unbounded" to Strands (`_check_limits` skips a cap it cannot find),
    and an unbounded budget nobody chose is exactly the state this field
    exists to end.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int
    total_tokens: int
    output_tokens: int


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    api_key_env: str
    model: str
    max_tokens: int
    temperature: float
    timeout_seconds: float
    # "none" | "low" | "medium" | "high". NOT "auto" — the provider 400s on it.
    #
    # "none" — thinking OFF — and it is not a latency preference, it is what
    # makes her able to speak at all on this model.
    #
    # deepseek-v4 rejects a forced `tool_choice` while thinking is enabled.
    # DeepSeek has never documented this; deepseek-ai/DeepSeek-V3#1376 has been
    # open since 2026-05-30 with no official response. A controlled test in
    # that thread (2026-08-03): thinking on + forced tool call → 400 "Thinking
    # mode does not support this tool_choice", 5/5; thinking disabled →
    # 200 with stop_reason=tool_use, 5/5.
    #
    # The same comment names the second failure, which is the one that bit us:
    # "at max_tokens=512 the thinking block eats the budget". Nora runs
    # max_tokens=400, so with thinking on the reasoning consumes the budget
    # before a tool call can be emitted — the model does not refuse, it runs
    # out mid-thought. Measured here over 8 runs at tool_choice="required":
    #
    #   none  →  tool_call 8/8
    #   low   →  tool_call 1/8,  no_tool 7/8
    #   high  →  tool_call 1/8,  no_tool 7/8
    #
    # THE MEASUREMENTS DISAGREE, and that is itself the finding. The `nora`
    # alias is an OmniRoute COMBO: it fans out to different upstreams per
    # request, so a live probe is not reproducible. One 8-sample run had "low"
    # producing a tool call 1/8; a 6-sample run twenty minutes later had it
    # 6/6 and correct every time. Do not treat any single run here as
    # authoritative, and pin the alias to one upstream before concluding
    # anything further.
    #
    # What IS consistent across runs: "none" picks the WRONG tool. Asked
    # 「你係邊個」 it called the clock 9 times out of 12. Forced to call
    # something by tool_choice="required" and given no budget to deliberate,
    # it reaches reflexively.
    #
    # So: "low", per DeepSeek's own guidance, with max_tokens raised off 400.
    # The budget matters because thinking shares it — deepseek-ai/DeepSeek-V3
    # #1376 (2026-08-03): "at max_tokens=512 the thinking block eats the
    # budget". At 400 a reasoning turn could run out mid-thought and emit no
    # tool call at all, which is a mute Nora.
    #
    # It was 4000 from 2026-08-03 to 2026-08-06; it is now 2000. 4000 was
    # never a measured number, and while it was ALSO the loop's whole output
    # budget (`build_limits` passed it straight through as `output_tokens`)
    # one call could and did spend the entire turn: 3,993 / 3,987 / 3,974
    # completion tokens on three consecutive calls. Measured on the same
    # deployment, the largest LEGITIMATE completion — reasoning block plus the
    # tool call, not a runaway — was 371 tokens, so 2000 is 5.4x headroom for
    # the thing that actually happens and no longer doubles as the loop's
    # budget (`[llm.limits] output_tokens` is that, and is 6x this).
    # Replies stay short because the persona says so, not because the ceiling
    # forces it.
    reasoning_effort: str = "low"
    limits: LimitsConfig


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    min_interval_seconds: float


class ReactionsConfig(BaseModel):
    """The 👀 / ✅ / ⚠️ progress reactions on the tagging message.

    Off is a supported operating mode, not a degradation: reactions are the
    ONLY thing Nora writes to a conversation that is not a reply, and some
    groups will not want an agent decorating their messages. Defaults to on
    because under R-5 she is otherwise silent when broken — with reactions
    off, a tag that dies produces no observable trace in the group at all,
    and the operator is back to reading logs to tell "thinking" from "dead".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True


class EngagementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: Literal["trigger_text"]
    trigger: TriggerConfig
    reactions: ReactionsConfig = ReactionsConfig()


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
