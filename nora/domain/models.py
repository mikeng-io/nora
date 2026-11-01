"""Platform-neutral data contracts. Every downstream module speaks these."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from .computer_context import ComputerView
from .culture import CultureRevision
from .decision_log import DecisionRow
from .documents_context import DocumentsView
from .external_files import ExternalFileRef
from .lineage import RenderRoom
from .media import MediaBlob, MediaRef
from .memo import Memo
from .notes import Note
from .profile import ProfileView
from .self_presence import Presence
from .standing import Standing
from .standing_ledger import LedgerEntry
from .subagents_context import SubagentsView


@dataclass(frozen=True)
class ConversationTarget:
    """Where a reply goes. Hashable so it can key per-conversation locks and debounce."""

    platform: str
    conversation_id: str
    conversation_type: str
    # DID THIS TURN ARRIVE BY VOICE. Voice is a SURFACE of a room, not a room
    # of its own: a guild's spoken utterances land in that guild's own text room
    # (`Policy.voice_room`), so `conversation_id` alone cannot say whether the
    # reply must be SPOKEN or POSTED. Defaulted False (the text posture).
    #
    # Not read off `NormalizedMessage.raw`: `raw` is the platform envelope,
    # persisted verbatim as `raw_json`, and a routing decision that changes
    # whether a reply is audible must not hang on an untyped dict lookup.
    voice: bool = False
    # The THREAD inside `conversation_id`'s room this turn belongs to, as the
    # platform's own channel id for it (a Discord thread or forum post —
    # `""` on every other platform and outside one). A thread is never a
    # room: `conversation_id` keeps naming the PARENT channel, so the room's
    # transcript holds the thread's messages in place — the same thing the
    # ledger's `thread` kind already means, a span inside one room — and the
    # tag is what routes the reply back into it.
    thread: str = ""


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

    There is deliberately **no `author_name`**: signal-cli's shipped
    `quote.schema.json` carries `id` (required), `author`, `authorNumber`,
    `authorUuid`, `text`, `attachments`, `mentions` and `textStyles` — and no
    `authorName` at all, so such a field would be provably always empty and
    render `<replying_to author="">`, i.e. "replying to nobody". Attribution
    comes from the resolved parent row, whose `sender_id` / `person_id` Nora
    recorded herself.

    `author_id` is kept because it is what `ReplyToNoraPolicy` matches against
    her own identifier set to decide that a reply IS a tag — an id, never a
    display name.
    """

    author_id: str
    text: str
    quoted_ts: int


@dataclass(frozen=True)
class EditTarget:
    """Which recorded message an EDIT changes.

    Two schemes because the platforms have two: WhatsApp, Instagram and
    Discord name the edited message by their own handle (`message_id`, stored
    as `messages.platform_message_id`); Signal names it by its author and its
    original sent-timestamp (`sent_ts`, signal-cli's `targetSentTimestamp`),
    the same pair it addresses a quote or a reaction by. Exactly one is set.

    `mentions_known` is False when the platform's edit event cannot carry
    mentions at all (Instagram's edit delta is a message id, a text and a
    time). Ingest then carries the original's forward rather than recording
    «mentioned nobody», which would be a claim the platform never made.
    """

    message_id: str = ""
    sent_ts: int | None = None
    mentions_known: bool = True


#: What a `Touch` does to the message it names. Closed on purpose: every
#: parser produces one of these two, and ingest records each in its own table
#: (`message_reaction`, `message_delete`).
TouchKind = Literal["reaction", "delete"]


@dataclass(frozen=True)
class Touch:
    """A reaction to, or a delete or unsend of, a message Nora recorded
    .

    Neither is a message. A reaction is metadata on somebody else's row, and
    rendering a bare emoji as its own transcript line would add a turn that
    never happened; a delete withdraws a row, it says nothing. So, like an
    edit, each is recorded BESIDE the message it touches (`message_reaction`,
    `message_delete`) and never as a `messages` row. They are Record items
    because the cited-message cues and the tie need them; nothing reads them to
    answer.

    `target` names the message the way the platform does, exactly as an
    edit's does: a handle (`message_id`) on WhatsApp, Instagram and Discord,
    the author's sent-timestamp (`sent_ts`) on Signal. `target_author` is the
    author the platform says the target has ("" where it does not say), used
    only to PREFER that author when two rows share a Signal timestamp.

    `emoji` is set for a reaction; `removed` marks a reaction being taken
    back, which is recorded as its own row (append-only), never as a delete of
    the earlier one.
    """

    kind: TouchKind
    target: EditTarget
    target_author: str = ""
    emoji: str = ""
    removed: bool = False


