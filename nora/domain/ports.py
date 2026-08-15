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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from nora.domain.identity import PlatformIdentity as PlatformIdentity
from nora.domain.media import GifCandidate as GifCandidate
from nora.domain.media import MediaBlob as MediaBlob
from nora.domain.media import MediaBytes as MediaBytes
from nora.domain.media import MediaRef as MediaRef
from nora.domain.models import (
    ConversationTarget,
    DeliveryStatus,
    NormalizedMessage,
    TagContext,
    TranscriptRow,
    UnattributedRow,
    WeatherObservation,
)
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
    """

    start: int
    length: int
    recipient: str


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
    platform: str
    connected: bool
    last_event_age_seconds: float | None = None


@dataclass(frozen=True)
class MemoryHealth:
    """Mirrors `AdapterHealth` — typed, not a bare dict. `MemoryStore.health()`
    used to be the one health check left dict-shaped while `PlatformAdapter.health()`
    was already typed; see module docstring's `MemoryStore` note."""

    reachable: bool


@dataclass(frozen=True)
class EngageDecision:
    """See module docstring for why `rationale` and `threshold` ride along
    with `confidence` instead of a bare bool or a bare number."""

    engage: bool
    reason: str
    rationale: str | None = None
    confidence: float | None = None
    threshold: float | None = None
    allow_episode: bool = False


@dataclass(frozen=True)
class EngagementRequest:
    message: NormalizedMessage
    decision: EngageDecision
    tag_id: str


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

    async def set_delivery_outcome(
        self, *, row_id: int, status: DeliveryStatus, ts: int | None = None
    ) -> None: ...

    async def unattributed(self, *, limit: int) -> list[UnattributedRow]: ...

    async def set_person_id(self, *, row_id: int, person_id: str) -> None: ...

    async def stats(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class MemoryStore(Protocol):
    """`ensure_*` methods are idempotent under concurrency — see module docstring."""

    async def ensure_workspace(self) -> None: ...

    async def ensure_peer(self, *, peer_id: str) -> None: ...

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

    async def peer_card(self, *, peer_id: str) -> list[str]: ...

    # `peer_id` is the OBSERVER, `target` the OBSERVED (design/24 §4). With
    # `target` set the recall is `(observer=peer_id, observed=target)` — Nora's
    # model of a person, not the person's self-model. Workspace-wide (no session
    # scope): knowledge blends across every conversation Nora shares with them;
    # the room roster gates disclosure at the tool layer.
    async def representation(
        self,
        *,
        peer_id: str,
        search_query: str,
        max_conclusions: int = 5,
        target: str | None = None,
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
    ) -> str | None: ...

    # Semantic search over the raw MESSAGES (not conclusions) — the verb behind
    # `search_memory`. Carries `reachable` beside the hits (see `MessageHits`):
    # a bare list made a Honcho outage indistinguishable from a genuine miss,
    # and `search_memory` reported the outage as «nothing was said», counted ok.
    # The caller still filters to the room roster and screens identifiers.
    async def search(
        self, *, query: str, conversation_id: str | None = None, limit: int = 8,
        workspace_id: str | None = None,
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
    counter that says interpretation is failing sits at zero."""

    available: bool
    text: str = ""
    reason: str = ""


@runtime_checkable
class MediaInterpreter(Protocol):
    """Bytes to a plain-text description. Never raises.

    Called from the ingest path for STICKERS ONLY (sticker spec §2.2, §5.3).
    Images, audio and video are not automatically interpreted: a sticker is
    published pack art, identical for everyone who has the pack, and a photo is
    a photograph of somebody's life. Reading the second one permanently into
    the durable record, the Honcho feed and every future transcript is a
    materially different act, and `media_fetch` already exists for the case
    where Nora has a reason to look.
    """

    async def describe(self, *, data: bytes, media_type: str) -> MediaDescription: ...


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
class SearchProvider(Protocol):
    """`query -> {status, results}` web search behind the one `web_search` tool
    (DEC-73). NEVER raises — a transport/auth failure is `{"status": "unavailable"}`,
    never an exception into the loop (R-5). `TavilySearchProvider` today; a PORT
    (operator: "same as web_search — adaptor") so another search backend plugs in
    with no tool change. Returns ranked results, each `{url, content}` with the
    content `untrusted`-wrapped and clamped; a second implementation means a shared
    contract suite both pass (tests/contract/). There is no `fetch` verb and no
    SSRF surface — the provider searches from ITS servers and is never handed a
    model-chosen URL, so the old `is_global`/`allowed_urls` apparatus is gone."""

    async def search(self, *, query: str) -> dict[str, Any]: ...


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
    `SearchProvider` (DEC-73 (2): Tavily searches from ITS servers and is never
    handed a URL) this port IS handed a URL that originated in model output, and
    fetches it from Nora's own network — the exact exposure the retired
    `web_fetch` had. `OpenGraphPreviewSource` documents the guard it applies;
    a second implementation that does not apply one reopens DEC-73's hole.
    """

    async def preview_for(self, *, url: str) -> LinkPreview | None: ...


@runtime_checkable
class EngagePolicy(Protocol):
    """Async now, ahead of need — see module docstring."""

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision: ...


@runtime_checkable
class ContextAssembler(Protocol):
    async def assemble(self, *, request: EngagementRequest) -> TagContext: ...
