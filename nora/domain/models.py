"""Platform-neutral data contracts (spec C.2). Every downstream module speaks these."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .media import MediaBlob, MediaRef


@dataclass(frozen=True)
class ConversationTarget:
    """Where a reply goes. Hashable so it can key per-conversation locks and debounce."""

    platform: str
    conversation_id: str
    conversation_type: str


@dataclass(frozen=True)
class Quote:
    """The message this one replies to — a POINTER, plus the quoting sender's
    unverified copy of what it said.

    `quoted_ts` is `dataMessage.quote.id`, the parent's platform sent-timestamp,
    and it is the only field of this type anything durable is built on: it is
    what `messages.quoted_ts` stores and what resolves to a parent row.

    `text` is carried for the ingest policies that need it and is **never
    rendered to the model** — R-20. It comes from the QUOTING sender's envelope
    and is verified against nothing, so Alice can construct a quote asserting
    Bob said anything at all. `nora.agent.prefill` shows the resolved parent
    row's body instead, or nothing.

    There is deliberately **no `author_name`**. signal-cli's shipped
    `quote.schema.json` carries `id` (required), `author`, `authorNumber`,
    `authorUuid`, `text`, `attachments`, `mentions` and `textStyles` — and no
    `authorName` at all. The old `q.get("authorName") or ""` therefore yielded
    `""` on every real message and rendered `<replying_to author="">`, i.e.
    "replying to nobody", for the whole life of that feature. A field that is
    provably always empty is exactly how the `peer_card` bug happened.
    Attribution comes from the resolved parent row, whose `sender_id` /
    `person_id` Nora recorded herself.

    `author_id` is kept because it is what `ReplyToNoraPolicy` matches against
    her own identifier set to decide that a reply IS a tag — an id, never a
    display name (design/10 §1.3).
    """

    author_id: str
    text: str
    quoted_ts: int


@dataclass(frozen=True)
class NormalizedMessage:
    platform: str                  # "signal"
    conversation_id: str           # base64 group id for Signal — platform-neutral key
    conversation_type: str         # "group" | "dm"
    sender_id: str                 # platform sender id (E.164 for Signal)
    sender_name: str | None        # platform-provided display name
    timestamp: int                 # ms epoch, platform-provided
    body: str                      # text + attachment placeholders
    raw: dict[str, Any]            # full envelope, persisted as raw_json
    # Ids the sender @-mentioned, in whatever forms the platform supplied.
    # Structural, NOT text: Signal replaces a mention in `body` with U+FFFC
    # (OBJECT REPLACEMENT CHARACTER) and carries who was mentioned in a
    # parallel array, so a mention is invisible to any substring match.
    # Defaulted so every existing positional construction stays valid.
    mentions: frozenset[str] = frozenset()
    # The message this one replies to. Signal carries a reply structurally, not
    # in the body, so without this a reply reads as a non-sequitur — "your
    # place" with no way to know whose. See `Quote` for why it is a pointer and
    # why the text it carries is never shown to the model.
    quote: Quote | None = None
    # What the PLATFORM said about each attachment, in body order. Structural,
    # like `mentions`: the body carries `[image]`, and which attachment that
    # placeholder stands for lives here. Produced by the parser.
    media: tuple[MediaRef, ...] = ()
    # What Nora actually HOLDS for each attachment she managed to copy —
    # produced by `nora.ingest.stages.media`, never by the parser, and empty on
    # every message that carried none or whose bytes could not be fetched.
    #
    # It rides the message rather than being a parameter on `RecordStore.append`
    # for exactly the reason `quote.quoted_ts` does (DEC-23, and DEC-15 before
    # it): WAL permits one writer, the ingest path drops a message on
    # contention, and a second write to attach custody to an already-inserted
    # row would be a second contender for it. Same INSERT, no extra parameter.
    media_blobs: tuple[MediaBlob, ...] = ()

    def target(self) -> ConversationTarget:
        return ConversationTarget(self.platform, self.conversation_id, self.conversation_type)


@dataclass(frozen=True)
class PersonRef:
    person_id: str
    name: str
    honcho_peer_id: str            # == person_id, by construction (spec D.2)
    first_name: str = ""
    last_name: str = ""
    # Other names the GROUP calls this person — 阿Miles, 米高, a handle. Shown to
    # Nora so she understands who a message is about; NEVER used to resolve who
    # a message is FROM. That distinction is the whole safety story: a sender is
    # authenticated by platform id only (design/10 §1.3), because a display name
    # is spoofable, whereas these are operator-authored and so trustworthy for
    # comprehension.
    aliases: tuple[str, ...] = ()


class DeliveryStatus(Enum):
    """A transcript row's send state. Mirrors `nora.domain.errors.FailureClass`'s
    reasoning: this was stringly-typed over `"pending"`/`"sent"`/`"failed"`
    (the retired `nora/store/db.py::_VALID_STATUS`) in the same phase that typed
    the adjacent failure taxonomy as an enum. `nora/infra/sql/audit.py::SqlRecordStore`
    is the persistence phase's adapter onto this type: it stores `.value` and
    round-trips through `DeliveryStatus(...)`, which raises `ValueError` on an
    unrecognized string on the way back out.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class TranscriptRow:
    """One row as returned by `RecordStore.recent`. Replaces the untyped
    `dict[str, Any]` rows the retired `nora/store/db.py::recent` used to
    return; `nora.infra.sql.audit.SqlRecordStore` is what produces this shape
    at runtime now.
    """

    id: int
    sender_id: str
    sender_name: str | None
    ts: int
    body: str | None
    is_nora: bool
    delivery_status: DeliveryStatus
    raw_json: str | None
    # The `ts` of the row this one replies to, or None. ONE column, not two:
    # the parent row's own `sender_id`/`person_id` is authoritative, whereas
    # `Quote.author_id` is supplied by whoever built the quote — storing both
    # would be two sources of truth, and the untrustworthy one is the one that
    # would get rendered. Store the pointer, derive the attribution.
    #
    # Defaulted so every existing positional construction stays valid.
    quoted_ts: int | None = None
    # The raw `messages.media_json` text — a JSON array of the `MediaBlob`s
    # Nora holds for this row, or None when she holds none. Returned as stored,
    # not as objects: nothing renders it today, and a decoded shape nobody
    # consumes would be an invented contract. `nora.domain.media` is what a
    # retrieval tool would decode it through when one exists.
    media_json: str | None = None


