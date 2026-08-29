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
    field_validator,
    model_validator,
)

from nora.domain.facts import (
    DEFAULT_MAX_PREDICATE_CHARS,
    DEFAULT_MAX_TERM_CHARS,
    FACT_PREDICATE_CEILING_CHARS,
    FACT_TERM_CEILING_CHARS,
)
from nora.domain.standing import (
    DEFAULT_STANDING_MAX_CHARS,
    DEFAULT_STANDING_STORE_TIMEOUT_SECONDS,
    STANDING_BODY_CEILING_CHARS,
    STANDING_STORE_CALLS_PER_FOLD,
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
    # `nora_sender_id` REMOVED (DEC-102, 2026-08-14). It named Nora in her own
    # audit rows, and it was a string an operator typed — one deployment's guess
    # at who Nora is. With a second platform wired, a single configured name is
    # necessarily wrong for every platform but one. Both send paths now take her
    # id from the PLATFORM itself (`PlatformAdapter.resolve_identity().primary`,
    # already resolved for the turn). Nothing ever matched on it: `is_nora` is a
    # column of its own, which is what actually marks her rows.
    enabled: bool


class DiscordPlatformConfig(BaseModel):
    """`design/discord-integration/`. No `base_url` (unlike Signal): the
    adapter talks to Discord's own Gateway/REST endpoints directly, not a
    local daemon. `token_env` names the environment variable holding the bot
    token (see `.env.example`) — the secret itself never lives in config.toml,
    the same convention `route.api_key_env` uses for the Google Maps key.

    Defaulted, unlike `SignalPlatformConfig`: Signal is core to every Nora
    deployment and has always been required; Discord is optional, so a
    config.toml written before it existed must still load with Discord off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    token_env: str = "DISCORD_BOT_TOKEN"  # noqa: S105 — names an env var, not a secret


class PlatformsConfig(BaseModel):
    """A model, not a free-form dict: `extra="forbid"` must keep rejecting an
    unknown platform name. A later phase widens this into a registry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: SignalPlatformConfig
    discord: DiscordPlatformConfig = Field(default_factory=DiscordPlatformConfig)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    workspace_id: str
    # The Nora aiPeer id (Honcho v2, design/24). Her replies feed under this peer
    # into each conversation's session, building ONE global Nora self-model card
    # (decision B-global). Defaulted so a config.toml written before v2 loads
    # unchanged; the value is a peer id, never a secret.
    ai_peer: str = "nora"
    # How many of HER OWN conclusions ride in `<self_card>` — episodic
    # continuity, so a promise she made last night survives falling out of the
    # transcript window. 0 disables the block entirely.
    #
    # Small on purpose. This is a PER-TAG block, so unlike the doctrine it is
    # not in the cached static head and is re-sent on every model call of the
    # loop: her live representation measured 2,459 chars (~820 tokens) at 25
    # conclusions on 2026-08-11, which at `turns = 20` is ~16k of the 128k
    # budget spent on background nobody asked for. Eight measured ~1,080.
    self_card_conclusions: int = 8
    # How many of HER CONCLUSIONS ABOUT THE TAGGER ride in `<peer_history>`.
    # 0 disables the block, which restores the pre-2026-08-15 behaviour exactly:
    # `about` then remains the only way she reaches a person's episodes.
    #
    # A SEPARATE knob from `self_card_conclusions` rather than a reuse. The two
    # blocks have different lifetimes and different failure modes — hers is
    # continuity she cannot get any other way, theirs duplicates a tool that
    # already exists — so the number that is right for one is not evidence
    # about the other, and one knob would make tuning either of them a guess
    # about both.
    #
    # Same per-tag arithmetic as above and it is the binding constraint here
    # too: this block is re-sent on every model call of the loop, so 8
    # conclusions (~1,080 chars, ~360 tokens) is ~7.2k of the 128k budget at
    # `turns = 20`. That is the honest price of not making her ask.
    tagger_card_conclusions: int = 8


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
    # Per-tool call-count overrides, applied OVER `NATIVE_TOOL_BUDGETS` by
    # `ToolRegistry.budgets()`. Empty by default: the table in
    # `nora/agent/tools.py` states every default with the reasoning beside the
    # tool it belongs to (DEC-10), and this is only the per-deployment delta —
    # an operator raising a cap without a code change and a rebuild.
    #
    # Names are VALIDATED against the live registry below, so a typo is a
    # startup failure. Silently ignoring `{"send_replies": 8}` would leave an
    # operator certain they had raised a cap that never moved, which is the
    # same class of bug as a dead `peer_card` endpoint answering `[]` for the
    # life of a deployment.
    budgets: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _budget_names_and_values_are_real(self) -> LLMConfig:
        # Imported at call time, not module scope. NOT because a contract
        # forbids it — no `lint-imports` rule covers this edge, and the claim
        # that one does was wrong (verification pass, 2026-08-11). The reason
        # is plainer: `nora.config` is imported by everything including tools
        # that never build an agent, and `nora.agent.tools` pulls in the whole
        # tool tree. Deferring keeps that cost on `Config` construction, which
        # is the one moment the tree is loading anyway.
        #
        # Safe from cycles by the contract that DOES exist: nothing under
        # `nora.agent` may import `nora.config`, so this edge has no return
        # path. And it validates against the LIVE table, so adding or removing
        # a tool changes what is accepted with no second list to maintain.
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

    # ON by default — a `config.toml` written before this field existed keeps
    # matching `text` exactly as it always did (the `[speech]`/`[weather]`
    # INERT-by-default shape runs the other way here: the trigger phrase was
    # already live for every deployment, so defaulting this OFF would be the
    # silent behavior change, not defaulting it on). Turning it off narrows
    # `CompositePolicy` (`nora/app/composition.py`) down to `MentionPolicy` +
    # `ReplyToNoraPolicy` — a real `@mention` or a reply to one of her own
    # messages still engage her; only the spoken/typed trigger PHRASE stops
    # matching. `min_interval_seconds` (the debounce) is unaffected either way.
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
    # Whether the retention sweep runs AT ALL. `max_per_conversation` is the
    # window when it does; this is the switch, and the two are separate keys on
    # purpose — «keep everything» is not expressible as a number. Widening the
    # window to fake it keeps `prune_all`'s `row_number()` scan over the whole
    # `messages` table running every tick in order to delete nothing, and leaves
    # a figure in the file that reads like a policy nobody holds.
    #
    # Defaults TRUE, which is this file's convention (a config.toml written
    # before a key existed keeps its behaviour), NOT a recommendation: at
    # `max_per_conversation = 500` a busy group evicts its oldest message every
    # time a new one lands, and on 2026-08-15 the deployed group was sitting at
    # exactly 500 with its earliest surviving message eight days old. Anything
    # that wants to read history — the transcript window's own past, a future
    # consolidator — needs this OFF, and Nora's own config.toml sets it so.
    #
    # `SqlRecordStore.prune_all` and the `_PrunableStore` port stay wired
    # either way; only the spawn in `__main__` is conditional, so turning this
    # back on is a restart rather than a code change.
    prune_enabled: bool = True

    @model_validator(mode="after")
    def _resolve_paths(self, info: ValidationInfo) -> StoreConfig:
        _resolve_context_field(self, "data_dir", info)
        return self


class DescriberConfig(BaseModel):
    """One modality's describer: may it run, and through which model.

    Two fields and not one, because «no model named» and «switched off» are
    different states an operator reaches for differently, and both have to be
    expressible without deleting the other.

    `provider` is the port selector (the `[image_gen].provider` shape, DEC-137):
    `"omniroute"` (default) keeps the process-wide OmniRoute client — a config
    written before this field existed must load unchanged — and `"kimi"` builds
    a DIRECT client to `api.kimi.com/coding/v1`. The direct lane exists because
    OmniRoute drops `video_url` content blocks (probe 2026-08-30: router prompt
    tokens 95 vs native 3164 for the same payload); Kimi's own endpoint accepts
    base64 video data-URIs and `ms://` file references.

    `api_key_env` NAMES the env var holding the direct lane's key (DEC-8):
    the value never lands in config.toml. Empty with `provider="kimi"` falls
    back to `KIMI_CODE_API_KEY`. A kimi provider whose key does not resolve is
    a wiring gap — the modality answers `unavailable` («I couldn't check»),
    never a half-built client call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = ""
    provider: Literal["omniroute", "kimi"] = "omniroute"
    api_key_env: str = ""
    # A per-modality byte ceiling for the describer, overriding the shared
    # `media.vision_max_bytes` (which exists for still images — "no still
    # image worth describing is that large"). Video bytes are routinely far
    # larger than any still, so the image-sized ceiling would turn every
    # real clip into `too_large`; a modality that does not need more leaves
    # this 0 and inherits the shared ceiling.
    max_bytes: int = 0


class DescribersConfig(BaseModel):
    """The three modalities, keyed by the thing they read.

    Audio and video default to `enabled = False` with no model: the seam ships
    INERT, the way `[presence]`/`[search]`/`[compute]`/`[language]` did before
    it. Image defaults ON because describing a picture is existing, shipped
    behaviour and this split must not switch it off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: DescriberConfig = DescriberConfig(enabled=True)
    audio: DescriberConfig = DescriberConfig()
    video: DescriberConfig = DescriberConfig()


class RustfsBlobConfig(BaseModel):
    """`[media.rustfs]` — an S3-compatible object-store backend for blob
    custody, alongside the filesystem default (DEC-136).

    Ships OFF (`enabled = false`): a `config.toml` written before this existed
    keeps writing to `store.data_dir / "media"` exactly as before, and turning
    this on is a deliberate operator choice, not a side effect of upgrading.
    When on AND the credentials resolve, the composition root builds a
    `RustfsBlobStore` instead of a `FilesystemBlobStore` — same port, same
    `BlobCipher` sealing the bytes before they leave the process, only WHERE
    the ciphertext lands changes. A misconfigured RustFS backend (missing
    endpoint/bucket, or an env var that does not resolve) degrades to the
    filesystem default rather than to custody OFF — the existing behaviour is
    always the safe fallback, matching `encryption_key_env`'s own posture.

    `access_key_env`/`secret_key_env` NAME the env vars (DEC-8), never inline
    credentials — and default to the SAME names `drop`'s own RustFS client
    already reads (`RUSTFS_ROOT_USER`/`RUSTFS_ROOT_PASSWORD`), so a deployment
    that already runs `drop` needs no new secret, only new wiring.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    endpoint: str = ""
    bucket: str = "nora-media"
    access_key_env: str = "RUSTFS_ROOT_USER"
    secret_key_env: str = "RUSTFS_ROOT_PASSWORD"  # noqa: S105 — names an env var, not a secret


class MediaConfig(BaseModel):
    """Byte custody for attachments (DEC-43).

    **Every field is defaulted and the whole section is optional**, unlike most
    of this file — a live `config.toml` written before this existed must keep
    loading, and `extra="forbid"` means a required section would be a startup
    failure on upgrade rather than a feature.

    There is deliberately **no filesystem path key here.** Media lives under
    `store.data_dir / "media"` by default — the record row and the blob it
    names are one custody boundary, backed up and deleted together. A
    media-specific root would let them be separated, and the first time that
    happened the transcript would reference blobs on a volume nobody backed
    up. (Under SQLite this root was `store.db_path`'s directory; a Postgres
    URL has no directory, so `data_dir` now carries it — still one key, still
    one boundary.) `rustfs` below is the one exception, and only because an
    object-store bucket is not a filesystem path in the first place — DEC-136.

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
    # The per-modality describers. `vision_model` above stays as the IMAGE
    # model's deprecated alias so a config written before this split keeps
    # loading — see `describer_for`. The other `vision_*` knobs keep their names
    # and stay shared across modalities: each is cited by name in DEC-56's
    # reasoning and in `derive_vision_timeout`'s docstring, and only the MODEL
    # has to differ per modality.
    describe: DescribersConfig = DescribersConfig()

    # ---- reading a sticker at INGEST (sticker spec §6) -----------------------
    #
    # The vision fields above govern `media_fetch`, which Nora CHOOSES to call.
    # These govern the automatic read that happens to every sticker before the
    # audit row is written, and they are separate because the two calls have
    # different budgets and different risk. Ships FALSE: document 26 slice 5's
    # position, and the animated-WebP question (spec §7) is unresolved until a
    # probe answers it on the deployed gateway.
    sticker_descriptions: bool = False
    # NOT a sentence-vs-transcription budget — the §7 probe (2026-08-15, live
    # MiMo-v2.5) found that reasoning tokens and answer tokens share this one
    # ceiling, so a small allowance is spent thinking with nothing left to
    # emit. 128 returned HTTP 200 with empty content on 4/4 calls (surfacing
    # as `unavailable`, not a timeout); 256 succeeded 2/3; 512 was the lowest
    # value that succeeded 3/3. 1024 matches `vision_max_tokens` above and
    # buys margin over that floor rather than pricing it exactly.
    sticker_prompt_max_tokens: int = 1024
    # DELIBERATELY not derived from `llm.timeout_seconds` the way
    # `derive_vision_timeout` is. That invariant protects the agent LOOP from a
    # hung call; this call runs on the ingest path before the loop exists, so
    # reusing it would borrow a guarantee about the wrong thing. The real
    # constraint: the custody stage is inline and the listener awaits it, so
    # this stall delays the next message — and when the sticker rides on the
    # TAGGING message it is added straight to Nora's reply latency. The §7
    # probe timed nine successful-or-failed calls at 3.9s-12.2s, with no
    # correlation to image size or token budget (gateway variance); 20s is
    # headroom over that observed maximum, not a guess.
    sticker_timeout_seconds: float = 20.0
    # The description is untrusted model output rendered into the durable body.
    # Bounded here; newline, bracket and identifier screening live in
    # `nora.domain.media.sanitize_description` and the custody stage.
    sticker_description_max_chars: int = 200

    def describer_for(self, modality: str) -> DescriberConfig | None:
        """The describer for one modality, with the legacy alias resolved.

        `None` for a modality nothing here describes — the caller's cue to
        answer `unavailable` («I couldn't check»), NEVER `nothing_found`
        («there's nothing there»). A wiring gap reported as an empty result is
        the R-27 failure this repo names as an ERROR served as a PASS.

        `vision_model` is folded in HERE and nowhere else, so exactly one place
        knows the alias exists: a config written before the split names only
        `vision_model`, and it has to keep describing images (see
        `test_a_config_written_before_the_split_still_describes_images`). The
        explicit key wins when both are set, or the alias would pin the new key
        open and the split would be a no-op.
        """
        # Against the FIELD SET, not `getattr` alone: `self.describe` is a
        # pydantic model, so `getattr(self.describe, "model_dump")` returns a
        # bound method — truthy, not a `DescriberConfig`, and typed as one by an
        # annotation mypy cannot see through. The caller would then
        # `AttributeError` on `.enabled` at startup. Unreachable while every
        # caller passes a literal; a lie in the signature regardless.
        if modality not in DescribersConfig.model_fields:
            return None
        describer: DescriberConfig | None = getattr(self.describe, modality, None)
        if describer is None:
            return None
        if modality == "image" and not describer.model:
            return describer.model_copy(update={"model": self.vision_model})
        return describer


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
    """Nora's own link shortener — `drop`'s internal `POST /api/links`
    (the `2026-08-12-nora-drop-design` spec under `docs/superpowers/specs/`
    §5, cutover 2026-08-12; the mint contract itself is unchanged from the
    Worker it replaced, `deploy/shortener/` — same bearer-auth, same
    `{"url"}` in / `{"slug","short_url"}` out shape — so this class did not
    need to change, only its default `base_url` and `token_env` did).

    **Optional, defaulted, `enabled` False** — the `[route]` shape: off (or an
    unset token) leaves long URLs in replies, never a failure. The shortener is
    SELF-HOSTED on purpose — a third-party one would see every destination the
    group routes to — and it is never load-bearing: a mint that fails leaves
    the long URL exactly as it was. `token_env` NAMES the env var holding the
    mint bearer token (DEC-8); the same value is `drop`'s own secret.

    `base_url` points at `drop`'s INTERNAL listener (`http://drop:8081` in
    this deployment) — the write API is deliberately unreachable from the
    public internet (spec §5a); the public `short_url` a mint returns is
    built server-side by `drop` from its own `DROP_PUBLIC_BASE_URL`
    (`https://nora.example.net`), which this class never needs to know.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str = "http://drop:8081"
    token_env: str = "NORA_DROP_TOKEN"  # noqa: S105 — names an env var, not a secret
    timeout_seconds: float = Field(default=5.0, gt=0)


class PreviewConfig(BaseModel):
    """Sender-generated link previews on Nora's outbound messages.

    **Optional, defaulted, `enabled` False** — the `[route]`/`[shortener]`
    shape, and off for a sharper reason than either of those: this is the one
    capability that makes Nora FETCH a URL that came out of a model, from her
    own network. DEC-73 deleted `web_fetch` and its whole SSRF apparatus
    precisely because Tavily is never handed a URL; enabling this section is an
    operator deciding to take a bounded version of that exposure back, so it is
    a decision rather than a default. `nora.infra.preview` documents the guard
    and the one residual risk it does not close.

    No key, so unlike `[search]` there is nothing else gating it — `enabled` is
    the whole switch. Off, a reply containing a link goes out exactly as it does
    today.

    `timeout_seconds` is small on purpose: this runs on the reply path with a
    human watching a 👀, so a dead host must cost a couple of seconds and a
    missing card, never a noticeable pause. `image` is separately switchable
    because it is the expensive half — a second fetch, and up to
    `image_max_bytes` base64'd into the JSON-RPC body — while the title and
    description are most of the value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    timeout_seconds: float = Field(default=3.0, gt=0)
    max_bytes: int = Field(default=262_144, gt=0)
    image: bool = True
    image_max_bytes: int = Field(default=262_144, gt=0)


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
    (§9, operator decision: 720 — see the field's own comment for the sizing
    math and why it moved off 180, design/27 §5).
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
    # 180 -> 720 (operator, 2026-08-19): 378 B/row x ~1,800 fixes/day x 720d ~=
    # 490MB — cheap, and it is what keeps Tier-4 forecasting training data from
    # being pruned before it is usable.
    retention_days: int = Field(default=720, ge=1)
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


class AnalyticsConfig(BaseModel):
    """The presence analytics substrate — the DERIVED layer (docs/superpowers/
    specs/2026-08-18-presence-analytics-substrate-design), DEC-115.

    Ships INERT like `[presence]`/`[search]`/`[compute]`/`[language]` before
    it: `enabled` defaults False, and while off `nora.analytics` builds
    nothing and writes nothing (spec §6, Acceptance Criterion 11).

    Computation knobs ONLY — no consumer knobs (spec §1.1: the substrate
    serves no particular consumer, and nothing here may name one).

    The three fire conditions (spec §4): `safety_lag` (threshold, backlog of
    unprocessed FIXES not messages), `settled_seconds` (event, the
    arrival/departure boundary — `[language]`'s `quiet_seconds` shape
    applied to movement), `max_age_seconds` (fires at lag == 0 ON PURPOSE,
    because labelling is recency-gated — spec §3.5/§4.2).

    `batch_limit` is inert in steady state (1,443x measured headroom, spec
    §4.3) and binds only during a full re-derive; `backfill_batch_limit`
    (used once a subject's lag exceeds `backfill_threshold`) is what makes
    that rebuild ~14 minutes instead of ~12 hours — the operator's own
    decision (2026-08-19) that this ships in P0 because a 12-hour rebuild
    means `stay_radius_m`/`stay_min_dwell_seconds` (both inherited, both
    unvalidated per spec §7.1) would never actually get tuned.
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
    # Tier 3 (design/27 §4, issue #6, DEC-119): robust_z_score's own
    # fail-closed floor — fewer than this many prior stays at a place and
    # no anomaly claim is made, ever (nora.domain.mobility.robust_z_score's
    # own docstring). 5 is a starting point, not measured — same honesty
    # design/27 §7.1 already gives stay_radius_m/stay_min_dwell_seconds.
    anomaly_min_samples: int = Field(default=5, ge=2)
    # |z| above this increments nora_analytics_anomalies_total — a METRIC
    # only; this substrate emits typed facts, never a consumer-facing
    # alert (spec §1.1's neutrality rule still applies to Tier 3).
    anomaly_z_threshold: float = Field(default=3.0, gt=0)
    # Tier 5 (design/27 §4, issue #6): corridor identity is a SIMILARITY
    # match, not exact — two trips along the same road on different days
    # rarely produce byte-identical address sequences (a slightly
    # different fix cadence, a missing address on one fix). Neither value
    # is measured; same "inherited, not measured" honesty design/27 §7.1
    # already gives stay_radius_m/stay_min_dwell_seconds.
    corridor_max_edit_distance: int = Field(default=2, ge=0)
    corridor_min_samples: int = Field(default=3, ge=1)
    # Tier 5: how many not-yet-clustered trips one corridor-clustering pass
    # may read back and re-cluster (`MobilityStore
    # .unclustered_named_trips_for_subject`). Unlike the two knobs above
    # this one IS measured, because `cluster_trips` is O(n^2) in an
    # edit-distance metric that is itself expensive, and the pass now runs
    # over an ACCUMULATED pool every cycle rather than one tick's handful
    # of fresh trips.
    #
    # Measured on this machine (2026-08-20), same corpus shape as
    # `MobilityRollup._record_corridors`' own table — n trips over 6
    # recurring corridors of 6-10 road names, jittered:
    #
    #     n=200  296 ms    n=500  1.87 s    n=1000  7.59 s
    #     n=400  1.19 s    n=600  2.67 s    n=2000  30.6 s
    #
    # Budget: one subject's clustering must stay under 2% of a
    # `tick_seconds=120` tick (2.4 s), which the curve puts at n≈560.
    # Rounded DOWN to 500 (1.87 s, 1.6% of a tick) for headroom on a host
    # that also runs Nora, signal-cli, Postgres and Honcho, and so several
    # subjects still fit: 4 subjects cost ~7.5 s, 6% of a tick.
    #
    # The pool DRAINS — a trip leaves it the moment it is linked to a
    # corridor — so this bound is a ceiling against pathological growth (a
    # subject whose routes never repeat), not the steady-state size. It is
    # read newest-first, so saturating it ages out the OLDEST never-matched
    # trips rather than starving the ones that just arrived.
    corridor_cluster_batch_limit: int = Field(default=500, ge=1)
    # Tier 5: `extract_trips`'s noise gate — a run of movement fixes
    # shorter than this between two stays is DROPPED rather than written
    # as a trip, the same fail-closed shape `min_dwell_ms` gives stays.
    # `ge=2`, not `ge=1` (unlike `cluster_min_samples`, whose scale this
    # otherwise matches): a one-fix "trip" has no PAIR for
    # `haversine_distance_m`/`derive_speed_kmh` to run over, so its
    # `distance_m` and `avg_speed_kmh` are structurally 0.0 — admitting
    # one would feed a fabricated zero-speed sample into
    # `presence_corridor_speed`'s percentiles.
    min_trip_fixes: int = Field(default=3, ge=2)
    # Trip settlement (design/27 §4, 2026-08-20 trip-settlement design
    # §2.4): the safety valve against a stuck `is_driving` signal — if
    # Life360 ever keeps reporting `True` continuously (a stale/stuck
    # flag, a bug) while the device is still actively sending fixes, a
    # trip could otherwise never settle and hold the watermark back
    # forever. This is NOT protection against a device that stops
    # reporting entirely: the cap is computed from the held-back group's
    # OWN fix-to-fix span, which freezes the instant new fixes stop
    # arriving, so a genuinely dark device causes an unbounded stall this
    # cap cannot fire against — that case self-heals a different way,
    # once fixes resume, via the existing chunk-gap split. This default
    # is a JUDGMENT CALL, not a measured value — no genuine multi-hour
    # trip data exists yet to derive a percentile from (the trips in
    # production today are exactly the fragments this fix corrects).
    # 3 hours is generous enough to cover a real long drive without
    # leaving a stuck signal unbounded.
    max_trip_duration_seconds: float = Field(default=10800.0, gt=0)
    tz_name: str = "Asia/Hong_Kong"
    # The substrate's own metrics/health endpoint. 9100 is Nora's own
    # (`[obs.metrics]`), 9101 is the presence collector's
    # (`PresenceConfig.metrics_port`), 9102 is the drop service's public
    # listener — the next free port in the same numbering.
    metrics_port: int = 9103

    @model_validator(mode="after")
    def _corridor_pool_can_hold_a_corridor(self) -> AnalyticsConfig:
        """A cluster-batch limit below `corridor_min_samples` makes corridor
        birth arithmetically impossible — the pool could never hold enough
        similar trips for `cluster_trips` to call any of them a cluster.

        That is exactly the shape of the bug this knob exists to fix
        (Tier 5's final whole-branch review, Critical #1: clustering that
        could only ever see one tick's batch minted zero corridors, forever,
        silently). Refusing the config is where it is cheapest to catch: the
        failure it prevents has no log line and no metric of its own, only a
        counter that never moves."""
        if self.corridor_cluster_batch_limit < self.corridor_min_samples:
            raise ValueError(
                "analytics.corridor_cluster_batch_limit "
                f"({self.corridor_cluster_batch_limit}) is below "
                f"corridor_min_samples ({self.corridor_min_samples}) — no "
                "corridor could ever be created"
            )
        return self


class SearchConfig(BaseModel):
    """Web search AND page extraction for the `web_search`/`web_fetch` tools —
    Tavily (DEC-73, DEC-116).

    **Optional and every field defaults**, the `RouteConfig` shape. `enabled`
    defaults **False** and ships that way: both tools reach a third party and
    spend credits, so both are off until an operator turns this on AND a key is
    present. Off (or keyless) leaves the registry without a provider and
    `web_search`/`web_fetch` answer `unavailable` (R-5), never a silent empty.

    SearXNG + Firecrawl and the OLD `web_fetch` are RETIRED (DEC-73): the
    self-hosted fetcher, the model-chosen-URL fetch over Nora's OWN network, and
    the SSRF surface with it — the `is_global`/`allowed_urls`/`deploy/egress.sh`
    apparatus — are gone. `web_fetch` returns on DEC-116 via Tavily's separate
    `/extract` endpoint, which fetches from Tavily's servers exactly as `/search`
    does, so the SSRF argument is unchanged by its return.

    `api_key_env` NAMES the env var holding the Tavily key (DEC-8), never the key
    itself — shared by both verbs, one key. `search_depth` is `"basic"` (1
    credit) or `"advanced"` (2, richer extraction); `max_results` bounds the
    search count (Tavily allows ≤20); `content_max_chars` clamps each search
    result's snippet. `extract_depth` is the same two-tier choice for `/extract`;
    `extract_max_chars` clamps a fetched page's content — set higher than
    `content_max_chars` because a page is one whole document rather than a
    snippet, but still bounded: a tool result is re-sent on every subsequent
    model call, so what accumulates must be bounded (the fetch-token lesson,
    2026-08-08).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    api_key_env: str = "TAVILY_API_KEY"
    url: str = "https://api.tavily.com/search"
    search_depth: Literal["basic", "advanced"] = "basic"
    max_results: int = Field(default=5, ge=1, le=20)
    content_max_chars: int = Field(default=1500, ge=1)
    timeout_seconds: float = 12.0
    extract_url: str = "https://api.tavily.com/extract"
    extract_depth: Literal["basic", "advanced"] = "basic"
    extract_max_chars: int = Field(default=3000, ge=1)


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


class SpeechSttConfig(BaseModel):
    """Local speech-to-text: which endpoint, which language, and a timeout.

    `[speech.stt]` is a first-class `[speech]` child (not a reuse of
    `[media.describe.audio]` — a voice note is conversation content, not an
    attachment description; see the 2026-08-21 STT infrastructure spec §0).

    Optional, defaulted, and INERT by default — the `[search]`/`[weather]`
    shape: `enabled = false` means Nora never calls out, and a config.toml
    written before this existed keeps loading (`extra="forbid"` makes that a
    real constraint). When enabled, the voice-note ingest path calls
    `endpoint` with the OpenAI-compatible `/v1/audio/transcriptions` contract;
    `language` hints the model's LID — NOT a BCP-47 tag, but one of
    FunASR/SenseVoice's own short codes (`auto`/`zh`/`en`/`yue`/`ja`/`ko`/
    `nospeech`, per `deploy/stt/server.py`'s `lid_dict`) — any other string is
    silently treated as `auto` by the model itself; `timeout_seconds` bounds
    ONE call (the whole ingest path waits for it — see the stage docstring).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    endpoint: str = "http://stt:8000"
    language: str = "yue"
    timeout_seconds: float = Field(default=30.0, gt=0)


class SpeechTtsConfig(BaseModel):
    """`[speech.tts]` — outbound speech, deliberately deferred.

    Present so the `[speech]` shape is stable and a later part lands without a
    schema migration. Inert (`enabled = false`) — nothing reads this yet, and
    the deploy/stt service does not do TTS. No provider is chosen or
    committed here (MiniMax / edge-tts / xAI undecided; operator deferred the
    TTS part as more complex).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: str = ""
    voice: str = ""


class SpeechConfig(BaseModel):
    """`[speech]` — Nora's voice capability, STT now and TTS later.

    Optional, defaulted, and INERT by default — the same convention as
    `[presence]`/`[search]`/`[weather]`/`[compute]`: a config.toml written
    before speech existed keeps loading, and nothing turns itself on because a
    section is missing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stt: SpeechSttConfig = SpeechSttConfig()
    tts: SpeechTtsConfig = SpeechTtsConfig()


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


class ComputeConfig(BaseModel):
    """Nora Compute's background poller + backend (`nora.app.compute_poller`,
    `nora.infra.compute.docker_backend.LocalDockerBackend`).

    Optional, defaulted, and INERT by default — the `[heartbeat]` shape: a
    config.toml written before this section existed keeps loading, and
    `make_artifact`'s slow path answers `unavailable` until an operator turns
    this on. UNLIKE `[heartbeat]`, the composition root builds the
    `ComputeJobStore` unconditionally regardless of `enabled` (mirroring the
    reason `nora.app.composition` already gives for `reminders`): the slow
    path needs somewhere to persist a job whether or not the poller that
    delivers it is running, and a stranded `FIRING` row must be swept at boot
    whether or not the feature is currently switched on — only the poller's
    own tick loop is gated on `enabled`.

    `sandbox_image`/`job_timeout_seconds`/`max_concurrent_jobs` are
    `LocalDockerBackend`'s own construction knobs (Nora Compute spec §2/§3).
    `poll_tick_seconds` is `ComputeJobPoller`'s wake interval — the same
    "best case for lateness, not an upper bound" shape `[heartbeat]
    .tick_seconds`'s own docstring describes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    sandbox_image: str = "smith:latest"
    job_timeout_seconds: int = 300
    max_concurrent_jobs: int = 2
    poll_tick_seconds: float = 10.0
    # `LocalDockerBackend`'s per-container resource caps -- docker-py's own
    # `containers.run` kwarg shapes: `mem_limit` a size string, `nano_cpus`
    # billionths of a CPU core (2_000_000_000 = 2 vCPUs). 1g/unbounded-CPU
    # (the first-shipped values) was tight for real workloads -- charts,
    # maps, larger markdown/html renders.
    mem_limit: str = "8g"
    nano_cpus: int = 2_000_000_000
    # NAMES the env var carrying the HOST-side path of whatever is bind-mounted
    # at `store.data_dir` — `*_env` for the same DEC-8 reason every secret is:
    # the VALUE is a deployment fact injected by the host, never a line in a
    # committed config file. Not a secret, but the same problem: only the host
    # knows it, and this container cannot compute it.
    #
    # It exists because `store.data_dir` is a path INSIDE this container
    # (`/app/data`), and `LocalDockerBackend` hands a workspace path to the
    # HOST Docker daemon as a bind-mount source. The daemon resolves it on the
    # host filesystem, where `/app/data` does not exist — so it would create an
    # empty directory there and every job would fail with nothing to read and
    # no exception raised. `docker-compose.yml` sets this variable from THE
    # SAME expression it uses for the `:/app/data` mount source, so the two
    # cannot disagree; `nora.app.composition` refuses to start when `enabled`
    # is true and this is unset or relative.
    data_host_root_env: str = "NORA_DATA_HOST_ROOT"


