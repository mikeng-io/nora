"""Platform-neutral data contracts (spec C.2). Every downstream module speaks these."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .facts import Fact
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
    # What the Dreamer has distilled about THIS ROOM's own slang — the fifth
    # `TranscriptInjection` gather branch (Task 10). `RoomLanguageReader.
    # room_language` is documented never to raise, so a failed or not-yet-run
    # read degrades to the same empty value a room with no evidence yet has —
    # `nora.language.render.render_room_language` renders no block at all for
    # either. Defaulted for the same reason `self_card`/`tagger_card` are: the
    # existing construction sites (tests, the fire path) keep working, and an
    # absent scope_id is indistinguishable from "nothing known yet" — which is
    # safe here for the same reason it is safe there: advisory only, never the
    # ground for a claim.
    room_language: RoomLanguage = field(default_factory=lambda: RoomLanguage(scope_id=""))
    # Dreamer Plan 2 (2026-08-20 exchange-patterns design) — the room's own
    # sibling block, same shape as `room_language` above: distilled by the
    # background consolidator, defaulted empty so existing construction sites
    # keep working, never the ground for a claim about any one person (see
    # `RoomExchange`'s own docstring for why that is a structural guarantee,
    # not a convention).
    room_exchange: RoomExchange = field(default_factory=lambda: RoomExchange(scope_id=""))
    # Dreamer Plan 3 (2026-08-20 room-portrait design) — the room's synthesis
    # of `room_language`/`room_exchange` above, same defaulted-empty shape.
    # `RoomPortraitReader.room_portrait` is documented never to raise, same
    # reason as its two siblings.
    room_portrait: RoomPortrait = field(default_factory=lambda: RoomPortrait(scope_id=""))
    # The fact tier (2026-08-20 fact-tier design), Task 5 — world-facts keyed
    # on the WORKSPACE, not this room, unlike every block above. Defaulted
    # empty for the same reason `room_language`/`room_exchange`/
    # `room_portrait` are: the existing construction sites keep working, and
    # nothing writes this tier yet (`nora.dreamer.fact_synthesizer` is a later
    # task) so every deployed `TagContext` holds `()` until then —
    # `nora.language.fact_render.render_facts` renders no block for that,
    # same as its siblings' empty state.
    facts: tuple[Fact, ...] = ()


@dataclass(frozen=True)
class LexemeCandidate:
    """One observation `LexemeObserver` (`nora.language.observer`, Task 6)
    extracted from a batch, joined back to its evidence.

    Lives here rather than in `nora.language` for the same FORCED reason
    `LexemeExample`/`RoomLexeme`/`RoomLanguage` do (see Task 4's own note):
    `nora.infra.sql.language.SqlLanguageStore.append_observations` consumes
    this type, `nora.infra` sits BELOW `nora.language` in the layers
    contract, and a lower layer may never import a higher one — so a type
    both packages need to name has to live at `nora.domain`, the layers'
    floor, exactly where `TagContext` already does.

    `message_id`/`person_id` are the EVIDENCE this candidate is grounded
    in — `LexemeObserver` joins them back against the batch itself by
    `message_id`, never trusting the model's own claim (a model can
    hallucinate a `message_id`; a candidate whose evidence is not in the
    batch is dropped before construction, never carried as one of these).
    `person_id` in particular is never model-supplied: it is the JOINED
    row's own `person_id`, the roster's canonical id.

    `surface_form` is what the model actually saw on the wire, kept apart
    from `canonical_form` (what it normalised to) because "kam"/"劲"/"勁"
    can all be one word, and collapsing them at extraction time would make a
    wrong normalisation unreviewable later.

    `instruction_sha256` is PROVENANCE, and it is the one field here that is
    about the writer rather than the evidence: `sense` is model-authored, and
    without a record of the instruction text that asked for it a wrong gloss
    cannot be attributed to a revision (see `nora.domain.provenance`). It has
    NO default on purpose — `confidence` beside it is the model's own
    self-report, which is the opposite of provenance, and a defaulted stamp
    would let a new adaptor write unattributable rows in silence, which is
    exactly the state this field exists to end.

    `model` is the OTHER half of that same provenance, and it is undefaulted
    for the same reason. The instruction text is one input to the row; the
    model that read it is another, and the two do not substitute for each
    other — the same instruction run against a different model produces
    different glosses, so `instruction_sha256` alone cannot answer "what
    produced this row". It is the model NAME the injected client reports, not
    a config value read alongside it: a value read from anywhere but the
    object that makes the call is a proxy that can drift from what actually
    went on the wire.
    """

    surface_form: str
    canonical_form: str
    sense: str
    part_of_speech: str
    message_id: int
    person_id: str | None
    confidence: float
    instruction_sha256: str
    model: str


@dataclass(frozen=True)
class LexemeExample:
    """One verbatim usage, kept WITH its attribution. The gloss above it is
    model-authored; this is not. Rendering one of these under every entry is
    what makes a wrong `sense` visible at the point of use instead of
    authoritative."""

    message_id: int
    person_id: str | None
    text: str


@dataclass(frozen=True)
class RoomLexeme:
    canonical_form: str
    sense: str
    occurrence_count: int
    distinct_speakers: int
    first_seen_ms: int
    last_seen_ms: int
    state: str          # 'live' | 'fading' | 'stale'
    examples: tuple[LexemeExample, ...] = ()


@dataclass(frozen=True)
class RoomLanguage:
    """What the Dreamer knows about one scope, as of `generated_ms`.

    Empty is a REAL state and the correct one for a room with no evidence yet —
    the renderer emits no block at all rather than an empty one.
    """

    scope_id: str
    lexemes: tuple[RoomLexeme, ...] = ()
    generated_ms: int = 0

    def __bool__(self) -> bool:
        return bool(self.lexemes)


@dataclass(frozen=True)
class ExchangeCandidate:
    """One observation `ExchangePatternObserver`
    (`nora.language.exchange_observer`, Dreamer Plan 2) extracted from a
    batch, joined back to its evidence — the exchange-pattern sibling of
    `LexemeCandidate`, with the same evidence-grounding rule (`message_ids`
    are real batch rows, `participants` are the JOINED rows' own `person_id`s,
    never the model's own claim).

    **`act` is the withhold rule's first structural guarantee.** It is drawn
    from a closed, repo-authored set (`nora.infra.sql.schema.EXCHANGE_ACTS`)
    — there is no free-text gloss field anywhere on this type for a model to
    write a claim like "X is dumb" into. `message_ids`/`participants` both
    hold at least two entries by construction (`ExchangePatternObserver`
    drops anything shorter, mirroring the DB's own `array_length(...) >= 2`
    CHECK) — the second guarantee: a candidate describing one person cannot
    exist.

    `instruction_sha256` is PROVENANCE, undefaulted, for the same reason
    `LexemeCandidate`'s is: `act` is drawn from a closed set, but WHICH act a
    stretch of messages was called is still a model judgement, and the
    instruction text that framed the choice is what a later reviewer has to
    be able to name.

    `model` is the second half of that provenance, undefaulted for the reason
    `LexemeCandidate.model` gives — and it is INDEPENDENTLY reachable here:
    `[language].exchange_model` lets this adaptor run a different model from
    the lexeme one against the same room, so two rows written on the same
    tick can legitimately name different models.
    """

    act: str
    message_ids: tuple[int, ...]
    participants: tuple[str, ...]
    confidence: float
    instruction_sha256: str
    model: str


@dataclass(frozen=True)
class ExchangeExample:
    """One verbatim usage backing a `RoomExchangePattern` — the exchange
    sibling of `LexemeExample`. `text` is a real message body, never
    model-authored, for the same reason `LexemeExample.text` is."""

    message_id: int
    person_id: str | None
    text: str


@dataclass(frozen=True)
class RoomExchangePattern:
    """One consolidated exchange pattern for a scope: a closed-set `act`
    traded among `participants` (always >= 2, sorted). Carries no field a
    model could have used to make a claim ABOUT any one of `participants` —
    `act` is the only classification, and it is drawn from
    `nora.infra.sql.schema.EXCHANGE_ACTS`, never free text."""

    act: str
    participants: tuple[str, ...]
    occurrence_count: int
    first_seen_ms: int
    last_seen_ms: int
    state: str          # 'live' | 'fading' | 'stale'
    examples: tuple[ExchangeExample, ...] = ()


@dataclass(frozen=True)
class RoomExchange:
    """What the Dreamer's Plan 2 knows about one scope's exchange patterns,
    as of `generated_ms` — the exchange sibling of `RoomLanguage`. Empty is
    the correct state for a room with no evidence yet."""

    scope_id: str
    patterns: tuple[RoomExchangePattern, ...] = ()
    generated_ms: int = 0

    def __bool__(self) -> bool:
        return bool(self.patterns)


@dataclass(frozen=True)
class RoomPortrait:
    """What the Dreamer's Plan 3 knows about one scope, as of `generated_ms`
    — a single synthesized narrative, the portrait sibling of `RoomLanguage`/
    `RoomExchange`. One row per scope by construction (UPSERT, not an
    append-only evidence log — see `nora.language.portrait_synthesizer`'s
    module docstring for why this has no evidence layer of its own). Empty is
    the correct state for a room with no `room_lexeme`/`room_exchange`
    material yet."""

    scope_id: str
    narrative: str = ""
    generated_ms: int = 0

    def __bool__(self) -> bool:
        return bool(self.narrative)


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
