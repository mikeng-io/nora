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
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = ""


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
    `language` hints the model (BCP-47, e.g. `yue-HK`); `timeout_seconds`
    bounds ONE call (the whole ingest path waits for it — see the stage
    docstring).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    endpoint: str = "http://stt:8000"
    language: str = "yue-HK"
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
    (operator, 2026-08-20): the store's read (`standing_facts`, Task 3 of the
    fact-tier plan, not yet built) is specified to scope `WHERE workspace =
    ...` in SQL, so once it exists a fact will not be able to reach a room
    outside its own workspace regardless of what a startup refusal would add
    on top — the property is meant to be structural, not something this
    section asserts. Until that store lands there is no `standing_facts` to
    read at all, so this only holds when the deferred reader is finally
    built matching the spec.

    `max_adjudications_per_tick = 10`: the Phase B cap — one research loop
    (search + classify) per surviving candidate is the expensive step, same
    shape and same default as `LanguageConfig.verify_max_candidates`, which
    bounds the analogous (search + classify) round trip for slang.

    `max_rendered_facts = 8`: the RENDER cap on the `<facts>` block, the same
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
    max_rendered_facts: int = Field(default=8, ge=1)
    retention_days: int = Field(default=180, ge=1)


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

    **No `enabled` field.** Phase 1 has nothing to enable: the corpus is read by
    the two existing adaptors whether or not anything else is switched on, and a
    flag that gates nothing is a flag an operator will eventually set expecting
    it to gate something. It arrives with the Dreamer RUNTIME, shipping `false`.

    `dir` is resolved relative to the config file, not the working directory,
    by `Config._resolve_paths` — NOT by a validator here, because a defaulted
    sub-model is constructed once at class-definition time with no validation
    context to resolve against.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dir: Path = Path("nora/dreamer")
    reload: Literal["mtime", "startup"] = "mtime"
    strict: bool = True


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
    # Optional and defaulted — see `ComputeConfig`. Ships INERT
    # (`enabled = false`); the `ComputeJobStore` is built unconditionally
    # regardless (the `reminders` shape), only the poller's tick loop is gated.
    compute: ComputeConfig = ComputeConfig()
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
    # mtime reload, which is what every deployment wants; the section exists so
    # a deployment can pin `reload = "startup"` or point `dir` elsewhere.
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