#: What Signal puts in a message body where an @-mention's name appears
#: (U+FFFC, OBJECT REPLACEMENT CHARACTER): the body's only trace of a
#: mention, the identity being in `NormalizedMessage.mentions`. Written as an
#: escape because the literal renders as a box or as nothing.
MENTION_PLACEHOLDER = "\ufffc"


def strip_mention_placeholders(text: str) -> str:
    """`text` without its mention placeholders and the whitespace they leave
    at either end: what a message SAYS, for a place that quotes it back
    (the Honcho search feed, a computer session's objective)."""
    return text.replace(MENTION_PLACEHOLDER, "").strip()


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
    # The PLATFORM'S OWN HANDLE for this message, when it has one, so a later
    # call can point back at it. Discord identifies a message by a snowflake
    # and its reaction endpoint needs exactly that; Signal has no such handle
    # and addresses a message by (author, timestamp) instead, so its parser
    # leaves this empty and nothing about Signal changes.
    #
    # Defaulted and additive rather than replacing `(sender_id, timestamp)`:
    # the two schemes are both real, and collapsing them would either invent an
    # id Signal does not have or throw away the one Discord needs. `react` on
    # the port takes both for the same reason.
    message_id: str = ""
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
    # The shared file links in `body` a provider recognised: produced by
    # `nora.ingest.stages.external_files`, never by the parser, and written to
    # `messages.external_files_json` in the same INSERT for `media_blobs`'
    # reason. No network call made them; the body keeps the URL verbatim.
    external_files: tuple[ExternalFileRef, ...] = ()
    # SPOKEN, not typed.
    # Set only by `nora.web.voice`, the wake path's entry point; every parser
    # leaves it False. It rides the message so the reply can be routed back
    # out the way it came in — see `ConversationTarget.voice`.
    voice: bool = False
    # Set when this message is an EDIT of an earlier one; everything else on
    # the message then describes the NEW version — `body`, `mentions`,
    # `media`, `raw`, and `timestamp` (when it was edited). `sender_id` is who
    # the platform says edited it, and is `""` where the platform does not say
    # (Instagram's edit delta carries no sender). Ingest records it beside the
    # message it edits rather than as a message of its own. Every parser leaves
    # it None otherwise.
    edit_of: EditTarget | None = None
    # Set when this "message" is a REACTION to, or a DELETE/UNSEND of, a
    # recorded message (`Touch`). Its `body` is then "" and it is never a
    # `messages` row: ingest records it beside the message it touches. Every
    # parser leaves it None for an ordinary message or an edit.
    touch: Touch | None = None
    # The platform channel id of the THREAD this was said in, or `""`. The
    # room is still `conversation_id` (the parent channel); the tag lets a
    # reply land back inside the same thread. See `ConversationTarget.thread`.
    thread: str = ""

    def target(self) -> ConversationTarget:
        return ConversationTarget(
            self.platform, self.conversation_id, self.conversation_type,
            voice=self.voice, thread=self.thread,
        )


@dataclass(frozen=True)
class PersonRef:
    person_id: str
    name: str
    honcho_peer_id: str            # == person_id, by construction
    first_name: str = ""
    last_name: str = ""
    # Other names the GROUP calls this person — 阿Miles, 米高, a handle. Shown to
    # Nora so she understands who a message is about; NEVER used to resolve who
    # a message is FROM. That distinction is the whole safety story: a sender is
    # authenticated by platform id only, because a display name
    # is spoofable, whereas these are operator-authored and so trustworthy for
    # comprehension.
    aliases: tuple[str, ...] = ()