@dataclass(frozen=True)
class UnattributedRow:
    """One audited message that carries no `person_id` — its sender resolved to
    nobody at the moment of ingest (D6).

    Returned by `RecordStore.unattributed`, which is what the startup
    lazy-adoption backfill (`nora.app.adoption`) re-resolves against a freshly
    loaded `identity.toml`. It carries `platform` — which `TranscriptRow`
    deliberately does not, being scoped to one conversation already — because
    person resolution is keyed `(platform, sender_id)`, so a row without its
    platform cannot be re-resolved at all.

    `body` is `str | None` for the same reason the column is nullable: an
    attachment-only message audits with no text. There is nothing to feed a
    peer from such a row, only a `person_id` to record.

    `raw_json` is the FULL stored envelope, and it is here for the operator
    control plane (`nora persons pending`), not for adoption — which ignores
    it. It has to be here rather than on a second port method because of what
    `sender_id` alone cannot answer: under Signal's sealed sender the same
    account arrives as a phone number on one message and as an ACI uuid on
    another (`nora.platform.signal.parser` reads `source or sourceNumber or
    sourceUuid`), so a column of `sender_id`s lists ONE account as TWO pending
    entries — and pasting those into two `[[persons.x.signal]]` blocks is
    precisely the copy-paste duplicate `nora.persons.load_persons` now refuses
    to start on. The envelope carries every id form that arrived together, so
    it is what lets them be correlated back into one account.

    The cost is one extra column on a scan `nora.app.adoption` runs at every
    start; it is bounded by that scan's own `limit` (1000 rows) and by an
    audit table already bounded by retention, and a Signal envelope is a
    couple of kilobytes. A second port method returning the same rows plus
    one column would have been the alternative, and it would oblige every
    future `RecordStore` to implement two near-identical scans.
    """

    id: int
    platform: str
    sender_id: str
    ts: int
    body: str | None
    # The conversation the message was in — needed so lazy-adoption feeds it into
    # the right conversation session (Honcho v2, design/24), the same way the
    # ambient feed does. Like `platform`, it is carried because a re-fed row must
    # know its session, which `TranscriptRow` (already conversation-scoped) omits.
    # REQUIRED, not defaulted (council 2026-08-07): a `""` would build a malformed
    # `nora-conv-` session, so an omission is a construction error, not a silent
    # garbage feed.
    conversation_id: str
    raw_json: str | None = None


@dataclass(frozen=True)
class TagContext:
    """Everything the tagging path assembles before the model is constructed."""

    tag_id: str                    # uuid4 hex — the correlation id
    message: NormalizedMessage
    tagger: PersonRef | None       # None = unmapped sender
    transcript: tuple[TranscriptRow, ...]   # oldest-first rows from db.recent
    peer_card: tuple[str, ...]
    started_at: float              # perf_counter, for the latency histogram
