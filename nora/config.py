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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_validator,
)


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
    # The Nora aiPeer id (Honcho v2, design/24). Her replies feed under this peer
    # into each conversation's session, building ONE global Nora self-model card
    # (decision B-global). Defaulted so a config.toml written before v2 loads
    # unchanged; the value is a peer id, never a secret.
    ai_peer: str = "nora"


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
    # Replies stay short because the doctrine says so, not because the ceiling
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


class QueueConfig(BaseModel):
    """The per-conversation FIFO queue that holds tags arriving while a turn is
    already in flight (R-29). `max_depth` bounds a FLOOD, not waiting: tags
    already queued keep their place however long the turn ahead takes, and there
    is deliberately no staleness rule — only the (max_depth + 1)th arrival is
    shed, counted as `nora_tags_dropped_total{reason="queue_full"}`. See
    `nora.engagement.queue`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_depth: int = Field(default=16, ge=1)


class EngagementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: Literal["trigger_text"]
    trigger: TriggerConfig
    reactions: ReactionsConfig = ReactionsConfig()
    queue: QueueConfig = QueueConfig()


class StoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # NAMES the env var holding the PostgreSQL URL, never the URL itself (DEC-8,
    # `api_key_env`-style). The value is `postgresql+asyncpg://…` and is injected
    # by the host (docker compose from `.env`), so a credential never lands in a
    # committed file. The composition root is the one reader.
    database_url_env: str = "DATABASE_URL"
    # The on-disk root for media byte custody (DEC-43/47). Under SQLite this was
    # derived from `db_path.parent`; a Postgres URL has no directory, so the media
    # root is its own key. Blobs live at `data_dir / "media"`.
    data_dir: Path
    max_per_conversation: int
    context_window: int

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> StoreConfig:
        _resolve_context_field(self, "data_dir", info)
        return self


class MediaConfig(BaseModel):
    """Byte custody for attachments (DEC-43).

    **Every field is defaulted and the whole section is optional**, unlike most
    of this file — a live `config.toml` written before this existed must keep
    loading, and `extra="forbid"` means a required section would be a startup
    failure on upgrade rather than a feature.

    There is deliberately **no path key here.** Media lives under
    `store.data_dir / "media"` — the record row and the blob it names are one
    custody boundary, backed up and deleted together. A media-specific root would
    let them be separated, and the first time that happened the transcript would
    reference blobs on a volume nobody backed up. (Under SQLite this root was
    `store.db_path`'s directory; a Postgres URL has no directory, so `data_dir`
    now carries it — still one key, still one boundary.)

    `enabled` defaults to True because the A9 fix is the point of the feature —
    but see DEC-43's open question: RETENTION for this data is an operator
    decision that has not been made, and this flag is the honest off switch
    until it is. Off means attachments are recorded exactly as before, with a
    placeholder and no handle, and `nora_media_custody_total{status=
    "not_configured"}` counting every one.

    `encryption_key_env` NAMES the environment variable holding the AES-256 key
    (base64 of 32 bytes), never the key itself (DEC-8, `api_key_env`-style). The
    bytes on disk are ciphertext (DEC-47), and the key is host-injected. Custody
    turns ON only when `enabled` is true AND that variable holds a valid key: a
    missing or malformed key degrades to custody OFF (the same
    `not_configured` state as `enabled = false`), never plaintext-by-accident
    and never a startup crash — media is a bonus and must not take the daemon
    down the way a missing LLM key does. Empty default = not configured.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    # Names the env var holding the base64-encoded 32-byte AES-256 key. Empty
    # means no key configured, which — like `enabled = false` — is custody OFF.
    encryption_key_env: str = ""
    # Signal's own attachment ceiling. A refusal is a policy outcome with its
    # own metric label, never a crash and never a truncated blob.
    max_bytes: int = 100 * 1024 * 1024
    # Short, because this runs on the ingest path the SSE reader awaits — the
    # same argument `SignalRoster`'s 4 s makes against `directory`'s 10 s.
    timeout_seconds: float = 8.0

    # ---- reading a stored image back through MiMo-V2.5 (DEC-56) --------------
    #
    # The WRITE half above copies bytes into custody; these govern the READ
    # half, `media_fetch` (`nora.agent.tools`), which decrypts a held blob and
    # describes it via MiMo through the process-wide OmniRoute client. They are
    # config, not literals, for DEC-8's reason: the model, the token ceiling and
    # the effort are properties of the deployment's gateway, and the two
    # SAFETY numbers below are the ones a slow model would otherwise turn into a
    # silent loop timeout.
    vision_model: str = "opencode-go/mimo-v2.5"
    # Generous, because MiMo reasons before it answers and a transcription of a
    # busy screenshot is long — but capped, so one description cannot eat a turn.
    vision_max_tokens: int = 1024
    vision_reasoning_effort: str = "low"
    # A ceiling on the bytes sent to MiMo, DELIBERATELY far below `max_bytes`
    # (100 MiB): a 100 MiB blob is a ~133 MiB base64 body, and no still image
    # worth describing is that large. Above this the tool answers `unavailable`
    # with an honest note rather than shipping a nine-figure request
    # (downscaling is a later option, not this build). 8 MiB → ~10.7 MiB body.
    vision_max_bytes: int = 8 * 1024 * 1024
    # A CEILING on the per-call vision timeout, not the timeout itself: the tool
    # derives the real budget as the smaller of this and a fixed fraction of
    # `llm.timeout_seconds` (`nora.infra.vision.derive_vision_timeout`), so it is
    # provably shorter than the loop's `asyncio.wait_for` budget and a hung MiMo
    # call becomes this tool's `unavailable` rather than the loop's silent
    # `invocation_timeout` (DEC-56, the way `factory.build_retry_policy` derives
    # its backoff from the same timeout).
    vision_timeout_seconds: float = 20.0