class DeliveryStatus(Enum):
    """A transcript row's send state, an enum like `nora.domain.errors.FailureClass`.
    `nora/infra/sql/audit.py::SqlRecordStore` is the persistence adapter onto
    this type: it stores `.value` and round-trips through `DeliveryStatus(...)`,
    which raises `ValueError` on an unrecognized string on the way back out.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    # The send may have landed and its answer is lost — terminal (a pending
    # row would keep the owner valid and invite a blind resend), never
    # folded into `failed`: an unknown outcome is not a proven non-send.
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class TranscriptRow:
    """One row as returned by `RecordStore.recent`;
    `nora.infra.sql.audit.SqlRecordStore` produces this shape at runtime.
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
    # The raw `messages.external_files_json` text: the file links recognised
    # in this row at ingest, or None. Decoded only by
    # `nora.domain.external_files` (the code lookup); renderers never read it,
    # because the in-place handle is rendered from the body itself.
    external_files_json: str | None = None
    # The IDENTITY-canonical name for this row's sender (`PersonRef.name`), or
    # None when the sender maps to nobody. Set by the context assembler, which
    # is the one place that holds `Persons`; the store never fills it.
    #
    # It sits BESIDE `sender_name` rather than replacing it (DEC-80): the two
    # are different facts with different trust. `sender_name` is the SELF-SET
    # Signal profile name — group-authored, spoofable, and the thing R-13 escapes
    # on the way into the prefill. `person_name` is operator-authored, so it is
    # the one the transcript renders and the one every OTHER surface (the
    # `tag_message from=`, `group_members`, `lookup_person`) already used. Keeping
    # both is what makes the projection auditable instead of a silent rewrite.
    person_name: str | None = None
    # The RESOLVED person, or None when the sender mapped to nobody at ingest.
    # `sender_id` above is the raw platform id; this is the roster's canonical
    # id, and it is what anything joining across stores keys on.
    # Defaulted so existing positional construction stays valid.
    person_id: str | None = None
    # The newest EDIT of this message, or None when it was never edited
    # When set, `body` above is the LATEST text
    # and the row keeps its original `id`, `ts` and `raw_json` — the position
    # it was said at, and the envelope a reply or a reaction reads the
    # platform handle from. What it first said and the latest envelope ride
    # here.
    edit: RowEdit | None = None
    # The reactions recorded against this row, folded to now by the ONE fold
    # (`fold_reactions`, the definition `record_query` established). None
    # means the read carried none — nothing recorded, or a read that does
    # not populate it — and renderers show NOTHING either way: «reaction
    # state unknown» and «no reactions» are never collapsed into a zero.
    reactions: RowReactions | None = None
    turn_id: int | None = None

    @property
    def turn_handle(self) -> str | None:
        return f"t{self.turn_id}" if self.is_nora and self.turn_id is not None else None


@dataclass(frozen=True)
class RowEdit:
    """What a transcript row needs to say it was edited.

    `ts` is when it was LAST edited (the platform's clock). `was` is what it
    FIRST said — `messages.body`, not the previous edit: a reply between two
    versions answered some earlier one, and the first is the one the room most
    likely read. `raw_json` is the latest edit's own envelope, which is the one
    the latest body's @-mention spans index into
    (`nora.engagement.mentions`).
    """

    ts: int
    was: str | None
    raw_json: str | None = None


@dataclass(frozen=True)
class ReactionRow:
    """One `message_reaction` row as the fold reads it.

    `sender_id` is the actor the platform named — the fold's per-actor key is
    this id, exactly as `SqlRecordQuery`'s distinct-on grouped it, so one
    person reacting from two accounts counts twice (the same definition
    `record_query` has always returned). `ts` is the platform's time for the
    event (arrival when it gives none); `id` is arrival order, the tie-break
    the append-only table resolves latest by.
    """

    id: int
    message_id: int
    sender_id: str
    emoji: str
    removed: bool
    ts: int


#: A platform's custom-emoji wire identity — Discord stores a reaction as
#: `str(payload.emoji)`, `<:name:id>` or `<a:name:id>` (animated). The
#: `:name:` is the scoped handle a model may see; the id is a platform
#: identifier and stays off every prompt, tool result and annotation (R-18).
_CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_]+):[0-9]+>")

#: The longest stored emoji an annotation passes through as-is — a family
#: glyph runs to about 11 codepoints; longer is payload, not emoji, and
#: collapses to a bounded placeholder rather than a raw dump or a string cut
#: that could render a DIFFERENT glyph.
_EMOJI_DISPLAY_MAX = 24

#: The most standing (emoji, n) pairs a tool result lists per message; the
#: rest reads as `reactions_more`.
REACTION_PAIRS_MAX = 16

#: The label's character ceiling on a surface that does not budget the
#: annotation itself (the tag transcript); a budgeted render passes its own
#: cap to `RowReactions.bounded_label`.
REACTION_LABEL_MAX = 240


def emoji_display(emoji: str) -> str:
    """A stored emoji's model-facing form.

    The Record keeps the emoji's RAW identity — the fold's per-actor key
    depends on it — and every model surface projects it through here: a
    custom-emoji wire form (`<:name:id>`, `<a:name:id>`) becomes its scoped
    `:name:`, never the platform id inside; an ordinary emoji or short string
    passes through; anything longer or carrying markup brackets collapses to
    `[emoji]`, a bounded placeholder, rather than leaking the raw form.
    """
    custom = _CUSTOM_EMOJI.fullmatch(emoji)
    if custom is not None:
        return f":{custom.group(1)[:32]}:"
    if len(emoji) <= _EMOJI_DISPLAY_MAX and "<" not in emoji and ">" not in emoji:
        return emoji
    return "[emoji]"


