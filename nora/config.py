"""Frozen config tree, pydantic-backed. `load()` is the only reader of config.toml.

Strictness is deliberate: a typo in `engagement.trigger.text` would silently disable Nora, so
`extra="forbid"` is on every model here — unknown or missing keys are a startup
failure, not a default.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from nora.domain.external_files import PROVIDERS as EXTERNAL_FILE_PROVIDERS
from nora.domain.ports import (
    DEFAULT_DREAM_STORE_TIMEOUT_SECONDS,
    DREAM_MAX_STORE_CALLS_PER_TICK,
)
from nora.domain.standing import (
    DEFAULT_STANDING_MAX_CHARS,
    DEFAULT_STANDING_STORE_TIMEOUT_SECONDS,
    DEFAULT_WORKING_SET_MAX_CHARS,
    DEFAULT_WORKING_SET_MAX_ITEMS,
    STANDING_BODY_CEILING_CHARS,
    STANDING_STORE_CALLS_PER_FOLD,
    WORKING_SET_CEILING_BYTES,
    WORKING_SET_CEILING_ITEMS,
)

# `nora.config` may import `nora.domain` — the forbidden direction is the
# other one (`nora.domain` may not read config). The ceiling is imported
# rather than restated so `StandingConfig`'s startup refusal and the
# database's own CHECK cannot come to disagree about the same number.


class ConfigError(Exception):
    """Any condition that must stop the process at startup."""


def _resolve_path(value: Path, base: Path) -> Path:
    expanded = Path(str(value)).expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


def _shadow_channel_shape_error(*, section: str, platform: str, channel: str) -> str | None:
    """The shape a shadow channel must have ON ITS PLATFORM, or `None`.

    Discord addresses a room as `"<guild_id>/<channel_id>"`, and `policy.toml` spells its
    `[[conversations]]` ids that way. A bare channel id is partitioned by `DiscordSender.send`
    into `guild_id="<the id>"`, `channel_id=""`, so the POST goes to `/channels//messages` and
    Discord answers 405: every Relevance Gate GO verdict would be dropped while the gate
    counted it delivered.

    A wrong-shaped channel is a CONFIG error and belongs here, at boot, beside the emptiness
    check, not in a fail-soft review path. That path is designed not to shout, so the only
    loud place left is the one that reads the value.

    Only Discord is checked, because only Discord's adapter carries a two-part id. Signal's
    conversation id is one opaque base64 string and a `/` in it means nothing.
    """
    if platform != "discord":
        return None
    guild, sep, channel_id = channel.partition("/")
    if sep and guild and channel_id and "/" not in channel_id:
        return None
    return (
        f"[{section}] shadow_channel must be \"<guild_id>/<channel_id>\" on "
        f"discord (the same shape policy.toml uses for a Discord room), not "
        f"{channel!r} — a bare channel id sends to /channels//messages, which "
        f"Discord refuses with HTTP 405"
    )


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
    # No `nora_sender_id` (DEC-102): her own id comes from the PLATFORM
    # (`PlatformAdapter.resolve_identity().primary`), never from operator-typed config,
    # which would be wrong for every platform but one. `is_nora` is a column of its own and
    # is what marks her rows.
    enabled: bool


class DiscordPlatformConfig(BaseModel):
    """No `base_url` (unlike Signal): the adapter talks to Discord's own Gateway/REST
    endpoints directly, not a local daemon. `token_env` names the environment variable
    holding the bot token (see `.env.example`); the secret itself never lives in
    config.toml, the same convention `route.api_key_env` uses for the Google Maps key.

    Defaulted, unlike `SignalPlatformConfig`: Signal is required in every deployment;
    Discord is optional, so a config.toml without it loads with Discord off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    token_env: str = "DISCORD_BOT_TOKEN"  # noqa: S105 — names an env var, not a secret


class WhatsAppPlatformConfig(BaseModel):
    """Unlike Discord but like Signal, the adapter talks to a LOCAL daemon, the containerized
    Baileys bridge (`deploy/whatsapp-bridge/`, compose service `whatsapp-bridge`), so the
    credential never appears here: the bridge holds the paired session on its own
    `./state/whatsapp` volume. No `account` field either: the bot's own number is derived
    from the bridge's `GET /self` at startup (`nora.platform.whatsapp.self_id`), the rule
    DEC-102 set for Signal, since a configured copy of who Nora is could silently disagree
    with pairing.

    Defaulted: a config.toml without it loads with WhatsApp off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str = "http://whatsapp-bridge:18081"


class InstagramPlatformConfig(BaseModel):
    """Same shape as WhatsApp's and for the same reason: the adapter talks to a LOCAL daemon,
    the containerized Go/instameow bridge (`deploy/instagram-bridge/`, compose service
    `instagram-bridge`), so the credential never appears here. The bridge holds the cookie
    session on its own `/session` volume. No `account` field either: the bot's own interop
    fbid is derived from the bridge's `GET /health` at startup
    (`nora.platform.instagram.self_id`), the rule DEC-102 set for Signal. The rule matters
    most here: the obvious id is the WRONG one, choosing it raises no error anywhere, and
    Nora simply never notices she was tagged.

    **This is deliberately NOT `[social.instagram]`, even though it names the same process
    on the same port.** One authenticated session serves both axes (a second would mean two
    concurrent logins for one account, the configuration most likely to provoke a
    checkpoint), but they share a SESSION, not a capability: this adapter cannot fetch a
    story and that client cannot send a message. Two config homes keep two things switched
    on separately, so a messaging outage cannot silently take the social axis with it, or the
    reverse. See `InstagramSocialConfig`.

    Defaulted and OFF: a config.toml without it loads with Instagram off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str = "http://instagram-bridge:18082"


class PlatformsConfig(BaseModel):
    """A model, not a free-form dict: `extra="forbid"` must keep rejecting an
    unknown platform name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: SignalPlatformConfig
    discord: DiscordPlatformConfig = Field(default_factory=DiscordPlatformConfig)
    whatsapp: WhatsAppPlatformConfig = Field(default_factory=WhatsAppPlatformConfig)
    instagram: InstagramPlatformConfig = Field(default_factory=InstagramPlatformConfig)


class HonchoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    workspace_id: str
    # The Nora aiPeer id. Her replies feed under this peer into each conversation's
    # session, building ONE global Nora self-model card. A peer id, never a secret.
    ai_peer: str = "nora"
    # How many of HER CONCLUSIONS ABOUT THE TAGGER ride in `<peer_history>`. 0
    # disables the block, leaving `about` as the only way she reaches a person's
    # episodes.
    #
    # A separate knob from the people-in-window one: the two blocks have different
    # lifetimes and different arithmetic. This block is re-sent on every model call of
    # the loop, so 8 conclusions (~1,080 chars, ~360 tokens) is ~7.2k of the 128k budget
    # at `turns = 20`.
    tagger_card_conclusions: int = 8
    # `<people_in_window>` on the PARTICIPATION turn: how many of HER CONCLUSIONS ABOUT
    # EACH SPEAKER in the window ride in the prompt. 0 disables the block entirely.
    #
    # Separate from `tagger_card_conclusions`: `<peer_history>` renders ONE person and is
    # re-sent on every model call of a tag loop; this renders up to
    # `people_in_window_speakers` of them on ONE fire turn. Eight each across four
    # speakers is ~1,440 tokens of background, so the default is 3: the participation turn
    # decides WHETHER to speak, and the top few conclusions answer «does this room mean
    # her?».
    people_in_window_conclusions: int = Field(default=3, ge=0)
    # How many DISTINCT SPEAKERS of the window get a `<people_in_window>` entry, most
    # recent speaker first. 0 disables the block.
    #
    # Bounded because the read is one Honcho round trip PER SPEAKER, concurrent, in front
    # of a fire turn that already holds the conversation's heartbeat slot. The bound is on
    # the READS, not just the render: raising it costs latency as well as tokens.
    people_in_window_speakers: int = Field(default=4, ge=0)
    # The wall-clock bound on the participation turn's memory reads, in seconds.
    #
    # `HonchoClient.representation` has its own 3s transport timeout, so this is a belt
    # over the port: `MemoryStore` implementations are not obliged to time anything out,
    # and these reads run while the heartbeat holds this conversation's slot. One bound
    # over the whole concurrent batch, not one per read.
    read_timeout_seconds: float = Field(default=5.0, gt=0)


class LimitsConfig(BaseModel):
    """R-8's three caps, all of which bound the WHOLE agent loop for one tag.

    `output_tokens` is separate from `LLMConfig.max_tokens`: `max_tokens` is the provider's
    per-CALL ceiling, this is the loop's budget across every call the turn makes. See
    `nora.agent.factory.build_limits`.

    None of the three is optional. A missing `output_tokens` would silently mean
    "unbounded" to Strands (`_check_limits` skips a cap it cannot find).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int
    total_tokens: int
    output_tokens: int


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str
    api_key_env: str
    api: Literal["openai-chat-completions"] = "openai-chat-completions"


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = ""
    model: str = ""


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = ""
    base_url: str = Field(default_factory=str)
    api_key_env: str = "OMNIROUTE_API_KEY"
    model: str
    max_tokens: int
    temperature: float
    timeout_seconds: float
    # "none" | "low" | "medium" | "high". NOT "auto": the provider 400s on it.
    #
    # "low", per DeepSeek's guidance. Thinking shares the `max_tokens` budget, so at a low
    # ceiling a reasoning turn can run out mid-thought and emit no tool call, which is a
    # mute Nora. deepseek-v4 also rejects a forced `tool_choice` while thinking is enabled
    # (400 "Thinking mode does not support this tool_choice"). "none" turns thinking off
    # but picks the wrong tool: forced to call something by tool_choice="required" with no
    # budget to deliberate, it reaches reflexively (the clock, for 「你係邊個」).
    #
    # The `nora` alias is an OmniRoute COMBO that fans out to different upstreams per
    # request, so a live probe is not reproducible. Do not treat a single run as
    # authoritative; pin the alias to one upstream before concluding more.
    #
    # `max_tokens` is the provider's per-call ceiling and is kept apart from the loop's
    # whole-turn output budget (`[llm.limits] output_tokens`): were they one number, a
    # single call could spend the entire turn. Replies stay short because the doctrine says
    # so, not because the ceiling forces it.
    reasoning_effort: str = "low"
    # Deferred tool loading on the TAG and FIRE paths. True: only `HOT_TOOLS` ship with
    # full schemas; every other tool the room's scope admits is a one-line
    # `<tool_catalogue>` entry she loads with `load_tools`, and `[llm.limits] turns` rises
    # by one for that call (`ToolSurface.limits`). False: every admitted schema is
    # resident. A config switch so one build serves both arms of a selection-accuracy
    # comparison and turning it off is an edit and a restart, not a deploy. The Dreamer
    # never defers: its adaptors' grants are a handful of tools, below where deferring pays.
    defer_tools: bool = False
    limits: LimitsConfig
    # Per-tool call-count overrides, applied OVER `NATIVE_TOOL_BUDGETS` by
    # `ToolRegistry.budgets()`. Empty by default: `nora/agent/tools.py` states every
    # default beside its tool (DEC-10), and this is only the per-deployment delta, so an
    # operator can raise a cap without a code change and a rebuild.
    #
    # Names are VALIDATED against the live registry below, so a typo is a startup failure.
    # Silently ignoring `{"send_replies": 8}` would leave an operator certain they had
    # raised a cap that never moved.
    budgets: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _budget_names_and_values_are_real(self) -> LLMConfig:
        # Imported at call time, not module scope: `nora.config` is imported by everything,
        # including tools that never build an agent, and `nora.agent.tools` pulls in the
        # whole tool tree. Deferring keeps that cost on `Config` construction.
        #
        # No cycle: nothing under `nora.agent` may import `nora.config` (import-linter
        # contract), so this edge has no return path. It validates against the LIVE table,
        # so adding or removing a tool changes what is accepted with no second list to
        # maintain.
        from nora.agent.tools import NATIVE_TOOLS

        for name, value in self.budgets.items():
            if name not in NATIVE_TOOLS:
                known = ", ".join(sorted(NATIVE_TOOLS))
                raise ValueError(
                    f"[llm.budgets] names an unknown tool {name!r}. "
                    f"Known tools: {known}"
                )
            if value < 1:
                raise ValueError(
                    f"[llm.budgets] {name} = {value}: a budget below 1 would "
                    "make the tool unreachable. Remove the tool instead of "
                    "starving it."
                )
        return self


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # ON by default, so a `config.toml` without this field keeps matching `text`. Turning
    # it off narrows `CompositePolicy` (`nora/app/composition.py`) down to `MentionPolicy`
    # + `ReplyToNoraPolicy`: a real `@mention` or a reply to one of her own messages still
    # engages her; only the spoken/typed trigger PHRASE stops matching.
    # `min_interval_seconds` (the debounce) is unaffected either way.
    enabled: bool = True
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
    already in flight (R-30). `max_depth` bounds a FLOOD, not waiting: tags
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


class ProactiveConfig(BaseModel):
    """The proactive act turn: a reminder Cognition wrote to itself comes due and she
    decides, in the room, whether anything needs saying.

    `reasoning_effort` is this turn's own, NOT `[participation]`'s. The two fail in
    opposite directions: a participation turn goes too SILENT with more reasoning, while a
    proactive turn at `none` speaks even where silence is right, restating a plan the room
    had already settled or a question she had asked minutes earlier. `low` keeps it silent
    when it should be.
    """

    model_config = ConfigDict(extra="forbid")

    reasoning_effort: str = "low"
    # One call's output ceiling for this turn, NOT `[llm] max_tokens`: reasoning counts
    # against it, and at `low` a proactive call can overrun a lower ceiling and die
    # `PartialReplyError`. A cap is not a reservation, so headroom costs nothing; the
    # turn's `[llm.limits] output_tokens` still bounds a runaway turn.
    max_tokens: int = Field(default=20000, gt=0)


class ParticipationConfig(BaseModel):
    """The participation occasion: Nora MAY speak in a room she was not tagged in, judged
    once per quiescence-closed window, with silence as the healthy default.

    Ships INERT (`enabled = false`), and even ON it engages nowhere until the operator ALSO
    opts a room in with `relevance_gate = true` on that room's `[[conversations]]` entry in
    policy.toml: the trust distinction "allowed to answer when tagged" vs "allowed to be
    spoken to unprompted" is drawn in the policy file, where every other trust distinction
    lives.

    `settle_seconds` is the quiescence trigger: a candidate window opens on a message and
    CLOSES when the room has been quiet this long; exactly one participation evaluation
    runs per closed window, with the whole burst in context, never one per message.
    `cooldown_minutes` caps SENDS, not evaluations: at most one participation message per
    room per window of this length, and an evaluation that concludes "speak" inside the
    cooldown stays silent.

    `shadow` is the review surface: every determination, speak AND silence, is mirrored to
    `shadow_channel`, tagged with the source room, the verdict, the text (sent, held, or
    the declined draft) and the window that closed. The mirror goes through the same
    Deliverer/guards as any send, under its own `audience="participation-shadow"` label,
    and is never counted in `nora_participation_spoke_total` (that series is live sends
    only). Shadow sits ON TOP of the `enabled` gate and the room opt-in, since a
    determination only exists to mirror when both already passed. The mirror is
    operator-facing, so the lean is Discord.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    settle_seconds: float = Field(default=10.0, gt=0)
    # The VOLUME arm of the window. The two arms are OR (quiet OR this many unjudged
    # messages, whichever first) because the quiet arm alone never closes a window in a
    # room that keeps talking: it reads time since the LAST message, which every message
    # resets.
    #
    # Deliberately SMALLER than `[relevance_gate] transcript_rows`, so consecutive windows
    # overlap and an intent spread across several messages stays visible in the next one,
    # and the cap actually fills.
    max_window_messages: int = Field(default=10, gt=0)
    # DECLARED for this occasion, not inherited from `[llm]`. `none`: more reasoning yields
    # more silent turns, and silence is this occasion's default, so extra thinking is not a
    # virtue here; the failure to avoid is a turn that HOLDS the answer and says nothing.
    # It is the setting the Relevance Gate runs at: both calls open with one decision
    # (speak or not), and a decision with a fixed shape does not improve for being
    # deliberated.
    reasoning_effort: str = "none"
    cooldown_minutes: float = Field(default=10.0, gt=0)
    shadow: bool = False
    # The mirror's destination on `shadow_platform`, in THAT platform's own id
    # shape — `"<guild_id>/<channel_id>"` on discord, the same form policy.toml
    # uses for a Discord room. Required when `shadow = true` — a mirror with
    # nowhere to post would fail soft on every determination, which is a config
    # error, not a runtime condition.
    shadow_channel: str = ""
    shadow_platform: str = "discord"

    @model_validator(mode="after")
    def _shadow_needs_a_channel(self) -> ParticipationConfig:
        if self.shadow and not self.shadow_channel:
            raise ValueError(
                "[participation] shadow = true requires shadow_channel — the "
                "mirror's destination channel id on shadow_platform"
            )
        # Shape, not just presence: a set-but-unsendable channel fails soft on
        # every determination exactly as an unset one does, so the two are one
        # config error and are refused in one place.
        if self.shadow:
            problem = _shadow_channel_shape_error(
                section="participation", platform=self.shadow_platform,
                channel=self.shadow_channel,
            )
            if problem is not None:
                raise ValueError(problem)
        return self


class ReactionConfig(BaseModel):
    """The reaction picker (`nora.agent.reaction`): on a tag turn's first
    reply, one cheap call asks whether a GIF lands it better, and a second
    picks one from Giphy's described results. Needs `GIPHY_API_KEY` as well;
    without it this does nothing whatever `enabled` says.

    Ships INERT. `model` and `reasoning_effort` empty inherit `[llm]`.
    `timeout_seconds` bounds the WHOLE pick, both calls and the search, and on
    expiry the reply goes out as written: it is time added before her words
    arrive, so it is kept short.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = ""
    # "none", not inherited: a reasoning budget spent inside `max_tokens`
    # truncates the verdict.
    reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = "none"
    timeout_seconds: float = Field(default=6.0, gt=0)
    max_tokens: int = Field(default=200, ge=16)