class RouteConfig(BaseModel):
    """Google routing for the `route` tool (DEC-46, design/14 §3).

    **The whole section is optional and every field defaults**, like
    `MediaConfig`: a `config.toml` written before this existed keeps loading,
    which `extra="forbid"` makes a real constraint rather than a courtesy.

    `enabled` defaults to **False** because `route` is the one grounding tool
    that costs money and reaches a third party — it is off until an operator
    turns it on, and while off the tool answers `unavailable` (the registry
    holds no `RouteClient`), never silently empty.

    `api_key_env` NAMES the environment variable holding the Google Maps
    Platform key; the value is never in config (DEC-8), exactly as
    `llm.api_key_env` names but never inlines the LLM key. The key must be
    restricted at Google to Routes + Places + Geocoding.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    api_key_env: str = "GOOGLE_MAPS_API_KEY"


class ShortenerConfig(BaseModel):
    """Nora's own link shortener (n.example.net — a Worker+KV this repo's
    `deploy/shortener/` Terraform owns, operator decision 2026-08-07).

    **Optional, defaulted, `enabled` False** — the `[route]` shape: off (or an
    unset token) leaves long URLs in replies, never a failure. The shortener is
    SELF-HOSTED on purpose — a third-party one would see every destination the
    group routes to — and it is never load-bearing: a mint that fails leaves
    the long URL exactly as it was. `token_env` NAMES the env var holding the
    mint bearer token (DEC-8); the same value is the Worker's secret.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str = "https://n.example.net"
    token_env: str = "NORA_SHORTENER_TOKEN"  # noqa: S105 — names an env var, not a secret
    timeout_seconds: float = Field(default=5.0, gt=0)


