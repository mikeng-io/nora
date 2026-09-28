"""Protocol seams every later phase plugs into (spec §4.2). Declarations only —
no implementation moves here; the concrete classes that satisfy these shapes
keep living where they live until the phase that migrates them. One named
exception: `ArtifactIngest` (Nora Drop spec §7a) ships with NO concrete class
anywhere in this repo yet — see its own docstring for why, and for what stands
in for a real implementation in the meantime.

Standard library only, same rule as the rest of `nora/domain`: if a port needs
aiohttp, strands, sqlalchemy, pydantic or prometheus_client to express itself,
the port is wrong. `nora.domain.models`, `nora.domain.identity` and
`nora.domain.utf16` are the only allowed sibling imports — the last one because
`Styled` below cannot check its own invariant without the arithmetic that
defines it, and a copy of that arithmetic here would be the exact drift
`nora.domain.utf16`'s docstring exists to prevent.

Each shape below encodes a decision that cost something to reach. Simplifying
it back to the "obvious" version silently reintroduces the bug it was built to
avoid:

- `PlatformIdentity.aliases` exists because signal-cli emits `source` OR
  `sourceNumber` and sometimes a UUID, and Telegram has both a numeric id and
  a username. R-12 (ignore Nora's own messages) must test *membership* in
  `aliases`, not equality against a single id, or self-recognition silently
  breaks the moment a second platform is wired in. The type now lives in
  `nora.domain.identity` (re-exported below) and carries typed `identifiers`
  alongside the flat `aliases` — see its own docstring, and R-17 for why a
  degraded derivation of it must be counted rather than merely logged.

- `SendResult` replaces a bare `bool` because Telegram answers rate limiting
  with a 429 and a `retry_after` value, and `Deliverer`'s latch counts
  *attempts*, not just outcomes. A bool would burn the single send the latch
  allows on the most common transient failure instead of letting the caller
  back off and retry.

- `EngageDecision` carries `rationale` and `threshold` (not just `confidence`)
  because design/02 requires the classifier's reason to reach the prefill,
  and a bare confidence is uninterpretable in a trace without the threshold
  it was compared against — 0.82 means nothing on its own.

- `PlatformAdapter` deliberately has **no `parse()`**. Adding one would force
  every adapter to pretend its transport is dict-shaped, which Discord's
  gateway events and Telegram's update batches are not. Parsers stay
  per-adapter, tested directly against their own transport shape.

- `react()` lives on THIS port, not a separate capability protocol: reacting
  is a platform capability like sending, and the alternative has the
  engagement layer `isinstance`-testing a narrower protocol, putting platform
  knowledge back in the layer whose whole job is not to have any.
  `ReactionResult` carries `supported` as well as `ok`, so "this platform has
  no reactions" (never an exception) stays distinguishable from "the reaction
  failed" — only one of those is worth an alert. There is deliberately no
  shared `UNSUPPORTED_REACTION` constant: DEC-13's gate forbids binding a
  locally-defined class's instance at module level, and it checks the *shape*,
  not mutability, so a frozen dataclass is no exception.
  `nora.engagement.stages.indicate` also tolerates an adapter with no `react`
  attribute at all — a decoration may never crash a tag. Parameters are
  `to_author`/`to_timestamp` because `target` here already means the
  `ConversationTarget`; Signal's `targetAuthor`/`targetTimestamp` are
  translated in `nora.platform.signal.react` with every other wire name.

- `RecordStore.recent` returns `TranscriptRow`, not the `dict[str, Any]` row
  blobs the old `nora/store/db.py` returned, and `delivery_status` is
  `DeliveryStatus`, not a bare string. `nora.infra.sql.audit.SqlRecordStore`
  implements this. Static conformance is checked by mypy (`nora.infra` is
  inside the strict override); the behavioural half is
  `tests/contract/test_audit_store_contract.py`, one suite parametrized over
  `SqlRecordStore` and `nora.testing.FakeRecordStore` so the fake cannot drift
  from production unnoticed.

- `RecordStore` still deliberately has **no `prune`**, even though a caller now
  exists. `SqlRecordStore.prune_all` is a retention sweep owned by the runtime,
  not something any stage programs against through the port. Putting it on the
  port would oblige every future implementation — including fakes — to
  implement a retention policy they do not have.

- `RecordStore` DOES carry `unattributed`/`set_person_id`, which looks like the
  same "retention chore" shape `prune` was refused for above, and is not. The
  distinction is who programs against it: `prune_all` is a policy the store
  decides for itself and no caller reasons about, whereas lazy adoption
  (`nora.app.adoption`) is a caller with a question only the store can answer —
  "which audited messages are still attributed to nobody?" — followed by a
  decision that same caller makes. A `MemoryStore`-backed or Postgres-backed
  `RecordStore` would have to answer that question too, so it belongs on the
  port. `set_person_id` mirrors `set_delivery_outcome` exactly: one row, by id,
  unknown id is a silent no-op.

- `set_delivery_outcome` is the old `set_delivery_status` plus an optional
  `ts`, and NOT a second write — `nora.infra.sql.audit` documents why the
  platform's timestamp must land on this already-existing update. `ts=None`
  leaves the row's timestamp alone.

- `RecordStore.append` has **no `max_per_conversation` parameter**. Retention
  window size is a policy decided at the store's construction, not a value
  every caller re-specifies per call — `Protocol` methods don't inherit
  defaults, so a per-call default is already duplicated wherever it is
  threaded today (`nora/infra/sql/audit.py:35`, `SqlRecordStore.__init__`'s
  `max_per_conversation` parameter).

- **A tier's WRITE half and its READ half are separate Protocols**, and the
  rule outlived the pair that established it (`RoomConsolidator` /
  `RoomLanguageReader`, deleted with the language tier). The two halves are
  consumed from different places under different obligations: a write runs
  from the Dreamer's background cycle and may take its time, while a read
  reached from `TranscriptInjection` sits on the turn's critical path and —
  like every read a live turn depends on — must never raise (an unavailable
  store degrades to an EMPTY value, never an exception the turn has to catch).
  Folding both onto one Protocol would force every future write-side
  implementation to also be a valid read-side one, which is not true in
  general.

- `MemoryStore.ensure_*` is declared **idempotent under concurrency**.
  `HonchoClient.ensure_peer`/`ensure_session` are check-then-act over
  per-peer state, and person-centric identity means the same peer id can be
  hit from two platforms at the same moment. The lock that makes this true
  landed here, in this phase — a per-peer `asyncio.Lock`
  (`HonchoClient._peer_lock` in `nora/infra/honcho/client.py`), not in a
  later one; the invariant is
  declared here so a static conformance check (mypy on `nora.infra`) has
  something to hold the implementation to.

- `MemoryStore.representation()` returns `Recollection`, NOT `list[str]` like
  its older sibling `peer_card`. The two differ in exactly the bit that
  matters: `peer_card` answers `[]` both when Honcho is down and when it knows
  nothing, which is how a dead endpoint returned `[]` on every call for the
  life of the deployment unnoticed. `representation`'s own transport makes
  that worse — an unknown peer answers 200 with an empty string, not 404 — so
  the reachability bit cannot be recovered by the caller and is carried
  instead. `peer_card` is left alone: changing its return type is a separate
  migration, and `Recollection` is what new call sites are held to.

- `MemoryStore.health()` returns `MemoryHealth`, mirroring
  `PlatformAdapter.health() -> AdapterHealth`, instead of the bare
  `dict[str, Any]` it used to share with nothing else on this port.

- `MemoryStore.feed()` stays `bool`, deliberately, even though `SendResult`
  exists precisely because "a bool loses retryability on a network call to a
  flaky service." Honcho is explicitly never load-bearing (see this module's
  own docstring in `nora/infra/honcho/client.py`): a failed feed is fire-and-forget,
  logged and counted, never retried. Giving it a retryable/retry_after shape
  would imply a retry policy that deliberately does not exist. This
  asymmetry with `SendResult` is intentional, not an oversight — recorded
  here so it isn't cargo-culted into a `FeedResult` later.

- `BlobStore` is `put`/`get` and NOTHING ELSE — in particular no `delete`, for
  the same reason `RecordStore` has no `prune`. Deleting media is a retention
  policy the operator has not made yet (DEC-43), and putting it on the port
  would oblige every implementation, including fakes, to implement a policy
  they do not have. `FilesystemBlobStore.delete` exists on the CONCRETE class,
  exactly as `SqlRecordStore.prune_all` does, so the mechanism is there the day
  the decision is.

  `put` returns the KEY and takes no key argument. A content-addressed store
  where the caller chooses the key is not content-addressed — it is a
  filesystem with extra steps, and the verifiability that justified hashing the
  bytes rather than the metadata would be gone.

- `ArtifactIngest` is the Nora Drop spec's §7a seam — the one thing that spec
  puts in scope from the not-yet-designed Compute layer: a future Compute tool
  hands `drop` bytes it produced; Compute itself never touches RustFS or
  Postgres. It is declared here with ZERO implementations in this repo, a
  departure from every port above (each already has a concrete adapter and a
  real caller before a second is declared) — reasonable per that spec's own
  accounting, since the seam belongs to Drop and Compute does not exist yet to
  write the other half. Paired with `nora.testing.FakeArtifactIngest` anyway,
  per the spec review that flagged the gap, so the shape is exercised by
  something rather than declared into a vacuum. See its own docstring for the
  `bytes`-not-path-or-stream call and the raise-on-failure call, both argued
  from precedent already on this port (`BlobStore`). The port has since gained
  an `update` method (Nora Compute spec §9), with both `register` and `update`
  implemented by `DropArtifactIngest` and `FakeArtifactIngest`, bound by
  `tests/contract/test_artifact_ingest_contract.py`.

- `ComputeBackend` is the Nora Compute spec's own port (§2): `launch` starts a
  job and returns immediately, `poll` reports its progress. Deliberately NEVER
  raises — see the Protocol's own docstring for why a launch failure is a
  `poll` result, not an exception, matching this file's existing fail-closed
  convention for ports a live turn depends on.

- `MediaSource` is separate from `PlatformAdapter` for the same reason
  `RosterSource` is: fetching attachment bytes is not part of the
  send/receive/react lifecycle every adapter must implement, and putting it
  there would oblige every future adapter and every fake to implement a fetch
  it may have no endpoint for. Like `RosterSource` it is bound to one platform
  (`platform: str`, no `platform=` parameter) and **must never raise** —
  `MediaBytes.available` carries the difference between "could not fetch" and
  "fetched nothing", which a bare `bytes | None` would collapse.

- `EngagePolicy.should_engage` is `async` even though today's trigger-text
  policy is synchronous, because the classifier that replaces it is I/O, and
  a later sync-to-async change would break the whole ingest pipeline at every
  call site.

Convention pinned by `tests/test_ports.py`: every port method is keyword-only
after `self`. A positional parameter added to a port later is a breaking
change at every call site; keyword-only buys free extensibility forever.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from nora.domain.culture import CultureRevision as CultureRevision
from nora.domain.decision_log import DecisionRow as DecisionRow
from nora.domain.identity import PlatformIdentity as PlatformIdentity
from nora.domain.media import GifCandidate as GifCandidate
from nora.domain.media import MediaBlob as MediaBlob
from nora.domain.media import MediaBytes as MediaBytes
from nora.domain.media import MediaObject as MediaObject
from nora.domain.media import MediaRef as MediaRef
from nora.domain.media import VideoReadingKey as VideoReadingKey
from nora.domain.models import (
    ConversationTarget,
    DeliveryStatus,
    EditedMessage,
    EditTarget,
    NormalizedMessage,
    TagContext,
    TranscriptRow,
    UnattributedRow,
    WeatherObservation,
)
from nora.domain.parking import StreetParking as StreetParking
from nora.domain.stimuli import NewStimulus, NewWake, UnitView
from nora.domain.standing import Standing as Standing
from nora.domain.utf16 import utf16_len

# `PlatformIdentity` is re-exported, not declared here — it moved next to
# `Identifier` in `nora.domain.identity` when it grew a typed `identifiers`
# field (design/10 §5), which also bought this module back some of its 300-line
# cap. The `X as X` above is not a typo: it is PEP 484's explicit re-export
# marker, which mypy's `no_implicit_reexport` requires before another module may
# import the name through here. `__all__` would satisfy mypy too, but would also
# narrow `from nora.domain.ports import *` to that one name.


@dataclass(frozen=True)
class SendResult:
    """Outcome of a send attempt. See module docstring for why this is not `bool`.

    `timestamp` is the PLATFORM's own sent-timestamp, and it is what a later
    reply's quote points at. `None` = the platform supplied none.
    """

    ok: bool
    retryable: bool = False
    retry_after: float | None = None
    timestamp: int | None = None


@dataclass(frozen=True)
class OutboundQuote:
    """The message an outbound reply is threaded onto — resolved, never
    model-supplied.

    OUTBOUND, and deliberately not `nora.domain.models.Quote`, which is the
    inbound shape: that one carries the QUOTING sender's unverified copy of
    what the parent said and exists so R-20 can refuse to render it. This one
    is built from a row Nora herself audited, so there is nothing here that a
    message could have forged.

    Every field is a platform's own handle to that row, because the two
    platforms address a message differently and neither can be derived from
    the other:

    * `ts` + `author_id` — Signal's `quoteTimestamp` / `quoteAuthor`. The
      parent's sent-timestamp is the id there (`dataMessage.quote.id`), which
      is why `messages.quoted_ts` stores exactly that.
    * `platform_message_id` — Discord's `message_reference`. `messages` has no
      column for it; it lives in the row's `raw_json`, and the adapter is the
      only thing that reads it back out.

    A platform that cannot fill its own field simply sends unthreaded — a
    reply that lost its thread is still a reply, and R-5's «silence beats a
    wrong answer» does not reach a decoration.
    """

    ts: int
    author_id: str
    platform_message_id: str | None = None


@dataclass(frozen=True)
class OutboundAttachment:
    """Verified bytes a platform may include with one outbound message."""

    content: bytes
    media_type: str
    filename: str


@runtime_checkable
class GifSource(Protocol):
    """Provider boundary for finding and retrieving reaction GIFs."""

    async def search(self, *, query: str) -> tuple[GifCandidate, ...]: ...

    async def fetch(self, *, candidate: GifCandidate) -> OutboundAttachment | None: ...


@dataclass(frozen=True)
class ReactionResult:
    """Outcome of a reaction attempt. `supported` is a separate axis from `ok` —
    see module docstring."""

    ok: bool
    supported: bool = True


@dataclass(frozen=True)
class Mention:
    """One outbound @-mention: a RANGE over the message text, plus who it names.

    **A mention is not text, in either direction.** `nora.engagement.mentions`
    is the INBOUND half of this type (DEC-34) and its docstring is the reason
    this one exists: Signal carries the identity in a parallel array and puts
    only a range in the body, so a mention Nora *sends* has to be expressed the
    same way — never by writing an id into the message.

    `start`/`length` are **UTF-16 code units**, not Python characters and not
    Unicode code points. signal-cli says so in two places (`signal-cli(1)`'s
    `--mention`: "The units of start and length should be UTF-16 code units,
    NOT Unicode code points", and the same sentence again on `--text-style`),
    and it matters in this group specifically: a CJK character is one UTF-16
    unit and a Python character, but an emoji outside the BMP is TWO UTF-16
    units and ONE Python character. Computing these offsets with `len()` is
    correct right up until somebody puts 🇭🇰 in front of a mention, at which
    point every mention after it silently points at the wrong span — Signal
    does not reject it, it renders somebody else's name over the wrong words.
    `nora.domain.utf16.utf16_len` is the one place that arithmetic lives — it
    moved out of `nora.agent.outbound` when `TextStyle` below became a second
    producer of ranges (DEC-82); see that module for why.

    `recipient` is an id form signal-cli's RECIPIENT grammar accepts — a bare
    ACI UUID, a `PNI:`-prefixed UUID, a `u:`-prefixed username, or an E.164
    number. Verified out of the shipped 0.14.7 jar, not guessed:
    `SendCommand.parseMentions` matches `(\\d+):(\\d+):(.+)` and hands group 3
    to `CommandUtil.getSingleRecipientIdentifier`, which is
    `RecipientIdentifier.Single.fromString` — UUID, then `PNI:`, then `u:`,
    then a phone number. That is the SAME grammar `targetAuthor` accepts, which
    `nora.platform.signal.react`'s module docstring already documents.

    **This value never reaches the message body**, and that separation is the
    whole R-18 story of the feature: `Deliverer.deliver` runs `scan_reply` on
    the TEXT, and an ACI in the text would (correctly) make her mute — so a
    design that substituted the id into the string would have shipped a mute
    Nora, while one that leaked it would have shipped R-18's exact exposure.
    Carrying it in a parallel field is what makes both impossible.

    `handle` is a FOURTH, OPTIONAL fact, and it exists for exactly one
    platform today (Instagram) for a reason none of Signal, Discord or
    WhatsApp shares: those three render the chip off `recipient` alone — the
    text at `start:length` may be anything, including the display name Nora
    actually wrote — but Instagram's own mention API renders a chip only when
    that span reads, LITERALLY, `"@" + the account's own username` (confirmed
    against upstream mautrix-meta, `igconv/from-matrix.go` +
    `textfmt/from-matrix.go`: it constructs the request text as `"@" +
    mention.Name` and sizes `Length` off that same string, never off the
    sender's drafted name for them). Nora writes a mention the way a person
    writes one — by the name she knows them by — so on Instagram the drafted
    span and the wire span are two different strings, and `handle` is the
    second one: `nora.platform.instagram.send` splices it in, over the SAME
    span `start`/`length` still name, immediately before the request leaves,
    which is deliberately downstream of `nora.agent.outbound.OutboundComposer`
    (`compose` owns the final TEXT this module's docstring is about; it does
    not own every platform's final WIRE BYTES, and Instagram's alone need a
    second string here). `None` — every other platform, and Instagram until
    an operator records a `username` for this account (design/instagram-
    adoption/02) — means exactly what it always meant: the span stays
    whatever Nora wrote.
    """

    start: int
    length: int
    recipient: str
    handle: str | None = None


class Style(StrEnum):
    """A Signal text style, spelled exactly as signal-cli's parser accepts it.

    **Exactly two members, and the shortfall is deliberate.** signal-cli 0.14.7
    also knows `NONE`, `SPOILER`, `STRIKETHROUGH` and `MONOSPACE`
    (`org.asamk.signal.manager.api.TextStyle$Style`), and none of them has a
    producer here: `nora.agent.markdown` converts the two emphasis forms the
    model actually emits and nothing else. Declaring the other four would buy a
    member no code path can reach, which R-17's argument about counters that
    can never increment applies to just as well — an unreachable enum member is
    indistinguishable from a deleted one, and it invites a caller to invent a
    syntax for it.

    The VALUES are the wire form, and they are case-sensitive. `TextStyle.Style
    .from(String)` is a chain of `String.equals` against the uppercase names —
    verified twice: on the bytecode, and against the live daemon, which answers
    a lowercase `bold` with `{"error": {"message": "Invalid style: bold"}}` and
    sends nothing at all. `StrEnum` so the member IS its wire string and there
    is no second table mapping one to the other.
    """

    BOLD = "BOLD"
    ITALIC = "ITALIC"


@dataclass(frozen=True)
class TextStyle:
    """One styled RANGE over the message text — the sibling of `Mention`.

    Same shape, same units, same hazard: `start`/`length` are **UTF-16 code
    units** (`nora.domain.utf16`), they are meaningful against exactly one
    string, and Signal renders a wrong range rather than rejecting it. Read
    `Mention`'s docstring for the full argument; everything in it applies here
    with `style` where `recipient` is.

    One thing is SHARPER here than for a mention. A mention is added to text
    the model wrote; a style is what is LEFT after `nora.agent.markdown`
    deleted the asterisks that expressed it — so the range indexes a string
    that did not exist when the model produced it. That is why the text and its
    styles travel as one `Styled` value below rather than as two arguments.

    **A malformed range costs the MESSAGE, not the styling.**
    `SendCommand.parseTextStyles` compiles `(\\d+):(\\d+):(.+)` and throws
    `UserErrorException` on anything else, which fails the whole send — checked
    against the live daemon on 2026-08-10: `-1:3:BOLD` came back «Invalid
    textStyle syntax (-1:3:BOLD) expected 'start:length:STYLE'» with nothing
    delivered. So `nora.platform.signal.send` drops a range it cannot express
    rather than posting it, exactly as it does for a mention.
    """

    start: int
    length: int
    style: Style


@dataclass(frozen=True)
class Styled:
    """A message's FINAL text together with the ranges that index it.

    **The point of this type is that the two cannot be separated.** DEC-82
    converts `**bold**` into a style range, which means one operation both
    deletes characters and produces offsets into the string that is left. Hand
    those back as two values and every later line is an opportunity to pair the
    new offsets with the old text; there is no error when that happens, only a
    group seeing bold over the wrong words. So the producer returns ONE frozen
    value, `nora.agent.outbound.Outbound` stores that value rather than copying
    its halves, and `Outbound.text`/`Outbound.styles` are properties off it —
    an `Outbound` whose styles index a different string than its text is not a
    bug to be found in review, it is unconstructible.

    `nora.agent.markdown.render_markdown` is the only producer in `nora/`, and
    `tests/architecture/test_outbound_offsets_are_computed_last.py` is what
    keeps that true on the AST.

    `__post_init__` is the runtime half: a range outside the text is refused at
    construction, so the fail-safe upstream (`render_markdown` returns the
    unstyled draft if anything goes wrong) fires on a bad range instead of the
    range reaching the wire. Skipped entirely when there are no styles, which
    is the overwhelmingly common message — an unstyled `Styled` costs nothing.
    """

    text: str
    styles: tuple[TextStyle, ...] = ()

    def __post_init__(self) -> None:
        if not self.styles:
            return
        limit = utf16_len(self.text)
        for style in self.styles:
            if style.start < 0 or style.length <= 0 or style.start + style.length > limit:
                raise ValueError(
                    f"style range {style.start}:{style.length} does not fit a "
                    f"{limit}-unit message"
                )


@dataclass(frozen=True)
class LinkPreview:
    """The sender-generated preview card for one URL in the message.

    Signal link previews are **sender-generated**: the sending client fetches
    the target's OpenGraph tags and attaches title/description/image to the
    message. The recipients' clients render what they are given and fetch
    nothing, which is the privacy property the design exists for — and the
    reason this is Nora's fetch to make, with Nora's SSRF exposure to manage
    (`nora.infra.preview`).

    `title` is MANDATORY on the wire (`signal-cli(1)`: "Specify the title for
    the link preview (mandatory)"), so a page with no usable title yields no
    preview at all rather than a half one — `OpenGraphPreviewSource` returns
    `None` in that case and the message goes out plain.

    `image_data_uri` is an RFC 2397 `data:` URI, NOT a path, and that is a
    deployment fact rather than a preference: `--preview-image` takes a file
    signal-cli must be able to READ, and signal-cli runs in a different
    container from Nora with no shared volume (`docker-compose.yml` — Nora
    mounts `./data`, signal-cli mounts `./state/signal-cli`, and neither sees
    the other). A path would therefore have been silently unreadable in
    production while passing every local test. Verified out of the 0.14.7 jar:
    `ManagerImpl` hands `Message.Preview.image()` to
    `AttachmentHelper.uploadAttachment(String)`, whose `getAttachmentStream`
    skips the file-path validation entirely when the string starts with
    `data:` and goes to `Utils.createStreamDetails` — the same RFC 2397 path
    `--attachment` documents.
    """

    url: str
    title: str
    description: str = ""
    image_data_uri: str | None = None


@dataclass(frozen=True)
class AdapterHealth:
    """What one platform's transport can say about itself right now.

    `degraded` and `detail` are ADDITIVE (design/instagram-adoption/01
    §Degradation, M0) and default to the pre-existing behaviour, so an adapter
    that never sets them reads exactly as it did before.

    **`degraded` is a THIRD state, not a spelling of `connected=False`.** A
    disconnected adapter is one whose socket is down and which the bridge is
    expected to heal on its own; a degraded one is present, registered, and
    structurally unable to act until a HUMAN intervenes — an Instagram session
    that got checkpointed. Collapsing them would mean either flapping the
    container healthcheck on an ordinary captcha, or — the failure that
    actually matters — reporting a platform as merely quiet when Nora is
    silently absent from every room on it.

    `detail` is the stated reason, rendered beside the flag. A degradation
    with no reason is a field nobody can act on.
    """

    platform: str
    connected: bool
    last_event_age_seconds: float | None = None
    degraded: bool = False
    detail: str = ""


@dataclass(frozen=True)
class MemoryHealth:
    """Mirrors `AdapterHealth` — typed, not a bare dict. `MemoryStore.health()`
    used to be the one health check left dict-shaped while `PlatformAdapter.health()`
    was already typed; see module docstring's `MemoryStore` note."""

    reachable: bool


@dataclass(frozen=True)
class EngageDecision:
    """See module docstring for why `rationale` and `threshold` ride along
    with `confidence` instead of a bare bool or a bare number.

    `relevance_gate` is the Relevance Gate's verdict
    (design/dreamer-agent-loop/36), deliberately NOT a third value of
    `engage`: `engage=True` means "a tag admitted, route to the engagement
    pipeline" and nothing else may mean that. A `relevance_gate=True` decision
    always has `engage=False`; it marks an untagged message in an opted-in
    room as a candidate for the quiescence trigger, and the dispatcher routes
    it there rather than to the tag path."""

    engage: bool
    reason: str
    rationale: str | None = None
    confidence: float | None = None
    threshold: float | None = None
    allow_episode: bool = False
    relevance_gate: bool = False


@dataclass(frozen=True)
class EngagementRequest:
    message: NormalizedMessage
    decision: EngageDecision
    tag_id: str
    # Which occasion the turn is answering. "tag" for everything the
    # engagement pipeline handles today; the recovery branches below gate on
    # it so that a "relevance_gate" request (design/dreamer-agent-loop/36) —
    # should one ever be admitted here — can never have a draft delivered
    # FOR it: no grounded value is silence there, not a recovered message.
    occasion: str = "tag"


@dataclass(frozen=True)
class Roster:
    """Who a platform says is currently in one conversation.

    `available` is a SEPARATE axis from emptiness, and that separation is the
    whole reason this is not a bare list. `RosterSource.roster` never raises —
    a wedged signal-cli must not take a tag down — so without this flag an
    unreachable daemon and an empty room produce the same value, and every
    room-scoped tool would answer "there is nobody here" when the truth is
    "I could not look". That ambiguity is exactly what hid the dead
    `peer_card` endpoint for the life of the deployment.

    `members` is one frozenset of id forms PER member, mirroring
    `nora.platform.signal.directory.SignalGroup.members`: which ids belong to
    the same account is the point of reading it (under sealed sender the same
    account reaches Nora as a phone number on one message and an ACI on
    another), and a flat set would destroy that pairing.
    """

    available: bool
    members: tuple[frozenset[str], ...] = ()


@dataclass(frozen=True)
class RoomActivity:
    """How busy one conversation is, read from the stored messages
    (`RecordStore.room_activity`, design/self-awareness/07).

    `count` is people's messages at or after a cutoff; `last_ts` is when a
    person last spoke there at all, in ms epoch, or `None` when nobody ever
    has. Nora's own rows count for neither: her talking is not the room being
    busy. `None` rather than 0 for «never», so no reader can render a room
    nobody spoke in as a message from 1970.
    """

    count: int
    last_ts: int | None = None
    # Distinct PEOPLE who spoke at or after the same cutoff as `count`
    # (design/self-awareness/07). A resolved `person_id` is the person; the
    # raw sender id stands in only for a row nobody resolved. Asked which
    # group has the most people talking, a message count lets one person
    # account for twenty lines, and she read every room to count heads.
    speakers: int = 0


@dataclass(frozen=True)
class Recollection:
    """What durable memory returned about ONE person, for ONE question.

    Same two-axis shape as `Roster`, for the same reason and with a sharper
    precedent: `MemoryStore.peer_card` returns `[]` both when Honcho is down
    and when it knows nothing, and that is precisely why a broken endpoint
    could return `[]` on every call for the whole life of the deployment
    without anybody noticing. Honcho's `representation` endpoint makes it
    worse, not better — an unknown peer answers **200 with an empty string**
    rather than 404, so "no such peer" and "nothing known" are already
    indistinguishable at the HTTP layer. `reachable` is the one bit that
    cannot be recovered afterwards, so it is carried rather than inferred.
    """

    reachable: bool
    conclusions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MessageHits:
    """What a raw-message search returned, and WHETHER IT RAN.

    `Recollection`'s argument, one endpoint over: `MemoryStore.search` returned
    a bare list and answered `[]` for a transport failure, a >=400, a malformed
    body AND a genuine miss. `search_memory` then reported all four as
    `nothing_found` — «I couldn't find anything like that in what's been said
    here» — which is an ERROR reading as a PASS, the one thing R-27 exists to
    forbid, and it was counted `ok`.

    That was invisible for the same reason the dead `peer_card` endpoint was:
    "nothing matched" is a true-sounding sentence for a search nobody ran. An
    `except` clause in the CALLER could not fix it either — the adapter catches
    everything itself, so the caller's handler was unreachable code that read as
    a fix (external review, 2026-08-11). The bit has to be carried out of the
    place that knows it.
    """

    reachable: bool
    hits: tuple[tuple[str, str], ...] = ()      # (peer_id, content)


@runtime_checkable
class RosterSource(Protocol):
    """Current membership of a conversation, read from the platform.

    Separate from `PlatformAdapter` on purpose. Membership is a DIRECTORY read
    (`nora.platform.signal.directory`'s read-only JSON-RPC allowlist), not part
    of the send/receive/react lifecycle every adapter must implement, and
    putting it on `PlatformAdapter` would oblige every future adapter — and
    every fake — to implement a roster it may have no endpoint for.

    `roster()` takes no `platform`: an implementation is bound to one, exactly
    as `PlatformAdapter` is. **It must never raise** — the caller is a tool
    inside a live turn, and the honest degradation is `Roster(available=False)`.
    """

    platform: str

    async def roster(self, *, conversation_id: str) -> Roster: ...


@runtime_checkable
class PlatformAdapter(Protocol):
    """No `parse()` — see module docstring.

    `platform: str`, not `name: str` — the platforms phase's fifth and final
    conformance gap (found while closing the other four; see
    `tests/contract/test_port_conformance.py`). Every other type on this port
    already says `platform` (`PlatformIdentity.platform`, `AdapterHealth.
    platform`), and so does the `platform=` metrics label everywhere else in
    the codebase — `name` was the outlier here, not `SignalAdapter`, so the
    port is what changed.
    """

    platform: str

    async def resolve_identity(self) -> PlatformIdentity: ...

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None: ...

    # `mentions`/`styles`/`preview` are DEFAULTED, and that is the only
    # defaulting on this port's send path. They are DECORATION on a message
    # that must go out regardless (R-5): a platform with no mention concept
    # ignores them, and every failure upstream of here — an unresolvable name,
    # an unreadable roster, a preview that would not fetch, a markdown render
    # that raised — degrades to the empty default and the plain message, never
    # to a refusal. The defaults are also what keeps a future adapter honest
    # without a flag day: it satisfies the port the day it is written, and
    # adding real support is a change to that adapter alone. See
    # `Mention`/`TextStyle`/`LinkPreview` for the wire facts each carries, and
    # `nora.agent.outbound` for who is allowed to build them.
    #
    # `styles` is passed as a bare tuple here even though it is INSEPARABLE
    # from the text upstream (`Styled`), because this is the boundary where the
    # message stops being one value and becomes a JSON-RPC payload of parallel
    # fields. The invariant is held on the near side of this call, by
    # `Outbound` reading both halves off one `Styled`; asking every adapter to
    # accept a `Styled` would put a domain composite in the signature of a
    # method whose whole job is to flatten it.
    async def send(
        self,
        *,
        target: ConversationTarget,
        text: str,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: OutboundQuote | None = None,
    ) -> SendResult: ...

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_author: str,
        to_timestamp: int,
        to_message_id: str = "",
        remove: bool = False,
    ) -> ReactionResult: ...
    # `to_message_id` is DEFAULTED and additive beside `(to_author,
    # to_timestamp)` because platforms genuinely disagree about what names a
    # message: Signal addresses one by who sent it and when, Discord by a
    # snowflake, and neither can be derived from the other. An adapter uses
    # whichever its platform speaks and ignores the rest.

    async def health(self) -> AdapterHealth: ...


@runtime_checkable
class RecordStore(Protocol):
    """No `prune`, no per-call `max_per_conversation` — see module docstring."""

    async def append(
        self,
        *,
        message: NormalizedMessage,
        is_nora: bool = False,
        delivery_status: DeliveryStatus = DeliveryStatus.PENDING,
        person_id: str | None = None,
    ) -> int: ...

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]: ...

    async def recent_before(
        self, *, conversation_id: str, before_ts: int, limit: int,
    ) -> list[TranscriptRow]:
        """`recent`, ending before a moment: the newest `limit` rows with
        `ts < before_ts`, returned oldest-first, with the same over-fetch, edit
        fold and `limit <= 0` rule as `recent`.

        One caller family: the transcript tools' `before`
        (design/self-awareness/08 §2), which reads «yesterday» in a room that
        has said more than a window's worth since. Same `idx_messages_conv_ts`
        seek as `recent`, with one more predicate.
        """
        ...

    async def depth_to(self, *, conversation_id: str, ts: int) -> int | None:
        """How many of this conversation's rows are `ts DESC` at-or-newer than
        `ts` — i.e. the `recent(limit=...)` size that would just reach the row
        carrying this timestamp. `None` if no row in this conversation carries
        it (pruned, pre-migration, or a foreign conversation).

        Exists for one caller: a reply whose `quoted_ts` falls outside the
        prefill's default window (design/11 §2.5's out-of-window case). Rather
        than a bounded lookup that hands back attribution only, the caller
        re-reads `recent` sized to `depth_to(...) + margin`, so the resolved
        parent's actual TEXT is in the window, not just its author. See
        `nora.engagement.context.TranscriptInjection._recent`.

        Same tie-break as `recent` (`ts DESC, id DESC`) — a duplicated
        timestamp still answers a well-defined question, «how many rows would
        `recent` return before reaching this one», even though `_parent_of`
        may then resolve to a different row sharing that `ts`.
        """
        ...

    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """Human rows with `id > after_id`, ASCENDING, at most `limit`.

        A cursor read, not a window read — the opposite ordering to `recent`,
        which is newest-first for the prompt. `is_nora` rows are excluded here
        rather than by the caller: this is the only read built for derivation,
        and the exclusion is the property the whole Dreamer rests on.
        """
        ...

    async def since_including_nora(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """All rows with `id > after_id`, ASCENDING, at most `limit`.

        Unlike `since`, this does NOT exclude Nora's own rows
        (design/standing-watermark/20). The standing fold needs her replies to
        re-judge her position ("I committed to...", "I answered..."); a read
        that strips her half of the conversation shows the fold a room where
        she never spoke. `since` stays unchanged — the Dreamer's derivation
        reads must keep excluding her — and this is the fold's read only.
        """
        ...

    async def set_delivery_outcome(
        self, *, row_id: int, status: DeliveryStatus, ts: int | None = None
    ) -> None: ...

    async def find_edited(
        self, *, conversation_id: str, target: EditTarget, sender_id: str,
    ) -> EditedMessage | None:
        """The recorded message an edit targets, or None if it was never
        recorded (design/message-edits/01 §2).

        `target.message_id` matches `messages.platform_message_id` in this
        conversation; `target.sent_ts` (Signal) matches `ts` from `sender_id`,
        the pair Signal addresses a message by. `sender_id` is ignored on the
        handle path — Instagram's edit names no sender at all. The result
        carries the CURRENT version (its newest edit folded in).
        """
        ...

    async def quoted_ts_of(self, *, conversation_id: str, ts: int) -> int:
        """The `ts` a reply's quote should point at. Signal quotes an edited
        message by the timestamp of the REVISION the replier saw, while the
        transcript keeps the message at its original `ts`; so a `ts` no
        message carries but one of its edits does resolves to that message's
        own `ts`. Any other `ts` comes back unchanged — an exact message match
        always wins (design/message-edits/01 §3)."""
        ...

    async def append_edit(self, *, message_id: int, message: NormalizedMessage) -> None:
        """Record one edit of the `messages` row `message_id`. Append-only:
        the row it edits is never updated (A9); the transcript reads fold the
        newest edit onto it instead (design/message-edits/01 §3)."""
        ...

    async def append_reaction(
        self, *, message_id: int, message: NormalizedMessage, person_id: str | None,
    ) -> int:
        """Record one reaction (or its removal) on the `messages` row
        `message_id`, from `message.touch`; returns the new row's id.
        Append-only: a removal is its own row, never an update or a delete of
        the reaction it takes back (design/cognition-loop/10-tiers.md §8). The
        reaction is Record, never a transcript line: no transcript read joins
        it (20-observe.md §1)."""
        ...

    async def append_delete(
        self, *, message_id: int, message: NormalizedMessage, person_id: str | None,
    ) -> int:
        """Record that the `messages` row `message_id` was deleted or unsent
        on its platform; returns the new row's id. The row itself is NOT
        touched and still renders: hiding it, and marking what cites it
        `unsupported`, is the withdrawal cascade (migration step 2c). This is
        the Record item that cascade will be fed from."""
        ...

    async def unattributed(self, *, limit: int) -> list[UnattributedRow]: ...

    async def set_person_id(self, *, row_id: int, person_id: str) -> None: ...

    async def room_activity(self, *, conversation_id: str, since_ts: int) -> RoomActivity:
        """People's messages in this conversation at or after `since_ts`, how
        many distinct people sent them, and the newest person's `ts` at any
        time (`RoomActivity`). One indexed
        read, for `room_list`: a count over `recent()` would cap at the fetch
        size and report a busy room as exactly as busy as the limit."""
        ...

    async def stats(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class StimulusStore(Protocol):
    """The per-unit `stimulus` table and the wake log
    (design/cognition-loop/20-observe.md §3; `nora.domain.stimuli`).

    Append-only in both tables (10-tiers.md §8): a stimulus is never updated
    or deleted, and a unit's watermark is its newest wake row, never a row
    rewritten. Implementations: `nora.infra.sql.stimuli.SqlStimulusStore` and
    `nora.testing.stimuli.InMemoryStimulusStore`, bound by
    `tests/contract/test_stimulus_store_contract.py`.
    """

    async def append(self, *, stimulus: NewStimulus, recorded_at_ms: int) -> int | None:
        """Append one stimulus and return its id. A `time` stimulus is unique
        on `(unit, ref)`: appending one already on record returns None and
        writes nothing, which is what lets the evaluator raise a daily floor
        on every tick without counting it twice."""
        ...

    async def view(
        self, *, unit: str, mode: str, now_ms: int, limit: int,
    ) -> UnitView:
        """One tick's read for one unit: its newest wake in `mode`, the
        stimuli pending against that wake's watermark and due by `now_ms`
        (ascending id, at most `limit`), and the `recorded_at` of its newest
        person-authored item (a `message` stimulus, `waits` included)."""
        ...

    async def record_wake(self, *, wake: NewWake, recorded_at_ms: int) -> int:
        """Append one wake row; it is the unit's new watermark in its mode."""
        ...

    async def aclose(self) -> None: ...


#: How a source's health row came about: a `poll` it made, or a `transition`
#: of an adapter's state observed by the sampler.
SourceHealthEvent = Literal["poll", "transition"]


@runtime_checkable
class SourceHealthLog(Protocol):
    """Source health as Record (design/cognition-loop/20-observe.md §1: «every
    collector poll and every platform adapter's up and down transitions are
    recorded, so "every source healthy for the interval" is a query, not an
    assumption»). Append-only. Never raises: health is recorded beside the
    work it describes and must never stop it."""

    async def record(
        self, *, source: str, scope: str, event: SourceHealthEvent, outcome: str,
        at_ms: int, detail: str = "",
    ) -> bool:
        """Append one row; False when it could not be written (logged)."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """`ensure_*` methods are idempotent under concurrency — see module docstring.

    **Every call names WHERE it is for** (DEC-214): the room
    (`conversation_id`) or the unit (`unit`) whose memory it reads or writes.
    Either resolves to the same thing — the unit — and the unit to its Nora
    workspace's HONCHO workspace and its own named scope inside it. A caller
    never names a Honcho workspace itself: a `workspace_id` parameter used to
    be here, and a unit name passed through it addressed a Honcho workspace
    that did not exist (`NoteCurator._discharge`, 2026-09-19). On the peer
    verbs (`ensure_peer`, `conclude`, `peer_card`, `representation`, `chat`)
    the key ROUTES only; it never narrows a read to one session. `peer_card`
    and `representation` also take `workspace` — a NORA workspace name — for
    a person's model across every unit of it, unscoped (the portal). On `feed`
    and `search` a `conversation_id` also names the room's own session, as it
    always has. A call whose key the deployment cannot route (a room or unit
    `policy.toml` does not place in any workspace) fails closed: a read is
    `unavailable`, a write is `False`, never a read of another workspace."""

    async def ensure_workspace(self) -> None: ...

    async def ensure_peer(
        self, *, peer_id: str, conversation_id: str | None = None,
        unit: str | None = None,
    ) -> None: ...

    # `conversation_id` (Honcho v2, design/24): the message is fed under
    # `peer_id` into that CONVERSATION's session — a multi-peer session, so a
    # human's words and Nora's reply share one session while each stays under its
    # own speaker's peer (DEC-36 at the message level). `metadata` is optional
    # structure for the deriver (ts, reply_to, …), not the message text.
    async def feed(
        self,
        *,
        peer_id: str,
        text: str,
        conversation_id: str,
        metadata: dict[str, object] | None = None,
    ) -> bool: ...

    # The conclusion WRITE lane (the `remember` tool's method): one settled
    # fact about `target`, stored as `(observer=peer_id, observed=target)` —
    # the exact pair `representation` reads back, so what is written is
    # readable the moment the write lands, with no second scope in between.
    # No SESSION key on purpose: the read lane is unit-wide by design (see
    # `representation`), so the write is too, and disclosure stays the room
    # roster's job at the tool layer. `conversation_id`/`unit` ROUTE it to
    # the unit's workspace and scope (see the class docstring). Bare best-effort `bool`
    # like `feed` — never load-bearing, never retried (see the module
    # docstring on `feed`'s asymmetry with `SendResult`).
    async def conclude(
        self, *, peer_id: str, content: str, target: str,
        conversation_id: str | None = None, unit: str | None = None,
    ) -> bool: ...

    async def peer_card(
        self, *, peer_id: str, conversation_id: str | None = None,
        unit: str | None = None, workspace: str | None = None,
    ) -> list[str]: ...

    # `peer_id` is the OBSERVER, `target` the OBSERVED (design/24 §4). With
    # `target` set the recall is `(observer=peer_id, observed=target)` — Nora's
    # model of a person, not the person's self-model. Unit-wide (no session
    # scope): knowledge blends across every room of the unit Nora shares with
    # them (DEC-214); the room roster gates disclosure at the tool layer.
    async def representation(
        self,
        *,
        peer_id: str,
        search_query: str,
        max_conclusions: int = 5,
        target: str | None = None,
        conversation_id: str | None = None,
        unit: str | None = None,
        workspace: str | None = None,
    ) -> Recollection: ...

    # The DIALECTIC verb: Honcho REASONS over the (observer, observed) model to
    # synthesise an answer, vs `representation` which retrieves raw conclusions.
    # `reasoning_level` is the caller's per-question depth/latency choice
    # (minimal→max). None on any failure — best-effort, like `feed`.
    async def chat(
        self,
        *,
        peer_id: str,
        query: str,
        reasoning_level: str = "low",
        target: str | None = None,
        conversation_id: str | None = None,
        unit: str | None = None,
    ) -> str | None: ...

    # Semantic search over the raw MESSAGES (not conclusions) — the verb behind
    # `search_memory`. Carries `reachable` beside the hits (see `MessageHits`):
    # a bare list made a Honcho outage indistinguishable from a genuine miss,
    # and `search_memory` reported the outage as «nothing was said», counted ok.
    # The caller still filters to the room roster and screens identifiers.
    async def search(
        self, *, query: str, conversation_id: str | None = None, limit: int = 8,
        unit: str | None = None,
    ) -> MessageHits: ...

    async def health(self) -> MemoryHealth: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class MediaSource(Protocol):
    """One platform's attachment bytes. Never raises — see module docstring."""

    platform: str

    async def fetch(self, *, ref: MediaRef) -> MediaBytes: ...


@runtime_checkable
class BlobStore(Protocol):
    """Content-addressed byte custody. `put` returns the key it computed; there
    is no `delete` on this port — see module docstring."""

    async def put(self, *, data: bytes) -> str: ...

    async def get(self, *, key: str) -> bytes | None: ...


@dataclass(frozen=True)
class MediaLookup:
    """Many objects by content address, with an AVAILABILITY axis.

    Two axes, like `MediaBytes`, `Roster` and `Recollection`, and for the
    identical reason: «the index could not be read» must stay distinguishable
    from «those bytes are not in it». `get` collapses them — it answers `None`
    for a missing row and for an unreachable database alike, which its own
    contract suite pins — so a caller built on `get` cannot report R-27's three
    states however carefully it is written. The render join was that caller:
    its failure had no counter at all and, on the deployment, no log an
    operator can reach.

    ONE call for a whole window, not one per blob. That is the other half:
    seven paths join now, two of them over batches (a 1000-row adoption scan, a
    Dreamer batch), and a sequential `get` per distinct blob is an unbounded
    number of round trips with no timeout on a process-start path.

    `objects` holds only the rows that EXIST; a sha with no row is simply
    absent, which is `nothing_found` for that blob and not a failure of the
    lookup.
    """

    available: bool
    objects: Mapping[str, MediaObject] = field(default_factory=dict)
    reason: str = ""


@runtime_checkable
class MediaObjectStore(Protocol):
    """The unified media index (DEC-138, migration 0032): metadata for every
    byte Nora holds — inbound photos, generated images, fetched attachments —
    keyed by the same content address the `BlobStore` uses. `source` is an
    attribute; generated objects carry prompt/title/description in
    provenance. Search by text so Nora can find and resend her own media
    days later without regenerating it.

    `save` is FIRST-SOURCE-WINS: it writes the index entry and never
    overwrites one. `comprehend` is the other verb and it is deliberately
    separate rather than a flag on `save` — custody writes the row inline
    (the handle is a promise the bytes are retrievable), and the model pass
    lands on it LATER, from a spawned job (issue #116). One verb doing both
    would make the ordering invisible and let a late index write erase a
    comprehension that beat it. It is an UPSERT, so a comprehension can land
    on a row nothing indexed yet, and it never touches `source` — the birth
    record stays the first source's. Its `provenance` argument MERGES into
    whatever is there rather than replacing it: what a READ leaves behind (a
    video's chunk record, issue #117 — how long the clip was, how much reached
    a model, whether the ceiling bound) is a fact about the reading, not about
    the birth, and the two have to coexist on one row."""

    async def save(self, *, obj: MediaObject) -> bool: ...

    async def comprehend(
        self, *, sha256: str, title: str, description: str, content: str,
        model: str, provenance: dict[str, object] | None = None,
    ) -> bool: ...

    async def search(
        self, *, query: str, limit: int = 20,
        sources: tuple[str, ...] = (),
    ) -> tuple[MediaObject, ...]: ...

    async def get(self, *, sha256: str) -> MediaObject | None: ...

    async def lookup(self, *, sha256s: tuple[str, ...]) -> MediaLookup: ...

    async def by_prefix(self, *, handle: str, limit: int = 20) -> tuple[MediaObject, ...]: ...


@runtime_checkable
class Transcriber(Protocol):
    """Local speech-to-text for voice notes (spec 2026-08-21 §4).

    Bound to `[speech.stt]`: `enabled = false` (or an unset endpoint) leaves
    the ingest stage with no transcriber, and a voice note is recorded with a
    bare handle — exactly as before this capability existed. `language` is
    one of the endpoint's own short LID codes (`yue`, not a BCP-47 tag like
    `yue-HK`) the endpoint may use; it never overrides a detected language,
    because the group mixes Cantonese and English.

    Implementations NEVER raise into the ingest loop: any failure (network,
    timeout, empty transcript) is returned as an unavailable `SttResult`, and
    the caller records the message without a transcript — a voice note that
    fails to transcribe is still a voice note, never a dropped message.
    """

    async def transcribe(
        self, *, data: bytes, media_type: str, language: str = ""
    ) -> SttResult: ...


@dataclass(frozen=True)
class SttResult:
    """Outcome of one transcription call. `available` is the single truth a
    caller reads: `False` means «could not transcribe», and `reason` names why
    (`error`, `timeout`, `unsupported`, `empty` — an ERROR is never a PASS,
    R-27). `empty` is a legitimate 200 with no words (a silent/wordless voice
    note) and is kept distinct from `error` (a transport/server failure) —
    conflating the two would make `nora_speech_transcriptions_total{status=
    "error"}` unable to tell an outage from an ordinary silent note. `text` is
    the Traditional-Chinese transcript (the endpoint already applies OpenCC
    s2t, so the caller never sees Simplified)."""

    available: bool
    text: str = ""
    reason: str = ""


@runtime_checkable
class Speaker(Protocol):
    """Text to speech audio (voice-channel TTS, design/voice-channel/01 §1).

    Bound to `[speech.tts]`: `enabled = false` (or an unset provider) leaves
    the composition root with no speaker, and nothing about Nora's behaviour
    changes. Implementations NEVER raise into a caller's turn: any failure
    (network, timeout, unknown voice, empty audio) returns an unavailable
    `SpeakResult`, and the turn proceeds text-only — a reply that fails to
    sound is still a reply, never a dropped turn.

    The audio is BYTES, not a path or URL: the voice-channel mixer consumes
    frames in memory, a file path would force every caller into disk
    lifecycle, and Discord's player has no file concept — it reads an
    `AudioSource`. `voice` is a per-call override of the configured default;
    an empty string means the implementation's own default.
    """

    async def speak(self, *, text: str, voice: str = "") -> SpeakResult: ...


@dataclass(frozen=True)
class SpeakResult:
    """Outcome of one synthesis call (design/voice-channel/01 §1). `available`
    is the single truth a caller reads. `reason` names why not (`error`,
    `timeout`, `empty`, `unsupported`) and `empty` stays distinct from
    `error` — the same distinction `SttResult` makes, for the same reason: a
    silent synthesis and an outage must not collapse into one metric.
    `media_type` rides along because one provider emits MP3 while another may
    emit WAV; the adapter that decodes to PCM needs to know which, and
    discovering it by sniffing is a guess."""

    available: bool
    audio: bytes = b""
    media_type: str = ""
    reason: str = ""


_URL_RUN = re.compile(
    r"https?://|www\.|[\w-]+\.(?:com|net|org|io|dev|app|gg|me|co|hk|cn|xyz)(?:/\S*)?",
    re.IGNORECASE,
)
_HANDLE_RUN = re.compile(r"@\S+")
_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{16,}\b")
# A base64-ish run long enough to be a token, required to carry a digit or a
# base64-only character so an ordinary long WORD («antidisestablishment…»)
# is not misread as one. A UUID is mostly dashes and short hex groups, which
# `_HEX_RUN` alone would miss.
_B64_RUN = re.compile(r"\b(?=[A-Za-z0-9+/]*[0-9+/])[A-Za-z0-9+/]{24,}={0,2}\b")
_UUID_RUN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def speakable(text: str) -> bool:
    """The fail-closed content gate for TTS (design/voice-channel/01 §7):
    is this text fit to be READ ALOUD?

    Anything that reads as a long opaque string must not be spoken — URLs,
    hostnames, `@handles`, hashes, UUIDs, keys. If it was pasted as a machine
    token, it stays a machine token: the caller either rewords the reply or
    sends it text-only, and this function is the check, not a model's memory
    of the rule mid-turn. The bias is deliberate: a false negative costs one
    reply its voice (safe — the text still lands), a false positive reads a
    40-character token aloud in a shared channel (unrecoverable).
    """

    return not (
        _URL_RUN.search(text)
        or _HANDLE_RUN.search(text)
        or _UUID_RUN.search(text)
        or _HEX_RUN.search(text)
        or _B64_RUN.search(text)
    )


@runtime_checkable
class VideoReadingStore(Protocol):
    """Durable cache of question-less VIDEO readings, keyed on everything the
    reading depends on (`VideoReadingKey`). Never raises.

    Separate from `MediaDescriptionStore` on purpose: that one's durable tier
    IS `media_objects`, whose row promises Nora holds the bytes — and a clip
    read by URL has no blob. A reading keyed by more than a sha256 cannot live
    in a table every handle lookup and search reads as a sha256 either.

    A failure degrades to a MISS (one re-read), never the message: `get`
    answers None, `put` swallows.
    """

    async def get(self, *, key: VideoReadingKey) -> str | None: ...

    async def put(self, *, key: VideoReadingKey, reading: str) -> None: ...


@runtime_checkable
class MediaDescriptionStore(Protocol):
    """The durable half of media dedup (sticker spec §4.1). Never raises.

    Two levels, because they answer different questions at different moments:

    * `resolve` is keyed on the platform's own sticker IDENTITY, which is known
      at parse time — before any bytes move. It is the only level that can turn
      a repeat sighting into zero platform RPC, which is the whole point: this
      runs on the ingest path the listener awaits.
    * `describe` is keyed on the sha256 of the CONTENT, because a description
      is a pure function of the bytes. The same art in two packs, or on two
      platforms, is one row and one vision call.

    The second level is deliberately shared with `media_fetch`'s existing
    `VisionCache`, which becomes a read-through in front of it. Two caches that
    could disagree about what one image is would be exactly the drift the
    comprehend-once rule exists to prevent, and would surface as Nora
    describing one picture two ways.

    Never raises for `MediaSource`'s reason: it sits on the ingest path, and a
    cache failure must degrade to a MISS — costing one re-description, never
    the message. `remember` therefore returns None on failure rather than
    signalling it; the caller has already produced a usable description and has
    nothing to do differently.
    """

    async def resolve(self, *, platform: str, token: str) -> MediaBlob | None: ...

    async def describe(self, *, sha256: str) -> str | None: ...

    async def remember(
        self, *, platform: str, token: str, blob: MediaBlob,
        description: str, model: str,
    ) -> None: ...


@dataclass(frozen=True)
class MediaDescription:
    """What an interpreter made of some bytes. Two axes, like `MediaBytes` and
    `Roster`: "could not describe" must stay distinguishable from "described
    nothing", or a wedged model and a blank image become the same value and the
    counter that says interpretation is failing sits at zero.

    **THREE FIELDS, ONE PASS (issue #116).** `text` is the description and
    keeps its name and its meaning; `title` and `content` come back from the
    SAME model call, because reading the bytes is the expensive part and extra
    output fields on that call are near-free. See `nora.domain.media
    .MediaObject` for what each one is and for why a query-conditioned answer
    belongs in none of them.

    `available=False` means NONE of the three is trustworthy — an unavailable
    result never carries a partial triple, so a caller cannot half-store a
    comprehension it could not make.
    """

    available: bool
    text: str = ""
    reason: str = ""
    # Defaulted, so a describer that only produces a sentence (and every
    # existing construction site) stays valid and simply comprehends less.
    title: str = ""
    content: str = ""
    # What the READ itself left worth recording — for a chunked video (issue
    # #117): how long the clip was, how much of it actually reached a model,
    # and whether the ceiling is why the rest did not. The caller merges it
    # into `media_objects.provenance` beside the birth record.
    #
    # **RETURNED, not stashed on the adapter.** The first cut kept it as
    # `MediaComprehender.last_provenance` on the grounds that a video-only
    # field in the domain would be a shape only one adapter can fill. That was
    # wrong twice over: it is not video-only in principle (it is «what this
    # read did»), and the comprehension job spawns ONE TASK PER SUBJECT, so a
    # message carrying a video and a photo runs two `describe` calls
    # concurrently on one adapter — the still's reset raced the video's
    # chunk loop and the chunk record, the RECORDED half of DEC-161, was lost
    # or landed on the wrong row. Single-flight is per-sha256, not
    # per-instance, so it never established the safety the comment claimed.
    # A value that travels with its result cannot race.
    provenance: dict[str, object] | None = None
    # WHICH MODEL wrote this text. Returned rather than asked of the adapter
    # afterwards, for `provenance`'s reason and one more: the answer is not a
    # function of the MODALITY. A voice note's title and description come from
    # the TEXT model (`comprehension_text_model`), not from the audio
    # describer — so a caller deriving it from the media type recorded `""`
    # for the one modality whose text is written entirely by a single named
    # model. Empty means the implementation does not track it and the caller
    # records nothing, which is honest; it never means "the image model".
    model: str = ""


@runtime_checkable
class MediaInterpreter(Protocol):
    """Bytes to a comprehension — title, description, content. Never raises.

    **It used to say STICKERS ONLY, and issue #116 is what changed that
    sentence.** The old position was that a sticker is published pack art,
    identical for everyone who has the pack, while a photo is a photograph of
    somebody's life — so only the first was read automatically. What that cost
    is the issue's own story: ten photos of a meal, the whole group replying
    around them, and the subject of the conversation is the one thing the
    record does not hold. Every reply is preserved and unreadable without it.

    What did NOT change is where the result may go. The comprehension lands in
    `media_objects` and in Honcho; the transcript renders only title and
    description; `scan_identifiers` runs on every render and on every serve,
    never once at store time. The read is automatic now — the egress screen
    around it is the same one, applied later rather than earlier.

    `label` is the media LABEL (`nora.domain.media.LABELS`) and it selects the
    authored prompt: a sticker is asked what it depicts, a photo what it shows.
    It is a SYSTEM-CONTROLLED fact — derived from the platform's own flags and
    the blob's MIME type — never anything a room typed, which is the property
    `nora/infra/vision/client.py`'s prompt table exists to keep. Empty resolves
    from the media type alone.

    `summarize` is the AUDIO half, and it takes text rather than bytes on
    purpose: a voice note's `content` is already in hand — `nora/infra/speech
    .py` transcribed it at ingest via the local STT service — so asking a
    vision model to listen again would spend a second, larger call to learn
    what Nora already knows. The title and description come from a cheap text pass over that
    transcript instead. Same port, same never-raises promise, same three-field
    result; `content` is what the CALLER already holds and this verb does not
    return it.
    """

    async def describe(
        self, *, data: bytes, media_type: str, label: str = "",
    ) -> MediaDescription: ...

    async def summarize(self, *, text: str) -> MediaDescription: ...


@dataclass(frozen=True)
class ArtifactRegistration:
    """What `ArtifactIngest.register` hands back.

    Bundles the id `drop` assigned with the URL a caller can hand straight to
    `send_reply`, for the same reason `Styled` bundles text with the ranges
    that index it: splitting them into two return values (or two calls) would
    let a caller hold an id with no URL yet, and the Nora Drop spec's whole
    point (§6a) is "produce an artifact → need a URL → reply *this turn*" —
    one round trip, one value. `artifact_id` still rides along rather than
    being dropped once the URL exists, because a future expiry/admin surface
    needs to name the row without re-parsing a slug back out of the URL.
    """

    artifact_id: str
    url: str


@runtime_checkable
class ArtifactIngest(Protocol):
    """The seam a future Nora Compute tool hands a produced file across, into
    `drop`'s own stores (Nora Drop spec §7a) — `drop`'s Postgres row and RustFS
    bytes, never Compute's concern. Sibling of `BlobStore`, which the drop spec
    calls out as "exactly S3's shape" (§4), but a DIFFERENT trust boundary:
    `BlobStore`/`FilesystemBlobStore` custody a known human's attachment; this
    custodies whatever a sandboxed compute job produced — self-generated,
    freeform output no Nora guard has read (the drop spec's own §7a risk note).

    **Shipped with zero implementations alongside this Protocol at first** — a
    named departure from every other port in this file, each declared next to
    at least one concrete adapter and a real caller already in-tree; still true
    of the *caller*, since Compute does not exist yet. `drop`'s own internal
    `POST /api/artifacts` (drop spec §3, §5a) is now built
    (`deploy/drop/artifacts.py`), and `nora.infra.drop.DropArtifactIngest` is
    the Nora-side HTTP-client half (drop spec §6a) — bound to
    `nora.testing.FakeArtifactIngest` by
    `tests/contract/test_artifact_ingest_contract.py`, the same as every other
    port's fake. `DropArtifactIngest` has no caller anywhere in this repo yet;
    that wiring is Compute's, not this port's.

    `data` is `bytes`, matching `BlobStore.put` exactly — not a path (the
    caller and `drop` are different containers with no shared filesystem
    guaranteed by anything decided so far) and not a stream (nothing else on
    this port, or anywhere in `nora.domain`, expresses one, and
    `FilesystemBlobStore`'s own docstring already treats a 100 MiB video as an
    ordinary `bytes` value at this exact size class). `title` and `media_type`
    name the two things `drop`'s render route needs to answer a `GET`
    honestly; `media_type` is spelled the way `OutboundAttachment.media_type`
    already spells it, not the drop spec's looser sketch-word `kind`, so a
    caller building both values is not inventing a second vocabulary for the
    same concept. `expires_at` is epoch-ms (`PresenceFix.ts`'s unit) and
    optional — the drop spec's own §8 leaves the retention default undecided,
    so `None` means "apply whatever default the implementation has," not
    "never expires"; this port does not invent the policy its own spec
    declined to.

    Unlike `WeatherProvider`/`SearchProvider`'s fail-soft `None`, this MAY
    RAISE — mirroring `BlobStore.put`, not the read-only lookups: a write to
    external custody either lands with a real id and URL or the caller must
    know it did not, and it is the caller (Compute's future tool) that maps
    that onto R-27's `ok`/`unavailable`, exactly as native tools already do
    around `BlobStore`/`RecordStore` today.

    `update` (Nora Compute spec §9) overwrites an existing artifact at its
    existing `artifact_id`/URL rather than minting a new one — `drop`'s own
    `PATCH /api/artifacts/{artifact_id}` (`deploy/drop/server.py`) is its
    concrete implementation. Note the caller asymmetry this port carries
    deliberately: Nora Compute's actual production caller for BOTH `register`
    and `update` is Smith's own HTTP client against `drop` directly (its own
    per-job credential, its own container — see the Nora Compute spec §2/§3
    on why it never imports this Python module), not `DropArtifactIngest`.
    This Protocol and its two implementations exist so the port stays
    conformant and testable on Nora's own side per this repo's own
    discipline, the same reason `register` already had `DropArtifactIngest`
    as a real second implementation before Compute had a caller for it.
    """

    async def register(
        self,
        *,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration: ...

    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration:
        """`expires_at=None` here is the update-specific OVERRIDE of
        `register`'s convention above, not a repeat of it: for `update` it
        means "leave the artifact's existing expiry unchanged," never "apply
        whatever default the implementation has" — there is no fresh row for
        an implementation default to apply to. `PgRustfsArtifactStore.update`
        (`deploy/drop/artifacts.py`) implements exactly this via `COALESCE`."""
        ...


class JobStatus(StrEnum):
    """A compute job's current state, as `ComputeBackend.poll` reports it."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class JobResult:
    """What `ComputeBackend.poll` returns. `url`/`summary` are populated only
    when `status is SUCCEEDED`; `error` only when `status is FAILED` — never
    both, and `RUNNING` carries neither (there is nothing to report yet)."""

    status: JobStatus
    url: str | None = None
    summary: str | None = None
    error: str | None = None


@runtime_checkable
class ComputeBackend(Protocol):
    """A compute job's execution environment (Nora Compute spec §2) —
    `LocalDockerBackend` is the only adapter this repo ships. `launch`
    returns immediately with a `job_id`; the job itself runs out-of-band.
    `task` is the caller's OWN distilled brief, never a raw conversation
    transcript (spec §4's "interpret once" boundary). `context` is Nora's
    own already-typed tool results (place/route facts) she chooses to hand
    along, unmodified — never auto-collected, never required (context-channel
    spec §4). Structured facts belong here, not folded into `task`'s prose:
    a second, lossy prose-compression step is exactly the failure the
    context-channel spec exists to remove.

    `artifact_id`, when set, means "this job revises an existing artifact"
    (spec §9) rather than creating a new one — `LocalDockerBackend` uses it
    to mint a narrower-scoped credential (Task 2) than a create job gets.

    NEVER raises. A backend that cannot even start a job (Docker unreachable,
    the image missing) reports that as a `poll` result with
    `status=JobStatus.FAILED`, not an exception out of `launch` — the same
    fail-closed-but-never-crash shape `RosterSource`/`MediaSource` already
    hold elsewhere in this file, so a caller (`make_artifact`, Phase 4) never
    needs a try/except around a call it expects to be load-bearing.
    """

    async def launch(
        self, *, task: str, artifact_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> str: ...

    async def poll(self, *, job_id: str) -> JobResult: ...


@runtime_checkable
class ReverseGeocoder(Protocol):
    """coordinate → a human place name, or None. The presence collector's
    optional address source (design/19 §5). NEVER raises — a failure is a missing
    address, so the fix stores the coordinate/Life360 label instead. Has more than
    one implementation (Google today, HK-gov/OSM as switchable options), so it is
    a port with a shared contract suite both pass (tests/contract/, CLAUDE.md)."""

    async def address_for(self, *, lat: float, lng: float) -> str | None: ...


@runtime_checkable
class GeocodeCacheStore(Protocol):
    """The durable half of `CachingBilingualGeocoder`'s cache (2026-08-19,
    the presence-analytics label follow-up). NEVER raises — a store failure
    costs a re-geocode next time, never the caller's answer.

    `Whereis.locate` is the only writer: a row is created only when a real
    `where_is` question reaches `ReverseGeocoder.address_for` (no saved place,
    no Life360 address). `nora.analytics` reads this table READ-ONLY to label
    a derived place — it never calls `ReverseGeocoder` itself — so a place
    nobody has ever asked about stays unlabeled rather than triggering a
    proactive Google call, which is the exact cost pattern (~12k unread calls,
    Aug 13 2026) this design exists not to repeat.

    Keyed on the SAME rounding `CachingBilingualGeocoder` uses for its
    in-memory tier, so a persisted hit and an in-process hit agree on
    identity."""

    async def get(self, *, lat_rounded: float, lng_rounded: float) -> str | None: ...
    async def put(
        self, *, lat_rounded: float, lng_rounded: float, address: str
    ) -> None: ...
    async def nearest_within(
        self, *, lat: float, lng: float, radius_m: float
    ) -> str | None: ...


@runtime_checkable
class WeatherProvider(Protocol):
    """Current weather for a place, as a `WeatherObservation`, or None. NEVER
    raises — a failure is a missing reading, and the `weather` tool says so
    rather than the loop paying for an exception (R-5, the grounding discipline).

    HK-only today (`HkoWeatherProvider`, the keyless HK Observatory open data),
    but a PORT because the region set will grow: another provider (a global
    weather API for somewhere the HKO does not cover) plugs in behind the same
    `weather` tool with no tool change. A second implementation means a shared
    contract suite both pass (tests/contract/, CLAUDE.md). `place` is what the
    model asked about; a provider that reports the whole region (HKO returns all
    26 stations) may ignore it and let the model pick the nearest — the DEC-24
    division — while a point provider uses it to locate.

    SCREENING OBLIGATION (Kimi audit 2026-08-08): `HkoWeatherProvider` never
    TRANSMITS `place` (HKO reports the whole territory), so the `weather` tool does
    not screen it. Any future provider that DOES send `place` upstream (a geocoding
    weather API) MUST identifier/coordinate-screen it at the tool layer first — the
    same egress guard `web_search`/`route` apply — or a model-invented identifier
    in `place` would leak. Pinned here so that provider inherits the obligation."""

    async def current(
        self, *, place: str | None = None, forecast: bool = False,
    ) -> WeatherObservation | None: ...


@runtime_checkable
class ParkingProvider(Protocol):
    """Real-time parking vacancy for a named HK car park, or None. NEVER
    raises — a failure is a missing reading, and the `parking_status` tool
    reports `unavailable` rather than the loop paying for an exception (R-5,
    the same grounding discipline as `WeatherProvider`).

    Backed by the Transport Department's open-data feed (data.gov.hk,
    hk-td-tis_5): a per-car-park vacancy JSON (~179 bytes) plus basic-info
    JSON (~500 bytes), reached through the car_park_list.csv index. HK-only
    today, but a PORT because the region set will grow — another parking
    source plugs in behind the same `parking_status` tool with no tool
    change. `name` is what the model asked about (a venue or car park name);
    matching it to a parking id is the provider's job, so the 105KB index
    never enters model context.

    `strict=True` is `find_parking`'s cross-ref mode: an EXACT name match
    only, so a Google-named lot never attaches another car park's vacancy
    through a substring fallback. `parking_status` (a user naming a spot)
    keeps the fallback, where a distinctive substring is the experience.

    RETURN SHAPE: `{"parking_id", "name", "vacancy", "lastupdate"}` on
    success, plus `opening_status`/`address`/`fee` when the feed's basic
    info actually carried them (never invented); `None` on any fetch/parse
    failure or an unmatched name. `vacancy` is an INT of spaces free, never
    fabricated; `lastupdate` is the feed's own timestamp string. The opening
    status rides `opening_status`, NOT `status` — the R-27 axis owns the
    `status` key and `parking_status` spreads this dict under its own
    `{"status": "ok", **data}`, so a provider value written into `status`
    would overwrite the contract's `ok`.
    """

    async def vacancy(
        self, *, name: str, strict: bool = False,
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class StreetParkingProvider(Protocol):
    """Metered on-street parking near a point — the street row `find_parking`
    appends. NEVER raises — `None` is a missing reading and the tool reports
    `unavailable` (R-5), never a confident zero.

    Backed by the TD on-street feed (hk-td-msd_1): a cached metered-space
    table plus a fresh per-space occupancy CSV. HK-only today, a PORT for
    the same reason `ParkingProvider` is — and a second implementation
    (including a test double) means a shared contract suite both pass
    (tests/contract/, CLAUDE.md).
    """

    async def near(
        self, *, lat: float, lng: float, radius_m: float = ...,
    ) -> StreetParking | None: ...


@runtime_checkable
class SearchProvider(Protocol):
    """`query -> {status, results}` web search, and `url -> {status, content}`
    page extraction, behind the `web_search`/`web_fetch` tools (DEC-73, DEC-116).
    NEVER raises — a transport/auth failure is `{"status": "unavailable"}`, never
    an exception into the loop (R-5). A PORT (operator: "same as web_search —
    adaptor") so another backend plugs in with no tool change — and since
    DEC-157 that claim is SPENT rather than anticipated: `TavilySearchProvider`
    and `ExaSearchProvider` both implement it, selected by `[search] provider`,
    and the branch lives at the composition root alone. `search` returns ranked
    results, `fetch` returns one page's content; both `untrusted`-wrapped and
    clamped, and the second implementation means the shared contract suite both
    pass (`tests/contract/test_search_provider_contract.py`) — including the
    `unavailable` note, which is model-facing instruction and must not differ
    with the operator's billing preference. **Still no SSRF surface** (DEC-73
    (2)'s argument, unchanged by `fetch`'s return AND by the second backend):
    the provider is handed a URL over its own authenticated API and fetches it
    from ITS servers, never Nora's — Nora's own network never makes a
    connection chosen by model output, so the old `is_global`/`allowed_urls`
    apparatus stays gone. That is a property of every adapter behind this port,
    not of one vendor, and an adapter that fetched over Nora's OWN network
    would not satisfy it. Contrast `LinkPreviewSource` (DEC-87), which IS handed a URL and
    fetches it over Nora's own network, and so re-grew that guard."""

    async def search(self, *, query: str) -> dict[str, Any]: ...

    async def fetch(self, *, url: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SteamPlayerState:
    """One Steam account's live player summary, as the `online_now` tool
    reads it (design/online-status/). `personastate` is Steam's own integer
    (0 offline, 1 online, 2 busy, 3 away, 4 snooze, 5 looking-to-trade,
    6 looking-to-play) — carried RAW, never flattened to a bool, because
    "away" and "snooze" are answers the model must be able to give.
    `profile_public` is derived from `communityvisibilitystate == 3`: when
    it is False, `game` being None means "cannot see", never "not playing"
    — the tool reports `profile_hidden` and asserts nothing about play
    state. `game`/`game_id` are present only while a title is running."""

    steam_id: str
    personastate: int
    profile_public: bool
    game: str | None = None
    game_id: str | None = None


@runtime_checkable
class SteamPresenceProvider(Protocol):
    """Live Steam player summaries behind the `online_now` tool
    (design/online-status/). NEVER raises — `None` is the WHOLE READ being
    unavailable (transport failure, auth failure, a body that is not
    Steam's envelope), and the tool reports `unavailable` (R-5), never a
    confident "everyone is offline". A request the server answered fine
    simply omits the ids it does not know, so an ABSENT KEY is that
    account's `nothing_found` — the two never share a shape.

    One call takes the whole batch (`steam_ids` is a frozenset; Steam's
    GetPlayerSummaries accepts up to 100 comma-separated ids per request)
    because the tool's one question must cost one network round trip, not
    one per person. A PORT (operator: same shape as `WeatherProvider`) so
    a second implementation — including a test double — means a shared
    contract suite both pass (tests/contract/)."""

    async def summaries(
        self, *, steam_ids: frozenset[str],
    ) -> dict[str, SteamPlayerState] | None: ...


@runtime_checkable
class SocialProvider(Protocol):
    """`{platform, what, target, limit} -> {status, ...}` social reads, behind
    the `x_read`, `x_search` (DEC-201), `instagram_read` and `instagram_search`
    (design/instagram-adoption/18) tools. NEVER raises — a transport failure of
    the bridge call is `{"status": "unavailable"}`, never an exception into
    the loop (R-5), and the R-27 three states apply end to end: an error is
    never a `nothing_found`.

    THREE implementations since social v1 (design/instagram-adoption/08):
    `OpenCliBridgeClient` (the default backend), `InstagramSocialClient` (the
    exception backend, one row), and `SocialRouter` — which is itself a
    `SocialProvider` and is what the registry is actually handed, so nothing
    above this port knows there are two backends. The contract suite the
    sentence above has been asking for since there was one implementation is
    `tests/contract/test_social_provider_contract.py`, and all three pass it.

    Two DECLARED status extensions ride beside the core three on this port,
    both of them produced by `SocialRouter` on a PERSON-keyed Instagram read
    (`ambiguous` on profile and story, `refused` on story only), and both
    named here so they cannot arrive silently (`nora.agent.ports
    .DECLARED_STATUS_EXTENSIONS` is where the vocabulary is declared):

      * `ambiguous` — the subject did not resolve. `target` is a `person_id`
        for a subject-keyed read, and no person-index entry means Nora does
        NOT get to guess a handle from a name (design/instagram-adoption/09).
        This is the SECOND sense the declaration carries — «no entry at all»
        beside «several candidates» — and the two share a word because they
        share a sentence: I don't know which one you mean, so I didn't look.
      * `refused` — the subject resolved and is out of reach. v1 is Tier 0
        only: someone in a room Nora is in (`07-social-boundary-and-scope`).

    Neither is a failure of the call, which is why neither may collapse into
    `unavailable` — «I may not look him up» and «I couldn't look» are
    different claims, and R-27 exists to keep them apart.

    READ-ONLY by construction at three layers: the tool only constructs
    allowlisted `(platform, what)` pairs (there is no free-form command
    passthrough), the BACKEND re-enforces its own frozen allowlist in the
    process that performs the read — never the process running the model — so
    a write verb (`twitter post/like/follow/delete`, …) is unreachable even
    under prompt injection, and the tool's `NativeTool` entry declares
    `read_only=True`. "Host-side" was HOW that was achieved when there was one
    backend, not the point of it: the instagram-bridge is in-container and
    holds an equivalent allowlist of its own (design/instagram-adoption/08).

    Retrieved text is stranger-written: the implementation `untrusted`-wraps
    it (R-13) with the source URL as provenance and CLAMPS it, because a tool
    result is re-sent on every subsequent model call. `target` is an EGRESS
    surface, screened for identifiers/coordinates at the tool layer
    (`nora.agent.tools`, unchanged) BEFORE it reaches here."""

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class SocialSubjectIndex(Protocol):
    """`person_id -> the network id Nora may read, and whether she may`
    (design/instagram-adoption/07 §tiers, /10 §identity).

    This is the seam that keeps a SUBJECT-keyed social read from becoming an
    address-keyed one. A story read names a person Nora already knows; the
    igid behind them is a FACT IN THE PERSON INDEX, never something the model
    supplies and never something inferred from a display name — a wrong
    binding does not degrade the answer, it reads a different human's stories
    silently.

    **Not Honcho, deliberately.** Honcho holds what Nora remembers and infers
    about people, which is the right home for "Miles posts a lot of food
    photos" and the wrong home for "Miles is igid 221731317": the binding is an
    AUTHORIZATION KEY (the tier decision is made on it), and a binding that
    can drift is a permission that can drift. It must also be auditable and
    revocable — a human reads one file and deletes one line — which inferred
    memory supports neither of.

    Separate methods rather than one fused answer, because the failures are
    different claims the model must not conflate. `igids_for` returns EVERY
    igid on file for the person, and its caller tells three states apart
    (design/instagram-adoption/18, 2026-09-24): none on file is
    `nothing_found` («no account on file, so I didn't look»), several is
    `ambiguous` («I can't tell which, so I didn't look»), and `None` — the
    index could not be read — is `unavailable`. An out-of-tier person is
    `refused` («I'm not going to look»). The earlier `igid_for -> str | None`
    folded all three unresolved cases into one `None`, and the router had to
    call every one of them `ambiguous`.

    **No method raises**, and all fail CLOSED: an index that cannot be read
    answers `None` / `False`, which costs a read that should have happened
    and never grants one that should not have.

    `person_for_igid` is the REVERSE lookup, and it exists for links
    (design/instagram-adoption/15 §Links): a link names ONE account, so its
    reach check runs `igid -> person -> is_tier_zero` on the igid the link
    named. Re-deriving through `igids_for` would turn a friend with two
    accounts into `ambiguous` although the link said which one. An igid that
    maps to no person is `None`, which the link path answers `refused`.

    The production implementation is
    `nora.infra.social.subjects.PersonsSubjectIndex` (`identity.toml` +
    `policy.toml`); `tests/contract/test_social_subject_index_contract.py`
    holds it and `nora.testing.FakeSocialSubjectIndex` to the same answers.
    """

    async def igids_for(self, *, person_id: str) -> frozenset[str] | None: ...

    async def is_tier_zero(self, *, person_id: str) -> bool: ...

    async def person_for_igid(self, *, igid: str) -> str | None: ...


@dataclass(frozen=True)
class UsernameLookup:
    """The answer to «which igid holds this Instagram username?»
    (design/instagram-adoption/15 §Links, /17 P9, P10).

    Three states, kept apart because each is a different sentence:

    * `found`: exactly one account holds the username, exactly (ASCII,
      case-insensitive); `igid` is its id, and it never leaves the router;
    * `none`: the lookup ran and no account holds it, which is `nothing_found`;
    * `unavailable`: the lookup could not be made or answered in a shape
      nothing can trust, which is `unavailable`, never «no such account».
    """

    status: Literal["found", "none", "unavailable"]
    igid: str = ""


@runtime_checkable
class InstagramAccountLookup(Protocol):
    """`username -> igid`, over the instagram-bridge's exact lookup
    (`GET /accounts?username=`, design/instagram-adoption/15 §Links).

    The RESOLUTION half of a typed handle or a pasted link. It is not the
    person index: `identity.toml`'s `username` is display metadata, read and
    never matched, because handles rename, and a link usually names someone
    who is not in the file at all (15). Only `SocialRouter` calls it, and the
    igid it answers is handed to a backend or to
    `SocialSubjectIndex.person_for_igid`, never to the model (R-18).

    **Never raises**, and fails closed: anything it cannot answer is
    `unavailable`. **Never re-picks**: an answer is `found` only for an
    account whose username IS the one asked for, whatever order the bridge
    listed accounts in (17 P9 measured an exact match that was not first).

    The production implementation is
    `nora.infra.social.InstagramSocialClient`; `nora.testing
    .FakeInstagramAccountLookup` is the fake.
    """

    async def igid_for_username(self, *, username: str) -> UsernameLookup: ...


@runtime_checkable
class LinkPreviewSource(Protocol):
    """`url -> LinkPreview | None` — the OpenGraph read behind an outbound
    preview card. **NEVER raises**, and `None` is the only failure value.

    That single-value degradation is deliberate, and it is the one place in
    this file where the two-axis `Roster`/`Recollection` shape is REFUSED
    rather than copied. Those two carry `available` because a CALLER has to
    tell "I could not look" from "there is nothing", and says the difference
    out loud to the group. Nothing downstream of this port ever will: the only
    consumer is `nora.agent.outbound.OutboundComposer`, whose entire policy for
    every failure is the same — send the message without a card. A `reachable`
    bit here would exist solely to be discarded at its one call site, which is
    an invented contract (`nora.domain.ports`' `MemoryStore.feed` makes the
    same argument for staying a bare `bool`).

    The **R-5 obligation is on the implementation, not the caller**: a preview
    is decoration on a message that must go out anyway, so a dead host, a slow
    host, a 404, a non-HTML body or a page with no `og:title` are all `None`,
    and a preview must never be the reason the group hears nothing.

    Whoever implements this inherits an **SSRF obligation**, because unlike
    `SearchProvider.fetch` (DEC-116: also handed a URL that originated in model
    output, but fetches it from ITS OWN servers) this port fetches the URL over
    NORA'S OWN network — the exact exposure the retired `web_fetch` had, and
    which `SearchProvider.fetch` deliberately does not reopen.
    `OpenGraphPreviewSource` documents the guard it applies; a second
    implementation that does not apply one reopens DEC-73's hole.
    """

    async def preview_for(self, *, url: str) -> LinkPreview | None: ...


@runtime_checkable
class EngagePolicy(Protocol):
    """Async now, ahead of need — see module docstring."""

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision: ...


@runtime_checkable
class ContextAssembler(Protocol):
    async def assemble(self, *, request: EngagementRequest) -> TagContext: ...


@dataclass(frozen=True)
class Proposal:
    """One validated result an adaptor wants written, with the provenance that
    says what produced it.

    `payload` is deliberately `object`: the runtime never names an adaptor's own
    value type. A `DreamSink` implementation knows what it stores; the loop that
    hands it a `Proposal` does not, and typing this as anything narrower would
    put `NoteCreationProposal` (or whatever the next tier's value type is) in
    the runtime's imports and make every new adaptor a change to the runtime.

    Provenance rides HERE rather than inside `payload` for the same reason. Both
    fields are the columns DEC-125/DEC-126 added to every adaptor-written table,
    and DEC-128 fixes what `instruction_sha256` must be: the digest of what was
    ACTUALLY SENT to the model, read per run, never cached at construction and
    never covering text the model did not see.

    `established_by` is the run that produced it — the handle a later
    `supersede` names when it retires this row.
    """

    payload: object
    instruction_sha256: str
    model: str
    established_by: str
    # The candidate row this proposal SETTLES, when the adaptor knows it —
    # adaptor-supplied, never model-authored, and never round-tripped through a
    # prompt (it is a uuid, and `dream_screen` drops any envelope item carrying
    # one). `None` for an adaptor with no candidate tier behind it.
    #
    # It exists so a sink can join back to the candidate by IDENTITY rather
    # than by re-deriving it from the payload's words. The fact tier had the
    # latter and it was wrong twice over: the model rewording a claim by one
    # character — which the adjudicator's own code documents as expected and
    # logs — made the join miss and report a WIRING GAP, and a triple match
    # against the whole table could donate a settled candidate's provenance to
    # an unrelated later fact.
    candidate_id: str | None = None


#: The per-CALL-SITE allowance `DreamerRuntime` multiplies by an adaptor's own
#: `store_calls_per_tick` to bound `inputs` and `commit` (issue #60), and the
#: default for `[dreamer] store_timeout_seconds`.
#:
#: **It lives HERE, in one place, because it had two homes for an afternoon**
#: — a literal in `nora.config` and another in `nora.agent.dreamer`'s
#: `__init__` default — and two copies of a number is how the standing tier's
#: own hold came to be understated by 2x. `nora.agent` may not import
#: `nora.config`, and both already import `nora.domain`, so this is the one
#: module both sides can read. `DEFAULT_STANDING_STORE_TIMEOUT_SECONDS` in
#: `nora.domain.standing` is the same arrangement for the same reason.
#:
#: **15s is sized against the CURATOR's degraded worst case, not against one
#: healthy call.** The number multiplies SITES, and a site inside a per-note
#: loop runs once per note: `NoteCuratorAdaptor.inputs` renders up to
#: `max_expired_per_tick` + `max_revisits_per_tick` notes (20 + 10 on the
#: shipped config) and each one costs a `record.recent` plus a
#: `MediaJoin.enrich` that carries its OWN 3s bound (`[media]
#: join_timeout_seconds`). A degraded-but-not-hung media store therefore
#: spends 30 x 3 = 90s inside one legitimate `inputs`, and at 5s x 10 sites
#: the bound would have fired at 50s — turning a slow tick into a counted
#: store timeout and, for a daily-slotted adaptor, a lost day. 15 x 10 = 150s
#: clears that with room and still sits under `[dreamer] tick_seconds = 300`.
#: Raise it only against a reading of that kind, never to quiet an alarm.
DEFAULT_DREAM_STORE_TIMEOUT_SECONDS = 15.0


#: The ceiling every `DreamAdaptor.store_calls_per_tick` may declare (issue
#: #191). Today's four are 5 (`CultureAdaptor`), 10 (`NoteCuratorAdaptor`), 12
#: (`NoteCandidateAdaptor`) and 17 (`ProactiveAdaptor`) — this is headroom over
#: the widest of those, not a measurement of any one of them.
#:
#: **It exists so `[dreamer] max_unit_hold_seconds`'s startup gate
#: (`nora.config`) can PROVE its number against `2 x
#: DREAM_MAX_STORE_CALLS_PER_TICK x store_timeout_seconds` without importing
#: `nora.language`.** `nora.config` may import `nora.domain` — the forbidden
#: direction is the other one — but the adaptors live four layers up, and a
#: startup validator that imported them to re-derive this number every boot
#: would be the heavy, cycle-risking route to a number this module can just
#: state. `tests/architecture/test_dream_store_calls_are_declared.py`'s
#: `test_no_adaptor_declares_more_store_calls_than_the_wall_clock_assumes` is
#: what keeps the two honest: every shipped adaptor's declared count is
#: checked against this ceiling there, so a new adaptor that needs more sites
#: fails LOUD, at test time, rather than making the wall clock's own proof
#: silently wrong. Raise it, and `[dreamer] max_unit_hold_seconds`'s default,
#: together.
DREAM_MAX_STORE_CALLS_PER_TICK = 20


#: The wall-clock bound on the participation turn's Honcho reads
#: (`FirePath._read_memory`, issue #210), and the default for
#: `[memory] read_timeout_seconds`.
#:
#: It lives HERE for `DEFAULT_DREAM_STORE_TIMEOUT_SECONDS`' reason above, which
#: is the same arrangement `DEFAULT_STANDING_STORE_TIMEOUT_SECONDS` has in
#: `nora.domain.standing`: `nora.app` may not import `nora.config`'s default
#: past the config object, both sides already import `nora.domain`, and two
#: copies of a timeout is how the standing tier's hold came to be understated
#: by 2x.
#:
#: **5s over the WHOLE concurrent batch, not per read.** `HonchoClient` already
#: bounds a single `representation` at 3s, so this is a belt over the PORT — a
#: `MemoryStore` implementation is not obliged to time anything out, and these
#: reads run while the heartbeat holds this conversation's slot. One bound over
#: the gather means adding a speaker costs concurrency, never hold time.
DEFAULT_MEMORY_READ_TIMEOUT_SECONDS = 5.0


@runtime_checkable
class DreamAdaptor(Protocol):
    """One Dreamer objective: what to research, over what, with which tools,
    and how to turn the answer into rows.

    The Dreamer runtime owns the loop, the tool ceiling, the budget and its own
    doctrine, and knows nothing about notes. Everything that IS tier-specific
    arrives through this port. **It is the generalisation of a port that was
    append-shaped**: `RoomConsolidator`'s `consolidate(scope_id, batch) -> int`
    (deleted 2026-09-07 with the language tier) said "extract, append,
    rescore, and nothing else", which is why an adaptor that INVALIDATES as
    well as appends did not fit it, why a unit-keyed adaptor did not fit
    its scope key, and why a watermark-free one had to be a separate parameter
    on `dream_loop` rather than another consolidator. This port has room for
    all three, which is what makes a new tier a new adaptor rather than a new
    parameter.

    **The runtime never names a tier's value type.** It hands the model's
    already-envelope-validated items to `accept`, which returns whatever that
    tier's typed values are as `object`, wraps each in a `Proposal` carrying the
    provenance only the runtime knows, and hands them back to `commit`. No
    tier's value type — `NoteCreationProposal`, or whatever comes next — ever
    appears in an import in `nora/agent/`.

    **Two deviations from the plan's sketch, both found by building it** —
    recorded here because each looks like an arbitrary difference until the
    reason is stated:

    1. **`accept` returns payloads, not `Proposal`s.** A `Proposal` carries
       `instruction_sha256`, `model` and `established_by`, none of which an
       adaptor can know: the runtime read the instructions, built the model
       provider and named the run. An adaptor returning `Proposal`s would have
       to be handed all three to fill in, which is provenance travelling one
       layer further than it needs to and one more place it can be filled in
       wrongly.
    2. **`commit` drives the sink; the runtime does not call it directly.** The
       runtime cannot: a tier's write is often TWO verbs in order — `propose`
       the new row, then `supersede` the old one naming the new id as
       `replaced_by` — and knowing that sequence means knowing the tier's own
       value type.
       So the sink is the adaptor's own collaborator (constructor-injected,
       never a port attribute the runtime reads and never uses) and `commit` is
       where a tier sequences its own writes.

       This does not weaken the keystone §3.2 asymmetry, which is about the
       MODEL not writing: there is still no write tool, the model still only
       returns a structured value, and code still disposes. `commit` is code.

    **Screening happens before `accept`, not after it** (keystone §7, and this
    is the other deviation from the plan, which screened parsed proposals).
    The runtime screens the model's raw envelope ITEMS — every string value in
    every item — against R-18/R-28 before any tier type exists. Screening a
    parsed `object` would need the adaptor to expose its own text for
    inspection, which is a second place a tier can forget something; screening
    the item is generic, is strictly more conservative (it sees fields a parse
    drops), and still drops at item granularity so one poisoned item never
    costs its siblings.

    `name` is the adaptor's identity in three places at once — the watermark
    row, the metric label, and the trace span — which is why it is one field
    rather than three that can disagree. **Renaming it re-reads the record from
    the beginning**: an unknown `(name, scope)` has `watermark_for` return 0.
    """

    name: str
    """The watermark identity, the metric label and the trace name. Changing it
    is a data migration, not a rename."""

    key_kind: str
    """`"scope"` or `"unit"` — which population the runtime ticks this
    adaptor over. Every room adaptor's unit of work is a `scope_id`; the fact
    adaptor's is a `Conversation.unit` (DEC-105). Not a wider or narrower
    batch — a DIFFERENT key, which is why `_dream_once`'s single shared
    per-scope watermark cannot host one unchanged."""

    uses_watermark: bool
    """False for an adaptor that does not consume a message batch at all, and
    which therefore gets no watermark, lag or quiet condition and must carry
    its own fire condition. `nora.language.note_curator.NoteCuratorAdaptor` is
    the shipped one: it reads LIVE STATE — which anchors have passed, which
    notes are due — and none of those questions has a "since when"."""

    objective: str
    """The objective FILE NAME, resolved by the runtime against
    `DreamerInstructions` — never the text. `nora.language` sits below
    `nora.instructions` in the layer contract and must not read a file or hold
    an instruction store, and the digest must be read per run rather than
    captured at construction (DEC-128): an objective that hot-reloads and a
    text captured once are indistinguishable from correct, forever."""

    envelope_key: str
    """The top-level JSON key holding this objective's items —
    `{"observations": [...]}` for the room adaptors. Declared rather than fixed
    at one shared literal because it is part of what the objective TELLS the
    model to return, and the two must not be able to drift apart in different
    files."""

    tools: frozenset[str]
    """Tool NAMES, resolved by the runtime against its registry.

    Names rather than tools because `nora.language` may not import
    `nora.agent` — the same inversion `ToolRegistry.tool_names()` already
    performs for the doctrine loader. The runtime refuses any name outside
    `ToolRegistry.read_only_tools()`, so this field is a narrowing of the
    ceiling and can never widen it: an adaptor asking for an act verb fails to
    build rather than quietly receiving one."""

    store_calls_per_tick: int
    """How many awaited collaborator CALL SITES this adaptor's `inputs` and
    `commit` paths hold together — the number the runtime multiplies by
    `[dreamer] store_timeout_seconds` to bound them (issue #60).

    **Declared rather than measured at run time, and required rather than
    defaulted.** `DreamerRuntime` wraps `inputs` and `commit` in
    `asyncio.wait_for`; before that, a wedged store call left `dream_loop`
    holding `<adaptor>:<key>` in its `running` set forever, so that unit never
    ticked again and the only symptom was an absence. A default here would
    silently fit the first adaptor that forgot to think about it, which is the
    failure this field exists to make impossible: an adaptor that does not
    declare it is not a `DreamAdaptor` (this protocol is `runtime_checkable`,
    so `isinstance` reads the attribute) and fails the contract suite.

    **SITES, not dynamic calls, and the difference is where the arithmetic
    bites.** A site inside a per-item loop runs once per item, so the real
    fan-out is the site count times the adaptor's own per-tick cap:
    `NoteCandidateAdaptor.commit` can reach 64 items (`MAX_ITEMS`) x 6 sites,
    and `NoteCuratorAdaptor.inputs` renders up to 30 notes x 2 evidence
    calls. `DEFAULT_DREAM_STORE_TIMEOUT_SECONDS` above is therefore sized
    against the widest of those degraded cases and NOT against one healthy
    call — an earlier draft of this paragraph divided a per-PHASE bound by a
    per-CALL latency, claimed three orders of magnitude of headroom, and was
    under 1x against the curator.

    The count's job is narrower and is the one the issue named: `tests/
    architecture/test_dream_store_calls_are_declared.py` counts the sites off
    each adaptor's own AST and fails when the two disagree, so adding a store
    call fails a test rather than silently widening the hold — the shape
    `STANDING_STORE_CALLS_PER_FOLD` and
    `Config._the_standing_slot_hold_fits_its_budget` already use one tier
    over."""

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The records this objective reasons over, rendered for the prompt.

        `batch` is empty for an adaptor with `uses_watermark = False`, and may
        be empty for one that has a watermark and no new messages — which is a
        REAL state and not an error: see `DreamSink.propose`'s empty-tuple
        contract, which is what preserves time-driven decay."""
        ...

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """The typed parse of already-envelope-validated, already-screened
        items — this tier's values, as `object` to the runtime.

        The division of labour is the one the existing observers already draw:
        the runtime validates the ENVELOPE (parses as JSON, is an object,
        carries `envelope_key`, is a list, is under a size cap, every element
        is a mapping) and `accept` does the typed parse, **dropping a malformed
        item silently rather than raising** — `_parse_item`'s discipline, which
        exists so one bad element does not discard a batch that was otherwise
        fine.

        Returning fewer values than it was given is therefore ordinary and
        means «some items were malformed», not «the batch failed». Returning
        NOTHING for a non-empty `items` is still ordinary — «I looked and there
        was nothing usable» — and it lets the watermark advance.

        **One narrow exception, and it is a raise rather than a fourth return
        value** (2026-09-17, `nora.language.culture`). An adaptor MAY raise
        when the model's result was READ and is being DISCARDED, and losing
        the batch with it would lose the only copy: `DreamerRuntime.run`
        reports any exception here as `unavailable`, which is the one status
        that HOLDS the watermark, so the batch is folded again rather than
        spent. It is deliberately not available for a malformed ITEM — that is
        still dropped silently, so one bad element cannot discard a batch that
        was otherwise fine — and it is not available for «there was nothing»,
        which is `()` and R-27's `nothing_found`. The test is whether a
        SECOND fold of the same batch is the right next move; for a malformed
        item it is not, and for an account the tier refused to write it is."""
        ...

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Write `proposals` through this adaptor's own sink, returning rows
        written.

        **`commit(key, ())` is a legal and meaningful call**, not a call to
        skip: it is R-27's `nothing_found` — «I looked and there was nothing» —
        and it is where time-driven decay lives. Verified against the shipped
        curator: `NoteCuratorAdaptor.commit` with zero proposals still records
        a WAKE for every note the run selected, advancing `revisit_at` and
        charging a strike to a note nobody answered. None of the adaptor's
        other fields has a slot for that. The runtime NOT calling `commit` is
        the different, third state: `unavailable`, «I could not check».

        Collapsing those two — treating an empty sequence as "nothing to do,
        skip the call" — silently drops decay and turns a wiring gap into a
        false negative. That is the ERROR-reads-as-PASS failure R-27 exists to
        prevent, one layer up.

        Returns rows WRITTEN, which is not `len(proposals)`: a tier refuses
        its own proposals by design — the note create gate refuses a candidate
        that duplicates a standing note without adding anything — so a legal
        commit of three proposals can write one row. The count is what
        `nora_dream_written_total` reports."""
        ...


@runtime_checkable
class DreamSink(Protocol):
    """Where a Dreamer adaptor's validated result is written.

    Two verbs, and deliberately no third. **There is no delete — not on this
    port, and not on any implementation of it.** That is the enforcement, not a
    convention: «invalidate, never delete» written as a type cannot be forgotten
    by either side, whereas the same rule written in a docstring is remembered
    until the night someone is tired. A single opaque `write(result)` is also
    rejected — it cannot express supersede at all, so the distinction between
    "this is new" and "this replaces that" would be lost at the boundary.

    **The key is the adaptor's own.** Every existing adaptor's unit of work is a
    `scope_id`; the fact adaptor's is a unit (`Conversation.unit`,
    DEC-105). The port takes `key: str` and does not care which, because
    `_dream_once`'s shared per-scope watermark cannot host a unit-keyed
    adaptor unchanged and the port must not bake in the assumption that it can.

    **An empty `propose` is R-27's three states expressed in the port:**

    - `propose(key, ())` — the adaptor ran and found nothing. `nothing_found`,
      «I looked and there was nothing». It is a LEGAL call and the sink may act
      on it, because decay is TIME-driven rather than evidence-driven: the
      shipped curator's own `commit` with zero proposals still records a wake
      for every note the run selected. None of the five adaptor fields has a
      slot for that, so this is where it lives.
    - not calling `propose` at all — the adaptor could not check. `unavailable`,
      «I could not check». An ERROR is never a PASS.

    Collapsing those two, or treating an empty tuple as "nothing to do, skip the
    call", silently drops decay and turns a wiring gap into a false negative.
    """

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> Sequence[str]:
        """Write `proposals` under `key`; return the id of each row written, in
        order.

        Ids, not a count: `supersede` has to be able to NAME what a previous
        `propose` produced, and a count cannot. An empty return is correct for
        an empty `proposals`.

        `now_ms` is on the verb rather than on `Proposal` because one tick
        writes many proposals at a single instant — putting the clock on each
        proposal invites a batch whose rows disagree about when the batch
        happened.
        """
        ...

    async def supersede(
        self,
        *,
        key: str,
        target: str,
        replaced_by: str | None,
        because: str,
        source: str | None,
        now_ms: int,
    ) -> int:
        """Retire `target`, naming WHAT replaced it and WHY. Returns rows
        affected.

        **`replaced_by` is the successor IN THIS TIER, and it is nullable at the
        port on purpose.** Two tiers derived the need for it from different
        specs — the fact tier, whose supersessions always have a successor
        because a contradicted fact is replaced by the corrected one, and the
        note tier, whose `retire` case has none (an anchor date passed; a dinner
        that happened is not replaced by anything). A non-nullable field would
        force the second tier to mint a placeholder id to satisfy the signature,
        which is the "invent a value to satisfy a schema" move this codebase
        forbids everywhere else.

        So: **permissive at the port, strict at each implementation.** A sink
        whose rows require a successor refuses the null itself — the fact
        store's `(superseded_by is null) = (superseded_at is null)` biconditional
        is a FACT-TIER rule enforced in its own CHECK, not a universal one. A
        port that hard-codes one tier's cardinality is the same mistake as a
        `payload` typed to one tier's value: the port stays general, and the sink
        is where the type AND the cardinality are known again.

        **`source` is a WARRANT** — a URL or equivalent pointer to evidence —
        and never the id of a row in the tier. That separation is why
        `replaced_by` exists rather than being smuggled through `source`: the
        same field meaning "a citation" to one tier and "the surviving row" to
        another is a collision where whoever writes first fixes it wrong for the
        other.

        `because` is required and stays narrow enough to be a constrained enum
        on the implementing side (`stale|wrong|merged` in the fact tier) — which
        it could not be if it had to carry a successor id as free text.
        """
        ...


@dataclass(frozen=True)
class ResolvedTrack:
    """What `TrackSource.resolve` hands back — enough to start playback and
    enough to persist as a `music_queue` row (design/discord-music/
    01-playback-and-source.md, 03-state-and-persistence.md). `stream_url`
    points at the underlying host's own CDN, not anything Nora serves —
    short-lived by that host's own policy, never persisted past the row's
    `url` column, which stores `locator` — a resolve INPUT to re-derive from,
    not a cache of `stream_url`.

    `stream_url` may be empty when this came from a cheap, unresolved
    listing (a playlist entry `YtDlpSource` only flat-extracted — title and
    ID, no per-track stream lookup) rather than a full resolve; `locator`
    is never empty — it is always a `query_or_url` that, handed back into
    `resolve()`, re-derives THIS track specifically (never a raw search
    string that could re-resolve to a different top result later)."""

    title: str
    stream_url: str
    duration_seconds: int | None
    source: str
    source_id: str
    locator: str


@runtime_checkable
class TrackSource(Protocol):
    """A query or URL, resolved to zero or more `ResolvedTrack`s — an empty
    sequence, never `None`, when nothing resolves. NEVER raises, matching
    `WeatherProvider`/`SearchProvider`'s discipline in this file rather than
    `ComputeBackend`'s: this resolves a fixed, deterministic lookup, it does
    not launch a job. A dead link, a private video, or an unresolvable query
    are all ordinary empty-sequence outcomes an implementation must be able
    to say plainly, never an exception into the caller.

    **More than one result means a playlist expanded**, not multiple guesses
    at the same query — an ordinary search or a single video URL always
    resolves to at most one track. Which URL shapes are treated as a
    playlist to expand, versus a single video to resolve, is entirely the
    implementation's call (`YtDlpSource`'s is narrow — an explicit
    `/playlist?list=...` link only, capped; see `01-playback-and-source.md`).

    `YtDlpSource` is the only implementation today, YouTube-only — an
    allowlist enforced BY the implementation, not left to whatever
    `yt-dlp`'s own extractor set happens to accept, because `query_or_url`
    is untrusted input a person pasted. Per this repo's port convention, a
    single implementation needs no contract suite; one becomes required the
    day a second `TrackSource` (a SoundCloud adapter) exists."""

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]: ...

    async def related(self, *, locator: str) -> Sequence[ResolvedTrack]:
        """Tracks to autoplay after `locator` finishes with nothing queued
        behind it — YouTube's own "Mix"/autoplay-radio recommendations
        seeded from that one track, in `YtDlpSource`. An empty sequence
        (never an exception, same discipline as `resolve`) covers every
        reason there is nothing to offer: the source has no notion of
        recommendations at all, the seed track has none, or the lookup
        failed — the caller's only correct response to an empty sequence is
        to fall through to its ordinary "queue is empty" behaviour, never to
        retry or guess a track.

        `locator` is a previously-resolved `ResolvedTrack.locator` (what
        `QueuedTrack.url` already holds for the just-finished track) —
        never a raw search query; recommending from an unresolved guess
        would compound one uncertain match into another."""
        ...

    async def refresh_session(self) -> None:
        """Throw away whatever per-source session state the implementation
        holds, so the NEXT `resolve` negotiates a new one. Called when a
        stream this source resolved turned out to be unplayable, before the
        retry re-resolves it.

        **A locator is only as good as the session that signed it.**
        Measured 2026-09-18: a `YoutubeDL` kept for the life of the process
        handed out CDN addresses that were answered `403 Forbidden` — every
        one, for 21 hours — because `videoplayback` addresses are bound to
        the visitor session that produced them. Re-resolving through that
        same session reproduces the failure exactly. Without this hook a
        bounded retry is decoration: it spends a network round-trip asking
        the broken thing for another broken answer.

        A source holding no such state satisfies this by doing nothing.
        NEVER raises, same discipline as `resolve` — a source that cannot
        refresh has simply not improved the retry's odds, which is not an
        error the caller can act on."""
        ...


@dataclass(frozen=True)
class MusicToolOutcome:
    """What a Nora agent music tool hands back (design/discord-music/
    07-agent-tool.md) — this file's R-27 three-state discipline: `ok` /
    `nothing_found` / `unavailable`, never conflated. `title`/`url` are only
    meaningful on `ok`; `queued` distinguishes "started playing" from
    "added to the queue behind something already playing" (same
    distinction `/music play`'s reply carries as `Now playing` vs
    `Queued`)."""

    status: Literal["ok", "nothing_found", "unavailable"]
    title: str | None = None
    url: str | None = None
    queued: bool = False
    note: str = ""


@runtime_checkable
class MusicToolBridge(Protocol):
    """What a Nora agent tool needs to control Discord music
    (design/discord-music/07-agent-tool.md), without `nora.agent` ever
    importing `discord.py` directly — `nora.agent` and `nora.platform` are
    PEERS in this repo's layered-architecture contract, neither may import
    the other, so this port is the seam. NEVER raises, matching
    `TrackSource`/`WeatherProvider`'s discipline in this file.

    Discord-only by construction, not by a platform check the CALLER has
    to remember: every method resolves `conversation_id` (a Discord tag's
    own `"<guild_id>/<channel_id>"` shape) and `sender_ids` to a live guild
    member currently in a voice channel. On any other platform, a member
    not currently in ANY voice channel, or no live Discord connection
    right now, every method answers `unavailable` or `nothing_found` —
    never guesses a channel, and never silently no-ops.

    `sender_ids` is a SET, not one id, for the reason `nora.persons.
    Persons.platform_ids`'s own docstring gives: identity.toml may map one
    person to more than one account on a platform (found live 2026-08-24 —
    a real person with two Discord accounts), and choosing which one to
    trust is not the caller's decision to make blind. `nora.agent.outbound`
    resolves the same ambiguity by intersecting every candidate id against
    the platform's own live roster; this port's equivalent is trying each
    candidate against who the platform confirms is ACTUALLY in a voice
    channel right now, never an arbitrary pick from the set."""

    async def play(
        self, *, conversation_id: str, sender_ids: frozenset[str], query_or_url: str,
    ) -> MusicToolOutcome: ...

    async def skip(self, *, conversation_id: str) -> MusicToolOutcome: ...

    async def top(self, *, conversation_id: str, position: int) -> MusicToolOutcome: ...

    async def stop(self, *, conversation_id: str) -> MusicToolOutcome: ...

    async def status(self, *, conversation_id: str) -> MusicToolOutcome: ...

    async def set_volume(
        self, *, conversation_id: str, percent: int,
    ) -> MusicToolOutcome: ...


@dataclass(frozen=True)
class VoiceChannelRef:
    """A live voice-channel membership, as the `online_now` tool reports it
    (design/online-status/). `channel_id` is the platform's own snowflake —
    design-sanctioned for the reply (the 02 table) — and `channel_name` is
    what the model actually says; either may be all the platform exposes."""

    channel_id: str
    channel_name: str | None = None


@runtime_checkable
class VoicePresenceSource(Protocol):
    """Who is in a Discord voice channel RIGHT NOW, behind the `online_now`
    tool (design/online-status/) — a READ-ONLY seam so `nora.agent` never
    imports `discord.py` (same layered-architecture argument
    `MusicToolBridge`'s docstring gives). NEVER raises — `None` is "no live
    client / the read failed", and the tool reports `unavailable` (R-5),
    never "nobody is in voice". Keyed by Discord user id; an absent key is
    that member's `nothing_found` (not in any voice channel, or not a
    member of a guild the client can see).

    Live-guilds only, zero new intents: the implementation scans the
    connected client's guilds and reads `member.voice` — `voice_states` is
    already in `Intents.default()`. Voice membership answers ONLY the
    narrow "who is in which voice channel" question: since the 2026-09-15
    correction it no longer stands in for "Discord online" — that is
    `DiscordPresenceSource`'s read, behind the `presences` intent. A second
    implementation — including a test double — means a shared contract
    suite both pass (tests/contract/)."""

    async def in_voice(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, VoiceChannelRef] | None: ...


@dataclass(frozen=True)
class DiscordPresenceState:
    """One member's per-person Discord presence, as the `online_now` tool
    reports it (design/online-status/02's CORRECTION): Discord's own status
    word plus rich presence. `status` is whatever `member.status` gives
    (`online` / `idle` / `dnd` / `offline` — an invisible member presents
    to the bot as `offline`, so `offline` stays honest: checked-and-offline,
    never a guess). The rich fields are present-optional, never
    load-bearing."""

    status: str
    game: str | None = None
    listening: str | None = None
    custom_status: str | None = None


@runtime_checkable
class DiscordPresenceSource(Protocol):
    """Per-person Discord presence — status + rich presence — behind
    `online_now`'s `online` arm (design/online-status/, the 2026-09-15
    correction: presence, NOT voice-channel membership, is what "Discord
    online" means). A READ-ONLY seam so `nora.agent` never imports
    `discord.py` (same layered-architecture argument `VoicePresenceSource`'s
    docstring gives). NEVER raises — `None` is "no live client / the read
    failed", and the tool reports `unavailable` (R-5), never "everyone is
    offline". Keyed by Discord user id; an absent key is that member's
    `nothing_found` (not a member of a guild the client can see).

    Live-guilds only: the implementation scans the connected client's
    guilds and reads `member.status` / `member.activities`, which the
    `presences` privileged intent keeps populated (`adapter.py`). A second
    implementation — including a test double — means a shared contract
    suite both pass (tests/contract/)."""

    async def presence(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, DiscordPresenceState] | None: ...


@dataclass(frozen=True)
class ImageReference:
    """A reference image for editing/conditioning: a Nora-held custody handle.

    Deliberately NOT a URL. The value is the short transcript marker code
    (the `a3f2c1` part of `[image a3f2c1]`) — the same value `media_fetch`
    takes — which resolves, inside THIS conversation only, to a blob Nora
    already holds (R-21/DEC-24 custody). An external URL or raw bytes are
    refused by the port's implementations: Nora never fetches an image
    *because* the user asked to edit it; the user must have already sent it
    (design/proposed/29-image-generation §5.1).

    **One exemption (DEC-193, `design/self-reference/`).** The media row whose
    `provenance.role` is exactly `"self"` — her canonical picture, written only
    by the operator's `scripts/ingest_self_asset.py` — resolves in ANY
    conversation, matched on its FULL sha256 and only when the conversation's
    own window has no match. It is her own operator-provided asset, so nothing
    external enters and nothing leaves that she did not already hold.
    """

    marker_code: str


# Resolves a marker code to bytes — the custody check. The CALLER (the tool
# closure) supplies it per turn: it reads the caller's own conversation store,
# so a code from another conversation resolves to nothing — except the one
# canonical self row (DEC-193, `ImageReference` above). Domain keeps the
# alias so the port can type the per-call resolver.
ReferenceResolver = Callable[[str], Awaitable[bytes | None]]


@dataclass(frozen=True)
class ImageSize:
    """A resolution tier + aspect ratio, mapped per-provider.

    The model-facing tool names the size word and aspect; the adapter
    translates (xAI's API takes `aspect_ratio` + `resolution` 1k/2k
    directly; another provider may map to width/height).
    """

    tier: Literal["small", "medium", "large"] = "medium"
    aspect_ratio: Literal["1:1", "16:9", "9:16", "3:2", "2:3"] = "1:1"


@dataclass(frozen=True)
class ImageResult:
    """Outcome of an image-generation attempt — this file's R-27 three-state
    discipline: `ok` / `nothing_found` / `unavailable`, never conflated.

    `artifact` (a URL / drop slug / provider handle), `raw` (the generated
    bytes, when the provider returned them — lets the caller send the image
    as a platform attachment without a second fetch) and `provider` are only
    meaningful on `ok`. `reason` distinguishes the failure classes the model
    must answer differently — quota-exhausted vs auth-expired vs
    provider-down — mirroring `MusicToolOutcome.note`; the adapter never
    guesses "quota" or "auth" from a 403 status alone (design/proposed/29-image-generation §7).
    """

    status: Literal["ok", "nothing_found", "unavailable"]
    artifact: str | None = None
    provider: str = ""
    reason: str = ""
    raw: bytes | None = None
    media_type: str = ""


@runtime_checkable
class ImageGenerator(Protocol):
    """Provider boundary for generating images from text prompts.

    Provider-agnostic by construction: the port's vocabulary (prompt,
    reference images, size, quality) is deliberately NOT any vendor's —
    the first implementation is xAI's subscription lane, later providers
    plug in behind the same seam and the model-facing tool does not change
    (design/proposed/29, DEC-137). NEVER raises — matching `TrackSource`
    / `WeatherProvider`'s discipline in this file: every failure is a
    three-state `ImageResult`, and a wiring gap is `unavailable`, never a
    raise and never `nothing_found`.

    Reference images are custody handles only (`ImageReference.marker_code`
    resolved inside the caller's own conversation) — the adapter refuses an
    external URL (design/proposed/29-image-generation §5.1).
    """

    async def generate(
        self,
        *,
        prompt: str,
        reference_images: Sequence[ImageReference] | None = None,
        size: ImageSize | None = None,
        quality: Literal["standard", "quality"] | None = None,
        reference_resolver: ReferenceResolver | None = None,
    ) -> ImageResult: ...

    async def health(self) -> bool: ...


@runtime_checkable
class CultureReader(Protocol):
    """The ONE verb a PROMPT is allowed on the culture store (design/culture-tier).

    A Protocol rather than `SqlCultureStore` itself: the concrete store also
    exposes `append` and `chain`, and naming only what is called makes «no turn
    or pass other than the fold ever writes a culture row» a fact a type checker
    enforces rather than a habit a later edit could quietly break. The tier's
    whole claim is that the account is written by a background fold reading a
    whole batch, never by a turn reacting to one window.

    **It lives here because every runtime reads it** (DEC-207, 2026-09-25). It
    used to live in `nora.app.fire`, beside its one consumer, on the argument
    that a port declared for everyone reads as an invitation for the second.
    The operator issued that invitation: the tag path, every fire occasion,
    proactive, both note passes and the standing debrief now read the room's
    account, so the one-consumer location stopped being true of the code.

    **`key` is a `primary_id`, NOT a `conversation_id`** (DEC-181). A Dreamer
    adaptor is handed `scopes()`, so that is what it wrote under, and every
    caller holding a thread id resolves it through the policy first — see
    `nora.language.culture_render.culture_key`. The parameter is named `key`
    rather than `conversation_id` so that a call site passing the wrong one
    has to say so.

    **Never raises for an absent row.** `None` is a first-class state: a room
    nobody has folded yet has no account, and that is the ORDINARY state for a
    while after the tier is enabled, because a fold needs new messages and a
    day. Implementations still raise on a genuine store failure — the caller
    decides what an unreachable store means, and for a prompt that is a thinner
    prompt plus a counter, never a silent «no account».
    """

    async def head(self, *, key: str) -> CultureRevision | None:
        """This scope's current account, or `None` if it has none yet."""
        ...


@runtime_checkable
class StandingStore(Protocol):
    """Where the debrief's per-conversation position is read and revised
    (`design/dreamer-agent-loop/26-standing.md`).

    **Two verbs, and the READ is the non-negotiable one.** `write` without a
    preceding `read` reproduces the language tier's exact failure — two
    contradictory positions coexisting, neither able to correct the other
    (`12-self-correction.md` §3, and the live evidence: across 963 processed
    messages the Dreamer's whole output was two rows, both wrong, both glossing
    the same thing, because nothing in its write path could see what was
    already written). That is why `read` is on this port at all rather than the
    fold being handed a blob: the fold's second argument comes from HERE, on
    every fold, and `nora_debrief_prior_total` is what makes «it read nothing»
    visible rather than assumed.

    **No delete, and unlike `DreamSink` no supersede either.** The row is
    REVISED, in place — that is the tier's shape (26 §4), not an economy. What
    a lossy revision is rebuilt from is `tag_trace` (`TagTraceStore.recent`),
    retained for exactly this reason, so history lives one tier down rather
    than in a column here.

    **Never raises for an absent row.** `read` answers `None`, which is a
    first-class state and not an error: a conversation Nora has not folded yet
    has no position, and the fold is told so explicitly (26 §4.2 — `prior` is
    "always present, even when empty"). Implementations still raise on a
    genuine store failure; the CALLER decides what an unreachable store means,
    and for the debrief that is `unavailable`, never a fold against a guessed
    empty prior.
    """

    async def read(
        self, *, platform: str, conversation_id: str
    ) -> Standing | None:
        """This conversation's current position, or `None` if it has none."""
        ...

    async def write(self, *, standing: Standing) -> None:
        """Revise the row in place, keyed `(platform, conversation_id)`.

        An upsert rather than an insert-or-update pair: two tags for one
        conversation cannot run concurrently (R-10's per-conversation lock), but
        a tag and the fire path CAN both reach a conversation, and a
        read-then-insert would race into a duplicate-key error on exactly the
        turn that already went unusually slowly.
        """
        ...


@runtime_checkable
class DecisionLogStore(Protocol):
    """Where every ambient decision is written, and where the NEXT decider
    reads what the others already did (issue #199).

    **Append-only, and the absent verbs are the point.** There is no `update`,
    no `delete` and no upsert: a decision happened, and a log whose rows can be
    rewritten is not a record of what happened — it is a record of what somebody
    later wanted to have happened. The same move `DreamSink` makes by having no
    delete. `id` is the primary
    key, so a duplicate is a caller reusing an id rather than a second decision,
    and Postgres says so.

    **Two reads, and `recent_for_unit` is the PRIMARY one.** A
    `kind="proactive"` row fires into the unit's home room and nowhere
    else (39 D2/D3); the thread it is about runs wherever the group is
    talking, which in the measured incident was a different room on a different
    platform. So a reader scoped to one room sees nothing and the duplicate it
    exists to prevent goes through anyway. `recent` — one room — is the narrow
    read, kept for a caller that genuinely means one room. They are two reads
    and not one filter because they need two indexes: collapsing them would
    make the unit case an unindexed scan, and AGENTS.md's rule is that
    something must measure the COST, not just the answer.

    **Both reads are bounded by the store, twice** — `since_ms` and `limit` —
    rather than by the caller slicing what came back. A block that rides into a
    cheap gate call on every quiet window cannot be allowed to grow with a busy
    room's history, and a bound applied after the rows arrive is a bound on the
    string, not on the query.

    **`limit` takes the NEWEST rows and the result is oldest-first.** Those are
    two separate statements and a store that gets the first one wrong looks
    correct: it answers a busy room's oldest 20 decisions, rendered under a
    «last 24 hours» note, with nothing saying the block is about yesterday.
    """

    async def append(self, *, row: DecisionRow) -> None:
        """Write one decision. Raises if the `id` already exists.

        **A row is written at turn COMPLETION** — after the message went out,
        or after the decision to stay silent was taken — never when the turn
        starts. That is what closes issue #199's ordering: the send at 16:43:57
        is durable before the tag turn at 16:44:04 reads anything, so the
        second turn can see the first one's answer. The gate's verdict is the
        one row with no turn behind it and is written when the verdict is
        reached, which is its completion.

        **It does NOT close a mid-turn race, and nothing here should be read as
        claiming otherwise.** A tag that lands while a participation turn is
        still running reads a log that does not yet contain it, and both turns
        answer. Serialising that is `ConversationSlots`' job — one turn per
        room at a time — and this log is a record, not a lock. What it removes
        is the SEQUENTIAL duplicate; the concurrent one is out of its reach by
        construction.

        Callers reach this through `nora.domain.decision_log.
        DecisionLogRecorder`, never directly: the recorder is where the bound
        and the fail-soft rule live, and a log write must never fail a turn.
        """
        ...

    async def recent(
        self, *, conversation_id: str, since_ms: int, limit: int = ...,
    ) -> Sequence[DecisionRow]:
        """This room's decisions at or after `since_ms`, the NEWEST `limit` of
        them, returned OLDEST-FIRST.

        Oldest-first to match `RecordStore.recent`, which the same prompt
        renders beside it — one document, one direction of time. Ties on `ts`
        break on `id` so two implementations answer the same rows in the same
        order; two decisions in one millisecond are reachable, and issue #199's
        own incident is a tag turn one second behind a participation answer.
        """
        ...

    async def recent_for_unit(
        self, *, unit: str, since_ms: int,
        writers: Sequence[str] = ..., limit: int = ...,
    ) -> Sequence[DecisionRow]:
        """The same read across every room of one unit, narrowed to
        `writers`. Same bounds, same order, same tie-break.

        **`writers` is on the QUERY and not a filter the caller applies after,
        and that distinction decides whether the feature works.** Every reader
        excludes its own writer; the bound is 20 rows; and `gate` writes one row
        per closed participation window per room, which out-produces every other
        writer by an order of magnitude. Filtered afterwards, half an hour of
        gate rows fills the bound and the caller is handed nothing — silently,
        because an empty result renders as an empty block. The bound must count
        rows the caller can USE. Empty means every writer.
        """
        ...