def display_reactions(
    counts: Iterable[tuple[str, int]],
) -> dict[str, int]:
    """Standing counts as the model reads them: each stored emoji projected
    to its bounded handle (`emoji_display`), identical handles merged — two
    custom emoji one handle cannot tell apart are one count, never two
    indistinguishable entries."""
    out: dict[str, int] = {}
    for emoji, n in counts:
        shown = emoji_display(emoji)
        out[shown] = out.get(shown, 0) + n
    return out


@dataclass(frozen=True)
class RowReactions:
    """The reactions recorded against a message, folded to its current state.

    `counts` is `(emoji, how many actors)` standing now: each actor's NEWEST
    recorded row per emoji, counted unless it took the reaction back. Ordered
    by emoji so every reader renders the same line. `observed_at` is the
    newest SOURCE-EVENT `ts` among the rows the fold read — a removal is an
    event too — never the read's as-of time. `recorded` is how many
    transition rows the fold read.

    **Coverage is recorded transitions only**, which is what `coverage` says.
    The Record holds what the transports delivered: an upstream clear, a
    snapshot, or any reaction the capture path never saw is absent, so a
    count is what was recorded, not a claim of the whole upstream set. An
    empty `counts` with `recorded > 0` means every recorded reaction was
    taken back — a fact, not a zero. `coverage="partial"` means the read's
    own bound cut the standing set short: `recorded` and `observed_at` still
    hold, and `counts` is EMPTY on purpose — a truncated set is never folded
    into an apparently complete count.
    """

    counts: tuple[tuple[str, int], ...] = ()
    observed_at: int | None = None
    recorded: int = 0
    coverage: str = "recorded"

    @property
    def display_counts(self) -> tuple[tuple[str, int], ...]:
        """`counts` as model-facing pairs (`display_reactions`)."""
        return tuple(display_reactions(self.counts).items())

    def bounded_label(self, cap: int) -> str | None:
        """The annotation text with at most `cap` characters of emoji pairs —
        pairs that do not fit fold into a trailing `+N`, how many STANDING
        reactions are not shown, a count, never a string cut mid-emoji or
        mid-escape. `partial` coverage renders `partial`, every recorded
        reaction taken back `removed`, nothing recorded None — an annotation
        never renders an empty set or a zero. The marker itself is the one
        overshoot `cap` can take and stays bounded.
        """
        if self.coverage != "recorded":
            return self.coverage
        out: list[str] = []
        used = dropped = 0
        for shown, n in self.display_counts:
            pair = f"{shown}x{n}"
            cost = len(pair) + bool(out)
            if used + cost <= cap:
                out.append(pair)
                used += cost
            else:
                dropped += n
        if dropped:
            marker = f"+{dropped}"
            return " ".join([*out, marker]) if out else marker
        return " ".join(out) or ("removed" if self.recorded else None)

    @property
    def label(self) -> str | None:
        """The annotation text — `bounded_label` at `REACTION_LABEL_MAX`, the
        ceiling a surface without its own budget takes."""
        return self.bounded_label(REACTION_LABEL_MAX)


def fold_reactions(rows: Iterable[ReactionRow]) -> dict[int, RowReactions]:
    """The `message_reaction` rows of a batch of messages, folded to each
    target's current state. THE ONE FOLD: `record_query` established it
    (latest per actor+emoji, `removed` wins, counts by emoji), and every
    read that annotates a target uses it — a second implementation would
    produce a second count for the same record.

    Latest is by `id` (arrival order), not `ts`: ingest appends source
    events, and a skewed platform clock cannot let an older event's row
    outrank the one appended after it — the same reason the edit fold is
    newest-by-arrival. Replay of the same recorded row folds identically;
    it cannot inflate a count.

    Returns only message ids that HAVE recorded rows: a message with none
    stays absent, so its `TranscriptRow.reactions` stays None and reads
    «nothing recorded», never a fabricated empty set.
    """
    latest: dict[tuple[int, str, str], ReactionRow] = {}
    recorded: dict[int, int] = {}
    observed: dict[int, int] = {}
    for r in rows:
        recorded[r.message_id] = recorded.get(r.message_id, 0) + 1
        observed[r.message_id] = max(observed.get(r.message_id, 0), r.ts)
        key = (r.message_id, r.sender_id, r.emoji)
        prev = latest.get(key)
        if prev is None or r.id > prev.id:
            latest[key] = r
    return states_from_standing(
        ((r.message_id, r.emoji, r.removed) for r in latest.values()),
        recorded=recorded,
        observed=observed,
    )