class VersionConfig(BaseModel):
    """The startup upgrade note (DEC-97, `nora.app.announce`).

    `announce` defaults to TRUE, which is the opposite of `[heartbeat]`,
    `[presence]` and `[search]` — and the asymmetry is deliberate. Those three
    ship inert because they reach a service, hold a key, or speak on a timer that
    a config written before they existed never opted into. This one is gated by
    something stronger than a flag: an UNSTAMPED BUILD ANNOUNCES NOTHING. A
    source checkout and any image built without `scripts/build-image.sh` have no
    `nora/version.json`, so `nora.infra.version.VersionManifest` answers
    `unavailable` and the composition root never even constructs the announcer.
    Defaulting it off as well would mean a correctly stamped upgrade goes
    unannounced because of a line nobody knew to add — the failure this feature
    exists to remove.

    It is here rather than in `policy.toml` because it is a DEPLOYMENT question
    ("does this deployment speak on boot"), not a trust one. WHERE she announces
    is a trust question, and that is read from `policy.toml`'s conversations —
    the rooms the operator already allowed her into, never a second list here
    that could silently disagree with the first (DEC-8).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    announce: bool = True


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
    # stated: `Config._lead_clears_the_tick` refuses the pair outright — this
    # was a comment in three files and a rule in none.
    min_lead_seconds: int = Field(default=120, ge=1)
    max_horizon_days: int = Field(default=365, ge=1)   # matches ReminderCaps
    # DEC-85. A recurring reminder is a CHAIN with only one `pending` row at a
    # time, so `max_pending_per_requester` counts a whole series as one — this
    # is the cap that bounds the series itself, in occurrences. Every series is
    # bounded: `RecurrenceRule.count` is required, so «forever» is not a state
    # the domain can represent, and this is the ceiling a request is clamped to.
    max_occurrences_per_series: int = Field(default=60, ge=1)
    # An IANA zone NAME, never an offset: a recurrence keeps a LOCAL wall clock
    # («08:00, whatever the offset does that week»), which «+08:00» cannot
    # express. Validated at series construction — an unknown zone raises
    # `UnknownRecurrenceZoneError` rather than being guessed at.
    timezone: str = "Asia/Hong_Kong"


class LanguageConfig(BaseModel):
    """The Dreamer — `nora.app.runtime.dream_loop`, distilling the slang a
    room has made its own out of its own message record (the
    2026-08-15 room-language-dreamer design doc under
    docs/superpowers/specs).

    **The whole section is optional and every field defaults**, the
    `[route]`/`[presence]` shape: a `config.toml` written before this
    section existed must keep loading, which `extra="forbid"` makes a real
    constraint rather than a courtesy.

    `enabled` defaults to **False** — a new subsystem does not switch itself
    on. Off, `nora.__main__.App.run` never spawns `dream_loop` (logging the
    same shape `store.prune_enabled` uses) and composition wires
    `language=None` into `TranscriptInjection`, exactly the "not yet wired"
    state Task 9 shipped it in — no model call is ever scheduled and the
    prefill's room-language block never renders.

    `tick_seconds = 300` (5 min): the loop's WAKE interval, the same
    "best case for lateness, not a bound" shape `[heartbeat].tick_seconds`
    documents — one tick runs every due scope in turn, so a busy deployment
    with many rooms can take longer than one interval to get back to a
    given scope. Five minutes is frequent enough that a burst of slang gets
    noticed within a session, without polling a per-scope `lag` count on
    every idle room every few seconds.

    `safety_lag = 300`: the first fire condition — `lag > safety_lag` fires
    once 300 unconsolidated human messages have piled up, independent of how
    quiet the room has gone since. This is the anti-runaway bound: a room
    that never falls quiet (so the second condition below never fires)
    still gets consolidated before its backlog grows large enough to bump
    into `batch_limit` and force a multi-tick catch-up.

    `quiet_seconds = 1800` (30 min): the second fire condition, paired with
    `lag > 0` — a room that has gone quiet for half an hour is a
    conversation that has PAUSED, the natural moment to consolidate what it
    just said without cutting off a burst still in progress. Deliberately
    shorter than `[heartbeat].tick_seconds` is long (60s) is a different
    question entirely: that bounds a reminder's lateness to a human waiting
    on it, this bounds how long a finished conversation sits unconsolidated
    with nobody waiting on anything.

    `max_age_seconds = 86400` (24h): the third fire condition, and the ONLY
    one that does not require `lag > 0` — it fires at `lag == 0` ON PURPOSE.
    `LanguageConsolidator.consolidate` rescores unconditionally, and rescore
    is what lets a word's `recognition_score` DECAY over time; a dormant
    room whose lag never grows would otherwise never rescore and its
    lexicon would render `live` forever, describing a slang era that ended
    months ago. 24h is the cadence that keeps decay visible without
    rescoring a silent room needlessly often.

    `batch_limit = 400`: the ceiling `RecordStore.since` reads per cycle.
    Bounds both the one model call's prompt size and
    `SqlLanguageStore.append_observations`'s single `INSERT ... SELECT`
    (each candidate costs ~7 bind params in one `UNION ALL`, so a batch
    above ~4,600 candidates would approach Postgres's 32,767-parameter
    ceiling — 400 messages, each yielding at most a handful of candidates,
    sits far under that). A scope whose `lag` exceeds this drains over
    several ticks rather than one, which is correct: this is a background
    distillation, not a turn anyone is waiting on.

    `max_rendered_lexemes = 6`: the RENDER cap, threaded to
    `nora.agent.prefill.build_prefill`'s `room_language_max_entries` via
    `EngagementPipeline`/`FirePath` — MATCHES that call's own default
    (`DEFAULT_ROOM_LANGUAGE_MAX_ENTRIES = 6`, Task 10's reviewed value)
    rather than raising it. This block sits in the UNCACHED volatile tier, so
    every extra rendered entry is paid on EVERY turn; doubling that per-turn
    cost to 12 was an earlier draft's unmeasured guess, and the reasoning
    for it (below) argues the OPPOSITE direction from what it concluded — a
    LARGER cap gives the renderer LESS room to drop an exampleless entry
    before under-filling, not more, so "give it more room" was never a
    reason to go above 6. Raise this only against a measured per-turn token
    cost, the same discipline `LLMConfig.max_tokens`'s own docstring insists
    on for its budget.
    Deliberately smaller than
    `nora.engagement.context.DEFAULT_ROOM_LANGUAGE_STORE_LIMIT` (15), which
    this section does NOT override — `SqlLanguageStore.room_language`
    applies its `LIMIT` with no filter on whether an entry has a quotable
    example, and the renderer then drops exampleless entries and cannot
    reach further down the ranked list to backfill. Equal numbers make the
    block silently under-fill on any tick where even one top-ranked lexeme
    has no example yet; keeping the store fetch wider than what is rendered
    is what gives the renderer room to drop a few and still fill the block.
    `Config._render_cap_stays_below_the_store_limit` REFUSES to start rather
    than merely document the relation — the `[reminders]`/`[heartbeat]`
    precedent (`Config._lead_clears_the_tick`): that relation "was a comment
    in three files and a rule in none," and an operator raising this value
    past the store limit would silently under-fill the block instead of
    being told why.

    `model`: the ONE completion `LexemeObserver` makes per cycle. Defaults to
    `[llm].model`'s own value (`"ollamacloud/deepseek-v4-flash"`) — the
    Dreamer is a background job nobody is waiting on, so reusing the
    deployment's already-working tier is a reasonable default, but this is a
    SEPARATE knob because an operator may want a cheaper or differently-tuned
    model for a bulk classification task than for a live conversational turn.

    `timeout_seconds = 120.0`: bounds `_LanguageModelClient.complete`
    (`nora.app.composition`), matching `[llm].timeout_seconds`'s own shipped
    value rather than inventing a second number with no basis — this call is
    the same shape (one `chat.completions.create`), just over a batch prompt
    instead of a turn's tools/doctrine. Every other model call in the
    codebase is explicitly bounded (`nora.engagement.stages.invoke`'s
    `asyncio.wait_for`, `nora.infra.vision.client.derive_vision_timeout`);
    this was the one exception, and quietly so — a hung call here leaves
    `dream_loop` marking the scope `running` and skipping it tick after tick
    (`nora_dream_skipped_total{reason="in_flight"}`) with no exception ever
    raised, so the exponential backoff non-negotiable 4 exists for — which
    only triggers on an exception — never engages. See
    `_LanguageModelClient`'s own docstring for the full argument.

    `verify_known_entities = False`: a THIRD gate, alongside `enabled` and
    `[search].enabled`, before `LexemeObserver` gets a `SearchProvider`
    collaborator at all (the 2026-08-20 dreamer-known-entity-check design
    doc under `docs/superpowers/specs`). Confirmed false
    positives from live traffic — "Big Walk" (an actual game Pulse already
    tracks) recorded as room-coined slang — are what this exists to catch:
    a well-formed candidate whose `canonical_form` turns up an established
    public meaning on `web_search` is dropped rather than stored. Off by
    default even when both other gates are on: verifying spends an extra
    Tavily credit AND an extra model completion per candidate, and turning
    search on for the chat agent is not the same decision as spending that
    budget on the Dreamer too.

    `verify_max_candidates = 10`: bounds how many of one cycle's candidates
    get the (search + classify) round trip — live traffic sees 0-3 candidates
    per fired batch, so this caps the WORST-CASE tail latency
    (`verify_max_candidates * (search_timeout + model_timeout)`) without
    touching the common case. Candidates beyond the cap are kept unverified,
    the same fail-open direction the whole step takes on any failure (see the
    design doc) — "did not get to check" is never treated as "assume the
    worst."
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    tick_seconds: float = Field(default=300.0, gt=0)
    safety_lag: int = Field(default=300, ge=1)
    quiet_seconds: float = Field(default=1800.0, ge=0)
    max_age_seconds: float = Field(default=86_400.0, gt=0)
    batch_limit: int = Field(default=400, ge=1)
    max_rendered_lexemes: int = Field(default=6, ge=1)
    model: str = "ollamacloud/deepseek-v4-flash"
    timeout_seconds: float = Field(default=120.0, gt=0)
    verify_known_entities: bool = False
    verify_max_candidates: int = Field(default=10, ge=1)

    # Dreamer Plan 2 (2026-08-20 exchange-patterns design) — gated by this
    # SAME `enabled` flag, not a second one: both consolidators are the one
    # Dreamer's work over the one shared watermark (`nora.app.runtime
    # ._dream_once`), and splitting them onto independent on/off switches is
    # an operator decision this design does not make unasked.
    #
    # `exchange_model`: `None` defaults to `model` above (one level down from
    # how `model` itself defaults to `[llm].model`) — a separate knob exists
    # because an operator may reasonably want a different tier for
    # closed-set act classification than for open-vocabulary lexeme glossing.
    exchange_model: str | None = None
    # `max_rendered_exchanges = 4`, deliberately smaller than
    # `max_rendered_lexemes = 6`: this block sits in the SAME uncached
    # volatile tier (`nora.language.render`'s own cost argument), and an
    # exchange-pattern line is a claim about TWO people rather than one word
    # — reason enough to be more conservative about how many ride on every
    # turn's token cost, not less.
    max_rendered_exchanges: int = Field(default=4, ge=1)


class FactsConfig(BaseModel):
    """The fact tier's gate (the `2026-08-20-fact-tier-design` spec under
    `docs/superpowers/specs/`).

    Nothing writes a fact yet (the schema and the `<facts>` block ship dormant,
    Tasks 1/2/5 of that plan); this section is the flag the future adaptor
    checks and the three bounds it stays inside once it runs.

    **The whole section is optional and every field defaults**, the
    `[route]`/`[language]` shape: a `config.toml` written before this section
    existed must keep loading, which `extra="forbid"` makes a real constraint
    rather than a courtesy.

    `enabled` defaults to **False** — a new subsystem does not switch itself
    on, and this one in particular writes claims into a block every tagged
    turn reads.

    **The workspace startup check the design doc's §7 argued for was CUT**
    (operator, 2026-08-20): the store's read scopes `WHERE workspace = ...` in
    SQL, so a fact cannot reach a room outside its own workspace regardless of
    what a startup refusal would add on top — the property is structural, not
    something this section asserts. `SqlFactStore.standing_facts` is built and
    does scope that way; what keeps the tier dark in production is `enabled`
    below, not a missing reader.

    `max_adjudications_per_tick = 10`: the Phase B cap — one research loop
    (search + classify) per surviving candidate is the expensive step, same
    shape and same default as `LanguageConfig.verify_max_candidates`, which
    bounds the analogous (search + classify) round trip for slang.

    `max_room_facts = 8` / `max_sender_facts = 4`: 19 §6's two render caps, one
    per slice. The ROOM slice is the working set — more than eight and the
    block competes with the transcript it sits beside. The SENDER slice is the
    narrowest and the most often empty, and four is enough to ground «you told
    us…». They replace one `max_rendered_facts`, which was the same
    per-turn-cost argument as `LanguageConfig.max_rendered_lexemes` (6) and
    `max_rendered_exchanges` (4) — this block sits in the same uncached
    volatile tail, and a fact line additionally carries a source URL and an
    excerpt, so raise it only against a measured per-turn token cost.

    `retention_days = 180`: how long a resolved `fact_candidate` (a
    `not_covered` verdict, or a superseded fact) is kept before a future prune
    sweep drops it — matching the tool-recall-log's own `retention_days = 180`
    for the same shape of durable-but-not-forever row.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    max_adjudications_per_tick: int = Field(default=10, ge=1)
    max_room_facts: int = Field(default=8, ge=1)
    max_sender_facts: int = Field(default=4, ge=1)
    retention_days: int = Field(default=180, ge=1)
    # The OPERATING caps on the tier's model-authored strings (25 §9.1). Both
    # columns were uncapped, which is the note-body defect on a JOIN KEY:
    # `fact_candidate_triple_key` is `(workspace, subject_id, predicate,
    # object)` and the adjudicator maps a verdict back to its candidate by that
    # triple, so an unbounded subject is an unbounded index entry on the one
    # column every read path uses.
    #
    # 200 for a term because the promotion veto already refuses anything over
    # 64 as "a sentence, not a thing" — 200 leaves room for a descriptive
    # object while keeping the index key small, and anything approaching it is
    # a claim the extractor should have split.
    #
    # These are ENFORCED AT EVERY WRITE, not merely validated here: 30 §5's
    # correction is that a scheme which only refuses a config above its ceiling
    # leaves oversized rows satisfying the CHECK while defeating the prompt
    # budget the cap exists to protect.
    # 21 §10's operator-owned veto list: the extra object strings THIS room
    # decides are not things, one per line, blank lines and `#` comments
    # ignored. Its CONTENTS are configuration; its existence, ownership and
    # versioning are the design's decision.
    #
    # **NO PATH configured vetoes nothing extra** — the rules in code are the
    # floor and this only narrows what promotes. **A path that is not there
    # REFUSES TO START**: those are different operator situations, and reading
    # the second as the first meant a mount that failed to attach silently
    # re-admitted everything the room had ruled out. This comment previously
    # merged them as "an absent file vetoes nothing extra", which was the
    # sentence the code was written from.
    #
    # `""` is NO PATH. Pydantic coerces an empty string to `PosixPath(".")`,
    # which is neither `None` nor a file — so with the refusal above, an
    # operator uncommenting `config.toml`'s own documented `veto_file = ""`
    # got a boot failure naming a path they never wrote. The empty string is a
    # pydantic artifact rather than an operator intent, and it is normalised
    # HERE, where that is knowable, rather than at the reader.
    # 31 §3.4's window: how far back a message may reach when deciding what
    # Nora "just rendered". Twenty-four hours because a correction arrives
    # either in the same sitting or after somebody looked something up
    # overnight, and because it is the horizon over which a claim she rendered
    # is still a thing a member could be reacting to. The ledger is pruned at
    # TWICE this, so a row is still readable for one window after it stops
    # being eligible — which is what lets the prune be a background sweep
    # rather than a precise deletion.
    #
    # Reversal condition: the age distribution of the quote-replies that
    # resolve, which is readable from the ledger itself.
    recognition_window_hours: int = Field(default=24, ge=1)
    veto_file: Path | None = None

    @field_validator("veto_file", mode="before")
    @classmethod
    def _an_empty_path_is_no_path(cls, value: object) -> object:
        """`veto_file = ""` means «no veto list», not «the current
        directory»."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # 31 §5's own key, and it exists because the classifier runs on the REPLY
    # PATH — awaited in `_run_turn` before context assembly, holding the
    # conversation's slot, ahead of every word she says.
    #
    # Without it the classifier was built with `[language] timeout_seconds`,
    # which is **120** because it bounds a DREAMER call in a background tick
    # where a slow model costs a tick and nothing else. Inherited onto this
    # path it is two minutes of silence in a live room before she answers.
    # `[standing]`, the other stage that holds this slot, bounds itself at 20
    # and made the total a startup gate; this walked past that gate entirely.
    #
    # 8 seconds because the call is one short classification over at most
    # `max_targets` one-line claims — not research, not a fold — and because
    # the fallback is not silence: a timeout is `unavailable`, RULE-F still
    # runs, and the correction is caught on the deterministic channel or lost.
    # Losing a correction costs the status quo; two minutes of dead air costs
    # the turn.
    dispute_classify_timeout_seconds: float = Field(default=8.0, gt=0)
    # Pass 4 and the retrieval verb's endpoint. `23` §9 settles the runtime as
    # bge-m3 on the Ollama service already running on this host, reached
    # through a PORT with a fake — not a direct HTTP call from the tier — so
    # that a service which is down reports `unavailable` rather than raising.
    #
    # Empty `embedding_url` is a real deployment and not a broken one: the
    # tier runs on the lexical channel alone, which loses cross-lingual and
    # paraphrased recall in a way that is measured
    # (`SqlEmbeddingStore.lexical`) and counted
    # (`nora_knowledge_channel_total{channel="dense",status="not_configured"}`).
    embedding_url: str = ""
    embedding_model: str = "bge-m3"
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    # Two numbers because they bound different things: the first is one
    # TICK's cost, the second the blast radius of a single failed call. A
    # sweep of 200 in batches of 32 that dies on the third batch has written
    # 64 correct rows and left 136 stale, which is the intended outcome.
    #
    # The sweep runs INSIDE the adjudication tick and outside the runtime's
    # per-run timeout, so `max_embed_rows_per_sweep / embed_batch_size`
    # sequential calls at `embedding_timeout_seconds` each is the worst case
    # it can hold that tick — measured by `nora_fact_embed_sweep_seconds`.
    max_embed_rows_per_sweep: int = Field(default=200, ge=1)
    embed_batch_size: int = Field(default=32, ge=1)
    max_term_chars: int = Field(default=DEFAULT_MAX_TERM_CHARS, ge=1)
    max_predicate_chars: int = Field(default=DEFAULT_MAX_PREDICATE_CHARS, ge=1)

    @model_validator(mode="after")
    def _caps_stay_under_the_database_ceilings(self) -> FactsConfig:
        """`StandingConfig`'s validator, one tier over and for the same reason:
        a ceiling is DDL and cannot be raised from config, so a value above it
        would be refused by Postgres at write time — which is a crash in a
        background tick nobody is watching rather than a message at startup."""
        for name, value, ceiling, check in (
            ("max_term_chars", self.max_term_chars,
             FACT_TERM_CEILING_CHARS, "fact_term_fits_the_ceiling_check"),
            ("max_predicate_chars", self.max_predicate_chars,
             FACT_PREDICATE_CEILING_CHARS,
             "fact_predicate_fits_the_ceiling_check"),
        ):
            if value > ceiling:
                raise ValueError(
                    f"[facts] {name} = {value} is above the database ceiling "
                    f"of {ceiling} ({check}). The ceiling is DDL and cannot be "
                    f"raised from config; every write above it would be "
                    f"refused by Postgres."
                )
        return self


class StandingConfig(BaseModel):
    """The debrief stage's gate and its four numbers
    (`design/dreamer-agent-loop/26-standing`).

    `FactsConfig`/`NotesConfig`'s shape above, for the same reasons: the whole
    section is optional and every field defaults, so a `config.toml` written
    before this section existed keeps loading under `extra="forbid"`; and
    `enabled` defaults to **False**, because a new subsystem does not switch
    itself on. This one has the strongest version of that argument in the
    repo — it is the only background-ish tier whose work happens **inside the
    live reply slot**, so switching it on lengthens a hold that real people
    wait behind. Off, composition builds no `debrief.Wiring`, the stage returns
    without awaiting, and `<standing>` never renders.

    **`max_chars` is a knob AND the database has a ceiling, and they are two
    different jobs rather than one duplicated** (operator decision,
    2026-08-24; an earlier draft argued a storage cap is an invariant and
    therefore must not be tunable, and was overruled). This value is the
    operating cap the sink refuses at — tunable without a migration, because
    the right size is a judgement about prompts and judgements about prompts
    change. `standing_body_fits_the_ceiling_check` is the absolute bound no
    writer can pass, config included; a constraint is DDL and cannot be a knob,
    which is why the two layers exist. **A `max_chars` above the ceiling
    refuses to START** — `policy.toml`'s own shape — rather than failing at the
    first oversized fold, which would be a config error discovered by a
    production turn.

    **Why 1200.** Standing is injected on the volatile tail of EVERY turn, so
    its size is a per-turn tax rather than a storage question: ~300 tokens in
    English, but **approaching 1200 tokens in a CJK-heavy room**, and this room
    is mixed by default — the cap has to be read against the denser case.

    **`window = 40`, and 40 is the CEILING rather than a preference.** The
    debrief folds `ctx.transcript` — the rows the REPLY path already assembled
    under `[store] context_window` — so a larger value selects exactly the same
    rows, and `_the_standing_window_fits_the_transcript_it_folds` refuses it
    rather than letting the number lie.

    It was 12, on S3's argument that revising a position needs less than
    composing a reply. Operator override 2026-08-27: she answers with 40 rows
    in view, so a fold reasoning from 12 judges a turn it can only see a third
    of. Going genuinely past 40 means raising `[store] context_window`, which
    widens EVERY reply's prompt — a per-turn cost on the live path, and not a
    Standing decision.

    **`timeout_seconds = 20`, and the number that matters is the SUM** — which
    is why the sum is a startup GATE (`max_slot_hold_seconds` below) and not a
    sentence here. This paragraph used to do the arithmetic itself, against
    `[llm] timeout_seconds = 60`, a value that does not exist: the field is
    REQUIRED with no default and this deployment sets 120. It also omitted the
    two store calls entirely. It was wrong by more than half and nothing
    checked it, which is the whole argument for the validator.

    The LATENCY gate is a different reading again: `nora_debrief_seconds`
    p95 <= 5s, measured against the SECOND-QUEUED tag, because the cost never
    lands on the person being reviewed (who already has their reply) and
    always lands on whoever is behind them. `max_slot_hold_seconds` bounds the
    worst case; this bounds the typical one. Raise either only against its own
    reading.

    `model` empty means `[llm] model` — the same defaulting
    `[language] exchange_model` uses. A separate name is available because the
    fold is a different job from replying (one structured output, no tools, no
    audience) and may want a cheaper or a more literal model; leaving it empty
    is the honest default rather than guessing one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    max_chars: int = Field(default=DEFAULT_STANDING_MAX_CHARS, ge=1)
    # 40, matching `[store] context_window` — the CEILING, not a preference.
    # The debrief folds `ctx.transcript`, so it can never see more rows than the
    # reply path assembled; `_the_standing_window_fits_the_transcript_it_folds`
    # refuses a larger value rather than letting the number lie.
    #
    # It was 12, on S3's argument that revising a position needs less than
    # composing a reply. Operator override 2026-08-27: she answers with 40 rows
    # in view, so a fold reasoning from 12 judges a turn it can only see a
    # third of.
    window: int = Field(default=40, ge=1)
    timeout_seconds: float = Field(default=20.0, gt=0)
    # The bound on the stage's TWO STORE CALLS — the read of the prior position
    # and the write of the revised one. It exists because they had no bound at
    # all: `create_sql_engine` is a bare `create_async_engine` with no
    # `command_timeout`, and both calls run while the per-conversation lock is
    # HELD, so a connection hanging mid-query during the post-reply fold wedged
    # that conversation indefinitely — on a turn that had already replied.
    #
    # Small on purpose. This is a single-row primary-key read and a single-row
    # upsert against a local Postgres; if either takes seconds the database is
    # in trouble and the honest answer is `unavailable`, not a longer wait held
    # in front of the next person in the room.
    store_timeout_seconds: float = Field(
        default=DEFAULT_STANDING_STORE_TIMEOUT_SECONDS, gt=0)
    # S2's ceiling: rebuild the position from the window rather than revising
    # it once this many folds have accumulated SINCE THE LAST REBUILD.
    #
    # It is the WEAKER of the two triggers on purpose. 26 §4: "Time alone
    # re-derives healthy conversations for nothing" — the forced case catches
    # the turn that actually went wrong, and this only bounds how far drift can
    # accumulate when nothing does. If `nora_standing_rederivations_total`
    # comes back dominated by `ceiling`, the trigger has degraded into a clock
    # wearing a condition (§6) and the answer is to look at why the ungrounded
    # case is not firing, not to raise this.
    #
    # 20 is a starting point and not a measured value: at one fold per tagged
    # turn it is roughly a busy evening in one room. Nothing has run long
    # enough to say what the right number is.
    rebuild_after_folds: int = Field(default=20, ge=1)
    model: str = ""
    # The fold's reasoning budget, DECLARED rather than inherited.
    #
    # It had none: `_DebriefModelClient` never passed `reasoning_effort`, so
    # the fold ran at whatever the provider defaults to — an undeclared setting
    # on a call that spends the conversation's slot. The tag path passes
    # `[llm] reasoning_effort` explicitly (`tier.reasoning_effort`); this one
    # did not, and nobody had chosen the value it was actually using.
    #
    # **The value is deliberately NOT argued here.** `design/dreamer-agent-loop/18-flows` §Flow-D is
    # explicit that the fold's reasoning effort "is a MEASUREMENT, not a
    # setting": the fold is integration — read the prior position and the
    # trace, write the revised one — not research, so a low or absent budget is
    # *plausible*, and that is exactly the class of claim `design/dreamer-agent-loop/09-measurement`
    # forbids asserting. The design specifies a decidable spike instead: run
    # the same fold at several budgets over a FIXED SET OF REAL TRACES and
    # score whether the revised position (a) preserves what the prior held and
    # (b) integrates what the trace added — both checkable by comparison, no
    # judge needed. `tag_trace` is retained precisely so that set exists.
    #
    # So this key exists to make the spike RUNNABLE, and the default only
    # matches `[llm] reasoning_effort` so that switching the tier on changes
    # one thing at a time. Empty means "inherit `[llm] reasoning_effort`",
    # which is the honest default: it says «nobody has measured this yet»
    # rather than inventing a number the design forbids inventing.
    # A closed set, unlike `[llm] reasoning_effort`'s bare `str`. The OpenAI
    # client types this parameter as exactly these values, so a typo here
    # ("hgh") currently reaches the provider and comes back a 400 DURING a
    # fold, inside the conversation's slot. Typed, it is a boot failure
    # instead — the refuse-to-start posture the rest of this section uses.
    # `""` is the seventh legal value and means "inherit `[llm]`".
    reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = ""


    # The WHOLE hold this stage can add to a conversation's slot, asserted at
    # startup rather than reasoned about in prose.
    #
    # It exists because the prose was WRONG. `debrief.py` argued the worst case
    # as «`[llm] timeout_seconds` + `[standing] timeout_seconds` = 60 + 20», and
    # `[llm] timeout_seconds` is a REQUIRED field with no default that this
    # deployment sets to 120 — so the real bounded worst case was 140s, not 80s,
    # and the two store calls on top of it were not bounded at all. The one
    # number that decides how long a live conversation can stop answering was
    # sized against a figure that does not exist, and nothing cross-checked it.
    #
    # **What this does NOT cover, stated because the field name overclaims.**
    # It bounds every AWAIT the stage makes. `objective.objective_body(...)` is
    # a SYNCHRONOUS call that stats the bind-mounted `nora/dreamer` tree on
    # every fold (`[dreamer] reload = "mtime"`), so `asyncio.wait_for` cannot
    # bound it and it is not in this sum — and because it blocks rather than
    # awaits, it stalls the whole event loop rather than one conversation's
    # slot. Unmeasured, and a `git checkout` on the host mid-fold is the
    # trigger. Recorded rather than papered over (review, 2026-08-27); making
    # it `reload = "startup"` for this reader, or moving it off the slot, is a
    # separate change with its own measurement.
    #
    # 165s is the REAL worst case at the shipped timeouts (120 + 20 + 5x5),
    # the fifth store call being the note-proposal sink.
    # It was 150, reverse-engineered from a sum that counted two store calls
    # when the stage makes four — never an operator judgement about an
    # acceptable hold, just the arithmetic agreeing with itself. Chosen to
    # ADMIT today's deployment rather
    # than to reject it: this validator's job is to make the arithmetic
    # impossible to get wrong silently, not to relitigate `[llm]`'s own timeout,
    # which was raised to 120 deliberately on 2026-08-08. Lower it and the
    # startup refusal tells the operator exactly which numbers no longer fit.
    # 45 -> 60 (2026-08-28). 45 was 20 + 5 x 5 EXACTLY, so the default budget
    # sat on the default hold with nothing between them: a second stage
    # arriving on the same slot — recognition, 8s — made a config with no
    # `[standing]` section refuse to start, which is a default that cannot be
    # left alone. 60 leaves seven seconds of headroom, so the next stage on
    # this slot fails the GATE with a number to reason about rather than
    # failing every deployment that never set the key. The deployment sets 150
    # explicitly and is unaffected.
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


class NotesConfig(BaseModel):
    """The note tier's gate (the `2026-08-21-note-tier-design` spec under
    `docs/superpowers/specs/`).

    `FactsConfig`'s shape immediately above, for the same reasons: the whole
    section is optional and every field defaults, so a `config.toml` written
    before this section existed keeps loading under `extra="forbid"`; and
    `enabled` defaults to **False**, because a new subsystem does not switch
    itself on — least of all one whose read lands in a block every tagged turn
    carries.

    **On today: the store is built and `<notes>` renders whatever is standing.**
    Off: composition wires `notes=None` into `TranscriptInjection`, `_notes`
    answers `()` without a query, and `render_notes(())` emits no block — the
    dormant state the tier shipped in. Nothing WRITES a note yet either way
    (`nora.agent` has no `note_create`/`note_update`, `nora.dreamer` has no
    curator), so turning this on today buys a `SELECT` that returns nothing;
    it is the operator's switch for the moment a writer lands, not a
    retroactive one.

    **There is deliberately no render cap here**, and that is the one place
    this section differs from `[facts]`' `max_rendered_facts`. §5.1 decides the
    index is UNCAPPED: a note silently missing from it is invisible to her, and
    invisible is indistinguishable from absent — the same
    `unavailable`-reported-as-`nothing_found` failure R-27 exists to prevent. A
    top-N knob here would be a supported way to reintroduce it. The prompt
    budget is paid instead by the per-entry caps in `nora.domain.notes` (a
    title is ≤ 60 chars and a description ≤ 200, enforced in the constructor
    rather than truncated at render) and by the curator retiring closed notes.
    An index long enough to hurt is a curation problem, and a cap would hide it.

    No `retention_days` either: retirement is a status change, not a delete
    (§7.4) — a retired note stays readable so a lossy step can be re-derived —
    so there is nothing here for a prune sweep to bound.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False

    # --- the REVISITOR (`nora.language.note_revisitor`) ------------------
    #
    # `max_revisits_per_tick = 10`: how many DUE notes one pass judges. A
    # ceiling on the PROMPT as much as on the work — each candidate carries its
    # own evidence block, so this multiplies with `revisit_evidence_messages`
    # below into the size of one model call. A workspace with more due than
    # this drains over several ticks, which is correct for a background pass
    # nobody is waiting on, and the overflow is COUNTED
    # (`nora_note_revisit_deferred_total`) rather than silently truncated.
    #
    # `revisit_evidence_messages = 40`: messages of a note's own
    # `established_in` rendered as the evidence it is judged against.
    #
    # **This comment used to claim "there is no setting of this that causes a
    # wrong retirement". That was FALSE and an external review caught it.** A
    # truncated window whose oldest row postdates the anchor hides exactly the
    # conversation an answer would be in, so a busy room could turn an ANSWERED
    # note into a `retired-expired` one — the unrecoverable direction. The
    # sentence is kept, corrected, because a config docstring asserting a
    # safety property the code does not have is worse than one saying nothing.
    #
    # **The first correction was still false, and an external review caught
    # that too.** It claimed safety on a `_covers` that measured truncation
    # against the ANCHOR, so a window starting before the anchor passed while an
    # answer given weeks earlier — any time after the note was written, which is
    # what a closing condition allows — sat outside it. Lowering this number
    # still turned an answered note into a retired one.
    #
    # It is safe now because `_covers` measures the window against the NOTE: a
    # truncated window whose oldest row does not predate `created_at` refuses
    # the expiry rather than judging on what it happens to hold. So the cap
    # fails in ONE direction — too few messages means a note stays open longer —
    # and that is a property of the COVERAGE CHECK rather than of the number.
    # Lowering this raises how often the check WITHHOLDS a decision, never how
    # often it gets one wrong.
    #
    # Twice-wrong is worth leaving on the page: a config docstring asserting a
    # safety property is a claim about code somebody else has to verify, and
    # this one was believed twice before it was true.
    max_revisits_per_tick: int = Field(default=10, ge=1)
    revisit_evidence_messages: int = Field(default=40, ge=1)
    #: The curator, separately from the tier. `[notes] enabled` decides whether
    #: the INDEX is injected and the four tools reachable; this decides whether
    #: the background pass runs. Two switches because they are two risks: the
    #: tag path only reads, and the curator MERGES AND RETIRES — the one thing
    #: in this tier that removes something from her index. An operator turning
    #: notes on should not thereby have signed up for a pass that reorganises
    #: them.
    #:
    #: Also off because the curator is the tier's only writer that acts across
    #: notes, and 27 §2 makes that the Dreamer's alone precisely because it is
    #: the dangerous half.
    curator_enabled: bool = False

    #: MERGE GROUPS per tick, not notes. The curator's cost is bodies read, and
    #: a group is the unit it reads — typically two notes, occasionally more.
    #:
    #: Small on purpose. A collided anchor is rare (it needs two notes about the
    #: same date AND the same people), so a tick that finds three groups is
    #: already unusual, and the cap is a bound on a surprise rather than a
    #: throughput knob. `nora_note_curator_bodies_read` is what says whether it
    #: binds.
    max_groups_per_tick: int = Field(default=3, ge=1, le=50)

    #: EXPIRED notes per tick. Larger than the group cap because expiry is the
    #: common case and each costs one body: after a quiet month, every note
    #: whose date has passed becomes eligible at once, and a cap of three would
    #: take a fortnight of ticks to work through a backlog that appeared in a
    #: day.
    #:
    #: When this binds, the pass reports "nothing to do" about the notes it did
    #: not reach — the `unavailable`-as-`nothing_found` failure at the pass
    #: level, which is why the gauge exists and why the cap is generous.
    max_expired_per_tick: int = Field(default=20, ge=1, le=200)

    # NO `max_body_chars`, and its absence is the decision. The field existed
    # here with a twelve-line docstring and a range validator and NOTHING READ
    # IT — the cap was `nora.domain.notes.BODY_MAX_CHARS` in four places the
    # whole time. A knob that changes nothing is the config-file form of a
    # declared metric with no call site. It is not threaded because the cap's
    # own argument is that it is enforced in the TYPE, and `__post_init__`
    # cannot see config; making the knob true would mean moving the refusal out
    # to a store boundary to serve the knob.