class RelevanceGateConfig(BaseModel):
    """The Relevance Gate: ONE cheap structured call that decides whether a
    quiescence-closed window is worth a participation turn at all.

    Ships INERT (`enabled = false`), and even ON it considers nothing until the
    operator ALSO opts a room in with `relevance_gate = true` on that room's
    `[[conversations]]` entry in policy.toml: the trust distinction "allowed
    to answer when tagged" vs "allowed to be spoken to unprompted" is drawn in
    the policy file, where every other trust distinction lives.

    `model` is its OWN OmniRoute combo (`nora-gate`), not the `nora` lane.
    The gate needs JSON mode and no reasoning; the tag lane needs neither
    and is tuned for the opposite. ADR 0006 is the standing measurement behind
    keeping them apart: a combo fans out per request and its upstreams can
    disagree about whether a request feature is supported, which on a
    fail-closed gate would read as a quiet room rather than as an outage.

    `max_tokens` is small ON PURPOSE. It is a ceiling on a verdict JSON, not a
    budget for prose; a gate that can emit paragraphs has room to start
    drafting the reply it must never draft.

    `shadow` is the review surface and the default when enabled: the gate runs
    on every closed window, and **only the `go` verdicts** are posted to
    `shadow_channel`. Nora is NEVER involved while shadow is on: no turn, no
    agent, whatever the score. The `no_go` verdicts are not delivered anywhere
    and are visible only as `nora_relevance_gate_total{verdict="no_go"}`, which
    keeps an undelivered verdict from being an unmeasured one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = "nora-gate"
    # DECLARED, never inherited from the route's default (the same rule
    # `_DebriefModelClient` carries).
    #
    # It PINS behaviour; it does not currently save tokens, because the combo's
    # members already reason none. The declaration is worth having since that is
    # a property of the combo's membership, which an operator can change without
    # touching this file.
    #
    # `none` because the gate is a classifier with a fixed two-value output.
    # Narrowed to the provider's closed set at composition (`_reasoning_effort`),
    # so a typo refuses to start rather than 400-ing on the first window that
    # closes.
    reasoning_effort: str = "none"
    # A ceiling on a verdict JSON (see the class docstring), sized to the gate's
    # own output, not carried over from another lane.
    max_tokens: int = Field(default=512, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=20.0, gt=0)
    # How many transcript rows the gate is shown. Its ONLY inputs are this
    # window and the room's standing; nothing else may be added here without
    # changing what the gate is.
    transcript_rows: int = Field(default=40, gt=0)
    shadow: bool = True
    # The review channel on `shadow_platform`, in THAT platform's own id shape:
    # `"<guild_id>/<channel_id>"` on discord, the same form policy.toml uses for a
    # Discord room. Required when `shadow = true`: a review surface with nowhere
    # to post would fail soft on every GO, which is a config error, not a runtime
    # condition.
    shadow_channel: str = ""
    shadow_platform: str = "discord"

    @model_validator(mode="after")
    def _shadow_needs_a_channel(self) -> RelevanceGateConfig:
        """Gated on `enabled` as well as `shadow`, unlike
        `ParticipationConfig`'s otherwise identical check.

        `shadow` DEFAULTS true here, so a deployment that turns the gate on
        without thinking about review gets review rather than live admission.
        That default makes the unconfigured instance `shadow=true,
        shadow_channel=""`, which the participation version of this check would
        refuse at import time. The relation being enforced is "a running review
        surface needs somewhere to post", and a disabled gate is not running.
        """
        if self.enabled and self.shadow and not self.shadow_channel:
            raise ValueError(
                "[relevance_gate] shadow = true requires shadow_channel — the "
                "review channel id on shadow_platform"
            )
        # Shape, not just presence; see the participation twin. Gated on
        # `enabled` for the reason above: a disabled gate posts nowhere, so its
        # unset (and therefore unshaped) channel is not a contradiction.
        if self.enabled and self.shadow:
            problem = _shadow_channel_shape_error(
                section="relevance_gate", platform=self.shadow_platform,
                channel=self.shadow_channel,
            )
            if problem is not None:
                raise ValueError(problem)
        return self


class StoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # NAMES the env var holding the PostgreSQL URL, never the URL itself (DEC-8,
    # `api_key_env`-style). The value is `postgresql+asyncpg://…` and is injected
    # by the host (docker compose from `.env`), so a credential never lands in a
    # committed file. The composition root is the one reader.
    database_url_env: str = "DATABASE_URL"
    # The on-disk root for media byte custody (DEC-43/47). A Postgres URL has no
    # directory, so the media root is its own key. Blobs live at
    # `data_dir / "media"`.
    data_dir: Path
    max_per_conversation: int
    context_window: int
    # How far PAST `context_window` a reply's quote may pull the transcript back,
    # when the quoted row would otherwise fall outside it. `recent(limit=...)` is
    # re-sized to `depth_to(quoted_ts) + quote_margin`, capped at
    # `context_window * quote_window_multiplier`; beyond the cap the reply renders
    # unthreaded. `quote_margin` is its own key rather than a literal because it is
    # a judgement about how much SURROUNDING context a resolved quote deserves, not
    # a mechanism constant.
    quote_margin: int = 10
    quote_window_multiplier: int = 3
    # How many of those rows the TAG prompt's `<group_transcript>` renders.
    # `context_window` stays the READ depth: `who_said` (DEC-146) keeps searching
    # all of it, and the quote resolver above sizes from it, so a lookup never
    # shrinks with the prompt. A quoted parent inside the read window is never cut
    # off: the render reaches back to it. The default (20, clamped to
    # `context_window`) lives in code, so a config.toml that omits the key gets it;
    # an explicit value above `context_window` is refused.
    prompt_window: int | None = Field(default=None, ge=1)

    @property
    def effective_prompt_window(self) -> int:
        """Rows the tag prompt shows: the configured `prompt_window`, or 20 when
        the key is omitted, never more than `context_window` (so a config that
        sets only a small `context_window` still loads). An EXPLICIT value above
        `context_window` is refused at load."""
        return min(20 if self.prompt_window is None else self.prompt_window,
                   self.context_window)
    # Whether the retention sweep runs AT ALL. `max_per_conversation` is the
    # window when it does; this is the switch, and the two are separate keys on
    # purpose: «keep everything» is not expressible as a number. Widening the
    # window to fake it keeps `prune_all`'s `row_number()` scan over the whole
    # `messages` table running every tick in order to delete nothing, and leaves a
    # figure in the file that reads like a policy nobody holds.
    #
    # Defaults TRUE, this file's convention that an absent key keeps the prior
    # behaviour, NOT a recommendation: with a small `max_per_conversation` a busy
    # group evicts its oldest message every time a new one lands. Anything that
    # wants to read history needs this OFF.
    #
    # `SqlRecordStore.prune_all` and the `_PrunableStore` port stay wired either
    # way; only the spawn in `__main__` is conditional, so toggling this is a
    # restart rather than a code change.
    prune_enabled: bool = True

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> StoreConfig:
        _resolve_context_field(self, "data_dir", info)
        if self.prompt_window is not None and self.prompt_window > self.context_window:
            raise ValueError(
                f"[store] prompt_window ({self.prompt_window}) cannot exceed "
                f"context_window ({self.context_window}): it is a slice of what "
                "was read, not a second read"
            )
        return self


class DescriberConfig(BaseModel):
    """One modality's describer: may it run, and through which model.

    Two fields and not one, because «no model named» and «switched off» are
    different states an operator reaches for differently, and both have to be
    expressible without deleting the other.

    `provider` is the port selector (the `[image_gen].provider` shape, DEC-137):
    `"omniroute"` (default) keeps the process-wide OmniRoute client, and `"kimi"`
    builds a DIRECT client to `api.kimi.com/coding/v1`. The direct lane exists
    because OmniRoute drops `video_url` content blocks; Kimi's own endpoint accepts
    base64 video data-URIs and `ms://` file references.

    `"opencode-go"` (DEC-197) builds a DIRECT client to OpenCode Go's own endpoint,
    `opencode.ai/zen/go/v1`, for MiMo v2.6 Flash, which reads video WITH its audio
    track and standalone audio, and which neither OmniRoute (drops `video_url`) nor
    `api.kimi.com` (does not serve it) can reach. That endpoint refuses any request
    without an `x-opencode-session` header (`400 MissingSessionID`), so its client
    carries one per request.

    `api_key_env` NAMES the env var holding the direct lane's key (DEC-8): the value
    never lands in config.toml. Empty falls back to the provider's own default,
    `KIMI_CODE_API_KEY` for `"kimi"`, `OPENCODE_GO_API_KEY` for `"opencode-go"`. A
    direct provider whose key does not resolve is a wiring gap: the modality answers
    `unavailable` («I couldn't check»), never a half-built client call and never a
    silent fallback to OmniRoute.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = ""
    provider: str = "omniroute"
    api_key_env: str = ""
    # ---- video chunking ----------------------------------------------------
    #
    # Only the video describer reads these; they live on the shared shape
    # because a second `[media.describe.video.chunks]` table would be a section for
    # two keys.
    #
    # A clip longer than `chunk_seconds` is read in chunks, up to `chunk_max`.
    # `chunk_max` is not merely a cost bound: when it binds, the cap is written
    # into `media_objects.provenance` AND said in the description, so a cut is
    # never silent.
    chunk_seconds: int = 90
    chunk_max: int = 10
    # A per-modality byte ceiling for the describer, overriding the shared
    # `media.vision_max_bytes` (which exists for still images: "no still image
    # worth describing is that large"). Video bytes are routinely far larger than
    # any still, so the image-sized ceiling would turn every real clip into
    # `too_large`; a modality that does not need more leaves this 0 and inherits
    # the shared ceiling.
    max_bytes: int = 0


class DescribersConfig(BaseModel):
    """The three modalities, keyed by the thing they read.

    Audio and video default to `enabled = False` with no model: the seam ships
    INERT. Image defaults ON because describing a picture is the baseline
    behaviour.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: DescriberConfig = DescriberConfig(enabled=True)
    audio: DescriberConfig = DescriberConfig()
    video: DescriberConfig = DescriberConfig()


class RustfsBlobConfig(BaseModel):
    """`[media.rustfs]`: an S3-compatible object-store backend for blob custody,
    alongside the filesystem default (DEC-136).

    Ships OFF (`enabled = false`): blobs go to `store.data_dir / "media"` unless
    this is turned on, a deliberate operator choice. When on AND the credentials
    resolve, the composition root builds a `RustfsBlobStore` instead of a
    `FilesystemBlobStore`: same port, same `BlobCipher` sealing the bytes before
    they leave the process, only WHERE the ciphertext lands changes. A
    misconfigured RustFS backend (missing endpoint/bucket, or an env var that does
    not resolve) degrades to the filesystem default rather than to custody OFF,
    matching `encryption_key_env`'s own posture.

    `access_key_env`/`secret_key_env` NAME the env vars (DEC-8), never inline
    credentials, and default to the RustFS service's own root credentials
    (`RUSTFS_ROOT_USER`/`RUSTFS_ROOT_PASSWORD`), so a deployment that runs RustFS
    needs no new secret, only new wiring.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    endpoint: str = ""
    bucket: str = "nora-media"
    access_key_env: str = "RUSTFS_ROOT_USER"
    secret_key_env: str = "RUSTFS_ROOT_PASSWORD"  # noqa: S105 — names an env var, not a secret


class MediaConfig(BaseModel):
    """Byte custody for attachments (DEC-43).

    **Every field is defaulted and the whole section is optional**, unlike most of
    this file: `extra="forbid"` means a required section would be a startup failure
    rather than a feature.

    There is deliberately **no filesystem path key here.** Media lives under
    `store.data_dir / "media"` by default: the record row and the blob it names are
    one custody boundary, backed up and deleted together. A media-specific root
    would let them be separated, and the transcript would then reference blobs on a
    volume nobody backed up. `rustfs` below is the one exception, and only because
    an object-store bucket is not a filesystem path in the first place (DEC-136).

    `enabled` defaults to True, but RETENTION for this data is an operator decision
    (DEC-43's open question), and this flag is the off switch. Off means
    attachments are recorded with a placeholder and no handle, and
    `nora_media_custody_total{status="not_configured"}` counts every one.

    `encryption_key_env` NAMES the environment variable holding the AES-256 key
    (base64 of 32 bytes), never the key itself (DEC-8, `api_key_env`-style). The
    bytes on disk are ciphertext (DEC-47), and the key is host-injected. Custody
    turns ON only when `enabled` is true AND that variable holds a valid key: a
    missing or malformed key degrades to custody OFF (the same `not_configured`
    state as `enabled = false`), never plaintext-by-accident and never a startup
    crash: media is a bonus and must not take the daemon down the way a missing LLM
    key does. Empty default = not configured.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    # Names the env var holding the base64-encoded 32-byte AES-256 key. Empty
    # means no key configured, which — like `enabled = false` — is custody OFF.
    encryption_key_env: str = ""
    # WHERE the (always-encrypted) bytes land — the filesystem default, or an
    # S3-compatible object store (DEC-136). See `RustfsBlobConfig`.
    rustfs: RustfsBlobConfig = RustfsBlobConfig()
    # Signal's own attachment ceiling. A refusal is a policy outcome with its
    # own metric label, never a crash and never a truncated blob.
    max_bytes: int = 100 * 1024 * 1024
    # Short, because this runs on the ingest path the SSE reader awaits — the
    # same argument `SignalRoster`'s 4 s makes against `directory`'s 10 s.
    timeout_seconds: float = 8.0

    # ---- reading a stored image back through MiMo-V2.5 (DEC-56) --------------
    #
    # The WRITE half above copies bytes into custody; these govern the READ half,
    # `media_fetch` (`nora.agent.tools`), which decrypts a held blob and describes it
    # via MiMo through the process-wide OmniRoute client. They are config, not
    # literals, for DEC-8's reason: the model, the token ceiling and the effort are
    # properties of the deployment's gateway, and the two SAFETY numbers below are
    # the ones a slow model would otherwise turn into a silent loop timeout.
    vision_model: str = "opencode-go/mimo-v2.5"
    # Generous, because MiMo reasons before it answers and a transcription of a
    # busy screenshot is long — but capped, so one description cannot eat a turn.
    vision_max_tokens: int = 1024
    vision_reasoning_effort: str = "low"
    # A ceiling on the bytes sent to MiMo, DELIBERATELY far below `max_bytes`
    # (100 MiB): a 100 MiB blob is a ~133 MiB base64 body, and no still image worth
    # describing is that large. Above this the tool answers `unavailable` with an
    # honest note rather than shipping a nine-figure request. 8 MiB is a ~10.7 MiB
    # body.
    vision_max_bytes: int = 8 * 1024 * 1024
    # A CEILING on the per-call vision timeout, not the timeout itself: the client
    # derives the real budget as the smaller of this and a fixed fraction of
    # `llm.timeout_seconds` divided by the WAVES one read runs in sequence
    # (`nora.infra.vision.derive_vision_timeout`), so it is provably shorter than the
    # loop's `asyncio.wait_for` budget and a hung MiMo call becomes this tool's
    # `unavailable` rather than the loop's silent `invocation_timeout` (DEC-56, the
    # way `factory.build_retry_policy` derives its backoff from the same timeout).
    #
    # A single-view read (every image, and every clip of `chunk_seconds` or less) is
    # ONE wave, so THIS value is what binds there.
    #
    # Precisely: the derived ceiling is `min(this, llm.timeout_seconds * 0.6 /
    # waves)`. This value binds exactly when it is no larger than the loop's own 0.6
    # share. A `waves`-window clip divides the LOOP SHARE, not this number: a
    # ten-window read of a 240 s loop gets 240 * 0.6 / 10 = 14.4 s regardless of what
    # is set here.
    vision_timeout_seconds: float = 20.0
    # The per-modality describers. `vision_model` above stays as the IMAGE model's
    # deprecated alias, see `describer_for`. The other `vision_*` knobs keep their
    # names and stay shared across modalities: each is cited by name in DEC-56's
    # reasoning and in `derive_vision_timeout`'s docstring, and only the MODEL has to
    # differ per modality.
    describe: DescribersConfig = DescribersConfig()

    # ---- whether STICKERS are among what gets read --------------------------
    #
    # Ships FALSE. This is a COST switch: the read is spawned and shares the
    # comprehension pass's wiring. Off excludes stickers from the read and NOTHING
    # ELSE: the identity ladder in `StickerReader` is custody, is wired whenever
    # `enabled` is on, and is what makes a repeat sighting cost zero platform RPC.
    sticker_descriptions: bool = False
    # ---- the spawned comprehension pass ------------------------------------
    #
    # ONE model read per medium at ingest, into title/description/content on
    # `media_objects`. `False` means handles in the body, nothing read, nothing
    # claimed, so the off state is a degrade rather than an error. One exception: a
    # voice note's words are written to `media_objects.content` by the custody stage
    # itself, inline, so they still reach the record and the transcript with this
    # off. What they do not reach with it off is Honcho, which gets the marker alone.
    # `sticker_descriptions` above is the sticker sub-gate: off excludes stickers from
    # the read and leaves the identity ladder (custody, and what makes a repeat
    # sighting free) alone.
    #
    # `comprehension_text_model` is the CHEAP TEXT pass: a voice note's
    # title/description over the STT transcript already in hand, and a video's over
    # its chunk run. Empty means no text pass: a voice note keeps its words with no
    # title (the words are the evidence, the title is a convenience) and a video
    # keeps its timecoded chunk run. Never a vision call: asking a model to listen
    # again to learn what Nora already knows is the one cost this avoids by
    # construction.
    comprehension: bool = True
    comprehension_text_model: str = ""
    # What the TRANSCRIPT may spend rendering one attachment's title +
    # description. The STORED comprehension is unbounded on purpose: the bound
    # belongs on the render, and `media_fetch` serves the rest. A voice note is
    # exempt: its detail is the words people said.
    comprehension_render_chars: int = 240
    # How long ONE media-join query may take before the render degrades to the bare
    # handles. Short on purpose: the degrade is cheap and correct (the row already
    # says `[image a3f2c1]`, so a join that does not happen claims nothing), while
    # `enrich` catches exceptions but not hangs, and a slow-but-alive Postgres would
    # hold every tag reply until the agent loop's own budget killed the turn as
    # `invocation_timeout`.
    join_timeout_seconds: float = 3.0
    # How many media reads may be in flight at once. Not a throughput knob but a
    # memory and quota one: each in-flight job holds a decrypted blob plus the base64
    # copy the request carries, against ceilings of 8 MiB for a still and 100 MiB for
    # a clip, and a video job runs up to `chunk_max` sequential model calls while it
    # holds them.
    comprehension_max_concurrent: int = 4
    # DEPRECATED and read by nothing, like the two below. The sticker read is one
    # subject among the others in the comprehension pass and shares `vision_max_tokens`
    # with every other modality. Kept for whoever tunes the shared ceiling: reasoning
    # tokens and answer tokens share ONE budget on the gateway's MiMo-v2.5, so a small
    # allowance is spent thinking with nothing left to emit (128 returned HTTP 200
    # with empty content; 512 was the lowest value that reliably succeeded).
    #
    # Declared but unread, for `sticker_description_max_chars`' reason below.
    sticker_prompt_max_tokens: int = 1024
    # DEPRECATED and read by nothing, like `sticker_prompt_max_tokens` above. The
    # sticker read is spawned, off the ingest path, so this no longer bounds a stall
    # that delays the next message. Declared but unread, for
    # `sticker_description_max_chars`' reason below.
    sticker_timeout_seconds: float = 20.0
    # DEPRECATED and read by nothing. It bounded a description rendered into the
    # durable BODY, and no description is rendered there: the comprehension lives in
    # `media_objects` and the render bound is `comprehension_render_chars` above. The
    # key stays declared because `extra="forbid"` is on every model in this file and
    # the deployment's `config.toml` is untracked: deleting the field would turn a
    # live file that still carries the line into a container that refuses to boot. It
    # goes when the operator has removed it from that file.
    sticker_description_max_chars: int = 200

    def describer_for(self, modality: str) -> DescriberConfig | None:
        """The describer for one modality, with the legacy alias resolved.

        `None` for a modality nothing here describes: the caller's cue to
        answer `unavailable` («I couldn't check»), NEVER `nothing_found`
        («there's nothing there»). A wiring gap reported as an empty result is
        the R-27 failure this repo names as an ERROR served as a PASS.

        `vision_model` is folded in HERE and nowhere else, so exactly one place
        knows the alias exists: a config naming only `vision_model` has to keep
        describing images (see
        `test_a_config_written_before_the_split_still_describes_images`). The
        explicit key wins when both are set, or the alias would pin the new key
        open and the split would be a no-op.
        """
        # Against the FIELD SET, not `getattr` alone: `self.describe` is a
        # pydantic model, so `getattr(self.describe, "model_dump")` returns a
        # bound method, truthy, not a `DescriberConfig`, and typed as one by an
        # annotation mypy cannot see through. The caller would then
        # `AttributeError` on `.enabled` at startup.
        if modality not in DescribersConfig.model_fields:
            return None
        describer: DescriberConfig | None = getattr(self.describe, modality, None)
        if describer is None:
            return None
        if modality == "image" and not describer.model:
            return describer.model_copy(update={"model": self.vision_model})
        return describer


class RouteConfig(BaseModel):
    """Google routing for the `route` tool (DEC-46).

    **The whole section is optional and every field defaults**, like
    `MediaConfig`: a `config.toml` without it keeps loading.

    `enabled` defaults to **False** because `route` is the one grounding tool
    that costs money and reaches a third party: it is off until an operator
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


class PreviewConfig(BaseModel):
    """Sender-generated link previews on Nora's outbound messages.

    **Optional, defaulted, `enabled` False**, the `[route]` shape, and off for a
    sharper reason: this is the one capability that makes Nora FETCH a URL that
    came out of a model, from her own network. DEC-73 deleted `web_fetch` and its
    whole SSRF apparatus because Tavily is never handed a URL; enabling this
    section is an operator deciding to take a bounded version of that exposure
    back, so it is a decision rather than a default. `nora.infra.preview`
    documents the guard and the one residual risk it does not close.

    No key, so unlike `[search]` there is nothing else gating it: `enabled` is
    the whole switch. Off, a reply containing a link goes out with no preview.

    `timeout_seconds` is small on purpose: this runs on the reply path with a
    human watching a 👀, so a dead host must cost a couple of seconds and a
    missing card, never a noticeable pause. `image` is separately switchable
    because it is the expensive half (a second fetch, and up to
    `image_max_bytes` base64'd into the JSON-RPC body) while the title and
    description are most of the value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    timeout_seconds: float = Field(default=3.0, gt=0)
    max_bytes: int = Field(default=262_144, gt=0)
    image: bool = True
    image_max_bytes: int = Field(default=262_144, gt=0)


class PresenceConfig(BaseModel):
    """The presence collector, DEC-54/DEC-55.

    **The whole section is optional and every field defaults, and `enabled`
    defaults to False**, like `[route]`: presence ships INERT. A `config.toml`
    without a `[presence]` section loads unchanged, and while off nothing
    constructs a collector or a Life360 client; `whereis` answers `unavailable`,
    exactly as `route` does keyless.

    Credentials are named, never inlined (DEC-8): `client_credential_env` names
    the env var holding the Basic client credential, and
    `username_env`/`password_env` the account login. The collector reads the
    VALUES from the environment; a committed file never carries them.
    `auth_token_env` names an OPTIONAL static bearer (the `LIFE360_AUTH_TOKEN`
    cookie from a life360.com browser login) for an account Life360 refuses the
    password grant to; when its value is set the client runs in token mode and
    the three login vars are not required (nora/infra/life360/client.py).

    `cadence_seconds` is the per-circle poll interval, roughly what the Life360
    app itself does (~60-120 s). `retention_days` is the prune window; see the
    field's own comment for the sizing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    client_credential_env: str = "LIFE360_CLIENT_CREDENTIAL"
    username_env: str = "LIFE360_USERNAME"
    password_env: str = "LIFE360_PASSWORD"  # noqa: S105 — names an env var, not a secret
    auth_token_env: str = "LIFE360_AUTH_TOKEN"  # noqa: S105 — names an env var, not a secret
    # ~60-120 s. `ge=1` guards a zero/negative that would busy-loop; the 60-120
    # band is guidance in config.example.toml, not a hard clamp, so an operator
    # can widen it deliberately without editing code.
    cadence_seconds: float = Field(default=90.0, ge=1.0)
    # Adaptive cadence. The poll interval is not fixed: it drops to
    # `cadence_moving_seconds` for a circle where ANYONE is driving, so `whereis`
    # stays fresh through a commute, and grows back toward `cadence_still_seconds`
    # as the circle sits still, so a parked circle is not polled every 20 s all
    # night. `cadence_seconds` remains the base for the auth-failure back-off, which
    # ALWAYS wins over the adaptive floor (a 429 lengthens the sleep regardless of
    # who is moving: ban safety is never traded for freshness). Both `ge`-guarded
    # against a busy-loop; a deployment that wants a fixed cadence sets moving ==
    # still == cadence_seconds.
    cadence_moving_seconds: float = Field(default=20.0, ge=1.0)
    cadence_still_seconds: float = Field(default=300.0, ge=1.0)
    # Sizing: 378 B/row x ~1,800 fixes/day x 720d ~= 490MB, which is cheap and keeps
    # Tier-4 forecasting training data from being pruned before it is usable.
    retention_days: int = Field(default=720, ge=1)
    # The collector's own metrics/health endpoint (liveness lives in the daemon). A
    # separate process, so a separate port from `obs.metrics.port`.
    metrics_port: int = 9101
    # How long the on-demand LIVE refresh (DEC-67) waits for a member's phone to
    # report a fresh position before `where_is` falls back to the stored fix. A
    # ceiling on the user's wait: the phone ping takes a few seconds, so ~8 s covers
    # a normal response without hanging the tag on an unreachable phone.
    refresh_timeout_seconds: float = Field(default=8.0, ge=0.5)
    # The collector's HTTP base URL, as NORA reaches it, for the on-demand LIVE
    # refresh (DEC-67). In compose that is the presence service by name, e.g.
    # "http://presence:9101". EMPTY (default) leaves `where_is` read-only (the
    # offline/eval shape). Nora reads this same `[presence]` block; the collector
    # serves the endpoint on `metrics_port`.
    refresh_url: str = ""
    # Names the env var holding the Google Maps key used to reverse-geocode a fix
    # Life360 returned WITHOUT an address, the same key the `route` tool uses.
    # OPTIONAL like everything else here: unset means no geocoder, and an
    # address-less fix stores the coordinate (never a crash). The value lives in the
    # env, never in this file (DEC-8).
    geocode_api_key_env: str = "GOOGLE_MAPS_API_KEY"
    # Which reverse-geocode backend to use: "google" (default) or "hkgov" (the
    # HK-gov ArcGIS locator, a switchable OPTION, never replacing Google). Both sit
    # behind the `ReverseGeocoder` port; the collector is unchanged by the choice.
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


class OnlineConfig(BaseModel):
    """The `online_now` tool's Steam half.

    **The whole section is optional and every field defaults, and `enabled`
    defaults to False**, like `[search]`/`[route]`: the tool ships INERT. A
    `config.toml` without an `[online]` section loads unchanged, and while off no
    Steam client is constructed; the `online`/`playing` arms answer
    `unavailable` (R-5), never a confident "everyone is offline". The `voice` arm
    is NOT gated here: it reads the live Discord client Nora already holds and
    needs no key, so it wires whenever the Discord platform is enabled.

    The key is named, never inlined (DEC-8): `api_key_env` names the env var
    holding the Steam Web API key; the composition root reads the VALUE from the
    environment and a committed file never carries it. An enabled section with the
    variable unset logs a warning and leaves the Steam half unwired: a wiring gap
    is `unavailable`, never a 403 per call.

    `timeout_seconds` bounds the one batched GetPlayerSummaries call a
    question costs; it sits on the reply path, so a dead api.steampowered.com
    must cost seconds and an `unavailable`, never a hung turn.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    api_key_env: str = "STEAM_WEB_API_KEY"
    timeout_seconds: float = Field(default=8.0, gt=0)


class ExtractionConfig(BaseModel):
    """Write-only Record perception; independent of the memory index read flag."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    # Same gateway and credentials as [llm]; never inherit its tag/reasoning lane.
    model: str = "ollamacloud/deepseek-v4.1-flash"
    messages_per_call: int = Field(default=20, ge=1, le=100)
    context_messages: int = Field(default=20, ge=1, le=100)
    min_message_age_seconds: float = Field(default=2, ge=0, le=60)
    timeout_seconds: float = Field(default=90, gt=0, le=300)
    max_tokens: int = Field(default=6000, ge=256, le=32000)

    @model_validator(mode="after")
    def _extraction_model_is_flash(self) -> ExtractionConfig:
        if self.enabled and not re.fullmatch(r"[^/]+/deepseek-v4\.1-flash", self.model):
            raise ValueError("extraction requires a configured DeepSeek V4.1 Flash route")
        return self


class MemoryIndexConfig(BaseModel):
    """The memory index. The index is written by every store's own write whatever this
    says; `refresh_seconds` is how often the nora process re-projects what those writes
    queued (`nora.app.runtime.memory_drain_loop`). Optional and defaulted, so a
    `config.toml` without the section loads.

    `read` (DEC-316) is the READ, and it selects ONE memory tool surface wherever
    tools are built: on, the memory graph's, `memory_search` over the index through
    `nora.agent.memory_search`, with no Honcho behind it and none of `ask_memory`,
    `search_memory`, `search_memory_workspace` or `who_said` offered; off (the default),
    the Honcho surface, and no `memory_search`. `about`, `remember` and
    `load_transcript` are on both.

    `embed` starts the embedding worker on the same drain tick: up to `embed_batch`
    nodes per tick through llama-server at `embedder_url` (the `embedder` compose
    service), and at most `embed_images_per_hour` images an hour (about 3.4 s of CPU
    each on the deploy host). `embedder_model` is `<gguf file name>@<sha256 prefix>`:
    the worker refuses to embed unless the server reports loading that file name
    (/props), and with the formatter's version it is the fingerprint stored with every
    vector, so changing it clears every stored vector at the worker's first tick and
    re-embeds them. The hash prefix is recorded, not checked: the server exposes no
    file hash. Off by default: the `embedder` service must be up first.

    While both are on, `memory_search` seeds from the question's vector (embedded by
    the same server, with the query instruction) and pins exact references ahead of it;
    the lexical seed runs only when the embedder fails or no node holds a current vector
    yet, and that answer is `partial`. With `read` on and `embed` off, every answer
    is that fallback's `partial`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    refresh_seconds: float = Field(default=60.0, gt=0)
    read: bool = False
    embed: bool = False
    embedder_url: str = "http://embedder:8080"
    embedder_model: str = "Qwen3-VL-Embedding-2B.Q4_K_M.gguf@7812c91cd1e0"
    embed_batch: int = Field(default=64, ge=1, le=1000)
    embed_images_per_hour: int = Field(default=120, ge=0)


class AnalyticsConfig(BaseModel):
    """The presence analytics substrate, the DERIVED layer (DEC-115).

    Ships INERT like `[presence]`/`[search]`: `enabled` defaults False, and while off
    `nora.analytics` builds nothing and writes nothing.

    Computation knobs ONLY, no consumer knobs: the substrate serves no particular
    consumer, and nothing here may name one.

    The three fire conditions: `safety_lag` (threshold, backlog of unprocessed FIXES
    not messages), `settled_seconds` (event, the arrival/departure boundary:
    `[language]`'s `quiet_seconds` shape applied to movement), `max_age_seconds` (fires
    at lag == 0 ON PURPOSE, because labelling is recency-gated).

    `batch_limit` is inert in steady state and binds only during a full re-derive;
    `backfill_batch_limit` (used once a subject's lag exceeds `backfill_threshold`) is
    what keeps that rebuild to minutes instead of hours, so that
    `stay_radius_m`/`stay_min_dwell_seconds` (both inherited, both unvalidated) can
    actually be tuned.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    tick_seconds: float = Field(default=120.0, gt=0)
    safety_lag: int = Field(default=600, ge=1)
    settled_seconds: float = Field(default=900.0, ge=0)
    max_age_seconds: float = Field(default=86_400.0, gt=0)
    batch_limit: int = Field(default=400, ge=1)
    backfill_batch_limit: int = Field(default=20_000, ge=1)
    backfill_threshold: int = Field(default=4_000, ge=1)
    grace_seconds: float = Field(default=60.0, ge=0)
    stay_radius_m: float = Field(default=120.0, gt=0)
    stay_min_dwell_seconds: float = Field(default=600.0, gt=0)
    cluster_eps_m: float = Field(default=100.0, gt=0)
    cluster_min_samples: int = Field(default=3, ge=1)
    min_nights: int = Field(default=3, ge=1)
    min_weekdays: int = Field(default=3, ge=1)
    label_freshness_days: int = Field(default=45, ge=1)
    # DEC-190: how close a Life360 saved place must be to claim a stay or a cluster.
    # Its own knob, never `cluster_eps_m`: the match band sits well below the nearest
    # non-match, so any value in 100-500 m is safe and 50 is unsafe (hence the floor).
    place_match_radius_m: float = Field(default=150.0, ge=100.0, le=500.0)
    # How far a non-driving fix may sit from an established stay's centre and still
    # extend it (time only, never position) -- `segment_stays`. One real stop's
    # resting fixes spread to 388 m; 400 cuts phantom trips and same-place split stays
    # against no wander, and 600 moves neither beyond noise
    # (scripts/replay_presence_corpus.py).
    stay_wander_radius_m: float = Field(default=400.0, gt=0)
    # Tier 3 (DEC-119): robust_z_score's own fail-closed floor: fewer than this many
    # prior stays at a place and no anomaly claim is made, ever
    # (nora.domain.mobility.robust_z_score's own docstring). 5 is a starting point, not
    # measured, like stay_radius_m/stay_min_dwell_seconds.
    anomaly_min_samples: int = Field(default=5, ge=2)
    # |z| above this increments nora_analytics_anomalies_total: a METRIC only; this
    # substrate emits typed facts, never a consumer-facing alert.
    anomaly_z_threshold: float = Field(default=3.0, gt=0)
    # Tier 5: corridor identity is a SIMILARITY match, not exact: two trips along the
    # same road on different days rarely produce byte-identical address sequences (a
    # slightly different fix cadence, a missing address on one fix). Neither value is
    # measured, like stay_radius_m/stay_min_dwell_seconds.
    corridor_max_edit_distance: int = Field(default=2, ge=0)
    corridor_min_samples: int = Field(default=3, ge=1)
    # Tier 5: how many not-yet-clustered trips one corridor-clustering pass may read
    # back and re-cluster (`MobilityStore.unclustered_named_trips_for_subject`).
    # `cluster_trips` is O(n^2) in an edit-distance metric that is itself expensive,
    # and the pass runs over an ACCUMULATED pool every cycle rather than one tick's
    # handful of fresh trips, so this one is sized from the cost curve: one subject's
    # clustering must stay under ~2% of a `tick_seconds=120` tick, which puts the
    # limit near 560 trips; 500 leaves headroom for a host that also runs Nora,
    # signal-cli, Postgres and Honcho, and for several subjects per tick.
    #
    # The pool DRAINS (a trip leaves it the moment it is linked to a corridor), so
    # this bound is a ceiling against pathological growth (a subject whose routes
    # never repeat), not the steady-state size. It is read newest-first, so
    # saturating it ages out the OLDEST never-matched trips rather than starving the
    # ones that just arrived.
    corridor_cluster_batch_limit: int = Field(default=500, ge=1)
    # Tier 5: `extract_trips`'s noise gate: a run of movement fixes shorter than this
    # between two stays is DROPPED rather than written as a trip, the same fail-closed
    # shape `min_dwell_ms` gives stays. `ge=2`, not `ge=1` (unlike
    # `cluster_min_samples`): a one-fix "trip" has no PAIR for
    # `haversine_distance_m`/`derive_speed_kmh` to run over, so its `distance_m` and
    # `avg_speed_kmh` are structurally 0.0, and admitting one would feed a fabricated
    # zero-speed sample into a corridor's samples (and `presence_corridor_speed`'s
    # percentiles).
    min_trip_fixes: int = Field(default=2, ge=2)
    # Trip settlement: the safety valve against a stuck `is_driving` signal. If
    # Life360 keeps reporting `True` continuously (a stale/stuck flag) while the
    # device is still actively sending fixes, a trip could otherwise never settle and
    # hold the watermark back forever. This is NOT protection against a device that
    # stops reporting entirely: the cap is computed from the held-back group's OWN
    # fix-to-fix span, which freezes the instant new fixes stop arriving, so a
    # genuinely dark device stalls without bound and this cap cannot fire; that case
    # self-heals once fixes resume, via the existing chunk-gap split. The default is
    # a JUDGMENT CALL, not a measured value: 3 hours covers a real long drive without
    # leaving a stuck signal unbounded.
    max_trip_duration_seconds: float = Field(default=10800.0, gt=0)
    # ── the unit rollups and their stimuli. Starting values, each a judgement to be
    # sized from the first weeks of rows.
    #
    # Two members' stays are one co-location when their centres are this close: the
    # stay radius (120 m) plus the resting scatter's median (~50 m), not the wander
    # radius, which would put neighbours in one flat.
    colocation_radius_m: float = Field(default=150.0, gt=0)
    # ...and they overlap at least this long: the stay's own min dwell, so a
    # pass in a lobby is not «together».
    colocation_min_overlap_seconds: float = Field(default=600.0, ge=0)
    # `response_window_seconds`: how long after one of her sends a reply, reaction or
    # edit still counts as answering it.
    response_window_seconds: float = Field(default=3600.0, gt=0)
    # ...and a member's message that does not quote her counts as answering her
    # latest send when it comes this soon after it, quotes no one else's message and
    # mentions no one else; later it counts only when it names her (a mention of her
    # account, or a word of `follow_names` in the body).
    follow_window_seconds: float = Field(default=120.0, ge=0)
    follow_names: list[str] = Field(default_factory=lambda: ["nora"])
    # A Rollup stimulus (co-location start, arrival, deviation) is raised
    # only for a row whose start is this recent on the Record's clock, so a
    # rebuild over months does not raise a stimulus per historical row.
    stimulus_horizon_seconds: float = Field(default=3600.0, gt=0)
    # «Silent in every room and at home for N days».
    deviation_silence_days: int = Field(default=2, ge=1)
    # DEC-245: at most this many deviation stimuli per person per local day, until
    # `rollup_read` lets a session read the rows behind them. A deviation past it is
    # counted (`outcome="capped"`) and not raised.
    deviation_stimuli_per_person_per_day: int = Field(default=1, ge=1)
    # «The collector's recorded polls succeeded» means polled at least this often
    # across the span: three still-cadence polls (300 s, `[presence]
    # cadence_still_seconds`). A longer gap is an outage.
    health_max_poll_gap_seconds: float = Field(default=900.0, gt=0)
    tz_name: str = "Asia/Hong_Kong"
    # The substrate's own metrics/health endpoint. 9100 is Nora's own
    # (`[obs.metrics]`), 9101 is the presence collector's
    # (`PresenceConfig.metrics_port`), 9102 is the drop service's public listener;
    # this is the next free port in the same numbering.
    metrics_port: int = 9103

    @model_validator(mode="after")
    def _corridor_pool_can_hold_a_corridor(self) -> AnalyticsConfig:
        """A cluster-batch limit below `corridor_min_samples` makes corridor birth
        arithmetically impossible: the pool could never hold enough similar trips for
        `cluster_trips` to call any of them a cluster.

        Clustering that could only ever see one tick's batch would mint zero corridors,
        silently. Refusing the config is where it is cheapest to catch: the failure it
        prevents has no log line and no metric of its own, only a counter that never
        moves."""
        if self.corridor_cluster_batch_limit < self.corridor_min_samples:
            raise ValueError(
                "analytics.corridor_cluster_batch_limit "
                f"({self.corridor_cluster_batch_limit}) is below "
                f"corridor_min_samples ({self.corridor_min_samples}) — no "
                "corridor could ever be created"
            )
        return self


class TavilyBackendConfig(BaseModel):
    """Tavily's own knobs: `[search.tavily]` (DEC-73, DEC-116, DEC-157).

    `api_key_env` NAMES the env var holding the Tavily key (DEC-8), never the key
    itself — shared by both verbs, one key. `search_depth` is `"basic"` (1
    credit) or `"advanced"` (2, richer extraction); `extract_depth` is the same
    two-tier choice for `/extract`. The size clamps and `max_results` are NOT
    here: they bound what re-enters the model's input and must not change when
    the operator swaps backends, so they live on `[search]` itself.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key_env: str = "TAVILY_API_KEY"
    url: str = "https://api.tavily.com/search"
    extract_url: str = "https://api.tavily.com/extract"
    search_depth: Literal["basic", "advanced"] = "basic"
    extract_depth: Literal["basic", "advanced"] = "basic"
    # Relevance floor: Tavily scores every result, and the score separates signal from
    # noise on real queries (opaque-ID lookups and current-state queries come back
    # near zero while real answers sit well above 0.15). Results below the floor are
    # dropped; if everything drops, the search reads `nothing_found` («I could not
    # find that»), never five junk links. 0.0 disables the floor (Tavily's raw ranking
    # only).
    #
    # TAVILY-ONLY (DEC-157): Exa's `/search` results carry NO `score` field, so there
    # is nothing for a floor to read. A `min_score` under `[search.exa]` would be a knob
    # with no call site: either a no-op or, fail-closed, a filter that drops every
    # result. Switching provider to `exa` therefore GIVES UP this filter and leans on
    # Exa's own ranking.
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)


class ExaBackendConfig(BaseModel):
    """Exa's own knobs: `[search.exa]` (DEC-157).

    `api_key_env` NAMES the env var holding the Exa key (DEC-8), never the key
    itself — one key, both verbs, exactly as Tavily's is. `url` is `POST
    /search`; `contents_url` is `POST /contents`, the SEPARATE endpoint behind
    `web_fetch` (Tavily's `/extract` one endpoint over), and like `/search` it
    is crawled from EXA'S servers, never Nora's — so DEC-73 (2)'s no-SSRF
    argument carries across the swap unchanged.

    `type` is Exa's search mode. `"auto"` is the balanced default; `"fast"` and
    `"instant"` trade depth for latency. The three `deep*` modes run multi-step
    research with synthesis and routinely outrun `[search] timeout_seconds`,
    which is sized for a single round trip on the reply path — raise it
    deliberately before choosing one, or the call is `unavailable` every time.

    There is no `min_score` here on purpose; `[search.tavily] min_score` says
    why.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key_env: str = "EXA_API_KEY"
    url: str = "https://api.exa.ai/search"
    contents_url: str = "https://api.exa.ai/contents"
    type: Literal[
        "instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning"
    ] = "auto"
    # How old a cached page `web_fetch` may be handed, in hours (Exa's
    # `maxAgeHours`): older than this and Exa crawls the page live. Unset, Exa serves
    # its cache however old. 0 always crawls (a few seconds, and a page may error
    # where its cache would have answered); -1 always uses the cache. A failed fresh
    # read falls back to the cache once and says so. `web_search` is not bounded: five
    # live crawls per search is too slow for the reply path, and its note says a
    # snippet can be out of date.
    fetch_max_age_hours: int = Field(default=24, ge=-1, le=720)


class SearchConfig(BaseModel):
    """Web search AND page extraction for the `web_search`/`web_fetch` tools:
    Tavily or Exa, one at a time (DEC-73, DEC-116, DEC-157).

    **Optional and every field defaults**, the `RouteConfig` shape. `enabled`
    defaults **False**: both tools reach a third party and spend credits, so both
    are off until an operator turns this on AND the selected backend's key is
    present. Off (or keyless) leaves the registry without a provider and
    `web_search`/`web_fetch` answer `unavailable` (R-5), never a silent empty.

    No self-hosted fetcher and no model-chosen-URL fetch over Nora's OWN network
    exists (DEC-73), so there is no SSRF surface here. `web_fetch` (DEC-116) uses a
    SEPARATE extraction endpoint on the same provider, which crawls from the
    provider's servers exactly as its `/search` does; Exa (DEC-157) crawls from
    Exa's servers on the same argument.

    **`provider` selects the backend; the shape below is the reason it can.**
    What lives HERE is provider-neutral: the switch, the result count, and the two
    size clamps that bound what re-enters the model's input on every subsequent
    call. Those must NOT move when the operator swaps backends, or a provider
    change silently becomes a token-budget change. What is one vendor's
    (endpoints, key name, depth/mode vocabulary, Tavily's relevance floor) lives in
    `[search.tavily]` / `[search.exa]`, so a third backend adds a sub-table and
    touches nothing else.

    `max_results` bounds the search count; `content_max_chars` clamps each search
    result's snippet; `extract_max_chars` clamps a fetched page's content, set
    higher than `content_max_chars` because a page is one whole document rather
    than a snippet, but still bounded: a tool result is re-sent on every
    subsequent model call, so what accumulates must be bounded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: Literal["tavily", "exa"] = "tavily"
    max_results: int = Field(default=5, ge=1, le=20)
    content_max_chars: int = Field(default=1500, ge=1)
    extract_max_chars: int = Field(default=3000, ge=1)
    timeout_seconds: float = 12.0
    tavily: TavilyBackendConfig = TavilyBackendConfig()
    exa: ExaBackendConfig = ExaBackendConfig()

    @property
    def api_key_env(self) -> str:
        """The env var naming the SELECTED backend's key (DEC-8).

        A property rather than a field: the composition root and its "enabled
        but keyless" warning both need «which secret is missing» without
        re-deriving the provider branch, and a real field would let a config
        set a key name for a backend it is not using.
        """
        return self.exa.api_key_env if self.provider == "exa" else self.tavily.api_key_env


class InstagramSummonsConfig(BaseModel):
    """The public Instagram summons: somebody in `identity.toml` tags her in a
    comment under a post, and she answers in the comments, once, through the
    instagram-bridge's guarded write.

    **Off, and in `dry_run`, by default, and those are two separate brakes.**
    `enabled` spawns the summons consumer at all (and lets the Instagram adapter
    queue the bridge's `summons` SSE events for it; off, each one is counted
    `disabled` and dropped). `mode = "dry_run"` runs the WHOLE turn and stores what
    she would have said in the ledger (state `staged`) without a POST; `mode =
    "live"` posts it. Live also needs the bridge's own `INSTAGRAM_COMMENT_WRITES=on`,
    which is a third brake Nora cannot flip.

    `poll_seconds` is DEPRECATED and read by nothing: a summons arrives as an SSE
    event, not a poll. It is still ACCEPTED, because the live `config.toml` sets it
    and this model forbids unknown keys; delete it from the live file, then from
    here.

    `max_age_minutes` is the only brake on a backlog: a card replayed long after it
    arrived (a bridge buffer, cards queued behind a checkpoint backoff) is recorded
    `stale` and never answered.

    `handle` is her own Instagram username, removed from the comment's text so
    the question she reads is the question, not her own name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    mode: Literal["dry_run", "live"] = "dry_run"
    poll_seconds: float | None = None  # DEPRECATED, read by nothing: see above
    max_age_minutes: float = Field(default=60.0, gt=0)
    handle: str = "noraexample"


class InstagramSocialConfig(BaseModel):
    """The Instagram social backend: `instagram_read`'s profile and story reads and
    `instagram_search`'s account search, via the instagram-bridge container.

    It serves the rows on `SocialRouter`'s exception list, `(instagram, story)`,
    `(instagram, profile)` and `(instagram, search)`: every Instagram read goes
    through the bridge, and a verb joins when its bridge route works end to end.
    Enabling it also composes `PersonsSubjectIndex`, because the profile and story
    rows take a PERSON and only the person index may resolve one to an igid (the
    search row takes free text and needs none).

    **Its own `enabled` rather than riding `[social] enabled`**, because the two
    backends fail and are provisioned independently: the OpenCLI path needs a systemd
    service and a headed browser on the HOST, this one needs a co-networked container
    holding an authenticated session. One flag for both would mean turning on stories
    to get profiles back, or losing X to a checkpointed Instagram session.

    `base_url` reaches a container on the private compose `nora` network, which
    publishes nothing to the host, hence a service name, not a loopback port. **This
    is deliberately NOT `[platforms.instagram]`'s address even though it names the
    same process.** The bridge serves both axes from ONE authenticated session (a
    second session would mean a second cookie jar and two concurrent logins for one
    account, the configuration most likely to provoke a checkpoint), but they share a
    SESSION, not a port: the messaging adapter cannot fetch a story and this client
    cannot send a message. Two config homes for one address keep two capabilities
    switched on separately, so a messaging outage cannot silently take the social
    axis with it, or the reverse.

    KEYLESS, like the OpenCLI path: there is no credential here because the
    session lives in the bridge's own volume.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str = "http://instagram-bridge:18082"
    # Comfortably under the tool's own budget and well over a reel read: the bridge
    # does a cheap `latest_reel_media` probe and then at most one `FetchReel`,
    # neither of which drives a browser, so nothing here needs the OpenCLI path's 90s.
    timeout_seconds: float = Field(default=30.0, gt=0)
    summons: InstagramSummonsConfig = Field(default_factory=InstagramSummonsConfig)

    @model_validator(mode="after")
    def _summons_needs_the_instagram_backend(self) -> InstagramSocialConfig:
        if self.summons.enabled and not self.enabled:
            raise ValueError(
                "[social.instagram.summons] enabled needs [social.instagram] "
                "enabled: a summons is read and answered over the same bridge"
            )
        return self


class LinkedInSocialConfig(BaseModel):
    """LinkedIn reads for `linkedin_read` and `linkedin_search` (DEC-201), over the
    SAME host-side OpenCLI bridge as X, so it needs `[social] enabled` too (refused
    at load otherwise: there would be no backend behind it).

    **Its own `enabled`**, off by default, because LinkedIn reads a
    logged-in account's view: the account must be a dedicated one with its
    profile viewing set to private (LinkedIn Settings, Visibility, Profile
    viewing options) before this is turned on. Off, both tools stay
    registered and answer that LinkedIn is not set up.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False


class SocialPollConfig(BaseModel):
    """The social stream's poller (design/cognition-loop/22-social-stream.md §3,
    §4): each member's Instagram account is probed, and a story or a list of
    posts and reels is read only when the probe says it changed. Every value is
    a starting value, sized after a week of `social_poll` rows.

    **Off by default.** It reads through the login Nora's Instagram messaging
    uses, so a checkpoint it provokes costs her Instagram presence; it needs
    `[social.instagram] enabled` (refused at load otherwise), the bridge it
    reads through.

    Each tick (`tick_seconds`) probes the members not probed for
    `ceiling_hours`, oldest first, and never a member probed (answered or not)
    within `floor_minutes`. After a week of observation, spare budget probes
    follow local hour-of-week posting rhythm, multiplied by `activity_multiplier`
    during current activity, with a probe and read per member reserved.
    `hourly_budget` caps the login's upstream calls in
    any hour, a probe counted at its source's cost and a read at one, with a
    pause of up to `jitter_seconds` before each call. A rejected or
    rate-limited call backs the login off for `back_off_start_minutes`,
    doubling to `back_off_max_hours` until a clean probe.

    With `[media] comprehension` on, a perception pass on the same tick reads
    each new item's media, spending at most `perception_daily_cap` MODEL CALLS
    per unit per local day (`[cognition] timezone`): one per image, and per
    video one per `[media.describe.video] chunk_seconds` window (up to
    `chunk_max`) plus its text pass. Back-filled items may use at most half of
    what polled items leave (§7). 0 perceives nothing.

    The back-fill (§7) pages each member's posts and reels back from their
    first list read, newest first, until `backfill_max_items` back-filled
    items, a page with nothing newer than `backfill_max_days`, or the end of
    the grid. It reads at most `backfill_pages_per_tick` pages a tick, one per
    member, after the tick's probes and due reads, and only while what is left
    of the hour's budget still covers a probe and a read of every member. 0
    pages turns it off. With `backfill_full_history`, the age and item limits
    do not apply: every opted-in member is walked to the grid end. Old bounded
    walks reopen once; known items are deduplicated, and a recorded grid end
    stays done across restarts. Budgets and failure handling stay in force.

    The re-walk (§5) pages once through each member's recorded items after
    their back-fill is done, so each item's tagged place is compared with
    what the network lists now. It reads at most `relist_pages_per_tick`
    pages a tick, one per member, after the back-fill and on its budget rule.
    0 turns it off.

    The recount (§5) reads a member's first page again once
    `recount_interval_hours` have passed since their last list read of any
    stage, so their newest items' likes, comments and reposts are re-read
    while they still move: after the back-fill and the re-walk, on their
    budget rule, one page a member a tick. 0 turns it off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    tick_seconds: float = Field(default=600.0, gt=0)
    floor_minutes: float = Field(default=30.0, gt=0)
    activity_multiplier: float = Field(default=3.0, ge=1)
    ceiling_hours: float = Field(default=6.0, gt=0)
    hourly_budget: int = Field(default=60, ge=1)
    back_off_start_minutes: float = Field(default=15.0, gt=0)
    back_off_max_hours: float = Field(default=6.0, gt=0)
    jitter_seconds: float = Field(default=10.0, ge=0)
    perception_daily_cap: int = Field(default=200, ge=0)
    backfill_full_history: bool = False
    backfill_max_days: float = Field(default=90.0, gt=0)
    backfill_max_items: int = Field(default=50, ge=0)
    backfill_pages_per_tick: int = Field(default=2, ge=0)
    relist_pages_per_tick: int = Field(default=2, ge=0)
    recount_interval_hours: float = Field(default=24.0, ge=0)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> SocialPollConfig:
        if self.floor_minutes > self.ceiling_hours * 60:
            raise ValueError(
                "[social.poll] floor_minutes must not exceed ceiling_hours: no member "
                "could be probed by the ceiling"
            )
        if self.back_off_start_minutes > self.back_off_max_hours * 60:
            raise ValueError(
                "[social.poll] back_off_start_minutes must not exceed back_off_max_hours"
            )
        return self


class SocialConfig(BaseModel):
    """READ-ONLY social reads: X for the `x_read` and `x_search` tools (DEC-201) via
    the host-side OpenCLI bridge (`python -m nora.infra.social`, deployed as
    `deploy/social-bridge/`), and Instagram profiles, stories and account search for
    `instagram_read` and `instagram_search` via the instagram-bridge container
    (`[social.instagram]` below).

    **Optional and every field defaults**, the `RouteConfig` shape. `enabled`
    defaults **False**: the tool reads third-party social content through an
    authenticated browser session, so it is off until an operator turns this on AND
    the bridge service runs on the host. Off (or the bridge down) leaves the registry
    without a provider and the X tools answer `unavailable` (R-5), never a silent
    empty.

    KEYLESS: there is no credential, so `enabled` is the whole gate (the `[preview]`
    shape). The read-only boundary does not live here at all: the bridge re-enforces
    its own `(site, command)` allowlist host-side before any `opencli` subprocess is
    spawned, so a write verb is unreachable whatever this config says.

    `bridge_url` is where Nora's container reaches the host-side bridge. The
    container is on the private compose `nora` network (NOT host networking),
    so it uses `http://host.docker.internal:19826` — the docker0 gateway the
    bridge also binds (see `deploy/social-bridge/`). `timeout_seconds` must
    exceed the bridge's own subprocess timeout (~75s, over OpenCLI's 60s
    browser timeout); `content_max_chars` clamps the retrieved text, because a
    tool result is re-sent on every subsequent model call and what accumulates
    must be bounded.

    **A story frame's bytes** (design/cognition-loop/22-social-stream.md §5),
    kept in custody by the poller, are deleted `story_media_keep_days` after
    the frame's `expires_at` once its media is both perceived and embedded,
    and `story_media_keep_max_days` after it whatever is done
    (`nora.app.social_expiry`). Its rows and its description stay; a post's
    and a reel's bytes are never deleted. **Off by default**
    (`story_expiry_enabled`): the pass deletes bytes for good, so it starts
    only when an operator turns it on, and turning it off stops it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    bridge_url: str = "http://127.0.0.1:19826"
    timeout_seconds: float = Field(default=90.0, gt=0)
    content_max_chars: int = Field(default=4000, ge=1)
    story_expiry_enabled: bool = False
    story_media_keep_days: int = Field(default=7, ge=0)
    story_media_keep_max_days: int = Field(default=30, ge=1)
    instagram: InstagramSocialConfig = Field(default_factory=InstagramSocialConfig)
    linkedin: LinkedInSocialConfig = Field(default_factory=LinkedInSocialConfig)
    poll: SocialPollConfig = Field(default_factory=SocialPollConfig)

    @model_validator(mode="after")
    def _linkedin_needs_the_opencli_bridge(self) -> SocialConfig:
        if self.linkedin.enabled and not self.enabled:
            raise ValueError(
                "[social.linkedin] enabled needs [social] enabled: LinkedIn is "
                "read through the same host-side OpenCLI bridge as X"
            )
        return self

    @model_validator(mode="after")
    def _the_story_bounds_are_ordered(self) -> SocialConfig:
        if self.story_media_keep_days > self.story_media_keep_max_days:
            raise ValueError(
                "[social] story_media_keep_days must not exceed story_media_keep_max_days: "
                "the hard limit is the latest a story frame's bytes are kept"
            )
        return self

    @model_validator(mode="after")
    def _the_poller_needs_the_instagram_backend(self) -> SocialConfig:
        if self.poll.enabled and not self.instagram.enabled:
            raise ValueError(
                "[social.poll] enabled needs [social.instagram] enabled: the poller "
                "reads through the same bridge"
            )
        return self


class WeatherConfig(BaseModel):
    """Current weather + forecast for the `weather` tool (DEC-72).

    **Optional and every field defaults**, the `RouteConfig` shape. UNLIKE the other
    grounding sections it can safely default `enabled=True` in a fresh config because
    the HK Observatory provider is KEYLESS, FREE, and leaks nothing (no user data
    egresses: the request carries no location), so there is nothing to gate on. It
    still ships `enabled=False` in `config.example.toml` for the "off until explicitly
    on" house rule; a real deployment flips it.

    `provider` selects the `WeatherProvider` adapter: `Literal["hko"]`, the `[presence]
    geocode_provider` precedent (also a `Literal`, not a bare string): a typo is a
    startup `ConfigError`, not a daemon that boots with `weather` silently dead, and
    adding a region extends the literal by one value. `url`/`lang` are the HKO Open
    Data endpoint and language; `lang="tc"` for a Cantonese group.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: Literal["hko"] = "hko"
    url: str = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
    lang: str = "tc"
    timeout_seconds: float = Field(default=8.0, gt=0)


class SpeechSttConfig(BaseModel):
    """Local speech-to-text: which endpoint, which model, which language, and a
    timeout.

    `[speech.stt]` is a first-class `[speech]` child, not a reuse of
    `[media.describe.audio]`: a voice note is conversation content, not an
    attachment description.

    Optional, defaulted, and INERT by default, the `[search]`/`[weather]` shape:
    `enabled = false` means Nora never calls out. When enabled, the voice-note
    ingest path calls `endpoint` with the OpenAI-compatible
    `/v1/audio/transcriptions` contract.

    `model` is one of the ids the STT service advertises on `/v1/models`
    (`deploy/stt/server.py`'s `MODEL_CONFIGS`): `noise-detection` (the default, the
    JackyHoCL Cantonese whisper) or `sensevoice`, the FunASR backup. The default is
    the engine the service itself defaults to. `language` hints the model's LID: NOT
    a BCP-47 tag, but one of the engines' own short codes (`auto`/`zh`/`en`/`yue`/
    `ja`/`ko`/`nospeech`); any other string is silently treated as `auto` by the
    model itself. `timeout_seconds` bounds ONE call (the whole ingest path waits for
    it; see the stage docstring).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    endpoint: str = "http://stt:8000"
    model: str = "noise-detection"
    language: str = "yue"
    timeout_seconds: float = Field(default=30.0, gt=0)


class SpeechEntryConfigBase(BaseModel):
    """Shared config for one `[speech.tts] sidecars` entry; see `SidecarEntry`."""

    model_config = ConfigDict(extra="forbid")


class SidecarEntry(SpeechEntryConfigBase):
    """ONE GUILD and the audio-controller that owns its voice connection.

    `guild` is a BARE Discord guild id. A `"<guild>/<channel>"` pair is refused with
    the reason spelled out: earlier keys held ids in this section (`voice_channel_ids`
    named channels, then `voice_guild_ids`), and an operator carrying either forward
    must SEE that the shape changed rather than discover it from a join that goes
    somewhere unexpected.

    `endpoint` must be non-empty: an entry with no controller behind it would
    admit a join the hub then cannot make. Leaving the guild out of the list
    is how you say "no voice here", and that fails closed."""

    guild: str
    endpoint: str

    @field_validator("guild")
    @classmethod
    def _bare_guild_id(cls, value: str) -> str:
        value = value.strip()
        if not value or "/" in value:
            raise ValueError(
                f"sidecars entry guild {value!r} must be a bare Discord guild "
                "id, not \"<guild_id>/<channel_id>\" — an entry maps a GUILD "
                "to the audio-controller that owns its voice connection "
                "(design/voice-channel/07 §12.5). Where a guild's SPEECH is "
                "recorded is policy.toml's `voice = true`, not here."
            )
        return value

    @field_validator("endpoint")
    @classmethod
    def _non_empty_base(cls, value: str) -> str:
        # STRIPPED, not merely checked: composition passes the value to the client
        # as loaded, so a padded " http://voice-sidecar:4600" would load clean and
        # die at the first request with a URL error instead of at load.
        value = value.strip()
        if not value:
            raise ValueError(
                "a sidecars entry has an empty endpoint. An entry with no "
                "controller behind it would admit a join the hub then cannot "
                "make — leave the guild out to mean 'no voice here', which "
                "fails closed."
            )
        return value


class SpeechTtsFormatConfig(BaseModel):
    """`[speech.tts.format]`: the speech formatter (`nora.agent.speech_format`): fits
    a spoken reply to the TTS voice after her words are final, on the spoken copy
    only.

    `mode`:
    - `off` (the default): the reply reaches MiniMax exactly as she wrote it,
      byte-identical.
    - `normalise`: emoji, markdown markers and stray symbols are removed
      deterministically; no model call, no added latency.
    - `model`: `normalise`, then one cheap model call may add pauses and
      interjection tags. Its output is spoken only when a code check finds
      every letter and digit of her reply unchanged and in order; anything
      else, a timeout included, speaks the normalised reply.

    `model` defaults to `nora-gate`; empty inherits `[llm] model`.
    `timeout_seconds` bounds the whole call and is time added before she is heard,
    so it is sized on measured latency. A reply with fewer than `min_chars` letters
    and digits, or more than `max_chars`, skips the model: too short to pace, or too
    long to format inside the timeout.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["off", "normalise", "model"] = "off"
    # The relevance gate's cheap combo, the alias the formatter is sized for. Empty
    # inherits `[llm] model`.
    model: str = "nora-gate"
    reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = "none"
    timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    max_tokens: int = Field(default=1024, ge=64)
    min_chars: int = Field(default=12, ge=0)
    max_chars: int = Field(default=200, ge=1)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> SpeechTtsFormatConfig:
        if self.min_chars > self.max_chars:
            raise ValueError(
                "[speech.tts.format] min_chars is above max_chars, so no reply "
                "would ever be formatted"
            )
        return self


class SpeechTtsConfig(BaseModel):
    """`[speech.tts]`: outbound speech, voice-channel TTS.

    Optional, defaulted, and INERT by default, the same `[search]`/`[weather]` shape
    as its STT sibling: `enabled = false` (or an empty `provider`) leaves the
    composition root with no speaker, and nothing about Nora's behaviour changes. The
    provider is MiniMax CN (`provider = "minimax_cn"`): `speech-2.8-turbo` with a
    native Cantonese `voice` and `language_boost` hard-set in the adapter. The key is
    read from the env var `api_key_env` NAMES (DEC-8), never from this file.

    `duck_gain`/`duck_ramp_ms` are the ducking law for speech-over-music: while
    speech plays, the music is scaled to `duck_gain` and back along a `duck_ramp_ms`
    linear ramp (a hard step is audible as a click). They are the mix's own
    transient gain; the listener's persisted `/music volume` is never touched by a
    duck cycle. The audio-controller currently HARD-CUTS speech over music instead of
    ducking, so these values are accepted and validated but nothing reads them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: str = ""
    voice: str = ""
    api_key_env: str = "MINIMAX_CN_API_KEY"
    # MiniMax voice_setting knobs (t2a_v2, speech-2.8-turbo): `speed` is in
    # [0.5, 2.0] (1.0 = natural), `pitch` semitones [-12, 12], `vol` in [0.1, 10]
    # (1.0 = source level), and `emotion` one of the model's spoken-emotion styles
    # (e.g. "fluent") when set. These ride in voice_setting on every synthesis; the
    # empty string keeps the field off the wire so the vendor's own default applies.
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    # `pitch` is the one INTEGER knob in voice_setting (MiniMax schema: `type:
    # integer` in [-12, 12]); a float default would change the wire from `0` to
    # `0.0`.
    pitch: int = Field(default=0, ge=-12, le=12)
    vol: float = Field(default=1.0, ge=0.1, le=10.0)
    # `emotion` one of the model's spoken-emotion styles (t2a_v2 enum:
    # happy/sad/angry/fearful/disgusted/surprised/calm/fluent/whisper). The adapter's
    # model is `speech-2.8-turbo`, which the vendor says does NOT support `whisper`,
    # so it is not accepted here: a value the vendor would refuse is a silent-voice
    # outage (every synthesis fails and the reply falls back to text). Empty keeps
    # the field off the wire, and the vendor says the model then matches the emotion
    # to the text itself.
    emotion: Literal["", "happy", "sad", "angry", "fearful", "disgusted",
                     "surprised", "calm", "fluent"] = ""
    # `voice_setting.text_normalization`: the vendor's own Chinese/English
    # normalization, which it says improves how digits are read at a small
    # latency cost. Off by default, the vendor's default.
    text_normalization: bool = False
    # `pronunciation_dict.tone`: static replacements applied to every
    # synthesis, each "original/replacement" (the replacement is text, IPA,
    # or tone-numbered romanisation in parentheses, e.g. "RV/(aa1)(wai1)").
    # Empty (the default) keeps the field off the wire. Validated below so a
    # malformed entry is refused at load, not by the vendor mid-turn.
    pronunciation: list[str] = Field(default_factory=list)
    # The speech formatter: see SpeechTtsFormatConfig.
    format: SpeechTtsFormatConfig = SpeechTtsFormatConfig()
    duck_gain: float = Field(default=0.25, gt=0, lt=1)
    duck_ramp_ms: int = Field(default=150, gt=0)
    # Each guild's audio-controller, as a LIST of `{guild, endpoint}` entries. The
    # sidecar IS the audio-controller, the sole owner of the Discord voice
    # connection.
    #
    # ONE CONNECTION PER GUILD, in ONE process. Discord's limit is one voice
    # connection per bot token PER GUILD, so a guild is the unit of ownership, but
    # that is a fact about CONNECTIONS, not about processes. The sidecar instantiates
    # its per-guild stack (`createCore`) once per guild and serves them all behind one
    # port, so a second guild is a line here and not a container. One connection's
    # lifecycle, outbox and desired-state file are single-connection by design, and
    # none of them is keyed by guild; each guild gets its own instance instead.
    #
    #     sidecars = [
    #       { guild = "1474…", endpoint = "http://voice-sidecar:4600" },
    #       { guild = "1497…", endpoint = "http://voice-sidecar:4600" },
    #     ]
    #
    # A LIST, NOT A TABLE KEYED BY GUILD, because of the config/example parity gate
    # (`tests/test_env_template.py`): it flattens nested tables to leaf paths, so a
    # guild-keyed table produces one key PER GUILD (`speech.tts.sidecars.<snowflake>`)
    # that `config.example.toml` cannot declare without writing real Discord
    # snowflakes into the tree, which that file's own `_MAY_DIFFER` note forbids. A
    # list is a single leaf, so the key set is stable however many guilds are
    # deployed.
    #
    # THIS LIST IS THE JOIN ALLOW-LIST: a guild is joinable exactly when a controller
    # exists for it, and two keys that must agree are a drift source rather than a
    # second opinion. Empty (the default) leaves the voice seam UNWIRED: a reply to a
    # voice channel is still an audit row, but no audio reaches the channel and every
    # `/music`/`/discord join` fails closed.
    #
    # WHERE a guild's speech is RECORDED is policy.toml's `voice = true`, not
    # here. The two are independent on purpose, but NOT free: a guild recorded
    # with no controller listed here still synthesizes a reply it cannot play
    # (see `DiscordAdapter._send_voice`, which refuses before paying for it).
    sidecars: list[SidecarEntry] = Field(default_factory=list)

    @property
    def sidecars_by_guild(self) -> dict[str, str]:
        """`{guild_id: endpoint}`, the shape every consumer wants, built from the
        list the parity gate needs. Duplicate guilds are refused at load, so this
        never silently drops one."""
        return {entry.guild: entry.endpoint for entry in self.sidecars}

    @field_validator("pronunciation")
    @classmethod
    def _pronunciation_entries_are_pairs(cls, value: list[str]) -> list[str]:
        """Each entry is exactly `original/replacement`, both sides non-empty.
        MiniMax splits on the slash; an entry without one, or with two, has no
        reading the operator can predict."""
        for entry in value:
            original, sep, replacement = entry.partition("/")
            if not sep or not original.strip() or not replacement.strip() or "/" in replacement:
                raise ValueError(
                    f"[speech.tts] pronunciation entry {entry!r} is not "
                    "'original/replacement' (one slash, both sides non-empty)"
                )
        return value

    @model_validator(mode="after")
    def _no_guild_is_listed_twice(self) -> SpeechTtsConfig:
        """Two entries for one guild is the operator having written two
        answers to "which controller owns this guild's voice connection".
        Picking one would send joins to a controller while events came back
        from the other. Refused at LOAD, while the operator is still holding
        the file, and NOT gated on `enabled` (`_lead_clears_the_tick`'s
        argument applies unchanged).

        There is deliberately NO cap on how many DISTINCT guilds may be
        listed. The single-guild bound belongs to a controller and each one
        still enforces its own; listing two here means two controllers, which
        is what multi-guild IS."""
        seen: set[str] = set()
        for entry in self.sidecars:
            if entry.guild in seen:
                raise ValueError(
                    f"[speech.tts] sidecars lists guild {entry.guild!r} twice — "
                    "one guild has exactly one audio-controller "
                    "(design/voice-channel/07 §12.5)"
                )
            seen.add(entry.guild)
        return self


class SpeechWakeConfig(BaseModel):
    """`[speech.wake]`: the wake gate's knobs.

    The wake container (`deploy/voice-wake/`) READS this section: compose
    bind-mounts config.toml read-only into it (as into nora) and
    `wakegate/config_toml.py` loads `[speech.wake]` plus `[speech.stt]`'s
    `endpoint`/`language`/`timeout_seconds` at startup, the same load-once-at-boot
    lifetime identity.toml and policy.toml have, so a change here needs a
    `voice-wake` restart.

    `stt_model` is the wake path's OWN engine choice, deliberately separate from
    `[speech.stt].model`: a voice note is transcribed off-line and takes the
    accurate `noise-detection` whisper, while a live 「喂 Nora」 utterance is
    latency-bound and takes `sensevoice`. The STT service pre-loads THIS model at
    boot.

    Ships INERT like its siblings: the container starts healthy-but-idle without
    `VOICE_GUILD_ID`/`VOICE_CHANNEL_ID` (which stay in .env: they are deployment
    identity, not a model knob), and `enabled` is the documented default for that
    posture. Nothing in the nora process reads this section.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    threshold: float = Field(default=0.5, ge=0, le=1)
    model_path: str = "/app/models/喂Nora.onnx"
    stt_model: str = "sensevoice"
    # End-of-speech. `vad_enabled` (default) asks Silero VAD "is there speech in
    # this 80 ms chunk?" and ends the utterance after `silence_tail_ms` of no-speech;
    # music, TV, fans and mic noise are loud-but-not-speech and do not hold the gate
    # open. `vad_enabled = false` falls back to loudness (`silence_rms`), which ducked
    # music defeats: a music floor above the RMS threshold stretches utterances and
    # swallows a second 「喂 Nora」 inside the window.
    vad_enabled: bool = True
    vad_threshold: float = Field(default=0.5, ge=0, le=1)
    silence_tail_ms: int = Field(default=800, gt=0)
    max_utterance_s: float = Field(default=10.0, gt=0)
    silence_rms: float = Field(default=400.0, ge=0)


class SpeechConfig(BaseModel):
    """`[speech]`: Nora's voice capability: STT (inbound, local) and TTS (outbound,
    cloud).

    Optional, defaulted, and INERT by default, the same convention as
    `[presence]`/`[search]`/`[weather]`/`[compute]`: nothing turns itself on because
    a section is missing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stt: SpeechSttConfig = SpeechSttConfig()
    tts: SpeechTtsConfig = SpeechTtsConfig()
    wake: SpeechWakeConfig = SpeechWakeConfig()


class HeartbeatConfig(BaseModel):
    """The background tick that fires due reminders (`nora.app.heartbeat`).

    Optional, defaulted, and INERT by default like `[presence]` and `[search]`:
    this is Nora's first unprompted-speech path, so it must never turn itself on
    because a section is missing.

    `timeout_seconds` is deliberately NOT `[llm].timeout_seconds`: that number
    bounds a turn a human is waiting on. Nobody is waiting on a fire turn, and
    cutting one short produces silence in a group nobody asked anything in.

    `tick_seconds` is the WAKE INTERVAL and nothing more. It is the BEST case
    for lateness (a reminder fires on the first tick at or after its `due_at`) and
    it is NOT an upper bound. Two things exceed it, both by design:

    * one tick runs its turn-groups SEQUENTIALLY and awaits each one, so a tick
      that finds N groups due can take up to N x `timeout_seconds`;
    * a group whose conversation slot is held by a tag is not queued or shed but
      DEFERRED to the next tick entirely, and a busy group can defer the same rows
      repeatedly; `nora_heartbeat_deferrals_total` is the counter that says it is
      happening.

    So the statement is "at or after `due_at`, never before".
    `[reminders].min_lead_seconds` is held above `tick_seconds` on purpose and is
    enforced by `Config._lead_clears_the_tick`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    tick_seconds: float = 60.0
    timeout_seconds: float = 120.0


class ComputeConfig(BaseModel):
    """The deferred-delivery poller (`nora.app.compute_poller`).

    Optional, defaulted, and INERT by default. No `ComputeBackend` adapter ships
    from this config and `enabled = true` builds nothing: the poller, the
    `compute_jobs` outbox and the port are the delivery path an adapter plugs into.
    The composition root builds the `ComputeJobStore` unconditionally regardless of
    `enabled`, and sweeps a stranded `FIRING` row at boot whether or not the feature
    is on: only the poller's own tick loop is gated.

    `poll_tick_seconds` is the poller's wake interval, the same "best case
    for lateness, not an upper bound" shape `[heartbeat].tick_seconds`
    describes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    poll_tick_seconds: float = 10.0


class ComputerLimitsConfig(BaseModel):
    """`[computer.limits]`: a RESERVED slot for per-command resource limits.

    The computer is a semi-trusted environment, so NO limit is applied: every field
    defaults to `None` and computerd ignores the field even when it is sent. The
    slot exists so that enabling limits is this config plus one computerd change
    (`deploy/computer/computerd.py`'s `run`), not a new wire field. Only the fields
    that are set are sent, and only when at least one is.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_seconds: int | None = Field(default=None, gt=0)
    max_processes: int | None = Field(default=None, gt=0)
    max_file_bytes: int | None = Field(default=None, gt=0)

    def as_pairs(self) -> tuple[tuple[str, int], ...]:
        return tuple(
            (name, value) for name, value in self.model_dump().items() if value is not None
        )


class ComputerSessionsConfig(BaseModel):
    """`[computer.sessions]`.

    A session is one task on the computer in a room, opened and continued by
    code on every computer call and expired by the sweeper. It expires when
    no computer call has happened for `idle_expiry_hours` and no tmux job in
    the room is running; a running job keeps it open up to
    `max_running_days` of no calls, after which it expires with the job
    flagged in the audit and left running. It also expires when every tmux
    session it was seen holding is gone and the room is idle. Expiry is a
    database state change and an audit row: it never ends a tmux job and
    never deletes a file, a profile or an audit row. An expired session is shown as
    lapsed for `lapsed_visible_hours`. `idle_after_minutes` is when an open
    session with no call and no running job is shown `idle` rather than
    `active`. The sweeper runs every `sweep_interval_seconds`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    idle_expiry_hours: int = Field(default=24, gt=0)
    max_running_days: int = Field(default=7, gt=0)
    lapsed_visible_hours: int = Field(default=24, gt=0)
    sweep_interval_seconds: float = Field(default=300.0, gt=0)
    idle_after_minutes: int = Field(default=30, gt=0)


class ComputerBrowseConfig(BaseModel):
    """`[computer.browse]`: `computer_browse`, a whole browser task handed to a
    sub-agent in the room's own browser (ADR 0022). INERT by default: the tool
    answers `unavailable` until `enabled`, and
    `[computer] enabled` must be on too (the browser is the computer's).

    `model` is the sub-agent's (empty: `[llm] model`); `max_steps` counts page
    actions, past which the agent is told to finish; `timeout_seconds` bounds
    the whole task; `turns`, `output_tokens` and `total_tokens` are its loop's
    three caps (`build_limits`); `read_chars` cuts one page's text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = ""
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_steps: int = Field(default=25, gt=0)
    timeout_seconds: float = Field(default=240.0, gt=0)
    turns: int = Field(default=40, gt=0)
    output_tokens: int = Field(default=12000, gt=0)
    total_tokens: int = Field(default=400_000, gt=0)
    read_chars: int = Field(default=6000, gt=0)


class ComputerContextConfig(BaseModel):
    """`[computer.context]`: the `<computer>` block, a deterministic window rebuilt
    each tag and fire turn from the
    live machine and the database, never a model's fold.

    Rendered only while `[computer] enabled` AND the room used the computer
    within `active_days` or has an open or lapsed session; every other room
    renders nothing. Each section is capped (`max_*`); files count only when
    changed within `recent_file_days`. `max_tokens` is the whole block's
    ceiling, estimated conservatively: past it the oldest lines are dropped
    first, with a line saying how many.

    The machine read must never hold a reply: it gets `read_timeout_seconds`,
    after which the block renders from the database alone and says the
    machine could not be read. A successful read is kept per room for
    `state_cache_seconds`, so consecutive turns do not each ask the VM.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_days: int = Field(default=7, gt=0)
    recent_file_days: int = Field(default=7, gt=0)
    max_open_sessions: int = Field(default=5, gt=0)
    max_lapsed_sessions: int = Field(default=3, ge=0)
    max_tmux: int = Field(default=5, ge=0)
    # Processes the room's user owns (a `nohup … &` job has no tmux session).
    max_processes: int = Field(default=5, ge=0)
    max_files: int = Field(default=8, ge=0)
    max_runs: int = Field(default=5, ge=0)
    # The newest Drop links the room made (`computer_export`), each with
    # the artifact_id a revision needs to keep the same link.
    max_links: int = Field(default=3, ge=0)
    max_tokens: int = Field(default=400, gt=0)
    read_timeout_seconds: float = Field(default=1.5, gt=0)
    state_cache_seconds: float = Field(default=30.0, ge=0)


class ComputerConfig(BaseModel):
    """Nora's computer (DEC-243, ADR 0019): the
    `computer_run`/`computer_read`/`computer_write`/`computer_edit` tools
    against computerd in an sbx microVM.

    Optional, defaulted, and INERT by default, the `[compute]` shape: every
    computer tool answers `unavailable` until an operator turns it on.
    Turning it on lets model-authored bash run on a VM on this host, which is
    an operator decision every time.

    `token_env` NAMES the env var holding computerd's bearer token (DEC-8);
    the value lives in `.env`. `nora.app.composition` refuses to start when
    `enabled` is true and that variable is unset or empty.

    `request_timeout_seconds` bounds every non-run call; a run waits for its
    own timeout (`run_timeout_seconds` by default, a model-supplied value
    clamped to `max_run_timeout_seconds`). `max_output_chars` bounds a run's
    stdout, `max_read_chars` one `computer_read`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str = "http://172.17.0.1:18765"
    token_env: str = "NORA_COMPUTER_TOKEN"  # noqa: S105 — names an env var, not a secret
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    run_timeout_seconds: int = Field(default=120, gt=0)
    max_run_timeout_seconds: int = Field(default=900, gt=0)
    max_output_chars: int = Field(default=8000, gt=0)
    max_read_chars: int = Field(default=20000, gt=0)
    # `computer_export` / `computer_import`: the largest file either moves. At most
    # computerd's own transfer cap (`MAX_TRANSFER_BYTES`, 50 MiB), which would refuse
    # a larger one anyway.
    export_max_bytes: int = Field(default=25 * 1024 * 1024, gt=0, le=50 * 1024 * 1024)
    import_max_bytes: int = Field(default=25 * 1024 * 1024, gt=0, le=50 * 1024 * 1024)
    limits: ComputerLimitsConfig = ComputerLimitsConfig()
    sessions: ComputerSessionsConfig = ComputerSessionsConfig()
    context: ComputerContextConfig = ComputerContextConfig()
    browse: ComputerBrowseConfig = ComputerBrowseConfig()

    @model_validator(mode="after")
    def _default_run_within_max(self) -> ComputerConfig:
        if self.run_timeout_seconds > self.max_run_timeout_seconds:
            raise ValueError(
                "[computer] run_timeout_seconds must not exceed max_run_timeout_seconds"
            )
        return self


class OtpConfig(BaseModel):
    """One-time codes sent in a direct message on Signal or WhatsApp
    (`nora.app.otp`): the substrate the portal's sign-in, and later adopting
    somebody, check a person against.

    Ships INERT (`enabled = false`): off, no service is built and the two
    `/app/v1/otp/*` routes are not mounted. A code goes only to somebody in
    `identity.toml`, whatever this section says.

    `secret_env` NAMES the env var holding the HMAC key the codes are hashed
    under (DEC-8), so the table cannot be turned back into codes; `api_token_env`
    names the bearer token the portal sends, because `/app/v1` otherwise trusts
    whoever reaches the port. `nora.app.composition` refuses to start when
    `enabled` is true and either is unset or empty.

    `ttl_seconds` is how long a code lives, `max_attempts` how many wrong codes
    kill it, `max_per_hour` and `max_per_day` how many codes one person can be
    sent in an hour and in a day. The day cap is what bounds a stranger who
    knows somebody's number: the messages that person receives, and the
    guesses (`max_attempts` per code) anybody gets at their codes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    secret_env: str = "NORA_OTP_SECRET"  # noqa: S105 — names an env var, not a secret
    api_token_env: str = "NORA_OTP_API_TOKEN"  # noqa: S105 — names an env var, not a secret
    ttl_seconds: int = Field(default=300, gt=0, le=3600)
    max_attempts: int = Field(default=5, gt=0, le=20)
    max_per_hour: int = Field(default=5, gt=0, le=60)
    max_per_day: int = Field(default=10, gt=0, le=200)


class RemindersConfig(BaseModel):
    """The anti-runaway caps on stored reminders, and the zone a recurrence keeps.

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
    # stated: `Config._lead_clears_the_tick` refuses the pair outright.
    min_lead_seconds: int = Field(default=120, ge=1)
    max_horizon_days: int = Field(default=365, ge=1)   # matches ReminderCaps
    # DEC-85. A recurring reminder is a CHAIN with only one `pending` row at a
    # time, so `max_pending_per_requester` counts a whole series as one — this
    # is the cap that bounds the series itself, in occurrences. Every series is
    # bounded: `RecurrenceRule.count` is required, so «forever» is not a state
    # the domain can represent, and this is the ceiling a request is clamped to.
    max_occurrences_per_series: int = Field(default=60, ge=1)
    # HOW OFTEN a series may repeat, at the fastest. Sub-day recurrence makes 「每分鐘」
    # expressible, and a room can only run ONE turn at a time: schedule faster than it
    # can serve and reminders do not arrive faster, they queue and starve interactive
    # tags.
    #
    # `ge=60` is the STRUCTURAL wall, not a taste: `[heartbeat] tick_seconds` is
    # how often she wakes, so below it the tick cannot deliver at all. Config
    # may raise this floor and may not lower it past the machine.
    min_interval_seconds: int = Field(default=300, ge=60)
    # An IANA zone NAME, never an offset: a recurrence keeps a LOCAL wall clock
    # («08:00, whatever the offset does that week»), which «+08:00» cannot
    # express. Validated at series construction — an unknown zone raises
    # `UnknownRecurrenceZoneError` rather than being guessed at.
    timezone: str = "Asia/Hong_Kong"


class StandingConfig(BaseModel):
    """The debrief stage's gate and its numbers.

    `NotesConfig`'s shape: the whole section is optional and every field
    defaults, and `enabled` defaults to **False**, because a new subsystem does
    not switch itself on. This one is the only background-ish tier whose work
    happens **inside the live reply slot**, so switching it on lengthens a hold
    that real people wait behind. Off, composition builds no `debrief.Wiring`,
    the stage returns without awaiting, and `<standing>` never renders.

    **`max_chars` is a knob AND the database has a ceiling, and they are two
    different jobs.** This value is the operating cap the sink refuses at,
    tunable without a migration, because the right size is a judgement about
    prompts. `standing_body_fits_the_ceiling_check` is the absolute bound no
    writer can pass, config included; a constraint is DDL and cannot be a knob.
    **A `max_chars` above the ceiling refuses to START** (`policy.toml`'s own
    shape) rather than failing at the first oversized fold.

    **Why 1200.** Standing is injected on the volatile tail of EVERY turn, so
    its size is a per-turn tax rather than a storage question: ~300 tokens in
    English, but approaching 1200 tokens in a CJK-heavy room, and rooms are mixed
    by default, so the cap is read against the denser case.

    **`fold_batch = 200`, the batch size.** The fold reads
    `since_including_nora(after_id=watermark, limit=fold_batch)`: the NEXT
    fold_batch rows since the last fold's row-id watermark, so every fold resumes
    where the last one ended. 200 is a batch size, not a recency window, so there
    is deliberately NO cap relationship to `[store] context_window`.

    **`timeout_seconds = 20`, and the number that matters is the SUM**, which is
    why the sum is a startup GATE (`max_slot_hold_seconds` below) and not a
    sentence here.

    The LATENCY gate is a different reading: `nora_debrief_seconds` p95 <= 5s,
    measured against the SECOND-QUEUED tag, because the cost never lands on the
    person being reviewed (who already has their reply) and always lands on
    whoever is behind them. `max_slot_hold_seconds` bounds the worst case; this
    bounds the typical one. Raise either only against its own reading.

    `model` empty means `[llm] model`, the same defaulting `[language]
    exchange_model` uses. A separate name is available because the fold is a
    different job from replying (one structured output, no tools, no audience)
    and may want a cheaper or a more literal model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    max_chars: int = Field(default=DEFAULT_STANDING_MAX_CHARS, ge=1)
    # `working_set`'s own two operating caps, `max_chars`'s siblings and the SAME
    # two-layer split: these are the judgement call, tunable without a migration;
    # `WORKING_SET_CEILING_ITEMS`/`_BYTES` (asserted below) are the DDL backstop no
    # config value may pass. A found option gets its own budget rather than sharing
    # `max_chars`' 1200 characters on purpose; see `DEFAULT_WORKING_SET_MAX_ITEMS`'s
    # docstring.
    working_set_max_items: int = Field(default=DEFAULT_WORKING_SET_MAX_ITEMS, ge=1)
    working_set_max_chars: int = Field(default=DEFAULT_WORKING_SET_MAX_CHARS, ge=1)
    # The fold's batch size. Every fold reads `since_including_nora(after_id=watermark,
    # limit=fold_batch)`: the NEXT fold_batch rows since the last fold's watermark.
    # It is a batch size, not a recency window, so it deliberately has NO cap
    # relationship to `[store] context_window`.
    fold_batch: int = Field(default=200, ge=1)
    timeout_seconds: float = Field(default=20.0, gt=0)
    # The bound on the stage's TWO STORE CALLS, the read of the prior position and
    # the write of the revised one. Both run while the per-conversation lock is HELD
    # and `create_sql_engine` is a bare `create_async_engine` with no
    # `command_timeout`, so without a bound a connection hanging mid-query during the
    # post-reply fold would wedge that conversation indefinitely.
    #
    # Small on purpose: a single-row primary-key read and a single-row upsert against
    # a local Postgres. If either takes seconds the database is in trouble and the
    # honest answer is `unavailable`, not a longer wait held in front of the next
    # person in the room.
    store_timeout_seconds: float = Field(
        default=DEFAULT_STANDING_STORE_TIMEOUT_SECONDS, gt=0)
    model: str = ""
    # The fold's reasoning budget, DECLARED rather than inherited: the tag path
    # passes `[llm] reasoning_effort` explicitly (`tier.reasoning_effort`), and so
    # does this one.
    #
    # **The value is deliberately NOT argued here.** The fold is integration (read
    # the prior position and the trace, write the revised one), not research, so a
    # low or absent budget is plausible, but its effort is a measurement, not a
    # setting. The decidable test is to run the same fold at several budgets over a
    # FIXED SET OF REAL TRACES and score whether the revised position (a) preserves
    # what the prior held and (b) integrates what the trace added; both are checkable
    # by comparison, no judge needed. `tag_trace` is retained so that set exists.
    #
    # So this key makes that test RUNNABLE, and empty means "inherit `[llm]
    # reasoning_effort`", so that switching the tier on changes one thing at a time.
    # A closed set, unlike `[llm] reasoning_effort`'s bare `str`. The OpenAI client
    # types this parameter as exactly these values, so a typo ("hgh") would reach the
    # provider and come back a 400 DURING a fold, inside the conversation's slot.
    # Typed, it is a boot failure instead, the refuse-to-start posture the rest of
    # this section uses. `""` is the seventh legal value and means "inherit `[llm]`".
    reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = ""


    # The WHOLE hold this stage can add to a conversation's slot, asserted at startup
    # rather than reasoned about in prose. `[llm] timeout_seconds` is a REQUIRED field
    # with no default, so a worst case argued in prose against an assumed value drifts;
    # the sum is checked instead.
    #
    # **What this does NOT cover, stated because the field name overclaims.** It
    # bounds every AWAIT the stage makes. `objective.objective_body(...)` is a
    # SYNCHRONOUS call that stats the bind-mounted `nora/dreamer` tree on every fold
    # (`[dreamer] reload = "mtime"`), so `asyncio.wait_for` cannot bound it and it is
    # not in this sum; because it blocks rather than awaits, it stalls the whole event
    # loop rather than one conversation's slot. Unmeasured, and a `git checkout` on
    # the host mid-fold is the trigger.
    #
    # The validator (`Config._the_standing_slot_hold_fits_its_budget`) sums
    # `timeout_seconds + STANDING_STORE_CALLS_PER_FOLD x store_timeout_seconds`, 40s at
    # the defaults (20 + 4x5); `[llm] timeout_seconds` is not in the sum because the
    # fold's model call never sees it. The validator's job is to make the arithmetic
    # impossible to get wrong silently; lower this and the startup refusal tells the
    # operator exactly which numbers no longer fit. The default (60) is headroom, not
    # a fit to any one stage: it admits a config with no `[standing]` section, and the
    # next stage added to this slot fails the GATE with a number to reason about
    # rather than failing every deployment that never set the key.
    max_slot_hold_seconds: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _max_chars_stays_under_the_database_ceiling(self) -> StandingConfig:
        if self.max_chars > STANDING_BODY_CEILING_CHARS:
            raise ValueError(
                f"[standing] max_chars = {self.max_chars} is above the "
                f"database ceiling of {STANDING_BODY_CEILING_CHARS} "
                f"(standing_body_fits_the_ceiling_check). The ceiling is DDL "
                f"and cannot be raised from config; every fold above it would "
                f"be refused by Postgres at write time."
            )
        return self

    @model_validator(mode="after")
    def _working_set_caps_stay_under_the_database_ceiling(self) -> StandingConfig:
        """`working_set_max_items`/`_max_chars`'s own version of the check
        above — refuse to START rather than fail at the first oversized fold,
        `StandingConfig`'s own posture applied to `working_set`'s pair of
        operating caps instead of `body`'s one."""
        if self.working_set_max_items > WORKING_SET_CEILING_ITEMS:
            raise ValueError(
                f"[standing] working_set_max_items = "
                f"{self.working_set_max_items} is above the database ceiling "
                f"of {WORKING_SET_CEILING_ITEMS} "
                f"(standing_working_set_fits_the_ceiling_check). The ceiling "
                f"is DDL and cannot be raised from config."
            )
        if self.working_set_max_chars > WORKING_SET_CEILING_BYTES:
            raise ValueError(
                f"[standing] working_set_max_chars = "
                f"{self.working_set_max_chars} is above the database ceiling "
                f"of {WORKING_SET_CEILING_BYTES} bytes "
                f"(standing_working_set_fits_the_ceiling_check). The ceiling "
                f"is DDL and cannot be raised from config."
            )
        return self


class CultureConfig(BaseModel):
    """The culture tier's gate.

    `NotesConfig`'s shape below: the whole section is optional and every field
    defaults.
    """

    model_config = ConfigDict(extra="forbid")

    #: Whether the culture adaptor runs at all.
    #:
    #: Default **False**, unconditionally: a new capability must not change what any
    #: deployed `config.toml` does on restart. Turning it on is a deliberate change
    #: (DEC-277).
    #:
    #: **What turning it on does: TWO things, under this one flag.** A background pass
    #: writes one short account of each room per wake into its own store, AND every
    #: runtime reads the room's current account as `<culture>`: the tag turn, every
    #: fire occasion and the character sessions (DEC-207). The relevance gate never
    #: does.
    #:
    #: There is no separate read switch.
    enabled: bool = False

    #: New human messages in a room that wake its fold at once (DEC-288). **200**: the
    #: busiest rooms reach it every few days, and every quieter room is woken by
    #: `stale_seconds` instead. A room is revised when it has said enough to revise
    #: from, not because a day passed (replacing DEC-185's daily clock).
    #: Keep it at or below `batch_limit`: then one fold drains what woke it. Above it,
    #: a backlog is walked in `batch_limit` slices that each fire, one account per
    #: slice, until `lag` drops under this.
    wake_batch: int = Field(default=200, ge=1)

    #: Seconds after a room's last fold at which ANY new human message wakes
    #: it, so a quiet room is still revised weekly. **604800** (7 days). A
    #: room with nothing new never fires (DEC-183: a quiet room's culture is
    #: accurate, not stale).
    stale_seconds: int = Field(default=604_800, ge=3600)

    #: Messages read per fold. **THE BATCH IS THE CORPUS** (the account is written
    #: from it), so this is not a throttle, it is how much of the room the model gets
    #: to see; a limit of 1 would starve the fold (DEC-182): one message a day is not
    #: a room's culture.
    #:
    #: 300 is a day of a busy room with headroom, against an input budget that
    #: comfortably holds it. `batch_limit` also caps how far one run advances the
    #: watermark (see `runtime.py`), and a NEW adaptor starts at 0, so at one batch
    #: per day a limit this size would take days to walk a long backlog. That is why
    #: the watermark is seeded at the record head on first run rather than at 0.
    batch_limit: int = Field(default=300, ge=1)


class PersonProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    update_every_messages: int = Field(default=100, ge=1)


class UnitProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    update_every_room_profile_changes: int = Field(default=5, ge=1)


class NoraProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    update_every_replies: int = Field(default=100, ge=1)


class ProfileConfig(BaseModel):
    """`[memory.profile]`: the Profile sessions, in shadow (design/unified-memory/45 §4,
    00 «Sequence» step 6). One Dreamer session per (unit, listed person), woken
    like the person's Character session (`[memory.profile.person] update_every_messages` and
    `[memory.profile] update_every_seconds`) and at once by a `told` line a turn wrote about them,
    writing typed lines through `profile_write`.

    **Off by default.** On, the sessions write `profile_line` and nothing reads
    it: no prompt changes until the switch (step 8). The first session of each
    person is the backfill (35 §4). Needs `[dreamer] enabled` and `[culture]
    enabled` (it reads the Character head and the room cultures).
    """

    model_config = ConfigDict(extra="forbid")

    write: bool = False

    #: THE SWITCH (45 §5, 00 «Sequence» step 8). On, prompts read the Profile
    #: accounts through `read_profile` and render `<profile>` blocks INSTEAD of
    #: `<peer_card>`, `<peer_history>`, `<people_in_window>`, `<culture>`,
    #: `<unit_culture>`, `<character>` and `<self>`. Off — the default — is the
    #: shadow: the sessions keep writing and every prompt renders the Honcho and
    #: prose-culture blocks it always has, so the flag is the whole rollback.
    #: Ignored unless `write` is also on (no store is wired without it).
    read: bool = False

    # Separate from the room culture fold's stale_seconds; same seven-day default.
    update_every_seconds: int = Field(default=604_800, ge=3600)
    person: PersonProfileConfig = PersonProfileConfig()
    unit: UnitProfileConfig = UnitProfileConfig()
    nora: NoraProfileConfig = NoraProfileConfig()


class ComprehendConfig(BaseModel):
    """Shadow Cognition steps one and two; enabled only with the graph switch."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    turns: int = Field(default=8, ge=1)
    total_tokens: int = Field(default=120_000, ge=1)
    output_tokens: int = Field(default=12_000, ge=1)
    timeout_seconds: float = Field(default=240, gt=0)
    live_concurrency: int = Field(default=1, ge=1)
    historical_concurrency: int = Field(default=1, ge=1)
    preview_tokens: int = Field(default=1500, ge=32)
    web_searches: int = Field(default=3, ge=0, le=3)
    page_fetches: int = Field(default=3, ge=0, le=3)


class GraphConfig(BaseModel):
    """Graph admission switch; storage and arrival producers are phase one's."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    comprehend: ComprehendConfig = ComprehendConfig()


class MemoryConfig(BaseModel):
    """Nora's extraction, memory index and Profile settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    graph: GraphConfig = GraphConfig()
    extraction: ExtractionConfig = ExtractionConfig()
    index: MemoryIndexConfig = MemoryIndexConfig()
    profile: ProfileConfig = ProfileConfig()


class LessonsConfig(BaseModel):
    """`[lessons]`: the Lessons session: one Dreamer session per unit, woken by
    closed `response` episodes, writing lessons through `lesson_write` and
    `lesson_retire`.

    **Off by default, and off is the safe reading.** On, the session writes lessons
    into its store; `render` decides whether they reach a prompt. Needs `[dreamer]
    enabled`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False

    #: Closed episodes in a unit (her sends with what followed, `unanswered`
    #: included) that wake its session at once. **50**; every quieter unit is woken by
    #: `stale_seconds` instead. A lower value would induce lessons from too few days
    #: of one unit.
    wake_batch: int = Field(default=50, ge=1)

    #: Seconds after a unit's last session at which ANY new closed episode
    #: wakes it, so a quiet unit is still read weekly. **604800** (7 days), the
    #: Character sessions' rule; a unit with nothing new never fires.
    stale_seconds: int = Field(default=604_800, ge=3600)

    #: A lesson not re-evidenced (written or reinforced) for this long is
    #: retired by code. **5184000** (60 days); unmeasured.
    horizon_seconds: int = Field(default=5_184_000, ge=86_400)

    #: Closed episodes one session is handed, the newest. Twice `wake_batch`,
    #: so a stale wake after a long gap reads two batches, not the whole gap;
    #: what is past it is consumed, as a Character session's backlog is.
    batch_limit: int = Field(default=100, ge=1)

    #: Render the unit's active lessons as `<lessons>` on tag and fire turns, after
    #: `<self>`. **Off by default**: it changes what she reads on every turn of a unit
    #: that has lessons, so it is switched on after its before/after eval, not by the
    #: deploy that ships it. Independent of `enabled`: lessons already written keep
    #: rendering if the session stops.
    render: bool = False

    #: The correction fast path: a message that speaks to her and reads as an
    #: instruction about how she acts wakes the unit's session once it is this many
    #: seconds old, instead of waiting for `wake_batch` episodes. **600** (10
    #: minutes): long enough for the conversation around it to settle, short enough
    #: that the lesson lands the same evening. `0` turns the fast path off.
    correction_quiet_seconds: int = Field(default=600, ge=0)

    #: The doctrine check's model: a separate call that reads each lesson against the
    #: law before it is written. `""` is `[llm] model`, the tag model, so the judge is
    #: never the Dreamer model that wrote the lesson.
    check_model: str = ""
    #: Its reasoning effort; `""` is `low`. A verdict over the law's rules is a
    #: judgement.
    check_reasoning_effort: str = ""
    #: Its wall clock and output ceiling. The answer is one JSON object with a
    #: sentence in it; 2048 leaves a reasoning model room before it answers.
    check_timeout_seconds: float = Field(default=60.0, gt=0)
    check_max_tokens: int = Field(default=2048, ge=128)


class NotesConfig(BaseModel):
    """The note tier's gate. `enabled` defaults to **False**, because a new
    subsystem does not switch itself on. On: the store is built, `<notes>`
    renders whatever is standing on a tag turn and its tools are reachable;
    the Cognition session (`[cognition]`) is the background writer that
    creates, revises, retires and merges notes. Off: composition wires
    `notes=None`, and no block is emitted.

    **There is deliberately no render cap here.** A note silently missing from
    the index is invisible to her, and invisible is indistinguishable from
    absent (R-27); the prompt budget is paid by the per-entry caps in
    `nora.domain.notes` and by retiring settled notes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False


class XaiImageGenConfig(BaseModel):
    """`[image_gen.xai]`: the xAI adapter's own knobs (DEC-137).

    `model` / `quality_model` mirror the same names Hermes' `image_gen/xai`
    plugin uses (`grok-imagine-image` / `grok-imagine-image-quality`).
    `refresh_token_env` NAMES the env var holding the BOOTSTRAP OAuth
    refresh token `nora xai-login` writes (DEC-8) — the value never lands in
    config.toml; the rotating token after it lives in the state file.

    `keepalive` / `refresh_interval_hours`: the daemon refreshes this login
    on a schedule whenever `[image_gen] enabled` and the token is set,
    whichever provider is selected (DEC-308, `nora.app.oauth_keepalive`).
    The interval is bounded to 1-72 h: shorter re-asks a refused login
    every few minutes, longer delays the staleness alert (twice the
    interval) that flags a dead login.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = "grok-imagine-image"
    quality_model: str = "grok-imagine-image-quality"
    timeout_seconds: float = 90.0
    refresh_token_env: str = "XAI_OAUTH_REFRESH_TOKEN"  # noqa: S105 — names an env var, not a secret
    #: References per call. Unset is xAI's documented limit (5, DEC-307); a value
    #: may only lower it. The bound is `XAI_MAX_REFERENCE_IMAGES`, pinned by a test.
    max_reference_images: int | None = Field(default=None, ge=1, le=5)
    keepalive: bool = True
    refresh_interval_hours: float = Field(default=24.0, ge=1, le=72)


_GptImageQuality = Literal["low", "medium", "high", "auto"]


class OpenAICodexImageGenConfig(BaseModel):
    """`[image_gen.openai_codex]`: gpt-image through the ChatGPT (Codex)
    subscription lane (DEC-304).

    `model` is the image model the `image_generation` tool runs;
    `responses_model` is the text model that hosts that tool on the Codex
    backend. `standard_quality` / `high_quality` map the port's
    `quality="standard"` / `"quality"`. `refresh_token_env` NAMES the env var
    holding the BOOTSTRAP refresh token `nora openai-codex-login` writes
    (DEC-8); the rotating token after it lives in the state file.
    `keepalive` / `refresh_interval_hours` as `[image_gen.xai]` (DEC-308).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(default="gpt-image-2", min_length=1)
    responses_model: str = Field(default="gpt-5.5", min_length=1)
    standard_quality: _GptImageQuality = "medium"
    high_quality: _GptImageQuality = "high"
    timeout_seconds: float = Field(default=180.0, gt=0)
    # Names an env var, not a secret.
    refresh_token_env: str = Field(default="OPENAI_CODEX_REFRESH_TOKEN", min_length=1)
    #: References per call. Unset is OpenAI's documented limit for gpt-image (16,
    #: DEC-307); a value may only lower it. The bound is `CODEX_MAX_REFERENCE_IMAGES`.
    max_reference_images: int | None = Field(default=None, ge=1, le=16)
    keepalive: bool = True
    refresh_interval_hours: float = Field(default=24.0, ge=1, le=72)


class ImageGenConfig(BaseModel):
    """The image-generation tool's gate (DEC-137).

    `enabled` defaults **False**, the same reasoning every other optional
    subsystem's gate gives: a new capability does not switch itself on. A
    misconfigured lane degrades the tool to `unavailable` rather than crashing
    the daemon (the DEC-136 RustFS posture).

    `provider` is the port selector: the composition root builds the
    `ImageGenerator` implementation the name selects (`xai`, DEC-137, or
    `openai_codex`, DEC-304); the model-facing tool never names a provider.
    It stays a plain string: an unknown name degrades the tool to
    `unavailable` with a logged warning, it does not stop the daemon.

    `allow_reference_images` is the privacy gate for the edits API: reference
    images (a friend's photo) upload to a third-party cloud, so the default
    is OFF, and when ON the adapter still enforces that every reference is a
    Nora-held custody handle (never an external URL).

    Credential env vars follow the DEC-8 `api_key_env`-style convention:
    config names the env var, the host's `.env` holds the secret.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: str = "xai"
    allow_reference_images: bool = False

    xai: XaiImageGenConfig = XaiImageGenConfig()
    openai_codex: OpenAICodexImageGenConfig = OpenAICodexImageGenConfig()


class MiniMaxCnVideoGenConfig(BaseModel):
    """`[video_gen.minimax_cn]`: the MiniMax CN adapter's own knobs.

    `model` defaults to `MiniMax-H3`; `MiniMax-H3-Max` is the fallback if the live API
    refuses H3 at 15 s, a config change.
    `api_key_env` NAMES the env var holding the CN plan key (DEC-8), the same one
    `[speech.tts]` reads; the value never lands in config.toml.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = "MiniMax-H3"
    api_key_env: str = "MINIMAX_CN_API_KEY"
    host: str = "https://api.minimaxi.com"
    timeout_seconds: float = Field(default=45.0, gt=0)


class VideoGenConfig(BaseModel):
    """The `video_create` tool's gate.

    `enabled` defaults **False**: a clip of 15 s at 480P is estimated at about 11% of the
    weekly quota the CN plan shares with her speech and Hermes, so it never switches itself
    on. On, it needs `[compute] enabled` (the poller that delivers the clip; refused at load)
    and the key named by `[video_gen.minimax_cn] api_key_env` (absent: the tool is withheld
    and a warning logged, never a crash).

    One model, one resolution, one duration: `resolution` is `480P` only. `ratio` is
    explicit, never `adaptive`: the live API refuses `adaptive` for a reference task
    (`aspect ratio "adaptive" is not supported for task ref2va`), so it is refused here for
    every task. `max_clips_per_room_per_day` is the hard cap `video_create` checks in code
    before a create: clips one room (platform plus conversation) may start per local day,
    counted from `compute_jobs`; the default of one is a placeholder until a 15 s clip's cost
    is measured. The old key `max_clips_per_day` is refused at load, naming the new one.
    `allow_reference_images` is the privacy gate `[image_gen]` has: a reference leaves the box
    for a third-party cloud. `allow_reference_video` and `allow_reference_audio` are the same
    gate for a clip and for a voice note or other audio someone sent, each off by default and
    separate, since a voice note sent as a reference lends that person's voice to the clip.
    `reference_seconds` is how much clip, and separately how much audio, one create may send,
    shared by the references of that kind; each is cut to its share. 6 to 15: the provider
    refuses a reference under 2 s (so three references need 6) and caps each kind at 15 s in
    total, and a clip's seconds are billed beside the output's (audio is not billed).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: Literal["minimax_cn"] = "minimax_cn"
    resolution: Literal["480P"] = "480P"
    duration_seconds: int = Field(default=15, ge=4, le=15)
    ratio: str = "3:4"
    allow_reference_images: bool = False
    allow_reference_video: bool = False
    allow_reference_audio: bool = False
    reference_seconds: float = Field(default=6.0, ge=6.0, le=15.0)
    max_clips_per_room_per_day: int = Field(default=1, ge=0)

    minimax_cn: MiniMaxCnVideoGenConfig = MiniMaxCnVideoGenConfig()

    @model_validator(mode="before")
    @classmethod
    def _old_cap_key_is_refused(cls, data: object) -> object:
        if isinstance(data, dict) and "max_clips_per_day" in data:
            raise ValueError(
                "[video_gen] max_clips_per_day was renamed max_clips_per_room_per_day "
                "(2026-10-09): the cap now counts each room's clips separately, not all "
                "rooms together. Rename the key; it is not read under the old name"
            )
        return data

    @field_validator("ratio")
    @classmethod
    def _ratio_is_explicit(cls, value: str) -> str:
        text = value.strip()
        if text.lower() == "adaptive":
            raise ValueError(
                '[video_gen] ratio = "adaptive" is refused: the live API rejected it for a '
                'reference task on 2026-10-02. Name the ratio, e.g. "3:4"'
            )
        left, sep, right = text.partition(":")
        if not (sep and left.isdigit() and right.isdigit() and int(left) and int(right)):
            raise ValueError(f'[video_gen] ratio must be "W:H", e.g. "3:4"; got {value!r}')
        return text


class MusicConfig(BaseModel):
    """The Discord music bot's gate.

    `enabled` defaults **False**, same reasoning every other optional
    subsystem's gate gives: a new capability does not switch itself on. Only
    load-bearing when `[platforms.discord] enabled` is also true —
    `nora/app/composition.py` does not build a `MusicPlayerManager` for a
    platform that isn't running, but this section itself does not know or
    care which platform is enabled.

    `idle_timeout_seconds` is the disconnect timer for an empty queue with
    nothing playing. The other timer (voice channel emptied of humans) has no
    config, since there is no grace period to size for an event that already
    means "leave now".

    `crossfade_seconds` — accepted but EFFECTIVE ZERO under the audio-owner
    controller: there is no local playback to blend, and advancement happens on
    ACTUAL track completion (the controller's `track-finished` event). The
    controller surfaces the value in its `/voice/status` as `crossfade_seconds: 0`.

    `autoplay_enabled` — False (default): the queue empties, nothing next, leave
    after `idle_timeout_seconds`. True has `MusicPlayer._advance` seed one
    YouTube "Mix" recommendation from whatever just finished before falling back
    to idle.

    `autoplay_prewarm_seconds` — 0 (default) means the next autoplay pick is
    decided after the current track ends. Above 0, `MusicPlayer` resolves the
    next pick (its Mix lookup AND its playable stream) while the current track
    is still audible, so the gap between track-end and the next track starting
    is instant rather than two fresh network calls. Only meaningful when
    `autoplay_enabled` is also True; ignored otherwise."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    idle_timeout_seconds: float = 300.0
    crossfade_seconds: float = 0.0
    autoplay_enabled: bool = False
    autoplay_prewarm_seconds: float = 0.0


class DoctrineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dir: Path
    reload: Literal["mtime", "startup"]
    strict: bool
    # BCP 47 language tag (RFC 5646): the deployment's language as a deployment
    # fact, rendered into every tag's runtime_context so the model is told from
    # OUTSIDE what to speak. A free-form tag (`yue-Hant-HK`) carries script AND
    # region a two-value enum could not, and reuses a standard rather than a
    # convention only this repo understands.
    language_hint: str
    canary: str
    display_name: str

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> DoctrineConfig:
        _resolve_context_field(self, "dir", info)
        return self


#: The Cognition session's loop bounds (DEC-247): rounds, tokens and time, each AT
#: LEAST 10x the tag turn's. `config.example.toml` states the same four and
#: `tests/test_config.py` holds them equal and above the 10x floor.
COGNITION_TURNS = 200
COGNITION_TOTAL_TOKENS = 10_240_000
COGNITION_OUTPUT_TOKENS = 120_000
COGNITION_TIMEOUT_SECONDS = 3600.0


class DreamBudgetConfig(BaseModel):
    """One adaptor's ceiling on a single Dreamer run: its caps and a clock.

    Mirrors `AdaptorBudget`, the value `nora.agent.dreamer`'s runtime actually
    receives — import-linter forbids `nora.agent` from importing `nora.config`
    at all, so the config type cannot cross that boundary. The
    `RemindersConfig`/`nora.domain.reminders.ReminderCaps` pair is the same
    shape and the same rule: the composition root builds the domain value from
    this one, and the DEFAULTS on both are the same numbers on purpose.

    `max_memory_calls` is the only cap that is not a token number; see its own
    comment for why a ceiling on READS could not be a per-tool budget.

    `turns`/`total_tokens`/`output_tokens` are `LimitsConfig`'s three caps —
    R-8's, applied to a Dreamer run instead of a tag turn — and are three
    fields for `LimitsConfig`'s own reason: Strands' `_check_limits` SKIPS a
    cap it cannot find, so a missing one means "unbounded" rather than
    "default".

    **PER ADAPTOR, not one global ceiling.** Proving a fact is worth more calls
    than glossing a word; a single number would starve the first or overpay for
    the second.

    **The numbers are UNMEASURED starting points, not measurements** — the same
    honesty `[analytics]`'s `stay_radius_m`/`stay_min_dwell_seconds` carry.
    `turns = 6` and `output_tokens = 4000` are `[llm.limits]`'s shipped
    20/12000 scaled down for a background job nobody waits on;
    `total_tokens = 40000` is `[llm.limits].total_tokens / 3`. Which model the
    Dreamer runs is decided by measurement; these are what a run is bounded by,
    not what a run was observed to need.

    **`timeout_seconds` is not optional, and the reason is specific to this
    subsystem.** Unlike a tag turn's timeout, a hung Dreamer call is QUIET:
    `dream_loop` marks the scope `running` and skips it every subsequent tick
    (`nora_dream_skipped_total{reason="in_flight"}`) without ever raising, so
    the exponential backoff — which only triggers on an EXCEPTION — never
    engages, and a hung provider is skipped FOREVER rather than backed off
    (`nora/app/composition.py`'s `_LanguageModelClient`). 120 s matches `[llm].timeout_seconds`
    and `[language].timeout_seconds` rather than inventing a third number with
    no basis; it wraps the WHOLE run, not one call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int = Field(default=6, ge=1)
    total_tokens: int = Field(default=40_000, ge=1)
    output_tokens: int = Field(default=4_000, ge=1)
    timeout_seconds: float = Field(default=120.0, gt=0)
    #: **The per-PASS memory ceiling**: how many reads a single run may make across
    #: `nora.agent.dreamer.MEMORY_TOOLS` TOGETHER — `about`, `ask_memory`,
    #: `search_memory_workspace`, `lookup_person`. The per-tool budgets above it
    #: (`[llm.budgets]`, `NativeTool.budget`) bound each verb on its own, which a pass
    #: spends by asking four verbs the same question.
    #:
    #: This is CODE and not instruction, and it fails closed: the (N+1)th read is
    #: answered `unavailable` with a reason, never an error and never a silent empty.
    #: Stating the cap in words does not work: a model told it has a hard cap declines
    #: the work rather than spending less.
    #:
    #: **`ge=1`, and 0 is refused rather than read as "off".** The proactive objective
    #: requires one memory read before any `remind`, so a ceiling of 0 is a
    #: configuration under which the adaptor can never legitimately do its job — a
    #: boot failure is the honest answer to it, the same posture `output_tokens = 0`
    #: gets by being `ge=1` above.
    #:
    #: **The `cognition` adaptor takes none of this** (DEC-247): a session has no call
    #: ceiling, and the config refuses a cognition block that sets this,
    #: `max_tool_calls` or `tool_calls`.
    max_memory_calls: int = Field(default=2, ge=1)

    #: **The per-adaptor delta over the per-tool call ceilings** — a tool name to how
    #: many times ONE pass may call it, overriding `NativeTool.budget` and
    #: `[llm.budgets]` for this adaptor alone.
    #:
    #: It exists because `NativeTool.budget` is a TAG TURN's number and the Dreamer
    #: would otherwise inherit it. `note_read = 3` is sized for a reply that reads a
    #: note or two before answering; a pass asked for one decision per open note needs
    #: a ceiling sized to the OPEN-NOTE COUNT, not a constant. A ceiling below that
    #: count does not fail: the pass grounds the first notes in a body and the rest in
    #: the index line alone and still returns `ok` — a judgement whose input cannot
    #: ground it answers anyway, wrong, at a rate nothing reports.
    #:
    #: **Not `[llm.budgets]`, which is the deployment-wide delta**: raising
    #: `note_read` there raises it for every reply Nora writes too, and this is a
    #: background batch pass nobody waits on. Same blast-radius argument `[dreamer]
    #: max_tokens` makes against raising `[llm] max_tokens`.
    #:
    #: **A key naming a tool this adaptor does not declare is a BOOT FAILURE**, not a
    #: silent no-op (`nora.agent.dreamer.tool_call_faults`, raised through
    #: `verify_tool_subsets`): a typo'd tool key would leave the ceiling the operator
    #: raised at the table's value, and the pass starved with the config file saying
    #: otherwise. Nothing measures that, so it refuses to start.
    #:
    #: `ge=1` per entry: 0 would be a tool the adaptor holds and can never call, which
    #: is `output_tokens = 0`'s shape — expressible only by removing it from the
    #: subset, where it is visible.
    tool_calls: dict[str, int] = Field(default_factory=dict)

    #: One pass's tool calls over EVERY verb together -> `AdaptorBudget.max_tool_calls`.
    #: Unset is no pass-wide ceiling.
    #:
    #: **The `cognition` adaptor takes none of this** (DEC-247), see
    #: `max_memory_calls` above.
    max_tool_calls: int | None = Field(default=None, ge=1)

    @field_validator("tool_calls")
    @classmethod
    def _positive_tool_calls(cls, value: dict[str, int]) -> dict[str, int]:
        for name, ceiling in value.items():
            if ceiling < 1:
                raise ValueError(
                    f"tool_calls[{name!r}] = {ceiling} — a ceiling below 1 is a "
                    "tool the adaptor holds and may never call; drop it from "
                    "the adaptor's subset instead, where it is visible"
                )
        return value


#: The flat margin `Config.dreamer_max_unit_hold_seconds` adds on top of
#: `Config._dream_unit_hold_known_minimum` when `[dreamer]
#: max_unit_hold_seconds` is unset. See that method's own docstring for why flat
#: rather than a multiplier: it covers a FIXED, small number of local single-row
#: Postgres calls the known minimum cannot sum, not a quantity that scales with
#: the model budget.
_DREAM_UNIT_HOLD_MARGIN_SECONDS = 300.0


class DreamerConfig(BaseModel):
    """Where the Dreamer's instruction corpus lives and how it reloads.

    Modelled field-for-field on `DoctrineConfig` above, minus the fields that are
    about NORA's prompt rather than about reading a directory (`canary`,
    `display_name`, `language_hint` are runtime context she is told; nothing in
    `nora/dreamer/` is rendered into a `<runtime_context>` block).

    **Whole section optional, every field defaulted**, the `[route]`/`[presence]`/
    `[language]` shape: a deployment that never touches it gets the bind-mounted
    tree and mtime reload.

    `enabled` turns the section from a description of a CORPUS into the switch for
    a SUBSYSTEM (DEC-128), and it is **False**: a new subsystem does not switch
    itself on, the `[presence]`/`[search]`/`[compute]`/`[analytics]` shape. Off,
    nothing constructs a `DreamerRuntime` and no adaptor ticks.

    `model = ""` means **fall back to `[llm].model`**, resolved by
    `Config.dreamer_model()` and nowhere else, the `MediaConfig.describer_for`
    shape, so exactly one place knows the fallback exists. Which model the
    Dreamer runs is answered by measurement (the DEC-58 method), not by a value
    picked here.

    `budgets` maps an ADAPTOR NAME to its `DreamBudgetConfig`. An adaptor with
    no block of its own gets `DreamBudgetConfig`'s own field defaults through
    `budget_for` — never a `KeyError` at tick time, and never an unbounded run.
    Collective Profile defaults are materialized in this map before composition
    and timeout derivation; explicit blocks still win.
    A PARTIAL block fills its missing fields from those same defaults rather
    than zeroing them, which matters because `output_tokens = 0` is not "no
    limit", it is a subsystem that silently never produces anything.

    `dir` is resolved relative to the config file, not the working directory,
    by `Config._resolve_paths` — NOT by a validator here, because a defaulted
    sub-model is constructed once at class-definition time with no validation
    context to resolve against.

    **THE CLOCK'S OWN FIVE NUMBERS** — `tick_seconds`, `safety_lag`,
    `quiet_seconds`, `max_age_seconds`, `batch_limit` — bound
    `nora.app.runtime.dream_loop`, which ticks EVERY scheduled adaptor, so they
    belong to `[dreamer]` and not to any one tier's section: a tier flag owning
    the runtime's clock would make `[dreamer] enabled = true` inert.

    - `tick_seconds` — how often the loop wakes.
    - `safety_lag` — unread messages above which a scope fires regardless of
      how quiet or how stale it is.
    - `quiet_seconds` — how long a scope with SOME lag must be silent before
      it fires (never interrupt a live conversation to read it).
    - `max_age_seconds` — how old an adaptor's watermark row may get before it
      fires at zero lag. This is the one that fires on a quiet room.
    - `batch_limit` — the most messages one cycle reads. A per-adaptor
      `AdaptorSchedule` entry overrides it; this is the shared default.

    **`store_timeout_seconds` is the per-CALL-SITE allowance the runtime
    multiplies by an adaptor's own `store_calls_per_tick`** to bound `inputs`
    and `commit`. Without that bound, a wedged store call would leave
    `dream_loop` holding `<adaptor>:<key>` in its `running` set forever, that
    unit never ticking again, with the skip counted under
    `reason="in_flight_adaptor"` — a label that cannot separate «a long run»
    from «wedged since Tuesday».

    **It is NOT `[standing] store_timeout_seconds`' number.** That value bounds
    ONE call; this one multiplies SITES, and a site inside a per-note loop runs
    once per note, so a single-call allowance would bound a legitimate degraded
    case too tightly, turning a slow tick into a counted store timeout and, for
    a daily-slotted adaptor, a lost day. `DEFAULT_DREAM_STORE_TIMEOUT_SECONDS`
    in `nora.domain.ports` carries that arithmetic and is the single definition
    this section and `nora.agent.dreamer` both read.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dir: Path = Path("nora/dreamer")
    tick_seconds: float = Field(default=300.0, gt=0)
    store_timeout_seconds: float = Field(
        default=DEFAULT_DREAM_STORE_TIMEOUT_SECONDS, gt=0)
    safety_lag: int = Field(default=300, ge=1)
    quiet_seconds: float = Field(default=1800.0, ge=0)
    max_age_seconds: float = Field(default=86_400.0, gt=0)
    batch_limit: int = Field(default=400, ge=1)
    reload: Literal["mtime", "startup"] = "mtime"
    strict: bool = True
    enabled: bool = False
    # Empty = `[llm].model`. Never read directly — `Config.dreamer_model()`
    # is the only resolver.
    model: str = ""
    # The Dreamer's OWN PER-CALL ceiling, `0` meaning «inherit `[llm]`» — resolved
    # by `Config.dreamer_max_tokens()`, the `dreamer_model()` shape.
    #
    # THIS IS NOT `budgets[...].output_tokens`. There are two ceilings:
    #
    #   `[dreamer.budgets.<adaptor>] output_tokens`  the STRANDS LOOP budget.
    #       Binds -> `stop_reason = "limit_output_tokens"`, raises NOTHING
    #       (`nora/agent/factory.py`).
    #   this                                          the PROVIDER's ceiling on
    #       ONE completion. Binds -> `MaxTokensReachedException` ->
    #       `PartialReplyError` (`nora/agent/factory.py`).
    #
    # `[llm] max_tokens` is sized for a TAG TURN. A batch pass is a different unit
    # of work whose output scales with the batch (one verdict per message plus a
    # candidate array). A run that overruns the per-call ceiling returns
    # `unavailable`, `_dream_adaptor_once` holds the watermark, and the same batch
    # is re-read and re-truncated on every fire, paced only by
    # `_DREAM_BACKOFF_MAX_SECONDS`, which reads exactly like a healthy hourly
    # adaptor.
    #
    # Raising `[llm] max_tokens` instead would be the wrong blast radius: that
    # number governs every one of Nora's own replies, and this is a background
    # batch pass nobody waits on.
    #
    # `0` is the default, so an unset field leaves the Dreamer on `[llm]`, the same
    # posture `model = ""` takes. `ge=0` and not `ge=1`: 0 is the sentinel, and a
    # real 0 would be a subsystem that silently never produces anything.
    max_tokens: int = Field(default=0, ge=0)
    # The Dreamer's OWN reasoning effort, `""` meaning «inherit `[llm]`» — the
    # shape `StandingConfig.reasoning_effort` uses, and TYPED for its reason: a
    # typo in a loose `str` reaches the provider and returns a 400, here inside a
    # background tick where it is one more `unavailable` among many. Typed, it is a
    # boot failure, which is the refuse-to-start posture.
    #
    # **It exists because reasoning is billed as COMPLETION, and this model can
    # spend the whole ceiling on it.** At "low" it can reason its way to an EMPTY
    # LIST on a transcript that plainly contains claims, where `none` returns the
    # correct candidates; a larger ceiling does not help, because the reasoning
    # grows to fill it. The failure has two faces depending on transcript size:
    # silence on a small one, `MaxTokensReachedException` on a real one. At "low"
    # the run reports `unavailable`, which HOLDS a watermark over a batch that was
    # in fact read.
    #
    # The field is Dreamer-WIDE: the Dreamer has its own `ModelSource`, so this
    # keeps one model object. The reading that would split it is a second adaptor
    # measurably better at a different effort, on a suite rather than a probe.
    #
    # `[llm] reasoning_effort` is UNTOUCHED, so her replies are unaffected.
    reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = ""
    budgets: dict[str, DreamBudgetConfig] = Field(default_factory=dict)
    # The WHOLE hold one (adaptor, key) unit may add to `running`, asserted at
    # startup rather than reasoned about in prose (`[standing]
    # max_slot_hold_seconds`' own shape, `Config.
    # _the_dream_unit_hold_exceeds_its_components` below).
    #
    # `_run_adaptor` (`nora/app/runtime.py`) wraps its WHOLE call to
    # `_dream_adaptor_once` in ONE `asyncio.timeout` at this number — the model
    # budget, both bounded store phases (`inputs`, `commit`), AND every cursor read
    # around them: `clock.last_fired`/`record_fire`, `language.watermark_for`/
    # `watermark_is_behind_the_record`/`lag`/`seconds_since_last_message`/
    # `watermark_age_seconds`/`advance_watermark`, `record.since`,
    # `media_join.enrich`. Without the wall clock, any ONE of those hanging would
    # leave `dream_loop` holding `<adaptor>:<key>` in `running` forever, and the only
    # symptom would be `nora_dream_skipped_total{reason="in_flight_adaptor"}`, which
    # cannot tell a long run from a wedge (`nora_dream_unit_timeout_total` is the
    # series that can).
    #
    # One wall clock around the whole unit covers a call nobody has written yet too,
    # where a per-call bound would need a new `wait_for` for every future cursor
    # read. The cost is that it cannot say WHICH call wedged:
    # `nora_dream_unit_timeout_total{adaptor}` answers "which adaptor", not "which
    # await".
    #
    # **The number must provably exceed every bounded piece it wraps, or the
    # wall clock would fire before a legitimate run finishes** — the same
    # argument `max_slot_hold_seconds` makes, checked the same way (a startup
    # `model_validator`, not a comment doing arithmetic by hand). The pieces:
    #
    #   samples x timeout_seconds                        the model budget —
    #                                                      `AdaptorSchedule.
    #                                                      samples` x this
    #                                                      adaptor's own
    #                                                      `DreamBudgetConfig.
    #                                                      timeout_seconds`.
    #   store_calls_per_tick x store_timeout_seconds      `inputs`'s bound.
    #   store_calls_per_tick x store_timeout_seconds      `commit`'s bound,
    #                                                      the SAME multiplier
    #                                                      again — the two
    #                                                      phases are bounded
    #                                                      separately.
    #
    # Every adaptor's schedule fixes `samples` at 1 in code. `store_calls_per_tick`
    # has no config equivalent at all — it is a class attribute in
    # `nora.language`, four layers above `nora.config` — so the check uses
    # `DREAM_MAX_STORE_CALLS_PER_TICK` (`nora.domain.ports`), a documented
    # ceiling with headroom over every shipped adaptor, pinned by
    # `tests/architecture/test_dream_store_calls_are_declared.py`.
    #
    # **`None` DERIVES the value at load time — it does NOT default to a fixed
    # literal.** A fixed literal goes stale the moment an operator raises ANY
    # `[dreamer.budgets.*] timeout_seconds`: either a refused boot (too low) or a
    # silently under-bounded wedge (an old literal that still validates but no
    # longer carries real headroom). `Config.dreamer_max_unit_hold_seconds()` is the
    # resolver: this field explicit -> that value, checked at startup against
    # `Config._dream_unit_hold_known_minimum()`; unset -> that minimum plus a flat
    # margin, ALWAYS above it by construction. See both for the exact arithmetic;
    # restating it here would be two homes for one number.
    #
    # Set this explicitly only to require MORE headroom than the derivation
    # gives, or to pin one number for a reading. An explicit value AT OR
    # BELOW the known minimum still refuses to start — derivation removes the
    # need to update a literal by hand, not the proof itself.
    max_unit_hold_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _collective_profile_defaults(self) -> DreamerConfig:
        # Composition passes this map to the runtime before Culture inheritance;
        # the outer unit timeout also reads it. Materialize the code defaults so
        # neither consumer silently falls back to the generic 40k/120s budget.
        for name in ("profile-unit", "profile-room", "profile-nora"):
            self.budgets.setdefault(name, self.budget_for(name))
        return self

    def budget_for(self, adaptor: str) -> DreamBudgetConfig:
        """The budget one adaptor runs under, ALWAYS bounded.

        A `.get` with a default rather than a subscript, and that is the whole
        point: an adaptor added later — or renamed, or misspelled in the TOML —
        must get a bounded budget, not a `KeyError` raised inside a background
        tick nobody is watching. The failure mode this replaces has no metric of
        its own: `_dream_once` catches the exception per scope, counts a
        failure, and backs the scope off, so a typo'd budget key would look
        exactly like a provider outage.

        The cost of that leniency is that a misspelled key is silent. It is
        still the right direction: the default is a real ceiling, so the worst
        case is a run bounded by numbers the operator did not choose, never a
        run bounded by nothing.

        The fallback is a freshly constructed `DreamBudgetConfig()`, not a
        module-level constant: DEC-13's gate
        (`tests/architecture/test_no_module_level_mutable_singletons.py`) bans a
        class instantiated and bound to a module-level name in its own module,
        and this earns no exemption for being frozen. It also keeps the class's
        own field defaults as the single answer to "what does an unconfigured
        adaptor get" — there is no second place to keep in step.
        """
        if adaptor == "cognition" and adaptor not in self.budgets:
            # The Cognition session's own code defaults (DEC-247), not the
            # class's: a session is bounded by rounds, tokens and time at
            # least 10x a tag turn's, never by the 6 turns / 40k a background
            # pass gets.
            return DreamBudgetConfig(
                turns=COGNITION_TURNS, total_tokens=COGNITION_TOTAL_TOKENS,
                output_tokens=COGNITION_OUTPUT_TOKENS,
                timeout_seconds=COGNITION_TIMEOUT_SECONDS,
            )
        if (adaptor in ("profile-unit", "profile-room", "profile-nora")
                and adaptor not in self.budgets):
            # ponytail: allow 60k source tokens for the 60k-char ceiling, plus
            # 25k non-source tokens (instructions, room-culture heads read every
            # unit session, held profile lines, first-session prose backfill).
            # Two 85k-input calls + 8k repair history + 16k output = 194k,
            # below 240k; revisit if the source ceiling or outside context grows.
            # Six turns leave room to finish after repair; 360s allows two
            # 120s calls plus tool time.
            # Starting allowances, not measured provider latency/token ratios.
            return DreamBudgetConfig(
                turns=6, total_tokens=240_000, output_tokens=16_000,
                timeout_seconds=360.0,
            )
        return self.budgets.get(adaptor, DreamBudgetConfig())


class CognitionConfig(BaseModel):
    """The cognition loop: stimuli, the wake evaluator and the Cognition session,
    the one background writer of the notebook, events, Standing and Nora's own
    reminders.

    **Ships OFF** (`enabled = false`): off, the ingest pipeline builds no
    stimulus stage, no evaluator is spawned and no session runs. On, every
    recorded person-authored item appends a stimulus with its cues, the
    evaluator ticks every `tick_seconds` and starts a session on each wake.
    Undo is this flag and a restart.

    The wake numbers are starting values. `quiet_seconds` and `max_age_seconds`
    reuse `[dreamer]`'s key NAMES, not their values. `floor_grace_seconds` is
    how late a daily floor may still be raised after its instant (a restart at
    07:02 raises the 07:00 floor; a deploy at 15:00 does not raise that
    morning's).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    tick_seconds: float = Field(default=30.0, gt=0)
    quiet_seconds: float = Field(default=1800.0, ge=0)
    cue_quiet_seconds: float = Field(default=120.0, ge=0)
    # DEPRECATED (DEC-279) and read by nothing: a Rollup stimulus does not start a
    # wake, it rides the next one, so there is no delay to wait. The key stays
    # declared because `extra="forbid"` is on every model in this file and the
    # deployment's `config.toml` is untracked: deleting the field would turn a live
    # file that still carries the line into a container that refuses to boot. It
    # goes when the operator has removed it from that file.
    rollup_delay_seconds: float = Field(default=60.0, ge=0)
    batch_max: int = Field(default=80, ge=1)
    max_age_seconds: float = Field(default=3600.0, gt=0)
    cooldown_seconds: float = Field(default=300.0, ge=0)
    max_concurrent_sessions: int = Field(default=2, ge=1)
    reply_quiet_seconds: float = Field(default=600.0, ge=0)
    # The daily floors, local wall-clock "HH:MM" in `timezone`: each wakes a
    # session with a `time` item that reviews the notebook.
    time_floors: list[str] = Field(default_factory=lambda: ["07:00", "00:00"])
    timezone: str = "Asia/Hong_Kong"
    floor_grace_seconds: float = Field(default=3600.0, ge=0)
    #: DEC-303: with each floor, a room-bound `time` item for every room holding
    #: an open event that starts within this many days. 0 turns it off.
    upcoming_days: float = Field(default=3.0, ge=0)
    store_timeout_seconds: float = Field(default=5.0, gt=0)
    #: Agenda items per session; a starting value.
    max_items: int = Field(default=12, ge=1)
    #: Strikes after which an item's stimuli are struck (`struck`); a starting value.
    max_strikes: int = Field(default=3, ge=1)
    # DEC-242: how long a TRACKED wake may stay OPEN (no outcome row) before it is
    # EXPIRED and read as failed: its unit stops being in flight and its stimuli are
    # pending again. The session's own wall clock (`AdaptorBudget.timeout_seconds`)
    # must end at least `nora.app.cognition_session.SESSION_TIMEOUT_MARGIN_SECONDS`
    # before it (the runner refuses to start otherwise).
    session_timeout_seconds: float = Field(default=3900.0, gt=0)
    #: At most this many self-set wakes pending per unit; a starting value.
    max_self_wakes: int = Field(default=5, ge=1)
    #: DEC-242: a rolling-24 h cap on `wake_at` calls per unit, starting at 12.
    #: The pending cap alone does not bound a session that re-arms itself on
    #: every pass, because a fired wake frees its slot.
    max_self_wakes_per_day: int = Field(default=12, ge=1)
    #: DEC-244 (7): a unit whose sessions fail is not woken again every
    #: `cooldown_seconds`, for ever. The next wake after `n` consecutive failed or
    #: timed-out sessions waits `cooldown_seconds * 2 ** (n - 1)` (a `closed` session
    #: resets it), capped at `backoff_max_seconds` (6 h); after
    #: `max_consecutive_failures` the stimuli those sessions claimed are struck
    #: (reason `failed`) so an always-failing agenda stops. Both are starting values.
    backoff_max_seconds: float = Field(default=21600.0, gt=0)
    max_consecutive_failures: int = Field(default=5, ge=1)
    #: DEC-244 (8): how much of a session's prompt the Record rows and Register index
    #: lines may spend. `record_chars` is shared across the agenda's items by what
    #: each needs; `message_chars` clips one message; `register_lines` caps the index
    #: lines listed per kind. When an item's messages do not fit, the OLDEST are
    #: dropped and the prompt says how many. Starting values.
    record_chars: int = Field(default=40000, ge=500)
    message_chars: int = Field(default=400, ge=40)
    register_lines: int = Field(default=40, ge=1)
    #: DEC-245: Nora's own sends in each message item's window are shown, marked as
    #: hers. The window opens `own_send_lookback_seconds` before the item's oldest
    #: message and runs to now; at most `own_sends_max` (the newest) per item. They
    #: share `record_chars` with the messages. Starting values.
    own_send_lookback_seconds: float = Field(default=1800.0, ge=0)
    own_sends_max: int = Field(default=6, ge=0)
    #: DEC-244: a context overflow is the AGENDA's (and may strike
    #: human stimuli) only when it is proven: the prompt's token estimate (a
    #: lower bound) is at least `overflow_window_fraction` of THIS model's window
    #: AND the same agenda overflowed on the attempt before. `None` (the
    #: default) is «window unknown»: no overflow is ever the agenda's, every one
    #: is `infra` (backs off, never strikes). Strands raises the overflow class
    #: for any provider message containing «context length» or «too many
    #: tokens», a 503 and a per-minute token limit included, so the class alone
    #: proves nothing.
    model_window_tokens: int | None = Field(default=None, ge=1000)
    overflow_window_fraction: float = Field(default=0.8, gt=0, le=1)

    @field_validator("time_floors")
    @classmethod
    def _floors_are_wall_clock(cls, value: list[str]) -> list[str]:
        for floor in value:
            hh, _, mm = floor.partition(":")
            if not (hh.isdigit() and mm.isdigit() and len(mm) == 2
                    and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError(f"time_floors entry {floor!r} is not HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def _zone_exists(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class SubagentConfig(BaseModel):
    """The sub-agent worker: a background Strands agent that does one objective for Nora
    and reports once, through the deferred-job outbox.

    **Ships OFF** (`enabled = false`): off, no `SubagentBackend` is built and no kind is
    registered with the poller, so boot is what it was. On, it runs on the Dreamer's model
    source and under the poller (`[dreamer] enabled` and `[compute] enabled` are both
    required, refused at load), bounded by `[dreamer.budgets.subagent]` alone: rounds,
    tokens and a wall clock, never per tool (DEC-247). Undo is this flag and a restart.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    #: The worker's reasoning effort: LOW, so a long job spends its tokens on work, not
    #: thought. Typed as the provider's closed set, so a typo refuses to start. The model
    #: itself is the Dreamer's (`[dreamer] model`, inheriting `[llm]`); a separate model
    #: is not offered until a measurement asks for one.
    reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = "low"


class AssessConfig(BaseModel):
    """The Assess adaptor: the nightly, deterministic data-quality checks, and the
    model-read measures (a cheap reader, lessons drift) listed beside them.

    **Ships OFF** (`mode = "off"`), the `[cognition]`/`[lineage]` shape: a new
    subsystem does not switch itself on. **Assess writes no tier**: off means `nora
    assess run-nightly` (`nora.app.assess_run`) is refused, never that a read it
    would have done was skipped silently (R-27: a refusal is `unavailable`, not a
    quiet `nothing_found`).

    `mode = "on"` runs the deterministic checks (`nora.app.assess_dq`) and
    records `assess_report` rows and `nora_dq_failures_total{check}`. There
    is no `"shadow"` here: a report is never live effect (nothing reads an
    `assess_report` row on a turn), so there is nothing a shadow mode would
    still be withholding.

    The model-read measures (the cheap reader over citations, lessons drift and
    churn) are NOT run by this flag: they need a live model call this repo's eval
    discipline keeps out of `make gate` (`nora-eval`'s own boundary), so they belong
    to a separate, explicitly invoked path. Their gates (precision/recall,
    false-derivation rate, unasked-act rate) are declared as
    `evals.corpora_assess`'s corpora, run offline (`nora-eval`), never by this flag.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["off", "on"] = "off"
    #: Local wall-clock "HH:MM" the operator's cron/systemd timer targets;
    #: read by the CLI's own help text only — nothing here schedules a run,
    #: the same boundary `[cognition] time_floors` draws for the wake
    #: evaluator's daily floors versus what fires them.
    nightly_at: str = "03:00"
    timezone: str = "Asia/Hong_Kong"
    #: How far back `rollup_incremental_vs_batch` (`nora.app.assess_dq`) rebuilds when
    #: asked to: a window, not the whole Record, because a full rebuild on the
    #: deployment's real history is real work.
    rollup_diff_window_seconds: float = Field(default=86_400.0, gt=0)
    store_timeout_seconds: float = Field(default=5.0, gt=0)

    @field_validator("nightly_at")
    @classmethod
    def _nightly_at_is_wall_clock(cls, value: str) -> str:
        hh, _, mm = value.partition(":")
        if not (hh.isdigit() and mm.isdigit() and len(mm) == 2
                and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError(f"nightly_at {value!r} is not HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def _zone_exists(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class LineageConfig(BaseModel):
    """Lineage at assembly: whether the pushed blocks that render derived rows
    (`<notes>`, `<standing>`, `<standing_working_set>`, `<culture>`) show each row or
    section only in the rooms its lineage passes for.

    **Ships OFF.** `off`: blocks render as they always have and nothing is
    computed. `shadow`: the predicates run on every prefill and what they would
    withhold is recorded on the `agent.prefill` span
    (`nora.prefill.lineage_withheld`), and every block still renders: the reading
    that gates `on`. `on`: a section that fails is not rendered.

    `record_query` applies lineage whatever this says, and never returns a message
    its room may not be shown.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["off", "shadow", "on"] = "off"


class RecordQueryConfig(BaseModel):
    """`record_query`.

    **Ships OFF.** Off, the tool is in no turn's tools, catalogue or the
    Relevance Gate's capability digest: the registry is built without its
    Record read, and a tool with nothing to read is withheld rather than
    offered to answer `unavailable`. Turn it on after the prepared
    `tool_selection` cases run on both arms (the nora-tool skill)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False


#: Dot-separated host labels of letters, digits and inner hyphens (no trailing dot).
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_HOST_LABELS = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")


class DocumentsConfig(BaseModel):
    """`document_publish`, `document_read` and `document_set_status`.

    **Ships OFF.** Off, none of the three is in any turn's tools, catalogue or the
    Relevance Gate's capability digest. A published document is opened in the web
    app, so a model that published one before the portal's document pages exist
    would hand people a path that shows nothing. Turn it on after the portal routes
    exist and the `route_document_*` tool_selection cases have run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    # The web app's public origin (`https://nora.67.zone`), so a published document can
    # be handed to the room as a link. EMPTY = no link is ever built or shown: she says
    # where to find the document by title instead, and never prints a path or a host.
    # An origin only: no path, query, fragment or credentials. The link carries the
    # document's 12-hex handle, never its UUID (a UUID in a reply is blocked at egress).
    public_url: str = ""

    @field_validator("public_url")
    @classmethod
    def _public_url_is_an_origin(cls, value: str) -> str:
        if not value:
            return value
        parts = urlsplit(value)
        try:
            port = parts.port  # raises on a non-numeric or out-of-range port
        except ValueError:
            port = -1
        host = parts.hostname or ""
        bad = (
            parts.scheme not in ("http", "https")
            # Letters, digits and hyphens in dot-separated labels: no control character,
            # backslash, `+`, underscore or trailing dot can get into a link she sends.
            or _HOST_LABELS.fullmatch(host) is None
            or port == -1
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or any(c.isspace() or not c.isprintable() for c in value)
            or "\\" in value
        )
        if bad:
            msg = (
                "[documents] public_url must be an origin like https://nora.67.zone "
                "(http or https, a plain host, optional port; no path, query, fragment, "
                "login, trailing dot or backslash)"
            )
            raise ValueError(msg)
        return f"{parts.scheme}://{parts.netloc}"


class GoogleConfig(BaseModel):
    """Nora's ONE Google identity: one Desktop-app OAuth client and one refresh token
    for her own account (`nora@67.zone`), shared by every Google service she uses
    (shared files today), never a client or variable per service. Each field NAMES the
    environment variable that holds the value (DEC-8), never the value. The scopes are
    declared by each service in code (`nora.infra.google.scopes`) and granted together
    by `scripts/google_auth.py`. An API KEY (the Maps key under `[route]`) is a
    different mechanism and stays with its feature."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id_env: str = "GOOGLE_OAUTH_CLIENT_ID"
    client_secret_env: str = "GOOGLE_OAUTH_CLIENT_SECRET"  # noqa: S105 — names an env var, not a secret
    refresh_token_env: str = "GOOGLE_OAUTH_REFRESH_TOKEN"  # noqa: S105 — names an env var, not a secret


class ExternalFilesConfig(BaseModel):
    """Opening a shared file link.

    **Ships OFF.** `enabled` is the OPEN tool's switch, and nothing else:
    recording a link as a `[Google Doc link c41d09]` handle at ingest needs no account,
    so it happens whatever this says. `sources` names the providers whose
    adapters are built, in order; each must be one the codebase recognises
    (`nora.domain.external_files.PROVIDERS`). A second provider is a new
    adapter and a new entry here. Google's credentials are `[google]`'s,
    shared with every other Google service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    sources: list[str] = Field(default_factory=lambda: ["google_drive"])

    @model_validator(mode="before")
    @classmethod
    def _no_per_source_credentials(cls, data: object) -> object:
        """A `[external_files.google_drive]` table is refused by name rather than by
        `extra="forbid"`'s generic message, so the fix is in the error: Google's
        OAuth env names live in `[google]`."""
        if isinstance(data, dict) and "google_drive" in data:
            raise ValueError(
                "[external_files.google_drive] is gone: Google's OAuth client and "
                "refresh token are configured once, in the top-level [google] section "
                "(client_id_env, client_secret_env, refresh_token_env), shared by every "
                "Google service. Delete [external_files.google_drive] and set [google] "
                "(see config.example.toml)"
            )
        return data

    @field_validator("sources")
    @classmethod
    def _known_sources(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(EXTERNAL_FILE_PROVIDERS))
        if unknown:
            raise ValueError(
                f"[external_files] sources names {unknown}; the known providers "
                f"are {list(EXTERNAL_FILE_PROVIDERS)}"
            )
        if len(set(value)) != len(value):
            raise ValueError("[external_files] sources names a provider twice")
        return value


class LoggingConfig(BaseModel):
    """Where the log goes is no longer a setting: it goes to stdout.

    There is no `path` key: under `extra="forbid"` a leftover `path =` line is a
    loud startup failure, which is the honest outcome, since an operator who set a
    path and silently kept getting stdout is the bug shape `format` below already
    refuses.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str
    format: str


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


class ErrorsConfig(BaseModel):
    """Sentry — error capture only, never tracing (`nora/obs/errors.py`).

    OFF by default and off without a DSN, because the shipped default must run
    on a laptop with no Sentry account. `dsn_env` NAMES the variable (DEC-8);
    the DSN itself never appears in `config.toml`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    dsn_env: str = "SENTRY_DSN"
    # Which deployment this is, so a laptop's errors and the box's do not group
    # into one issue. Free-form; `""` records `unknown` rather than nothing,
    # since an unlabelled issue is worse than a wrongly labelled one.
    environment: str = ""


class ObsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logging: LoggingConfig
    metrics: MetricsConfig
    tracing: TracingConfig
    # Defaulted rather than required: a section nobody wrote must mean "off", not
    # "refuse to start".
    errors: ErrorsConfig = ErrorsConfig()


class LangfuseEvalConfig(BaseModel):
    """DEC-6: Langfuse as the store for eval results (datasets, dataset
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
    # tunable for cost: `deepseek-v4-pro` is sharp but dear, and the judge runs once
    # per graded case. The ONE hard invariant is that it must NOT equal `llm.model`:
    # a model grading its own output self-scores; the runner refuses that. The judge
    # sees the source data, so a cheaper DIFFERENT model is a reasonable trade. Kept
    # as a bare id, not the whole tier.
    judge_model: str = "deepseek-v4-pro"
    # How many times the judge grades each reply; the gate binds the MAJORITY safety
    # verdict. 1 = single sample (noisy); 3 removes that single-sample noise. Odd is
    # preferable so a majority always exists.
    judge_samples: int = Field(default=1, ge=1)
    # How many times `--suite journeys` runs EACH journey; the gate binds the
    # MAJORITY per-journey verdict. Routing is stochastic: a single run passes an
    # 85%-per-journey model only ~52% of the time and lets a lucky broken model
    # through, so n=1 is too noisy to gate on. Odd is preferable so a majority
    # always exists.
    journey_samples: int = Field(default=3, ge=1)


class AgentConfig(BaseModel):
    """The agent's own identity, so a clone (a second agent called "Delia") is a
    config + doctrine-file change, not a code change.

    `name` is the MACHINE name: the Strands `agent_id` and the Langfuse trace
    span (`invoke_agent <name>`). Lowercase and STABLE — changing it starts a new
    trace lineage (orphans saved Langfuse views), which is what a genuinely new
    agent wants. The HUMAN-facing name is `[doctrine].display_name` and the wake
    word is `[engagement].trigger.text`; a clone sets all three (plus its own
    doctrine files and its own Honcho `ai_peer`/`workspace_id`).

    Optional and defaulted like `[media]`/`[route]`; the default is the `nora`
    identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "nora"
    # HER OWN PUBLIC HANDLES, by network key — `signal = "@nora.000"`. What people
    # type to reach her, rendered in `<presence>` so she can recognise «@noraexample»
    # as herself in plain text and answer «what's your IG?».
    #
    # DECLARED ONLY WHERE NOTHING CAN REPORT IT. A handle here is a copy, and a copy
    # goes stale in silence the day the account is renamed. So a network whose
    # adapter reports its own handle live (Instagram, from the bridge's `/health`) is
    # read from there and this value is only its fallback; X and Threads have no
    # adapter, and WhatsApp's bridge exposes no handle.
    #
    # DISPLAY TEXT, NEVER AN IDENTITY. Nothing matches a sender against these
    # and they never reach `self_aliases()` (`nora.domain.identity`: a handle
    # is re-assignable, so matching on one resolves a stranger as a known
    # person). Empty — the default — renders no handle line at all.
    handles: dict[str, str] = Field(default_factory=dict)

    @field_validator("handles")
    @classmethod
    def _handles_are_network_keys(cls, value: dict[str, str]) -> dict[str, str]:
        """Network keys are the wire keys (`signal`, `whatsapp`, `x`), because
        they are joined against the platform a turn is on. `Signal` would
        silently name a network no turn is ever on."""
        for network, handle in value.items():
            if network != network.strip().lower() or not network:
                raise ValueError(
                    f"[agent.handles] key {network!r} must be a lowercase network "
                    "key such as \"signal\" or \"whatsapp\""
                )
            if not handle.strip().lstrip("@"):
                raise ValueError(
                    f"[agent.handles] {network} is empty; delete the key instead "
                    "of declaring a handle with nothing in it"
                )
        return value


class McpMountConfig(BaseModel):
    """One MCP server Nora may mount as conversation tools.

    Optional and defaulted like `RouteConfig`: `enabled`
    defaults False, so a section existing in a committed config doesn't mean
    it's live. `token_env` NAMES the environment variable holding the bearer
    token (DEC-8's `api_key_env`/`token_env` shape) — the value is never here,
    it lives in `.env`. `budget` is the flat per-server tool-call ceiling
    every tool this server advertises shares — not per-tool,
    because auto-discovered tools have no config-time identity to key on.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str
    token_env: str
    budget: int


class ToolsConfig(BaseModel):
    """The `[tools.mcp.<name>]` mount points. A second server is
    a second `mcp` dict entry — no schema change, matching the ARCHITECTURE
    doc's documented target ("add an MCP server -> no Python")."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mcp: dict[str, McpMountConfig] = Field(default_factory=dict)


class ContentChecksConfig(BaseModel):
    """Optional content/disclosure filters; credentials and doctrine stay guarded."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False


class Config(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    model_routes: dict[str, ModelRoute] = Field(default_factory=dict)
    _secret_path: Path = PrivateAttr(default=Path("secret.toml"))
    _credentials: dict[str, str] | None = PrivateAttr(default=None)

    @property
    def credentials(self) -> dict[str, str]:
        import os

        from nora.secret import read
        values = self._credentials if self._credentials is not None else read(self._secret_path)
        return {**values, **os.environ}

    def redaction_values(self) -> list[str]:
        names = {"NORA_BRIDGE_TOKEN", "GIPHY_API_KEY", "PULSE_API_TOKEN", "PULSE_MCP_TOKEN"}

        def collect(data: dict[str, object]) -> None:
            for key, value in data.items():
                if isinstance(value, dict):
                    collect(value)
                elif key.endswith("_env") and isinstance(value, str):
                    names.add(value)

        collect(self.model_dump())
        values = self.credentials
        return [values[name] for name in names if values.get(name)]

    def endpoint(self, purpose: str, model: str = "") -> tuple[str, str, str]:
        route = self.model_routes.get(purpose, ModelRoute())
        provider = route.provider or self.llm.provider
        endpoint = self.providers[provider] if provider else self.llm
        selected = route.model or (model if provider == self.llm.provider else "") or self.llm.model
        return endpoint.base_url, self.credentials.get(endpoint.api_key_env, ""), selected

    @model_validator(mode="after")
    def _validate_model_routes(self) -> Config:
        if self.llm.provider and self.llm.provider not in self.providers:
            raise ValueError("unknown default model provider")
        if not self.llm.provider and not self.llm.base_url:
            raise ValueError("llm requires provider or base_url")
        purposes = {"tag", "participation", "proactive", "summons", "browse", "vision",
                    "speech_format", "reaction", "standing", "relevance_gate", "lesson_check",
                    "dreamer", "subagent", "extraction", "eval_judge", "context_filter"}
        for purpose, route in self.model_routes.items():
            if purpose not in purposes:
                raise ValueError("unknown model route purpose")
            if route.provider and route.provider not in self.providers:
                raise ValueError("unknown model route provider")
            if route.provider and route.provider != self.llm.provider and not route.model:
                raise ValueError("a changed provider requires an explicit model")
        for modality in ("image", "audio", "video"):
            describer = self.media.describer_for(modality)
            if describer and describer.provider not in {"omniroute", "kimi", "opencode-go"}:
                if describer.provider not in self.providers:
                    raise ValueError("unknown media model provider")
                if describer.enabled and not describer.model:
                    raise ValueError("a media provider requires an explicit model")
        return self

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def reject_retired_memory_names(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        renamed_keys = {
            "extraction": {"batch_size": "messages_per_call",
                           "context_size": "context_messages",
                           "settle_seconds": "min_message_age_seconds"},
            "index": {"enabled": "read", "drain_seconds": "refresh_seconds"},
            "profile": {"enabled": "write", "render": "read"},
        }
        for old, new in (
            ("persona", "memory.profile"),
            ("profile", "memory.profile"),
            ("message_readings", "memory.extraction"),
            ("memory_index", "memory.index"),
        ):
            if old in data:
                values = data[old]
                if isinstance(values, dict):
                    for key, replacement in renamed_keys[new.split(".")[-1]].items():
                        if key in values:
                            raise ValueError(
                                f"[{old}] {key} has been renamed to [{new}] {replacement}"
                            )
                raise ValueError(f"[{old}] has been renamed to [{new}]; rename the section")
        memory = data.get("memory", {})
        if isinstance(memory, dict):
            for key in HonchoConfig.model_fields:
                if key in memory:
                    raise ValueError(f"[memory] {key} has been renamed to [honcho] {key}")
            for section, renames in renamed_keys.items():
                values = memory.get(section, {})
                if isinstance(values, dict):
                    for old, new in renames.items():
                        if old in values:
                            raise ValueError(
                                f"[memory.{section}] {old} has been renamed to "
                                f"[memory.{section}] {new}"
                            )
        culture = data.get("culture", {})
        if isinstance(culture, dict):
            for old, new in (
                ("person_wake_batch", "[memory.profile.person] update_every_messages"),
                ("unit_wake_batch", "[memory.profile.unit] update_every_room_profile_changes"),
                ("nora_wake_batch", "[memory.profile.nora] update_every_replies"),
            ):
                if old in culture:
                    raise ValueError(f"[culture] {old} has been renamed to {new}")
        return data

    # Optional and defaulted — see `AgentConfig`; the default is today's identity.
    agent: AgentConfig = AgentConfig()
    content_checks: ContentChecksConfig = ContentChecksConfig()
    platforms: PlatformsConfig
    llm: LLMConfig
    honcho: HonchoConfig
    memory: MemoryConfig = MemoryConfig()
    store: StoreConfig
    # Optional, and defaulted — see `MediaConfig`.
    media: MediaConfig = MediaConfig()
    # Optional and defaulted — see `RouteConfig`. Off unless an operator turns
    # it on; a config.toml without a `[route]` section keeps loading.
    route: RouteConfig = RouteConfig()
    # Optional and defaulted — see `PreviewConfig`. Off unless an operator turns
    # it on; off means a reply carrying a link goes out with no card.
    preview: PreviewConfig = PreviewConfig()
    # Optional and defaulted — see `PresenceConfig`. Off unless an operator turns
    # it on; a config.toml without a `[presence]` section keeps loading, and the
    # collector is a SEPARATE process (`python -m nora.presence`) that reads this
    # same tree.
    presence: PresenceConfig = PresenceConfig()
    # Optional and defaulted — see `AnalyticsConfig`. Ships INERT
    # (`enabled = false`); off, `nora.analytics` builds and writes nothing.
    analytics: AnalyticsConfig = AnalyticsConfig()
    # Optional and defaulted — see `MemoryIndexConfig`.
    # Optional and defaulted — see `SearchConfig`. Off (inert) unless an operator
    # turns it on AND a Tavily key is present (`api_key_env`, DEC-8/DEC-73); a
    # config.toml without a `[search]` section keeps loading.
    search: SearchConfig = SearchConfig()
    # Optional and defaulted — see `OnlineConfig`. Off (inert) unless an
    # operator turns it on AND a Steam Web API key is present (`api_key_env`);
    # a config.toml without an `[online]` section keeps loading, and the
    # `voice` arm is gated on the Discord platform, not on this section.
    online: OnlineConfig = OnlineConfig()
    # Optional and defaulted — see `SocialConfig`. Off (inert) unless an
    # operator turns it on AND the host-side bridge runs (DEC-151); keyless,
    # so `enabled` is the whole gate.
    social: SocialConfig = SocialConfig()
    # Optional and defaulted — see `WeatherConfig`. Keyless (HK Observatory), so
    # safe to enable; ships off in the example (DEC-70).
    weather: WeatherConfig = WeatherConfig()
    # Optional and defaulted — see `SpeechConfig`. Ships INERT (`enabled =
    # false`); off, Nora neither transcribes voice notes at ingest nor speaks
    # outbound. On, `[speech.stt]` calls the local STT endpoint (`stt:8000`)
    # during the voice-note custody stage.
    speech: SpeechConfig = SpeechConfig()
    # Optional and defaulted — see `HeartbeatConfig`/`RemindersConfig`. The tick
    # ships INERT (`enabled = false`); the caps apply the moment a reminder can
    # be created, whether or not anything is firing them.
    heartbeat: HeartbeatConfig = HeartbeatConfig()
    reminders: RemindersConfig = RemindersConfig()
    # Optional and defaulted — see `NotesConfig`. Ships INERT
    # (`enabled = false`); off, composition wires `notes=None` into
    # `TranscriptInjection` and the `<notes>` block never renders. On, the
    # index is read from `SqlNoteStore` on every tag — the Cognition session
    # writes notes (`note_create`/`note_revise`/`note_set_status`/`note_merge`), so it
    # is empty until it has.
    notes: NotesConfig = NotesConfig()
    # Optional and defaulted — see `CultureConfig`. Ships INERT
    # (`enabled = false`): off, composition builds no adaptor, no schedule
    # entry and no store, so the tier is absent rather than idle.
    culture: CultureConfig = CultureConfig()
    lessons: LessonsConfig = LessonsConfig()
    # Ships INERT (`enabled = false`): off, composition builds no adaptor.
    # Optional and defaulted — see `StandingConfig`. Ships INERT
    # (`enabled = false`); off, composition builds no `debrief.Wiring`, the
    # debrief stage returns without awaiting anything, and `<standing>` never
    # renders. It is the one optional tier whose work runs INSIDE the live
    # reply slot, so turning it on lengthens a hold real people wait behind —
    # a decision, not a default.
    standing: StandingConfig = StandingConfig()
    # Optional and defaulted — see `MusicConfig`. Ships INERT
    # (`enabled = false`); off, `AdapterRegistry.from_config` builds no
    # `MusicPlayerManager` and `DiscordAdapter` gets `music_manager=None`
    # (its own default), so `/music` commands never get registered.
    music: MusicConfig = MusicConfig()
    # Optional and defaulted — see `ComputeConfig`. Ships INERT
    # (`enabled = false`); the `ComputeJobStore` is built unconditionally
    # regardless (the `reminders` shape), only the poller's tick loop is gated.
    compute: ComputeConfig = ComputeConfig()
    # Optional and defaulted, see `ComputerConfig`. Ships INERT
    # (`enabled = false`): no runtime is built and every computer tool answers
    # `unavailable`.
    computer: ComputerConfig = ComputerConfig()
    # Optional and defaulted, see `OtpConfig`. Ships INERT (`enabled = false`):
    # no service is built and no `/app/v1/otp/*` route is mounted.
    otp: OtpConfig = OtpConfig()
    # Optional and defaulted — see `ImageGenConfig`. Ships INERT
    # (`enabled = false`); a misconfigured lane degrades the tool to `unavailable`
    # rather than crashing the daemon.
    image_gen: ImageGenConfig = ImageGenConfig()
    # Optional and defaulted, see `VideoGenConfig`. Ships INERT (`enabled = false`): no
    # backend is built and the registry withholds `video_create`.
    video_gen: VideoGenConfig = VideoGenConfig()
    # Optional and defaulted — see `ToolsConfig`. Empty means no MCP server is
    # mounted.
    tools: ToolsConfig = ToolsConfig()
    # Optional and defaulted — see `ParticipationConfig`. Ships INERT
    # (`enabled = false`): off, no `ParticipationTrigger` is built, the
    # dispatcher drops the participation verdict, and no quiescence window is
    # ever evaluated. On, it STILL engages nowhere until a room is opted in via
    # `relevance_gate = true` in policy.toml.
    participation: ParticipationConfig = ParticipationConfig()
    proactive: ProactiveConfig = ProactiveConfig()
    # The Relevance Gate: the admission call in FRONT of the participation turn.
    # Ships INERT; see the cross-section validator below for why it cannot be enabled
    # on its own.
    relevance_gate: RelevanceGateConfig = RelevanceGateConfig()
    reaction: ReactionConfig = ReactionConfig()
    engagement: EngagementConfig
    doctrine: DoctrineConfig
    # Optional and defaulted — see `DreamerConfig`. Reads the bind-mounted tree with
    # mtime reload by default. Ships INERT (`enabled = false`): off, no
    # `DreamerRuntime` is constructed.
    dreamer: DreamerConfig = DreamerConfig()
    # Optional and defaulted — see `CognitionConfig`. Ships OFF: no stimulus is
    # written and no wake evaluator runs until the operator sets `enabled`.
    cognition: CognitionConfig = CognitionConfig()
    # Optional and defaulted — see `SubagentConfig`. Ships OFF.
    subagent: SubagentConfig = SubagentConfig()
    # Optional and defaulted — see `AssessConfig`. Ships OFF: `nora assess
    # run-nightly` is refused and nothing is written to `assess_report`.
    assess: AssessConfig = AssessConfig()
    # Optional and defaulted — see `LineageConfig`. Ships OFF.
    lineage: LineageConfig = LineageConfig()
    # Optional and defaulted — see `RecordQueryConfig`. Ships OFF.
    record_query: RecordQueryConfig = RecordQueryConfig()
    # Optional and defaulted — see `DocumentsConfig`. Ships OFF.
    documents: DocumentsConfig = DocumentsConfig()
    # Optional and defaulted — see `ExternalFilesConfig`. Ships OFF.
    external_files: ExternalFilesConfig = ExternalFilesConfig()
    # Nora's one Google identity, by env-var NAME — see `GoogleConfig`.
    google: GoogleConfig = GoogleConfig()
    identity_file: Path
    policy_file: Path
    obs: ObsConfig
    evals: EvalsConfig

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> Config:
        _resolve_context_field(self, "identity_file", info)
        _resolve_context_field(self, "policy_file", info)
        # `[dreamer]` is a DEFAULTED section, so it cannot resolve its own path
        # the way `DoctrineConfig` does: its default instance is built once at
        # class-definition time, with no validation context to resolve against.
        # Resolved here instead, where the context is.
        _resolve_context_field(self.dreamer, "dir", info)
        return self

    def dreamer_model(self) -> str:
        """`[dreamer].model`, falling back to `[llm].model` when empty.

        Lives on `Config` and not on `DreamerConfig` because the fallback
        source is a SIBLING section a sub-model cannot see — the one place that
        holds both. `MediaConfig.describer_for`'s rule applies unchanged: the
        fallback is folded in HERE and nowhere else, so exactly one place knows
        it exists and no caller can reach past it to the raw field.

        The fallback is `[llm].model`, the process-wide default; an explicit
        `[dreamer].model` wins over it.
        """
        return self.dreamer.model or self.llm.model

    def dreamer_max_tokens(self) -> int:
        """`[dreamer].max_tokens`, falling back to `[llm].max_tokens` when 0.

        Here and not on `DreamerConfig` for `dreamer_model`'s reason, which is
        structural rather than stylistic: the fallback source is a SIBLING
        section a sub-model cannot see. Folding it in at the one place that
        holds both means no caller can reach past it to the raw field and read
        a 0 as a real ceiling.

        `0` is the sentinel, never a value — see `DreamerConfig.max_tokens`
        for what this ceiling is and, more to the point, what it is NOT
        (`budgets[...].output_tokens` is the Strands LOOP budget and binds
        without raising; this is the provider's per-call ceiling and raises
        `MaxTokensReachedException`).
        """
        return self.dreamer.max_tokens or self.llm.max_tokens

    def _dream_unit_hold_known_minimum(self) -> float:
        """The provably-bounded portion of one Dreamer (adaptor, key) unit's
        cycle, shared by `dreamer_max_unit_hold_seconds`'s derivation and by
        `_the_dream_unit_hold_exceeds_its_components`'s startup gate, so the
        two can never disagree about what "the minimum" means.

        Three terms, each a CONSERVATIVE MAX rather than a per-adaptor figure;
        `_the_dream_unit_hold_exceeds_its_components`'s own docstring has the
        proof that a max-of-maxes bounds any real adaptor's actual sum:

        - `max_samples x (max_timeout + WATCH_FLUSH)`: the model budget, PLUS the
          reasoning-strip watch's own write bound. `_sample` calls `watch.flush()`
          on EVERY exit (`nora.agent.dreamer` `_sample`'s own docstring), OUTSIDE
          `budget.timeout_seconds`'s own `wait_for`.
        - `2 x DREAM_MAX_STORE_CALLS_PER_TICK x store_timeout_seconds`: the two
          store bounds (`inputs`, `commit`).
        - `media.join_timeout_seconds`: `media_join.enrich`'s own internal bound
          (`MediaJoin._lookup`'s `wait_for`), PER QUERY, not per unit cycle.
          `enrich` chunks the batch's distinct media shas into groups of
          `DEFAULT_QUERY_BLOBS` and calls `_lookup` once per chunk
          (`nora.domain.media_join._enrich`), so a batch whose rows reference more
          distinct blobs than that makes MORE than one query. Counted ONCE here on
          purpose: there is no declared ceiling on media blobs per row to multiply
          this by, and inventing one would be a number nobody chose wearing the
          shape of a bound. `dreamer_max_unit_hold_seconds`'s flat margin absorbs
          EXTRA queries the same way it absorbs the cursor reads below: each
          additional chunk costs at most another `join_timeout_seconds`.

        **What this does NOT cover, stated rather than implied.** The cursor
        reads the unit wall clock exists for (`clock.last_fired`/`record_fire`,
        every `language.*` watermark call, `record.since` itself) carry no bound of
        their own, so there is no declared number to sum here.
        `dreamer_max_unit_hold_seconds`'s flat margin, not this method, is where
        their NORMAL (non-hung) running time is given room.
        """
        from nora.agent.reasoning_strip import DEFAULT_WRITE_TIMEOUT_SECONDS

        max_timeout = max([
            # `DreamBudgetConfig()`'s own field default — the ceiling an
            # adaptor with no `[dreamer.budgets.<name>]` block, or a partial
            # one that leaves `timeout_seconds` unset, actually runs under
            # (`DreamerConfig.budget_for`).
            DreamBudgetConfig().timeout_seconds,
            *(b.timeout_seconds for b in self.dreamer.budgets.values()),
        ])
        model_budget = max_timeout + DEFAULT_WRITE_TIMEOUT_SECONDS
        store_budget = (
            2 * DREAM_MAX_STORE_CALLS_PER_TICK * self.dreamer.store_timeout_seconds
        )
        media_budget = self.media.join_timeout_seconds
        return model_budget + store_budget + media_budget

    def dreamer_max_unit_hold_seconds(self) -> float:
        """`[dreamer] max_unit_hold_seconds`, DERIVED when unset.

        `None` (the shipped default) means "the known minimum
        (`_dream_unit_hold_known_minimum`) plus a flat margin", computed fresh from
        THIS config's own numbers rather than a literal that goes stale the moment an
        operator raises a budget timeout or a sample count.

        **`_DREAM_UNIT_HOLD_MARGIN_SECONDS = 300.0`, flat rather than a
        multiplier.** A multiplier would scale with the model budget even though
        nothing about a SLOWER MODEL CALL makes a single-row Postgres read slower.
        What the margin has to cover is everything
        `_dream_unit_hold_known_minimum` counts once but that can genuinely run more
        than once (see its own docstring): `clock.last_fired`/`record_fire` and every
        `language.*` watermark call (roughly eight single-row primary-key reads or
        upserts against a LOCAL database with no cross-network hop) PLUS any
        `media_join.enrich` query beyond the first, at up to `join_timeout_seconds`
        each, for a batch whose rows reference more than `DEFAULT_QUERY_BLOBS`
        distinct media blobs. 300s is several dozen times what any one cursor read
        takes even badly degraded and leaves room for roughly a hundred extra
        media-join chunks. It does not balloon on a deployment that raises a model
        budget for reasons unrelated to either.

        Explicit (non-`None`) is passed through UNCHANGED: an operator who set a
        number made a deliberate judgement, and
        `_the_dream_unit_hold_exceeds_its_components` still refuses a value at or
        below the known minimum; the margin is a DEFAULT convenience, not a floor
        this method imposes on every value.
        """
        if self.dreamer.max_unit_hold_seconds is not None:
            return self.dreamer.max_unit_hold_seconds
        return (
            self._dream_unit_hold_known_minimum()
            + _DREAM_UNIT_HOLD_MARGIN_SECONDS
        )

    @model_validator(mode="after")
    def _the_gate_needs_something_behind_it(self) -> Config:
        """`[relevance_gate] enabled` requires `[participation] enabled`.

        The gate decides whether a turn happens; if no turn CAN happen, the whole
        ambient path is a model call whose answer is discarded. Ambient traffic
        constructs no agent, so a GO with participation off would reach nothing: the
        gate would be correct and completely inert.

        A cross-section relation, so it cannot live on either sub-model
        (`_lead_clears_the_tick`'s reason). A refusal at startup is the only version
        of this that is not a silent behaviour change, because the failure mode here
        is INVISIBLE: every window would be judged, every GO counted, and the room
        would never hear anything, which reads exactly like a gate correctly
        declining.

        This holds until a narrower engage path is built for ambient speech, at
        which point the thing behind the gate is that path and this validator names
        it instead.
        """
        if self.relevance_gate.enabled and not self.participation.enabled:
            raise ValueError(
                "[relevance_gate] enabled = true requires [participation] "
                "enabled = true — the gate decides whether a participation "
                "turn runs, so with the turn off every GO reaches nothing and "
                "the gate is inert while looking healthy"
            )
        return self

    @model_validator(mode="after")
    def _lead_clears_the_tick(self) -> Config:
        """`[reminders].min_lead_seconds` must exceed `[heartbeat].tick_seconds`.

        Otherwise a reminder set with the minimum lead becomes due before the tick
        that is already running comes round again, and Nora answers 「等陣提你」 to
        something she will not say until after the moment has passed. The promise
        she makes at create time is the thing at stake, which is why this refuses
        rather than warns.

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

    @model_validator(mode="after")
    def _repeat_floor_clears_the_tick(self) -> Config:
        """`[reminders] min_interval_seconds` must be >= `[heartbeat]
        tick_seconds`, for the same reason `min_lead_seconds` must clear it.

        The floor's whole justification (in `config.py`, in
        `ReminderCaps.min_interval_seconds` and in
        `nora.domain.reminders.MIN_PERIOD_SECONDS`) is that the tick is how
        often she wakes, so a series faster than it cannot be delivered at all.
        `tick_seconds` is an operator setting, so the floor is checked against
        it: 900 with a 300-second floor would make every five-minute series
        undeliverable at rate, the failure the floor exists to prevent.

        `>=` rather than `>` because a series landing exactly on the tick is
        deliverable — unlike a minimum LEAD, which must clear the tick already
        running.
        """
        if self.reminders.min_interval_seconds < self.heartbeat.tick_seconds:
            raise ValueError(
                f"[reminders] min_interval_seconds = "
                f"{self.reminders.min_interval_seconds} must be at least "
                f"[heartbeat] tick_seconds = {self.heartbeat.tick_seconds}: a "
                "series may not repeat faster than she wakes up to check, or "
                "occurrences pile up and fire late in clumps."
            )
        return self

    @model_validator(mode="after")
    def _the_standing_slot_hold_fits_its_budget(self) -> Config:
        """The debrief HOLDS the conversation's slot, so the number that
        matters is the SUM of every bound on that hold, not any one of them.

        `[llm] timeout_seconds` is a REQUIRED field with no default, and the
        stage's store calls need bounds of their own, so a worst case argued in
        prose drifts. A stage that can stop a live conversation answering, on a
        turn that already delivered its reply, is checked here instead: the same
        refuse-to-start posture `policy.toml` and `max_chars`-above-the-ceiling
        use.

        **NOT gated on `enabled`**: a number which lies is wrong on the page
        whether or not it is running, and «the moment it IS switched on is the
        worst time to discover it meant something else». Gated, setting
        `max_slot_hold_seconds = 60` while the tier is off boots fine, and then
        flipping `enabled = true` refuses to start at exactly the moment least
        wanted. Ungated, the same edit fails on the next restart, while the
        operator is still holding the config file.
        """
        hold = (
            # **`[llm] timeout_seconds` IS NOT IN THIS SUM.** It bounds nothing in
            # this stage: the fold's model call is built with `[standing]
            # timeout_seconds` AND wrapped in a `wait_for` on the same value, and
            # never sees the `[llm]` figure at all. Including it would make an
            # operator-facing budget read a longer hold than the real bounded one;
            # erring safe does not excuse a number that lies about the deployment.
            self.standing.timeout_seconds
            # The stage's bounded store awaits, counted by the constant and nowhere
            # else: a count named in a comment goes stale when the stage changes. The
            # stage's own test counts the awaits against the constant.
            + STANDING_STORE_CALLS_PER_FOLD * self.standing.store_timeout_seconds
        )
        if hold > self.standing.max_slot_hold_seconds:
            raise ValueError(
                f"[standing] would hold a conversation's slot for up to "
                f"{hold:g}s — [standing] timeout_seconds "
                f"({self.standing.timeout_seconds:g})"
                f" + {STANDING_STORE_CALLS_PER_FOLD} x store_timeout_seconds "
                f"({self.standing.store_timeout_seconds:g}) — which is over "
                f"[standing] max_slot_hold_seconds "
                f"({self.standing.max_slot_hold_seconds:g}). Lower a timeout or "
                f"raise the budget deliberately; do not leave them disagreeing."
            )
        return self

    @model_validator(mode="after")
    def _cognition_sessions_need_the_dreamer(self) -> Config:
        """A Cognition session borrows the Dreamer's model source, tool source
        and objective corpus, so `[cognition]
        enabled = true` with `[dreamer] enabled = false` would build an
        evaluator whose hook has nothing to run on. Refused at load, where the
        operator is looking, not discovered as a session that never starts."""
        if self.cognition.enabled and not self.dreamer.enabled:
            raise ValueError(
                "[cognition] enabled = true needs [dreamer] enabled = true: "
                "a session runs on the Dreamer's model, tools and objectives"
            )
        return self

    @model_validator(mode="after")
    def _lessons_need_the_dreamer(self) -> Config:
        """A Lessons session is a Dreamer adaptor; `[lessons] enabled` without
        the Dreamer would build nothing and say nothing."""
        if self.lessons.enabled and not self.dreamer.enabled:
            raise ValueError(
                "[lessons] enabled = true needs [dreamer] enabled = true: "
                "the Lessons session runs on the Dreamer"
            )
        # The doctrine check is "a model other than the session": a judge on the
        # writer's own model grades its own homework. Compared by the configured
        # name, so an operator who points two aliases at one model is not caught; a
        # same-name one is.
        judge = self.lessons.check_model or self.llm.model
        if self.lessons.enabled and judge == self.dreamer_model():
            raise ValueError(
                f"[lessons] check_model resolves to {judge!r}, the Dreamer's own model: "
                "the doctrine check must be a model other than the one writing lessons"
            )
        return self

    @model_validator(mode="after")
    def _cognition_has_no_call_ceilings(self) -> Config:
        """`[dreamer.budgets.cognition]` names no per-tool, memory or pass-wide
        call ceiling (DEC-247: a session is bounded by rounds, tokens and time,
        never per tool). `max_memory_calls`, `max_tool_calls` and a
        `tool_calls` table are fields of the SHARED budget class that every
        other adaptor uses, so the class accepts them; this refuses them for
        this one adaptor, where they would be inert (the guard holds no such
        ceiling) and an operator would believe they bound something."""
        block = self.dreamer.budgets.get("cognition")
        if block is None:
            return self
        stale = sorted(
            k for k in ("max_memory_calls", "max_tool_calls", "tool_calls")
            if k in block.model_fields_set
        )
        if stale:
            raise ValueError(
                f"[dreamer.budgets.cognition] sets {', '.join(stale)}: a Cognition session "
                "has no call ceiling of any kind (DEC-247); it is bounded by turns, "
                "total_tokens, output_tokens and timeout_seconds. Delete the key(s)"
            )
        return self

    @model_validator(mode="after")
    def _cognition_sessions_need_their_own_budget(self) -> Config:
        """`[cognition] enabled = true` REFUSES to load without an explicit
        `[dreamer.budgets.cognition]` block. `budget_for` answers an adaptor with
        no block from the class defaults (6 turns, 40k tokens, 120 s), which is a
        budget nobody chose for a session that reads and writes across up to
        `max_items` items. The block is in `config.example.toml`; a deployment that
        turns sessions on copies it, or sizes its own."""
        if self.cognition.enabled and "cognition" not in self.dreamer.budgets:
            raise ValueError(
                "[cognition] enabled = true needs an explicit "
                "[dreamer.budgets.cognition] block: without one the session silently "
                "runs on the code defaults (200 turns, 10.24M tokens, 3600 s). Copy the block "
                "from config.example.toml or size your own"
            )
        return self

    @model_validator(mode="after")
    def _subagent_has_no_call_ceilings(self) -> Config:
        """`[dreamer.budgets.subagent]` names no per-tool, memory or pass-wide call ceiling
        (DEC-247: the worker is bounded by rounds, tokens and time, never per tool).
        The shared budget class accepts `max_memory_calls`, `max_tool_calls` and a `tool_calls`
        table; this refuses them for this adaptor, where they would be inert (its guard holds no
        such ceiling) and an operator would believe they bound something. Same rule as
        `_cognition_has_no_call_ceilings`."""
        block = self.dreamer.budgets.get("subagent")
        if block is None:
            return self
        stale = sorted(
            k for k in ("max_memory_calls", "max_tool_calls", "tool_calls")
            if k in block.model_fields_set
        )
        if stale:
            raise ValueError(
                f"[dreamer.budgets.subagent] sets {', '.join(stale)}: a sub-agent has no call "
                "ceiling of any kind (DEC-247, amended); it is bounded by turns, "
                "total_tokens, output_tokens and timeout_seconds. Delete the key(s)"
            )
        return self

    @model_validator(mode="after")
    def _subagent_needs_its_own_budget(self) -> Config:
        """`[subagent] enabled = true` REFUSES to load without an explicit
        `[dreamer.budgets.subagent]` block, for `_cognition_sessions_need_their_own_budget`'s
        reason: `budget_for` would answer a missing block from the class defaults (6 turns, 40k
        tokens, 120 s), a budget nobody chose for a job that runs for minutes. The block is in
        `config.example.toml`."""
        if self.subagent.enabled and "subagent" not in self.dreamer.budgets:
            raise ValueError(
                "[subagent] enabled = true needs an explicit [dreamer.budgets.subagent] "
                "block: without one the worker silently runs on the class defaults (6 turns, "
                "40k tokens, 120 s). Copy the block from config.example.toml or size your own"
            )
        return self

    @model_validator(mode="after")
    def _subagent_needs_the_dreamer_and_the_poller(self) -> Config:
        """The worker runs on the Dreamer's model source and delivers through the compute
        poller, so `[subagent] enabled = true` needs `[dreamer] enabled` and `[compute]
        enabled`. Refused at load (the smaller change: the poller is `[compute]`'s and stays
        its own switch), not found as a job that never starts or a result that is never
        delivered."""
        if self.subagent.enabled and not (self.dreamer.enabled and self.compute.enabled):
            raise ValueError(
                "[subagent] enabled = true needs [dreamer] enabled = true (the worker runs "
                "on the Dreamer's model source) and [compute] enabled = true (the poller "
                "that delivers its result)"
            )
        return self

    @model_validator(mode="after")
    def _video_needs_the_poller(self) -> Config:
        """A clip is delivered by the compute poller, so `[video_gen] enabled = true` needs
        `[compute] enabled = true`: refused at load, the subagent precedent, rather than found
        as a clip that renders, is paid for, and is never delivered."""
        if self.video_gen.enabled and not self.compute.enabled:
            raise ValueError(
                "[video_gen] enabled = true needs [compute] enabled = true (the poller that "
                "delivers the finished clip)"
            )
        return self

    @model_validator(mode="after")
    def _the_dream_unit_hold_exceeds_its_components(self) -> Config:
        """`[dreamer] max_unit_hold_seconds`, EXPLICITLY SET, must PROVABLY
        exceed the known-bounded pieces the wall clock in `_run_adaptor`
        (`nora/app/runtime.py`) wraps, or the wall clock could fire before a
        legitimate run finishes: `_the_standing_slot_hold_fits_its_budget`'s
        own argument, one tier over.

        **Only checked when the operator SET a value.** `None`, the shipped
        default, is `Config.dreamer_max_unit_hold_seconds`'s job: it derives
        a number that is ALWAYS above `_dream_unit_hold_known_minimum` by
        construction (that minimum plus a positive margin), so re-checking a
        derived value here would be redundant at best and, worse, a second
        place the two arithmetics could drift apart.

        See `Config._dream_unit_hold_known_minimum` for what the minimum
        covers and what it deliberately does not (the unbounded cursor reads,
        which `dreamer_max_unit_hold_seconds`'s margin, not this check, gives
        room to run their NORMAL, non-hung length in).
        """
        explicit = self.dreamer.max_unit_hold_seconds
        if explicit is None:
            return self
        minimum = self._dream_unit_hold_known_minimum()
        if explicit <= minimum:
            raise ValueError(
                f"[dreamer] max_unit_hold_seconds ({explicit:g}) does not "
                f"exceed the known-bounded minimum it must wrap: "
                f"{minimum:g}s (model budget, both of #60's store bounds, "
                f"and the media join's own bound — see Config."
                f"_dream_unit_hold_known_minimum). Lower a timeout/sample "
                f"count, raise max_unit_hold_seconds deliberately, or leave "
                f"it unset to derive one; do not leave them disagreeing."
            )
        return self



def load(path: Path, *, require_secrets: bool = True) -> Config:
    """Validate settings; runtime loading also resolves required credentials.

    Schema-only callers pass require_secrets=False. Credential-source and restart
    semantics are documented in docs/configuration.md.
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
    if cfg.llm.provider:
        provider = cfg.providers[cfg.llm.provider]
        cfg = cfg.model_copy(update={"llm": cfg.llm.model_copy(update={
            "base_url": provider.base_url, "api_key_env": provider.api_key_env,
        })})
    cfg._secret_path = path.parent / "secret.toml"
    from nora.secret import SecretError, read, require
    try:
        cfg._credentials = read(path.parent / "secret.toml") if require_secrets else None
        if require_secrets:
            default = cfg.providers[cfg.llm.provider] if cfg.llm.provider else cfg.llm
            require(cfg.credentials, default.api_key_env)
            for route in cfg.model_routes.values():
                if route.provider:
                    require(cfg.credentials, cfg.providers[route.provider].api_key_env)
    except SecretError as exc:
        raise ConfigError(str(exc)) from None
    return cfg