def states_from_standing(
    standing: Iterable[tuple[int, str, bool]],
    *,
    recorded: Mapping[int, int],
    observed: Mapping[int, int],
    partial: Collection[int] = (),
) -> dict[int, RowReactions]:
    """Each target's state assembled from its LATEST standing rows — one
    `(message_id, emoji, removed)` per (target, actor, emoji) — with the
    metadata measured across the whole recorded history. `fold_reactions`
    reaches this through its own Python reduction and `audit.reaction_states`
    through the database's, so the fake and the SQL read share ONE counting
    definition.

    A target in `partial` keeps its `recorded` and `observed_at` but carries
    NO counts and `coverage="partial"`: a truncated standing set is never
    folded into an apparently complete count. `recorded` keys the result —
    a target with no rows is absent, so its `TranscriptRow.reactions` stays
    None and reads «nothing recorded», never a fabricated empty set.
    """
    withheld = frozenset(partial)
    standing_counts: dict[int, dict[str, int]] = {}
    for message_id, emoji, removed in standing:
        if removed or message_id in withheld:
            continue
        per = standing_counts.setdefault(message_id, {})
        per[emoji] = per.get(emoji, 0) + 1
    return {
        message_id: RowReactions(
            counts=(
                () if message_id in withheld
                else tuple(sorted(standing_counts.get(message_id, {}).items()))
            ),
            observed_at=observed[message_id],
            recorded=recorded[message_id],
            coverage="partial" if message_id in withheld else "recorded",
        )
        for message_id in recorded
    }


@dataclass(frozen=True)
class EditedMessage:
    """The recorded message an edit targets, as the edit path needs it
    (`RecordStore.find_edited`).

    `body` and `mentions` are the CURRENT version — its newest edit folded in,
    or the message itself if it was never edited — because the gate compares
    an edit against the version it REPLACES. `mentions` is None when that
    version predates `messages.mentions_json`, which the gate reads as
    «unknown». `first_body` is what it first said, for Honcho's framing.
    `platform_message_id` is its own handle ("" where the platform has none):
    an edit that opens a turn is answered AS this message, so a reaction or a
    threaded reply points at it and not at the edit.

    `replies_to_nora` is whether it quotes one of Nora's own messages. An edit
    cannot change what a message replies to, but not every platform repeats
    the quote on the edit event, so the gate reads it from here rather than
    from the edit. `edited_ts` is its newest edit's time, or None: an edit no
    newer than that is a redelivery, not a change.
    """

    id: int
    ts: int
    sender_id: str
    person_id: str | None
    is_nora: bool
    body: str | None
    mentions: frozenset[str] | None
    first_body: str | None
    platform_message_id: str = ""
    replies_to_nora: bool = False
    edited_ts: int | None = None
    # Every edit's own timestamp on record for this message. `edited_ts` is
    # the NEWEST BY ARRIVAL (the version shown), which a Signal edit from a
    # slow-clocked device can make lower than an earlier edit's; staleness is
    # judged against the MAXIMUM here, and an edit whose timestamp is already
    # on record is a redelivery — on Signal a timestamp IS an edit's identity.
    edit_timestamps: frozenset[int] = frozenset()
    # The `messages.media_json` of the original: the handles an edit's
    # attachment placeholders are given back, instead of re-fetching bytes
    # Nora already holds (`nora.ingest.stages.edit_media`).
    media_json: str | None = None
    # `messages.hidden_at IS NOT NULL`: hidden (a platform delete or unsend). The
    # row is still on record, so a lookup still finds it; a stage that would attach
    # or fold something onto it must not (`nora.ingest.stages.edit_target`).
    hidden: bool = False


def mentions_json(mentions: frozenset[str]) -> str:
    """`messages.mentions_json` / `message_edit.mentions_json` for one message.

    HERE, beside the type, for `nora.domain.media.media_json`'s reason: the SQL
    store and `FakeRecordStore` must write the identical string, and they are
    in layers that cannot reach each other. Sorted, so the same set is the same
    text. An empty set is `"[]"`, never NULL — NULL is reserved for «stored
    before the column existed», which the gate treats as unknown.
    """
    return json.dumps(sorted(mentions))


def parse_mentions_json(text: str | None) -> frozenset[str] | None:
    """The inverse, total: NULL or anything that is not a JSON list of
    strings is None («unknown»), never an empty set («mentioned nobody»)."""
    if text is None:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return frozenset(value)