class PresenceConfig(BaseModel):
    """The presence collector (design/19), DEC-54/DEC-55.

    **The whole section is optional and every field defaults, and `enabled`
    defaults to False**, like `[route]`: presence ships INERT. A `config.toml`
    without a `[presence]` section loads unchanged, and while off nothing
    constructs a collector or a Life360 client — `whereis` answers `unavailable`
    (§7.10), exactly as `route` does keyless.

    Credentials are named, never inlined (DEC-8): `client_credential_env` names
    the env var holding the Basic client credential (the study's auth), and
    `username_env`/`password_env` the account login. The collector reads the
    VALUES from the environment; a committed file never carries them.

    `cadence_seconds` is the per-circle poll interval - ~60-120 s, roughly what
    the Life360 app itself does (§1). `retention_days` is the prune window
    (§9, operator decision: 180).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    client_credential_env: str = "LIFE360_CLIENT_CREDENTIAL"
    username_env: str = "LIFE360_USERNAME"
    password_env: str = "LIFE360_PASSWORD"  # noqa: S105 — names an env var, not a secret
    # ~60-120 s (§1). `ge=1` guards a zero/negative that would busy-loop; the
    # 60-120 band is guidance in config.example.toml, not a hard clamp, so an
    # operator can widen it deliberately without editing code.
    cadence_seconds: float = Field(default=90.0, ge=1.0)
    # Adaptive cadence (design/19 §4.1, backlog 2026-08-07). The poll interval
    # is not fixed: it drops to `cadence_moving_seconds` for a circle where
    # ANYONE is driving — so `whereis` stays fresh through a commute — and grows
    # back toward `cadence_still_seconds` as the circle sits still, so a parked
    # circle is not polled every 20 s all night. `cadence_seconds` remains the
    # base for the auth-failure back-off, which ALWAYS wins over the adaptive
    # floor (a 429 lengthens the sleep regardless of who is moving — ban safety
    # is never traded for freshness). Both `ge`-guarded against a busy-loop; a
    # deployment that wants the old fixed cadence sets moving == still ==
    # cadence_seconds.
    cadence_moving_seconds: float = Field(default=20.0, ge=1.0)
    cadence_still_seconds: float = Field(default=300.0, ge=1.0)
    retention_days: int = Field(default=180, ge=1)
    # The collector's own metrics/health endpoint (§4: liveness lives in the
    # daemon). A separate process, so a separate port from `obs.metrics.port`.
    metrics_port: int = 9101
    # How long the on-demand LIVE refresh (DEC-67) waits for a member's phone to
    # report a fresh position before `where_is` falls back to the stored fix. A
    # ceiling on the user's wait — the phone ping takes a few seconds, so ~8 s
    # covers a normal response without hanging the tag on an unreachable phone.
    refresh_timeout_seconds: float = Field(default=8.0, ge=0.5)
    # The collector's HTTP base URL, as NORA reaches it, for the on-demand LIVE
    # refresh (DEC-67). In compose that is the presence service by name, e.g.
    # "http://presence:9101". EMPTY (default) → `where_is` stays read-only (the
    # old behaviour, and the offline/eval shape). Nora reads this same
    # `[presence]` block; the collector serves the endpoint on `metrics_port`.
    refresh_url: str = ""
    # Names the env var holding the Google Maps key used to reverse-geocode a fix
    # Life360 returned WITHOUT an address (§5) — the same key the `route` tool
    # uses. OPTIONAL like everything else here: unset means no geocoder, and an
    # address-less fix stores the coordinate as before (never a crash). The value
    # lives in the env, never in this file (DEC-8).
    geocode_api_key_env: str = "GOOGLE_MAPS_API_KEY"
    # Which reverse-geocode backend to use (2026-08-08). "google" (default, the
    # proven one) or "hkgov" (the HK-gov ArcGIS locator, a switchable OPTION —
    # never replacing Google). Both sit behind the `ReverseGeocoder` port; the
    # collector is unchanged by the choice.
    geocode_provider: Literal["google", "hkgov"] = "google"
    # HK-gov backend (only read when `geocode_provider = "hkgov"`). The full
    # `.../reverseGeocode` service URL from the operator's LANDSD Map API portal
    # account, and the env var holding its access token (DEC-8: the token is an
    # env value, never inlined). `hkgov_lang_zh`/`_en` are the langCode strings
    # the deployment's locator honours (portal-specific; tune if the default
    # returns the wrong language). Live-verifying these needs the operator's key.
    hkgov_geocode_url: str = ""
    hkgov_token_env: str = "HKGOV_MAPS_TOKEN"  # noqa: S105 — names an env var, not a secret
    hkgov_lang_zh: str = "zh"
    hkgov_lang_en: str = "en"


class SearchConfig(BaseModel):
    """Web search for the `web_search` tool — Tavily (DEC-73).

    **Optional and every field defaults**, the `RouteConfig` shape. `enabled`
    defaults **False** and ships that way: `web_search` reaches a third party and
    spends credits, so it is off until an operator turns it on AND a key is
    present. Off (or keyless) leaves the registry without a provider and
    `web_search` answers `unavailable` (R-5), never a silent empty.

    SearXNG + Firecrawl and the `web_fetch` verb are RETIRED (DEC-73): Tavily
    returns ranked results WITH a content snippet each in one call, from ITS
    servers, so there is no self-hosted fetcher, no model-chosen-URL fetch, and no
    SSRF surface — the `is_global`/`allowed_urls`/`deploy/egress.sh` apparatus is
    gone with them.

    `api_key_env` NAMES the env var holding the Tavily key (DEC-8), never the key
    itself. `search_depth` is `"basic"` (1 credit) or `"advanced"` (2, richer
    extraction); `max_results` bounds the count (Tavily allows ≤20);
    `content_max_chars` clamps each result's snippet — a tool result is re-sent on
    every subsequent model call, so what accumulates must be bounded (the
    fetch-token lesson, 2026-08-08).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    api_key_env: str = "TAVILY_API_KEY"
    url: str = "https://api.tavily.com/search"
    search_depth: Literal["basic", "advanced"] = "basic"
    max_results: int = Field(default=5, ge=1, le=20)
    content_max_chars: int = Field(default=1500, ge=1)
    timeout_seconds: float = 12.0


