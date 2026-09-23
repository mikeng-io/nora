"""Platform-neutral data contracts (spec C.2). Every downstream module speaks these."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .culture import CultureRevision
from .decision_log import DecisionRow
from .media import MediaBlob, MediaRef
from .notes import Note
from .standing import Standing


@dataclass(frozen=True)
class ConversationTarget:
    """Where a reply goes. Hashable so it can key per-conversation locks and debounce."""

    platform: str
    conversation_id: str
    conversation_type: str
    # DID THIS TURN ARRIVE BY VOICE (design/voice-channel/07 §12.2, reversed
    # 2026-09-17). Voice is a SURFACE of a room, not a room of its own: a
    # guild's spoken utterances land in that guild's own text room
    # (`Policy.voice_room`), so `conversation_id` alone can no longer say
    # whether the reply must be SPOKEN or POSTED. Defaulted False, which is
    # the text posture every existing construction already meant.
    #
    # Not read off `NormalizedMessage.raw`: `raw` is the platform envelope,
    # persisted verbatim as `raw_json`, and a routing decision that changes
    # whether a reply is audible must not hang on an untyped dict lookup.
    voice: bool = False


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
class EditTarget:
    """Which recorded message an EDIT changes (design/message-edits/01 §1).

    Two schemes because the platforms have two: WhatsApp, Instagram and
    Discord name the edited message by their own handle (`message_id`, stored
    as `messages.platform_message_id`); Signal names it by its author and its
    original sent-timestamp (`sent_ts`, signal-cli's `targetSentTimestamp`),
    the same pair it addresses a quote or a reaction by. Exactly one is set.
    """

    message_id: str = ""
    sent_ts: int | None = None


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
    # SPOKEN, not typed (design/voice-channel/07 §12.2, reversed 2026-09-17).
    # Set only by `nora.web.voice`, the wake path's entry point; every parser
    # leaves it False. It rides the message so the reply can be routed back
    # out the way it came in — see `ConversationTarget.voice`.
    voice: bool = False
    # Set when this message is an EDIT of an earlier one; everything else on
    # the message then describes the NEW version — `body`, `mentions`,
    # `media`, `raw`, and `timestamp` (when it was edited). `sender_id` is who
    # the platform says edited it, and is `""` where the platform does not say
    # (Instagram's edit delta carries no sender). Ingest records it beside the
    # message it edits rather than as a message of its own
    # (design/message-edits/02 §4). Every parser leaves it None otherwise.
    edit_of: EditTarget | None = None

    def target(self) -> ConversationTarget:
        return ConversationTarget(
            self.platform, self.conversation_id, self.conversation_type, voice=self.voice,
        )


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
    # id, and it is what anything joining across stores keys on (design/20).
    # Defaulted so existing positional construction stays valid.
    person_id: str | None = None
    # The newest EDIT of this message, or None when it was never edited
    # (design/message-edits/01 §3). When set, `body` above is the LATEST text
    # and the row keeps its original `id`, `ts` and `raw_json` — the position
    # it was said at, and the envelope a reply or a reaction reads the
    # platform handle from. What it first said and the latest envelope ride
    # here.
    edit: RowEdit | None = None


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
class EditedMessage:
    """The recorded message an edit targets, as the edit path needs it
    (`RecordStore.find_edited`, design/message-edits/01 §2).

    `body` and `mentions` are the CURRENT version — its newest edit folded in,
    or the message itself if it was never edited — because the gate compares
    an edit against the version it REPLACES. `mentions` is None when that
    version predates `messages.mentions_json`, which the gate reads as
    «unknown». `first_body` is what it first said, for Honcho's framing.
    """

    id: int
    ts: int
    sender_id: str
    person_id: str | None
    is_nora: bool
    body: str | None
    mentions: frozenset[str] | None
    first_body: str | None


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
    # The custody index for this row, exactly as `TranscriptRow` carries it.
    # It is here because lazy adoption FEEDS these rows to Honcho, and issue
    # #116 moved what a photo showed out of `body` and behind a join keyed on
    # this column — without it, a back-filled row reaches memory as
    # `[image a3f2c1]` and nothing else, which is the state the issue was
    # opened to end. Defaulted, so every existing construction stays valid.
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
    # What Honcho has concluded about NORA HERSELF — episodes, not identity.
    # Deliberately sourced from the `representation` verb and never from
    # `peer_card`: her card distils her own output into IDENTITY claims («Kind:
    # AI chatbot/assistant», «Owner: 主人 with admin authority» — both observed
    # live 2026-08-11), and identity is doctrine's lane and operator-only. A
    # learned self-description injected beside an authored one wins, because it
    # reads as evidence. Episodes do not compete: «you told Aaron you would talk
    # after work» is a fact about the past, which is what she actually lacks.
    #
    # Defaulted, so the ~40 existing construction sites (tests, the fire path)
    # keep working and an absent card is the SAME shape as a card Honcho could
    # not answer — which is safe here only because this block is advisory: it is
    # never the ground for a claim, so an empty one costs continuity, not truth.
    self_card: tuple[str, ...] = ()
    # What Honcho has concluded about THE TAGGER — the same `representation`
    # verb, with `target` set to them instead of to her.
    #
    # `peer_card` above and this are NOT redundant, and the reason is worth
    # keeping: read live 2026-08-15 they overlapped in exactly ZERO items. The
    # card renders IDENTITY claims («Name: Miles Novak», «Aliases: …»); the
    # representation states dated episodes («asked what to eat for dinner»).
    # Who they are, and what happened with them.
    #
    # Her own card is excluded (see `self_card`) because a learned
    # self-description outranks an authored one. That argument does NOT reach
    # here: the objection was about doctrine's lane, and other people's cards
    # are facts about them rather than claims about her. So the asymmetry is
    # deliberate — for her, representation only; for them, both.
    #
    # This DUPLICATES what `about` would return for the same person, which is
    # the trade being made: her episodes with the person actually speaking are
    # the likeliest recall in any turn, and paying for them up front costs
    # ~360 tokens against a model→tool→model round trip and the risk she never
    # asks. `Room.tagger_card_injected` is what stops the duplicate landing
    # TWICE in one turn — `about` on the tagger points at this block instead of
    # fetching it again.
    tagger_card: tuple[str, ...] = ()
    # The note tier (2026-08-21 note-tier design), §5.1 — the INDEX of tracked
    # uncertainties, unit-keyed and unlike every block before it.
    # Defaulted empty for the same reason its siblings are: the existing
    # construction sites keep working, and
    # `nora.language.note_render.render_notes` renders no block
    # for an empty tuple, so a deployed prompt gains nothing until it does.
    # Deliberately the whole standing set and never a top-N: a note absent from
    # the index is invisible to her, and invisible is indistinguishable from
    # absent. The prompt budget is paid by the per-entry caps in
    # `nora.domain.notes` and by the curator's retirement pass, not here.
    notes: tuple[Note, ...] = ()
    # Standing (design/dreamer-agent-loop/26-standing.md) — HER OWN position in
    # THIS conversation, folded from the previous turn by the debrief stage.
    #
    # It is the odd one out among the blocks above and in three ways worth
    # naming here, because each one is a rule somewhere else. It is the only
    # one that is Nora's own output rather than a distillation of other
    # people's (§8 — a position, not a record). It is the only one keyed on the
    # CONVERSATION where `notes` is keyed on the unit, and unlike
    # it is not shared across a unit's rooms at all: a position held
    # in one room is not a position held in another. And it is the only block
    # in the prompt that is REVISED IN PLACE rather than rebuilt from the store
    # each turn, which is why `nora.language.standing_render` still makes it
    # hedge itself despite it being hers — an injection reaching any other
    # block lives one turn, and one reaching this row persists until a later
    # fold drops it (§4.1, carried risk, stated and not solved).
    #
    # `None` rather than a defaulted empty `Standing`: a conversation with no
    # position yet is not a conversation she has an empty opinion about, and
    # `render_standing(None)` emits no block for exactly that reason. Defaulted
    # like its siblings so the ~40 existing construction sites keep
    # working, and `[standing] enabled = false` (the shipped default) leaves it
    # `None` on every turn.
    standing: Standing | None = None
    # The room's CULTURE head — an account of what this room is and why it
    # became so (design/culture-tier). Beside `standing` because both are
    # room-scoped and both are her own output, and apart from it on the one
    # axis that matters: `standing` is where she had got to in this
    # CONVERSATION and is rewritten every turn; this is what the ROOM is like
    # and nothing in it ever retires.
    #
    # Defaulted like its siblings so the ~40 existing construction sites keep
    # working. It is `None` on every turn but ONE: `[culture] enabled = false`
    # is the shipped default, and even switched on the only path that fills
    # this field is the PARTICIPATION occasion (`FirePath._read_culture`) —
    # `01-the-adaptor §6` authorises exactly that one consumer, and the tag
    # path deliberately leaves it unset. `None` is also the ordinary state for
    # a while after the tier is switched on: a fold needs new messages and a
    # day.
    culture: CultureRevision | None = None
    # The shared decision log (issue #199) — what NORA'S OWN LOOP already did
    # across this UNIT, as `<loop_decisions>`. `(room label, rows)` per
    # group, because the row that matters is usually from the OTHER room: a
    # `kind="proactive"` row fires into the unit's home room and nowhere
    # else, while the thread it is about runs wherever the group is talking,
    # and in the measured incident those were different rooms on different
    # platforms. A room-scoped block would have seen nothing.
    #
    # Populated for the two occasions that weigh a floor nobody offered them —
    # participation and the proactive act turn (`FirePath.build_context`,
    # `_DECISION_WRITERS_BY_KIND`) — because «she already raised this two hours
    # ago» is what changes the answer for both. The act turn was left out until
    # 2026-09-17 on the argument that it already holds the decide pass's index;
    # that index is what she MEANT to raise, and two rows due 30 minutes apart
    # duplicated a raise because nothing said what the loop had since DONE.
    # Each occasion reads with its own writer set (`decision_log`).
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
    # Defaulted empty like its five siblings, and `render_decisions` is not
    # called at all for an empty tuple, so a deployed prompt gains nothing
    # until something populates it.
    decisions: tuple[tuple[str, tuple[DecisionRow, ...]], ...] = ()
    # `<people_in_window>` (issue #210) — what Honcho has concluded about EACH
    # PERSON WHO SPOKE in the transcript window, as `(display name, conclusions)`
    # pairs, most recent speaker first. The same `representation` verb as
    # `tagger_card` above with `target` set to them, run once per speaker.
    #
    # It exists because `tagger_card` is keyed on ONE person — whoever tagged
    # her — and the participation occasion has no tagger at all
    # (`participation_occasion` builds `person_id=None`, so `ref is None`, so
    # `peer_card`, `tagger_card` and `self_card` were all empty on every
    # ambient turn). The ambient decider's question is not about one person; it
    # is about the people in this window, and «Aaron asks this every week and
    # never means it» is exactly the input that separates a turn worth taking
    # from one that is not.
    #
    # NOT a superset of `tagger_card` and not a replacement for it: that block
    # names the person who is ASKING, this one names the people who are
    # TALKING, and on the tag path those are different facts with different
    # weight. Populated only for the participation occasion today
    # (`FirePath.build_context`), for `decisions`' reason: a reminder firing
    # was decided when its row was written.
    #
    # Defaulted empty like its siblings, and `_people_in_window_block` renders
    # nothing for an empty tuple, so a deployed prompt gains nothing until
    # something populates it.
    people_in_window: tuple[tuple[str, tuple[str, ...]], ...] = ()


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