@dataclass(frozen=True)
class UnattributedRow:
    """One audited message that carries no `person_id` — its sender resolved to
    nobody at the moment of ingest.

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
    precisely the copy-paste duplicate `nora.persons.load_persons` refuses
    to start on. The envelope carries every id form that arrived together, so
    it is what lets them be correlated back into one account.

    The cost is one extra column on a scan `nora.app.adoption` runs at every
    start; it is bounded by that scan's own `limit` and by an audit table
    already bounded by retention, and a Signal envelope is a couple of
    kilobytes.
    """

    id: int
    platform: str
    sender_id: str
    ts: int
    body: str | None
    # The conversation the message was in — needed so lazy-adoption feeds it into
    # the right conversation session, the same way the ambient feed does. Like
    # `platform`, it is carried because a re-fed row must know its session, which
    # `TranscriptRow` (already conversation-scoped) omits. REQUIRED, not
    # defaulted: a `""` would build a malformed `nora-conv-` session, so an
    # omission is a construction error, not a silent garbage feed.
    conversation_id: str
    raw_json: str | None = None
    # The custody index for this row, exactly as `TranscriptRow` carries it.
    # It is here because lazy adoption FEEDS these rows to Honcho and what a
    # photo showed lives behind a join keyed on this column, not in `body` —
    # without it, a back-filled row reaches memory as `[image a3f2c1]` and
    # nothing else. Defaulted, so every existing construction stays valid.
    media_json: str | None = None