class WeatherConfig(BaseModel):
    """Current weather + forecast for the `weather` tool (DEC-72).

    **Optional and every field defaults**, the `RouteConfig` shape. UNLIKE the
    other grounding sections it can safely default `enabled=True` in a fresh
    config because the HK Observatory provider is KEYLESS, FREE, and leaks
    nothing (no user data egresses — the request carries no location) — there is
    nothing to gate on. It still ships `enabled=False` in `config.example.toml`
    for the "off until explicitly on" house rule; a real deployment flips it.

    `provider` selects the `WeatherProvider` adapter — `Literal["hko"]` today, the
    `[presence] geocode_provider` precedent (also a `Literal`, not a bare string):
    a typo is a startup `ConfigError`, not a daemon that boots with `weather`
    silently dead, and adding a region extends the literal by one value — the same
    one-line change every switchable adapter here costs. `url`/`lang` are the HKO
    Open Data endpoint and language; `lang="tc"` for a Cantonese group.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: Literal["hko"] = "hko"
    url: str = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
    lang: str = "tc"
    timeout_seconds: float = Field(default=8.0, gt=0)


class HeartbeatConfig(BaseModel):
    """The background tick that fires due reminders (`nora.app.heartbeat`).

    Optional, defaulted, and INERT by default like `[presence]` and `[search]`:
    this is Nora's first unprompted-speech path, so it must never turn itself on
    because a section is missing from a config.toml written before it existed.

    `timeout_seconds` is deliberately NOT `[llm].timeout_seconds`: that number
    bounds a turn a human is waiting on. Nobody is waiting on a fire turn, and
    cutting one short produces silence in a group nobody asked anything in.

    `tick_seconds` is the WAKE INTERVAL and nothing more. It is the BEST case
    for lateness — a reminder fires on the first tick at or after its `due_at` —
    and it is NOT an upper bound, which an earlier version of this docstring
    claimed. Two things exceed it, both by design:

    * one tick runs its turn-groups SEQUENTIALLY and awaits each one, so a tick
      that finds N groups due can take up to N x `timeout_seconds`;
    * a group whose conversation slot is held by a tag is not queued or shed but
      DEFERRED to the next tick entirely (§5.3), and a busy group can defer the
      same rows repeatedly — `nora_heartbeat_deferrals_total` is the counter
      that says it is happening.

    So the honest statement is "at or after `due_at`, never before" (§5.6).
    `[reminders].min_lead_seconds` is held above `tick_seconds` on purpose and
    is now enforced by `Config._lead_clears_the_tick`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    tick_seconds: float = 60.0
    timeout_seconds: float = 120.0