class XaiImageGenConfig(BaseModel):
    """`[image_gen.xai]` — the xAI adapter's own knobs (DEC-137).

    `model` / `quality_model` mirror the same names Hermes' `image_gen/xai`
    plugin uses (`grok-imagine-image` / `grok-imagine-image-quality`).
    `refresh_token_env` NAMES the env var holding the long-lived OAuth
    refresh token (DEC-8) — the value never lands in config.toml.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = "grok-imagine-image"
    quality_model: str = "grok-imagine-image-quality"
    timeout_seconds: float = 90.0
    refresh_token_env: str = "XAI_OAUTH_REFRESH_TOKEN"  # noqa: S105 — names an env var, not a secret


class ImageGenConfig(BaseModel):
    """The image-generation tool's gate (design/proposed/29, DEC-137).

    `enabled` defaults **False** — the same reasoning every other optional
    subsystem's gate gives: a new capability does not switch itself on. A
    config.toml written before this existed keeps loading unchanged, and a
    misconfigured lane degrades the tool to `unavailable` rather than
    crashing the daemon (the DEC-136 RustFS posture).

    `provider` is the port selector — the composition root builds the
    `ImageGenerator` implementation the name selects; the model-facing tool
    never names a provider.

    `allow_reference_images` is the privacy gate for the edits API: reference
    images (a friend's photo) upload to a third-party cloud, so the default
    is OFF, and when ON the adapter still enforces that every reference is a
    Nora-held custody handle (never an external URL — §5.1).

    Credential env vars follow the DEC-8 `api_key_env`-style convention:
    config names the env var, the host's `.env` holds the secret.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: str = "xai"
    allow_reference_images: bool = False

    xai: XaiImageGenConfig = XaiImageGenConfig()


class MusicConfig(BaseModel):
    """The Discord music bot's gate (the `discord-music` design bundle,
    node 00 — overview and scope).

    `enabled` defaults **False**, same reasoning every other optional
    subsystem's gate gives: a new capability does not switch itself on. Only
    load-bearing when `[platforms.discord] enabled` is also true —
    `nora/app/composition.py` does not build a `MusicPlayerManager` for a
    platform that isn't running, but this section itself does not know or
    care which platform is enabled.

    `idle_timeout_seconds` is the bundle's node 03 (state and persistence)
    disconnect timer 2 (queue empty, nothing playing) — timer 1 (voice
    channel emptied of humans) has no config, since there is no grace period
    to size for an event that already means "leave now".

    `crossfade_seconds` — 0 (the default) disables crossfading entirely: a
    track ends and the next starts with a hard cut, today's behaviour.
    Above 0, `DiscordVoiceSession` blends the outgoing and incoming tracks'
    audio for that many seconds before a track's own natural end. Spike
    (2026-08-24): only a natural track-end crossfades; `/music skip` stays
    an instant cut on purpose (skipping is "get me out of this NOW", not a
    DJ transition).

    `autoplay_enabled` — False (default) means the bundle's ordinary
    idle-timeout behaviour: the queue empties, nothing next, leave after
    `idle_timeout_seconds`. True (the autoplay design node under
    design/discord-music/) has `MusicPlayer._advance` seed one YouTube
    "Mix" recommendation from whatever just finished before falling back
    to idle — the operator's own evidence: "i expect after it FINISH it
    will continue to add
    recommend/related song NOT stop".

    `autoplay_prewarm_seconds` — 0 (default) means the next autoplay pick
    is decided after the current track ends, the pre-R-64 behaviour.
    Above 0, `MusicPlayer` resolves the next pick (its Mix lookup AND its
    playable stream) while the current track is still audible, so the
    gap between track-end and the next track starting is instant rather
    than two fresh network calls. Only meaningful when `autoplay_enabled`
    is also True; ignored otherwise."""

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


class DreamBudgetConfig(BaseModel):
    """One adaptor's ceiling on a single Dreamer run: three caps and a clock.

    Mirrors `AdaptorBudget`, the value `nora.agent.dreamer`'s runtime actually
    receives — import-linter forbids `nora.agent` from importing `nora.config`
    at all, so the config type cannot cross that boundary. The
    `RemindersConfig`/`nora.domain.reminders.ReminderCaps` pair is the same
    shape and the same rule: the composition root builds the domain value from
    this one, and the DEFAULTS on both are the same numbers on purpose.

    `turns`/`total_tokens`/`output_tokens` are `LimitsConfig`'s three caps —
    R-8's, applied to a Dreamer run instead of a tag turn — and are three
    fields for `LimitsConfig`'s own reason: Strands' `_check_limits` SKIPS a
    cap it cannot find, so a missing one means "unbounded" rather than
    "default".

    **PER ADAPTOR, not one global ceiling.** Proving a fact is worth more calls
    than glossing a word; a single number would starve the first or overpay for
    the second.

    **The numbers are UNMEASURED starting points, not measurements** — the same
    honesty `[analytics]`'s `stay_radius_m`/`stay_min_dwell_seconds` carry
    (`INHERITED, not measured`, design/27 §7.1). `turns = 6` and
    `output_tokens = 4000` are read off `[llm.limits]`'s shipped 20/12000 scaled
    down for a background job nobody waits on; `total_tokens = 40000` is
    `[llm.limits].total_tokens / 3`. Plan gate D2 (which model the Dreamer runs)
    is OPEN and decided by measurement later; these are what a run is bounded by
    until then, not what a run was observed to need.

    **`timeout_seconds` is not optional, and the reason is specific to this
    subsystem.** Unlike a tag turn's timeout, a hung Dreamer call is QUIET:
    `dream_loop` marks the scope `running` and skips it every subsequent tick
    (`nora_dream_skipped_total{reason="in_flight"}`) without ever raising, so
    the exponential backoff — which only triggers on an EXCEPTION — never
    engages, and a hung provider is skipped FOREVER rather than backed off
    (`nora/app/composition.py`'s `_LanguageModelClient`, which records this from
    the last time it was gotten wrong). 120 s matches `[llm].timeout_seconds`
    and `[language].timeout_seconds` rather than inventing a third number with
    no basis; it wraps the WHOLE run, not one call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int = Field(default=6, ge=1)
    total_tokens: int = Field(default=40_000, ge=1)
    output_tokens: int = Field(default=4_000, ge=1)
    timeout_seconds: float = Field(default=120.0, gt=0)


class DreamerConfig(BaseModel):
    """Where the Dreamer's instruction corpus lives and how it reloads.

    Modelled field-for-field on `DoctrineConfig` above, minus the four fields
    that are about NORA's prompt rather than about reading a directory
    (`canary`, `display_name`, `language_hint` are runtime context she is told;
    there is no equivalent here because nothing in `nora/dreamer/` is rendered
    into a `<runtime_context>` block).

    **Whole section optional, every field defaulted**, the `[route]`/
    `[presence]`/`[language]` shape — so a `config.toml` written before this
    section existed keeps loading under `extra="forbid"`, and a deployment that
    never touches it gets the bind-mounted tree and mtime reload.

    Phase 1 shipped this section with **no `enabled` field**, on the argument
    that it had nothing to enable — the corpus was read by the two existing
    adaptors whether or not anything else was switched on, and a flag that gates
    nothing is a flag an operator will eventually set expecting it to gate
    something (DEC-128). That argument named its own expiry: *"it arrives with
    the Dreamer RUNTIME, shipping `false`."* This is that arrival. `enabled`
    below is what turns the section from a description of a CORPUS into the
    switch for a SUBSYSTEM, and it ships **False** — a new subsystem does not
    switch itself on, the `[presence]`/`[search]`/`[compute]`/`[language]`/
    `[analytics]`/`[facts]` shape. Off, nothing constructs a `DreamerRuntime`
    and the existing consolidators keep running exactly as they do today.

    `model = ""` means **fall back to `[language].model`** — resolved by
    `Config.dreamer_model()` and nowhere else, the `MediaConfig.describer_for`
    shape, so exactly one place knows the fallback exists. Empty is the shipped
    value ON PURPOSE: it reproduces today's tier, which makes this section's
    arrival a no-op at runtime. Plan gate D2 — which model the Dreamer runs —
    is OPEN and is answered by measurement (the DEC-58 method), not by a value
    picked here.

    `budgets` maps an ADAPTOR NAME to its `DreamBudgetConfig`. An adaptor with
    no block of its own gets `DreamBudgetConfig`'s own field defaults through
    `budget_for` — never a `KeyError` at tick time, and never an unbounded run.
    A PARTIAL block fills its missing fields from those same defaults rather
    than zeroing them, which matters because `output_tokens = 0` is not "no
    limit", it is a subsystem that silently never produces anything.

    `dir` is resolved relative to the config file, not the working directory,
    by `Config._resolve_paths` — NOT by a validator here, because a defaulted
    sub-model is constructed once at class-definition time with no validation
    context to resolve against.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dir: Path = Path("nora/dreamer")
    reload: Literal["mtime", "startup"] = "mtime"
    strict: bool = True
    enabled: bool = False
    # Empty = `[language].model`. Never read directly — `Config.dreamer_model()`
    # is the only resolver.
    model: str = ""
    # The Dreamer's OWN reasoning effort, `""` meaning «inherit `[llm]`» — the
    # shape `StandingConfig.reasoning_effort` already uses, and TYPED for its
    # reason: a typo in a loose `str` reaches the provider and returns a 400,
    # here inside a background tick where it is one more `unavailable` among
    # many. Typed, it is a boot failure, which is the refuse-to-start posture.
    #
    # **It exists because reasoning is billed as COMPLETION, and this model
    # spends the whole ceiling on it.** Measured 2026-08-28 against the real
    # objective and a real failing room's transcript, through OmniRoute at the
    # deployed settings:
    #
    #   max_tokens=2000   upstream returned nothing usable
    #   max_tokens=4000   933 completion tokens, four correct candidates
    #   max_tokens=8000   upstream returned nothing usable
    #   max_tokens=16000  7,307 completion tokens for the SAME 1,136-character
    #                     answer
    #
    # and, holding the ceiling fixed and varying only the effort:
    #
    #   effort=low   1,311 completion, 5,768 characters of deliberation,
    #                and the answer was `{"candidates": []}`
    #   effort=none  100 completion, four correct candidates
    #
    # So more room is not the lever — at 16,000 it spent seven thousand tokens
    # to write the same eleven hundred characters, and at "low" it reasons its
    # way to an EMPTY LIST on a transcript that plainly contains claims. The
    # failure has two faces depending on transcript size: silence on a small
    # one, `MaxTokensReachedException` on a real one. That is also the most
    # likely reading of August's 963 messages producing 2 rows, on this same
    # model at this same setting.
    #
    # Dreamer-WIDE rather than per-adaptor, and the other two passes are now
    # MEASURED rather than assumed — the first version of this comment said
    # neither had been, which is a gap that should not survive a review it can
    # be closed before:
    #
    #   fact-entity-resolution  low   4 of 4 runs resolved `big walk`/`Big Walk`
    #                           none  3 of 4; the fourth SPLIT them
    #   fact-adjudication       low   `supported` with a source_url, tool-free
    #                           none  `not_covered`
    #
    # Entity resolution loses a little recall at "none", and it loses it in the
    # direction `21` §5 chose on purpose: default to SPLIT, because two
    # entities wrongly kept apart is a missing edge and two wrongly merged is a
    # corrupted node. A small rate of over-splitting is the failure that design
    # asked for.
    #
    # The adjudicator's real risk was not verdict quality, it was that a model
    # told not to deliberate would stop REACHING FOR A TOOL — and an
    # adjudicator that never searches marks nothing `supported`, writes no
    # fact, and stalls the chain one step past extraction while every counter
    # reports an honest empty. Measured with a `web_search` schema attached,
    # three runs at each effort:
    #
    #   low    tool_calls=2 every run
    #   none   tool_calls=2 every run, the same two queries
    #
    # It still researches. The earlier tool-free result — "low" answering
    # `supported` with an invented `source_url` while "none" said
    # `not_covered` — was a probe with nothing to search WITH, and
    # `not_covered` was the correct answer to it rather than a recall loss.
    #
    # The Dreamer already has its own `ModelSource`, so this costs one line and
    # keeps one model object; per-adaptor would need one object per adaptor.
    # The reading that would split it is an adjudication run against real tools
    # measurably worse at "none" — that is the measurement to take when the
    # tier has traffic, and it needs production rather than a probe.
    #
    # `[llm] reasoning_effort` is UNTOUCHED, so her replies are unaffected.
    reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = ""
    budgets: dict[str, DreamBudgetConfig] = Field(default_factory=dict)

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
        return self.budgets.get(adaptor, DreamBudgetConfig())


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
    # How many times `--suite journeys` runs EACH journey; the gate binds the
    # MAJORITY per-journey verdict (F8a, external review 2026-08-10). Routing is
    # stochastic — a single run passes an 85%-per-journey model only ~52% of the
    # time and lets a lucky broken model through — so n=1 is too noisy to gate on.
    # Odd is preferable so a majority always exists.
    journey_samples: int = Field(default=3, ge=1)


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
    # Optional and defaulted — see `PreviewConfig`. Off unless an operator turns
    # it on; off means a reply carrying a link goes out with no card, which is
    # every deployment's behaviour before this section existed.
    preview: PreviewConfig = PreviewConfig()
    # Optional and defaulted — see `PresenceConfig`. Off unless an operator turns
    # it on; a config.toml without a `[presence]` section keeps loading, and the
    # collector is a SEPARATE process (`python -m nora.presence`) that reads this
    # same tree.
    presence: PresenceConfig = PresenceConfig()
    # Optional and defaulted — see `AnalyticsConfig`. Ships INERT
    # (`enabled = false`); off, `nora.analytics` builds and writes nothing
    # (spec §6, Acceptance Criterion 11).
    analytics: AnalyticsConfig = AnalyticsConfig()
    # Optional and defaulted — see `SearchConfig`. Off (inert) unless an operator
    # turns it on AND a Tavily key is present (`api_key_env`, DEC-8/DEC-73); a
    # config.toml without a `[search]` section keeps loading.
    search: SearchConfig = SearchConfig()
    # Optional and defaulted — see `WeatherConfig`. Keyless (HK Observatory), so
    # safe to enable; ships off in the example, on in this deployment (DEC-70).
    weather: WeatherConfig = WeatherConfig()
    # Optional and defaulted — see `SpeechConfig`. Ships INERT (`enabled =
    # false`); off, Nora neither transcribes voice notes at ingest nor speaks
    # outbound (TTS deferred). On, `[speech.stt]` calls the local SenseVoice
    # endpoint (`stt:8000`) during the voice-note custody stage.
    speech: SpeechConfig = SpeechConfig()
    # Optional and defaulted — see `HeartbeatConfig`/`RemindersConfig`. The tick
    # ships INERT (`enabled = false`); the caps apply the moment a reminder can
    # be created, whether or not anything is firing them.
    heartbeat: HeartbeatConfig = HeartbeatConfig()
    reminders: RemindersConfig = RemindersConfig()
    # Optional and defaulted — see `LanguageConfig`. Ships INERT
    # (`enabled = false`); off, `dream_loop` is never spawned and
    # composition wires `language=None` into `TranscriptInjection`, the
    # "not yet wired" state Task 9 shipped it in.
    language: LanguageConfig = LanguageConfig()
    # Optional and defaulted — see `FactsConfig`. Ships INERT
    # (`enabled = false`); nothing writes a fact yet, so this is a flag and
    # three bounds for a future adaptor, not a running subsystem.
    facts: FactsConfig = FactsConfig()
    # Optional and defaulted — see `NotesConfig`. Ships INERT
    # (`enabled = false`); off, composition wires `notes=None` into
    # `TranscriptInjection` and the `<notes>` block never renders. On, the
    # index is read from `SqlNoteStore` on every tag — nothing writes a note
    # yet, so it is empty until a curator lands.
    notes: NotesConfig = NotesConfig()
    # Optional and defaulted — see `StandingConfig`. Ships INERT
    # (`enabled = false`); off, composition builds no `debrief.Wiring`, the
    # debrief stage returns without awaiting anything, and `<standing>` never
    # renders. It is the one optional tier whose work runs INSIDE the live
    # reply slot, so turning it on lengthens a hold real people wait behind —
    # a measured decision, not a default.
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
    # Optional and defaulted — see `ImageGenConfig`. Ships INERT
    # (`enabled = false`); a config.toml written before DEC-137 keeps loading
    # unchanged, and a misconfigured lane degrades the tool to `unavailable`
    # rather than crashing the daemon.
    image_gen: ImageGenConfig = ImageGenConfig()
    # Optional and defaulted — see `VersionConfig` for why this one defaults ON
    # where every other optional capability defaults off.
    version: VersionConfig = VersionConfig()
    # Optional and defaulted — see `ToolsConfig`. Empty means no MCP server is
    # mounted; a config.toml without a `[tools]` section keeps loading.
    tools: ToolsConfig = ToolsConfig()
    engagement: EngagementConfig
    doctrine: DoctrineConfig
    # Optional and defaulted — see `DreamerConfig`. A config.toml with no
    # `[dreamer]` section keeps loading and reads the bind-mounted tree with
    # mtime reload, which is what every deployment wants. Ships INERT
    # (`enabled = false`): off, no `DreamerRuntime` is constructed and the
    # existing consolidators keep running unchanged.
    dreamer: DreamerConfig = DreamerConfig()
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
        """`[dreamer].model`, falling back to `[language].model` when empty.

        Lives on `Config` and not on `DreamerConfig` because the fallback
        source is a SIBLING section a sub-model cannot see — the one place that
        holds both. `MediaConfig.describer_for`'s rule applies unchanged: the
        fallback is folded in HERE and nowhere else, so exactly one place knows
        it exists and no caller can reach past it to the raw field.

        Empty is the shipped value and it is not a gap: it reproduces today's
        tier, which is what makes `[dreamer]` growing a runtime a no-op until
        plan gate D2 measures a model and fills this in. An explicit value wins,
        or the fallback would pin the new knob open and the split would be a
        no-op the other way.
        """
        return self.dreamer.model or self.language.model

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

    @model_validator(mode="after")
    def _render_cap_stays_below_the_store_limit(self) -> Config:
        """`[language].max_rendered_lexemes` must stay strictly below
        `nora.engagement.context.DEFAULT_ROOM_LANGUAGE_STORE_LIMIT` (15) — the
        SAME "was a comment in three places and a rule in none" shape
        `_lead_clears_the_tick` above already fixed for `[reminders]`/
        `[heartbeat]`.

        `SqlLanguageStore.room_language` applies its `LIMIT` with NO filter on
        whether an entry has a quotable example, and
        `nora.language.render.render_room_language` then DROPS exampleless
        entries and cannot reach further down the ranked list to backfill — it
        was only ever handed `room_language_store_limit` rows. A render cap at
        or above the store limit leaves the renderer no room to drop even one
        exampleless entry before under-filling the block, and nothing in
        `LanguageConfig` on its own stops an operator from setting it there:
        `max_rendered_lexemes` is just `Field(ge=1)`, with no upper bound.
        Refuses rather than warns for the same reason `_lead_clears_the_tick`
        does — the failure is silent (a smaller-than-expected block, not an
        error) and only visible by reading a rendered turn by hand.

        The store limit itself is a MODULE CONSTANT, not a second
        `[language]` field (`LanguageConfig`'s own docstring) — imported here,
        deferred inside the validator rather than at module scope, the same
        shape `LLMConfig._budget_names_and_values_are_real` uses for
        `nora.agent.tools.NATIVE_TOOLS`: `nora.config` is imported by
        everything, so a module-scope import of an `nora.engagement` symbol
        would pay that package's whole import cost on every `Config`
        construction rather than only when this validator actually runs.
        Safe from a cycle by the SAME contract that protects the `NATIVE_TOOLS`
        case: `nora.engagement` may not import `nora.config` at all
        (import-linter's "only app and __main__ read config"), so this edge
        has no return path.
        """
        from nora.engagement.context import DEFAULT_ROOM_LANGUAGE_STORE_LIMIT

        if self.language.max_rendered_lexemes >= DEFAULT_ROOM_LANGUAGE_STORE_LIMIT:
            raise ValueError(
                f"[language] max_rendered_lexemes = "
                f"{self.language.max_rendered_lexemes} must be strictly less "
                f"than the room-language STORE limit "
                f"({DEFAULT_ROOM_LANGUAGE_STORE_LIMIT}, "
                "nora.engagement.context.DEFAULT_ROOM_LANGUAGE_STORE_LIMIT): "
                "the renderer drops exampleless entries and cannot reach "
                "further down the ranked list than the store limit fetched, "
                "so a render cap at or above it silently under-fills the "
                "block."
            )
        return self

    @model_validator(mode="after")
    def _exchange_render_cap_stays_below_the_store_limit(self) -> Config:
        """`[language].max_rendered_exchanges` must stay strictly below
        `nora.engagement.context.DEFAULT_ROOM_EXCHANGE_STORE_LIMIT` — the
        exact same relation `_render_cap_stays_below_the_store_limit` above
        enforces for lexemes, applied to Plan 2's own render/store pair for
        the identical reason: `SqlExchangeStore.room_exchange` filters only
        on `recognition_score`, and `render_room_exchange` drops any entry
        with no quotable example without being able to backfill past the
        store limit it was handed.
        """
        from nora.engagement.context import DEFAULT_ROOM_EXCHANGE_STORE_LIMIT

        if self.language.max_rendered_exchanges >= DEFAULT_ROOM_EXCHANGE_STORE_LIMIT:
            raise ValueError(
                f"[language] max_rendered_exchanges = "
                f"{self.language.max_rendered_exchanges} must be strictly "
                f"less than the room-exchange STORE limit "
                f"({DEFAULT_ROOM_EXCHANGE_STORE_LIMIT}, "
                "nora.engagement.context.DEFAULT_ROOM_EXCHANGE_STORE_LIMIT): "
                "the renderer drops exampleless entries and cannot reach "
                "further down the ranked list than the store limit fetched, "
                "so a render cap at or above it silently under-fills the "
                "block."
            )
        return self

    @model_validator(mode="after")
    def _the_standing_window_fits_the_transcript_it_folds(self) -> Config:
        """`[standing] window` can never exceed `[store] context_window`.

        The debrief folds `ctx.transcript` — the rows the REPLY path already
        assembled — so a larger value selects exactly the same rows and the
        config would be claiming a fold it does not perform. Refused rather
        than clamped, for the reason this repo refuses a cap with no cap and an
        index with a hidden top-N: a knob that silently does nothing is worse
        than no knob, because someone reads it and believes it.

        **NOT gated on `enabled`**, unlike the slot-hold check below. A hold
        that is too long costs nothing while the tier is off, but a window that
        lies is wrong on the page whether or not it is running — and the moment
        it IS switched on is the worst time to discover the number meant
        something else.
        """
        if self.standing.window > self.store.context_window:
            raise ValueError(
                f"[standing] window = {self.standing.window} is larger than "
                f"[store] context_window = {self.store.context_window}. The "
                f"debrief folds the transcript the REPLY path assembled, so it "
                f"can never see more rows than that — a larger value is "
                f"silently the same number. Raise [store] context_window "
                f"(which widens every reply's prompt) or lower this."
            )
        return self

    @model_validator(mode="after")
    def _the_standing_slot_hold_fits_its_budget(self) -> Config:
        """The debrief HOLDS the conversation's slot, so the number that
        matters is the SUM of every bound on that hold — not any one of them.

        **This validator exists because the sum was got wrong in prose and
        nothing checked it.** `nora.engagement.stages.debrief`'s docstring
        argued the worst case as `[llm] timeout_seconds + [standing]
        timeout_seconds`, "60 + 20", and `[llm] timeout_seconds` is a REQUIRED
        field with no default that this deployment sets to **120**. The real
        bounded hold was 140s, and the stage's two store calls were not bounded
        at all — so a live conversation could stop answering indefinitely, on a
        turn that had already delivered its reply.

        A comment cannot be wrong twice: this is the same refuse-to-start
        posture `policy.toml` and `max_chars`-above-the-ceiling already use.
        Cross-section, so it lives on `Config` rather than on `StandingConfig`
        — the whole point is that `[standing]` alone cannot see the number that
        dominates its own budget.

        **NOT gated on `enabled` any more**, and the reason is its own
        sibling's, applied consistently. `_the_standing_window_fits_the_
        transcript_it_folds` above argues that a number which lies is wrong on
        the page whether or not it is running, and that «the moment it IS
        switched on is the worst time to discover it meant something else».
        That argument is STRONGER here, not weaker: the window's failure is a
        fold reading fewer rows, and this one's failure is a live conversation
        that stops answering.

        Gated, the trap was concrete: set `max_slot_hold_seconds = 60` while
        the tier is off (boots fine), then flip `enabled = true` — and Nora now
        refuses to start, at exactly the moment named as the worst one to find
        out. Ungated, the same edit fails on the next restart, which is when
        the operator is still holding the config file (review finding,
        2026-08-27).
        """
        hold = (
            # **`[llm] timeout_seconds` IS NOT IN THIS SUM, and leaving it in
            # was the fourth wrong arity for this one number.** It bounds
            # nothing in this stage: the fold's model call is built with
            # `[standing] timeout_seconds` AND wrapped in a `wait_for` on the
            # same value, and never sees the `[llm]` figure at all. Including
            # it made an operator-facing budget read «this conversation can be
            # held 165 seconds» when the real bounded hold is 45. Erring safe
            # does not excuse it — a number that lies about the deployment is
            # wrong on the page, which is the argument this validator exists
            # to enforce.
            self.standing.timeout_seconds
            # Recognition holds the SAME slot — `EngagementPipeline._run_turn`
            # awaits `_recognise` before context assembly — so its model call
            # belongs in this sum for the same reason `[standing]`'s does. It
            # was absent, and the classifier inherited `[language]`'s 120s
            # Dreamer ceiling, which is how a background timeout ended up
            # ahead of a live reply (review finding, 2026-08-28).
            #
            # UNGATED on `[facts] enabled`, matching the argument this
            # validator already makes for `[standing]`: the moment a tier is
            # switched on is the worst time to discover the budget meant
            # something else.
            #
            # The store awaits on the recognition path — the ledger read and
            # up to `max_targets` marks — are NOT in this sum, because nothing
            # bounds them and a number invented for them would be the fourth
            # wrong arity this validator has already been through. That is a
            # gap and it is written down as one.
            + self.facts.dispute_classify_timeout_seconds
            # FIVE. The stage holds five bounded store awaits:
            # `store_verdict.latest` (the trigger's read), `store.read` (the
            # prior), `store.write` (the revised position), `store_verdict.save`
            # and `notes_sink.propose`. It was written as 2x when only the
            # middle pair existed, then as 4x once the verdict landed, and each
            # time the prose disagreed with the code. The constant is the one
            # source and the stage's own test counts the awaits against it.
            + STANDING_STORE_CALLS_PER_FOLD * self.standing.store_timeout_seconds
        )
        if hold > self.standing.max_slot_hold_seconds:
            raise ValueError(
                f"[standing] would hold a conversation's slot for up to "
                f"{hold:g}s — [standing] timeout_seconds "
                f"({self.standing.timeout_seconds:g})"
                f" + [facts] dispute_classify_timeout_seconds "
                f"({self.facts.dispute_classify_timeout_seconds:g})"
                f" + {STANDING_STORE_CALLS_PER_FOLD} x store_timeout_seconds "
                f"({self.standing.store_timeout_seconds:g}) — which is over "
                f"[standing] max_slot_hold_seconds "
                f"({self.standing.max_slot_hold_seconds:g}). Lower a timeout or "
                f"raise the budget deliberately; do not leave them disagreeing."
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