@dataclass(frozen=True)
class TagContext:
    """Everything the tagging path assembles before the model is constructed."""

    tag_id: str                    # uuid4 hex — the correlation id
    message: NormalizedMessage
    tagger: PersonRef | None       # None = unmapped sender
    transcript: tuple[TranscriptRow, ...]   # oldest-first rows from db.recent
    peer_card: tuple[str, ...]
    started_at: float              # perf_counter, for the latency histogram
    # What Honcho has concluded about THE TAGGER — the same `representation`
    # verb, with `target` set to them instead of to her.
    #
    # `peer_card` above and this are NOT redundant: the card renders IDENTITY
    # claims («Name: Miles Novak», «Aliases: …»); the representation states dated
    # episodes («asked what to eat for dinner»). Who they are, and what happened
    # with them.
    #
    # Her own card is excluded because a learned self-description outranks an
    # authored one. That argument does NOT reach here: the objection was about
    # doctrine's lane, and other people's cards are facts about them rather
    # than claims about her. So the asymmetry is deliberate — for her, no card;
    # for them, both.
    #
    # This DUPLICATES what `about` would return for the same person, which is
    # the trade being made: her episodes with the person actually speaking are
    # the likeliest recall in any turn, and paying for them up front costs
    # less than a model→tool→model round trip and the risk she never asks.
    # `Room.tagger_card_injected` is what stops the duplicate landing
    # TWICE in one turn — `about` on the tagger points at this block instead of
    # fetching it again.
    tagger_card: tuple[str, ...] = ()
    # The note tier — the INDEX of tracked
    # uncertainties, unit-keyed and unlike every block before it.
    # Defaulted empty: `nora.language.note_render.render_notes` renders no
    # block for an empty tuple.
    # Deliberately the whole standing set and never a top-N: a note absent from
    # the index is invisible to her, and invisible is indistinguishable from
    # absent. The prompt budget is paid by the per-entry caps in
    # `nora.domain.notes` and by `note_set_status`, not here.
    notes: tuple[Note, ...] = ()
    # The unit's kept memos (DEC-306), the `<memos>` index: unit-wide like
    # `notes`, filled by the tag and fire assemblers; `()` renders no block.
    memos: tuple[Memo, ...] = ()
    # Standing — HER OWN position in THIS conversation, a fold of the previous
    # turn.
    #
    # It is the odd one out among the blocks above in three ways, each a rule
    # somewhere else. It is the only one that is Nora's own output rather than a
    # distillation of other people's (a position, not a record). It is the only one
    # keyed on the CONVERSATION where `notes` is keyed on the unit, and unlike it
    # is not shared across a unit's rooms at all: a position held in one room is
    # not a position held in another. And it is the only block in the prompt that
    # is REVISED IN PLACE rather than rebuilt from the store each turn, which is
    # why `nora.language.standing_render` makes it hedge itself despite it being
    # hers — an injection reaching any other block lives one turn, and one
    # reaching this row persists until a later fold drops it.
    #
    # `None` rather than a defaulted empty `Standing`: a conversation with no
    # position yet is not a conversation she has an empty opinion about, and
    # `render_standing(None)` emits no block for exactly that reason. With
    # `[standing] enabled = false` it stays `None` on every turn.
    standing: Standing | None = None
    ledger: tuple[LedgerEntry, ...] = ()
    # The room's CULTURE head — an account of what this room is and why it
    # became so. Beside `standing` because both are
    # room-scoped and both are her own output, and apart from it on the one
    # axis that matters: `standing` is where she had got to in this
    # CONVERSATION and is rewritten every turn; this is what the ROOM is like
    # and nothing in it ever retires.
    #
    # It is `None` on every turn but ONE: with `[culture] enabled = false` it is
    # unset, and even switched on the only path that fills this field is the
    # PARTICIPATION occasion (`FirePath._read_culture`) — the tag path
    # deliberately leaves it unset. `None` is also the ordinary state for a while
    # after the tier is switched on: a fold needs new messages and a day.
    culture: CultureRevision | None = None
    # What each person in this room is like, `(name, account)` per person who
    # has one (the Character tier, DEC-278), read through `read_character` on
    # the same occasions and under the same `[culture] enabled` as `culture`.
    character: tuple[tuple[str, CultureRevision], ...] = ()
    # What this room's UNIT is like across its rooms, read through
    # `read_culture` on the same occasions as `culture`,
    # keyed `unit_culture_key(unit)`. `None` until the unit has an account.
    unit_culture: CultureRevision | None = None
    # Her own account in this room's unit: Character · Nora, read through
    # `read_culture` keyed `nora_character_key(unit)` on the tag and fire turns.
    # `None` until the unit has one.
    self_account: CultureRevision | None = None
    # THE SWITCH (design/unified-memory/45 §5, `[memory.profile] read`): each entity
    # of this turn's Profile account — self, room, unit, then the persons — read
    # through `read_profile` and rendered as `<profile>` blocks INSTEAD of
    # `self_account`, `culture`, `unit_culture`, `character`, `peer_card`,
    # `tagger_card` and `people_in_window` (all of which the assembler leaves
    # unset when it fills this). Empty on the Honcho surface and in every
    # harness that wires no profile reader. Lines whose `exclude_persons` name
    # a member of the rendering room are dropped by the assembler before the
    # view is built (`ProfileView`'s docstring).
    profiles: tuple[ProfileView, ...] = ()
    # What she has learned to do differently in this unit: the bodies of its
    # active lessons, oldest first, read under `[lessons] render` on tag and
    # fire turns where `<self>` may show.
    # Empty: no block.
    lessons: tuple[str, ...] = ()
    # The shared decision log — what NORA'S OWN LOOP already did across this
    # UNIT, as `<loop_decisions>`. `(room label, rows)` per group, because the row
    # that matters is usually from the OTHER room: a `kind="proactive"` row fires
    # into the unit's home room and nowhere else, while the thread it is about
    # runs wherever the group is talking. A room-scoped block would see nothing.
    #
    # Populated for the two occasions that weigh a floor nobody offered them —
    # participation and the proactive act turn (`FirePath.build_context`,
    # `_DECISION_WRITERS_BY_KIND`) — because «she already raised this two hours
    # ago» is what changes the answer for both. Each occasion reads with its own
    # writer set (`decision_log`).
    #
    # It is NOT a sixth context block on the same axis as its neighbours. Every
    # block above is a distillation of the WORLD — what people said, what they
    # are like, what the unit has not settled. This is a record of her own
    # turns, which is why it says so and carries an age per row rather than
    # reading as state.
    #
    # The LABEL is the operator-authored policy label, never an id, and the
    # rows carry no title and no model text — see `decision_line`.
    #
    # Defaulted empty; `render_decisions` is not called at all for an empty
    # tuple.
    decisions: tuple[tuple[str, tuple[DecisionRow, ...]], ...] = ()
    # `<people_in_window>` — what Honcho has concluded about EACH PERSON WHO SPOKE
    # in the transcript window, as `(display name, conclusions)` pairs, most recent
    # speaker first. The same `representation` verb as `tagger_card` above with
    # `target` set to them, run once per speaker.
    #
    # It exists because `tagger_card` is keyed on ONE person — whoever tagged
    # her — and the participation occasion has no tagger at all
    # (`participation_occasion` builds `person_id=None`, so `ref is None`, so
    # `peer_card` and `tagger_card` are both empty on every ambient turn). The
    # ambient decider's question is about the people in this window, and «Aaron
    # asks this every week and never means it» is exactly the input that
    # separates a turn worth taking from one that is not.
    #
    # NOT a superset of `tagger_card` and not a replacement for it: that block
    # names the person who is ASKING, this one names the people who are
    # TALKING, and on the tag path those are different facts with different
    # weight. Populated only for the participation occasion
    # (`FirePath.build_context`), for `decisions`' reason: a reminder firing
    # was decided when its row was written.
    #
    # Defaulted empty; `_people_in_window_block` renders nothing for an empty
    # tuple.
    people_in_window: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # `<room_members>`: who is who in THIS room, each listed person with the
    # other names the group calls them (`PersonRef.aliases`). Only the room's
    # own listed persons (`policy.toml` `persons`) that identity maps, never
    # the whole identity file: a roster fed the address book would list people
    # who are not members (see `build_prefill`'s docstring). Empty for a
    # `persons = "*"` room, which would need a live roster read on every turn.
    room_members: tuple[PersonRef, ...] = ()
    # `<presence>` — where she is on this turn: the room, the group's other
    # rooms with each network's transport state, that other groups exist (never
    # which), and her own public handles. Read at turn start by
    # `nora.engagement.self_presence.PresenceReader`, never cached across turns:
    # a cached "up" is how she promises a send that then fails.
    #
    # `None` is a context nobody read one for (an offline harness); the prefill
    # then renders `Presence.minimal` — the network alone.
    presence: Presence | None = None
    # WHO THIS ROOM IS, for lineage: the room the blocks render into, every other
    # room of the policy, the location circles and `[lineage] mode`, built by the
    # assembler from `nora.agent.lineage.LineagePolicy`. `nora.agent.prefill`
    # renders each derived row's section only where its lineage passes for
    # this room. `None` (an offline harness, a caller with no policy) is
    # `mode = "off"`: every block renders.
    lineage_room: RenderRoom | None = None
    # `<computer>`: what is on her computer
    # for this room, read fresh at turn start from the machine and the
    # audit by `ComputerContextReader`, never a fold. `None` while
    # `[computer]` is off, in a room that has not used the computer within
    # `active_days` and has no open or lapsed task, and in every harness
    # that wires no reader: the block then renders nothing.
    computer: ComputerView | None = None
    # `<documents>`: this room's newest documents with
    # their ids, read at turn start by `DocumentsContextReader`. `None` while
    # `[documents]` is off, in a room with no document, and in every harness
    # that wires no reader: the block then renders nothing.
    documents: DocumentsView | None = None
    # `<subagents>`: this room's sub-agent jobs, running and finished
    # within a day, read at turn start by `SubagentsContextReader`. Tag turn only. `None`
    # while `[subagent]` is off, in a room with no job in the window, and in every harness
    # that wires no reader: the block then renders nothing.
    subagents: SubagentsView | None = None
    # `<recalled>`, the pre-fetch (design/unified-memory/00 step 3b, 25 §5):
    # the memory index searched from this message's own seeds before the
    # turn, as `nora.agent.memory_search.render_packet`'s items (the same
    # shape `memory_search` answers with), cut at `PREFETCH_TOKENS`. Tag turn
    # on the graph memory surface only (`[memory.index] read`); `()` on the
    # Honcho surface, on a failed or empty search, and in every harness that
    # wires no search: the block then renders nothing. `recalled_partial` is
    # the search's `partial`: the index was still being built.
    recalled: tuple[dict[str, Any], ...] = ()
    recalled_partial: bool = False
    # The rows READ, when the prompt shows fewer of them (`[store] prompt_window`).
    # `transcript` is what `<group_transcript>` renders and what `#n` / `reply_to`
    # index; this is the deeper read a LOOKUP (`who_said`) searches. Empty means
    # the two are the same rows: use `transcript`.
    lookup_transcript: tuple[TranscriptRow, ...] = ()