class RemindersConfig(BaseModel):
    """The three anti-runaway caps on stored reminders.

    Mirrored by `nora.domain.reminders.ReminderCaps`, which is what the tool
    layer actually receives — import-linter forbids `nora.agent` from importing
    `nora.config` at all, so the config type cannot cross that boundary. The
    composition root builds the domain value from this one; the DEFAULTS on both
    are the same numbers on purpose, and this is the pair to keep in step.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_pending_per_requester: int = Field(default=20, ge=1)
    # Above `[heartbeat].tick_seconds`, so a reminder set with the minimum lead
    # cannot land in the tick that is already running. ENFORCED, not merely
    # stated: `Config._lead_clears_the_tick` refuses the pair outright — this
    # was a comment in three files and a rule in none.
    min_lead_seconds: int = Field(default=120, ge=1)
    max_horizon_days: int = Field(default=30, ge=1)


class DoctrineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dir: Path
    reload: Literal["mtime", "startup"]
    strict: bool
    # BCP 47 language tag (RFC 5646) — the deployment's language as a deployment
    # fact, rendered into every tag's runtime_context so the model is told from
    # OUTSIDE what to speak. Replaces the invented `chinese_script` string, which
    # nothing ever read: a free-form tag (`yue-Hant-HK`) carries script AND
    # region a two-value enum could not, and reuses a standard rather than a
    # convention only this repo understands.
    language_hint: str
    canary: str
    display_name: str

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> DoctrineConfig:
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
    # The model that GRADES the adversarial live run (the judge). Defaulted and
    # tunable for cost (operator 2026-08-09): `deepseek-v4-pro` is sharp but dear,
    # and the judge runs once per graded case. The ONE hard invariant is that it
    # must NOT equal `llm.model` — a model grading its own output self-scores; the
    # runner refuses that. Since Gap-1 grounding (the judge now sees the source
    # data) does more for accuracy than raw tier, a cheaper DIFFERENT model is a
    # reasonable trade — set it here. Kept as a bare id, not the whole tier.
    judge_model: str = "deepseek-v4-pro"
    # How many times the judge grades each reply; the gate binds the MAJORITY
    # safety verdict (A2). 1 = single sample (noisy — the judge caught 1/3 vs 3/3
    # across runs); 3 kills that single-sample noise. Odd is preferable so a
    # majority always exists.
    judge_samples: int = Field(default=1, ge=1)


class AgentConfig(BaseModel):
    """The agent's own identity (2026-08-08), so a clone — a second agent called
    "Delia" — is a config + doctrine-file change, not a code change.

    `name` is the MACHINE name: the Strands `agent_id` and the Langfuse trace
    span (`invoke_agent <name>`). Lowercase and STABLE — changing it starts a new
    trace lineage (orphans saved Langfuse views), which is what a genuinely new
    agent wants. The HUMAN-facing name is `[doctrine].display_name` and the wake
    word is `[engagement].trigger.text`; a clone sets all three (plus its own
    doctrine files and its own Honcho `ai_peer`/`workspace_id`).

    Optional and defaulted like `[media]`/`[route]`: a config.toml written before
    this section keeps loading, and the default reproduces today's identity
    exactly (`nora`), so no existing trace is orphaned by the section's arrival.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "nora"