@dataclass(frozen=True)
class DailyForecast:
    """One day of a multi-day forecast — a row of `WeatherObservation.forecast_days`."""

    date: str                  # provider date string, e.g. "20260809"
    weekday: str               # e.g. 「星期日」
    weather: str               # the day's forecast sentence
    wind: str                  # wind description ("" if the provider omits it)
    max_c: float | None
    min_c: float | None
    rain_probability: str      # PSR bucket 「低」/「中」/「高」, or "" if absent


@dataclass(frozen=True)
class WeatherObservation:
    """The weather picture a `WeatherProvider` returns — current, warnings, the
    day's outlook, and (only when asked) a multi-day forecast. NEVER a coordinate.

    Region-neutral on purpose (the port has more than one provider ahead): a
    provider fills what it has and leaves the rest empty (`""`/`None`/`()`), and
    the `weather` tool renders whatever is present. `temperatures` is (place, °C)
    pairs — a region may report many stations (HK reports 26) or one; the MODEL
    picks the station nearest the place it was asked about (DEC-24, the division
    `where_is`/`transit` draw). `warnings` and `typhoon` are always carried
    because a signal in force is relevant to EVERY weather answer — Nora must not
    say 「天氣幾好」 with a T8 up. `forecast_days` is empty unless the caller asked
    for the multi-day forecast (`forecast=True`), keeping the default payload lean."""

    as_of: str                                      # provider timestamp, ISO-8601
    temperatures: tuple[tuple[str, float], ...]     # (station/place name, celsius)
    humidity_pct: int | None                        # None if the provider omits it
    warnings: tuple[str, ...]                        # active warnings, verbatim
    general_situation: str                          # the broad synoptic picture
    today_forecast: str                             # today/tonight forecast sentence
    outlook: str                                    # the days-ahead outlook line
    typhoon: str                                    # tropical-cyclone track note, "" if none
    forecast_days: tuple[DailyForecast, ...]        # empty unless forecast requested