class McpMountConfig(BaseModel):
    """One MCP server Nora may mount as conversation tools (spec §2.1/§2.2).

    Optional and defaulted like `RouteConfig`/`ShortenerConfig`: `enabled`
    defaults False, so a section existing in a committed config doesn't mean
    it's live. `token_env` NAMES the environment variable holding the bearer
    token (DEC-8's `api_key_env`/`token_env` shape) — the value is never here,
    it lives in `.env`. `budget` is the flat per-server tool-call ceiling
    every tool this server advertises shares (spec §4) — not per-tool,
    because auto-discovered tools have no config-time identity to key on.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str
    token_env: str
    budget: int


class ToolsConfig(BaseModel):
    """The `[tools.mcp.<name>]` mount points (spec §2.2). A second server is
    a second `mcp` dict entry — no schema change, matching the ARCHITECTURE
    doc's documented target ("add an MCP server -> no Python")."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mcp: dict[str, McpMountConfig] = Field(default_factory=dict)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Optional and defaulted — see `AgentConfig`; the default is today's identity.
    agent: AgentConfig = AgentConfig()
    platforms: PlatformsConfig
    llm: LLMConfig
    memory: MemoryConfig
    store: StoreConfig
    # Optional, and defaulted — see `MediaConfig`. A config.toml written before
    # DEC-43 must keep loading, which `extra="forbid"` makes a real constraint
    # rather than a courtesy.
    media: MediaConfig = MediaConfig()
    # Optional and defaulted — see `RouteConfig`. Off unless an operator turns
    # it on; a config.toml without a `[route]` section keeps loading.
    route: RouteConfig = RouteConfig()
    # Optional and defaulted — see `ShortenerConfig`. Off unless an operator
    # turns it on; never load-bearing (a failed mint leaves the long URL).
    shortener: ShortenerConfig = ShortenerConfig()
    # Optional and defaulted — see `PresenceConfig`. Off unless an operator turns
    # it on; a config.toml without a `[presence]` section keeps loading, and the
    # collector is a SEPARATE process (`python -m nora.presence`) that reads this
    # same tree.
    presence: PresenceConfig = PresenceConfig()
    # Optional and defaulted — see `SearchConfig`. Off (inert) unless an operator
    # turns it on AND a Tavily key is present (`api_key_env`, DEC-8/DEC-73); a
    # config.toml without a `[search]` section keeps loading.
    search: SearchConfig = SearchConfig()
    # Optional and defaulted — see `WeatherConfig`. Keyless (HK Observatory), so
    # safe to enable; ships off in the example, on in this deployment (DEC-70).
    weather: WeatherConfig = WeatherConfig()
    # Optional and defaulted — see `HeartbeatConfig`/`RemindersConfig`. The tick
    # ships INERT (`enabled = false`); the caps apply the moment a reminder can
    # be created, whether or not anything is firing them.
    heartbeat: HeartbeatConfig = HeartbeatConfig()
    reminders: RemindersConfig = RemindersConfig()
    # Optional and defaulted — see `ToolsConfig`. Empty means no MCP server is
    # mounted; a config.toml without a `[tools]` section keeps loading.
    tools: ToolsConfig = ToolsConfig()
    engagement: EngagementConfig
    doctrine: DoctrineConfig
    identity_file: Path
    policy_file: Path
    obs: ObsConfig
    evals: EvalsConfig

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> Config:
        _resolve_context_field(self, "identity_file", info)
        _resolve_context_field(self, "policy_file", info)
        return self

    @model_validator(mode="after")
    def _lead_clears_the_tick(self) -> Config:
        """`[reminders].min_lead_seconds` must exceed `[heartbeat].tick_seconds`.

        The relation was a COMMENT in three places — both config files and
        `RemindersConfig` — and nothing enforced it, so an operator who
        lengthened the tick got a silent behaviour change instead of a refusal:
        a reminder set with the minimum lead becomes due before the tick that is
        already running comes round again, and Nora answers 「等陣提你」 to
        something she will not say until after the moment has passed. The
        promise she makes at create time is the thing at stake, which is why
        this refuses rather than warns.

        Checked whatever `[heartbeat] enabled` says. The caps apply the moment a
        reminder can be CREATED, which is independent of anything firing them —
        a config that only becomes wrong when someone flips the switch is a
        config that becomes wrong at the worst moment.
        """
        if self.reminders.min_lead_seconds <= self.heartbeat.tick_seconds:
            raise ValueError(
                f"[reminders] min_lead_seconds = {self.reminders.min_lead_seconds} "
                f"must be greater than [heartbeat] tick_seconds = "
                f"{self.heartbeat.tick_seconds}: a reminder set with the minimum "
                "lead would come due inside the tick that is already running, and "
                "would be answered later than she promised."
            )
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
