"""The memory index's closed vocabularies.

One place for the node kinds, the relations and the node states, read by the
table's CHECKs (`nora.infra.sql.schema`), by the projector
(`nora.infra.sql.memory_index`) and by the projection counter's help text, so
the three cannot disagree. A new relation or kind is a schema change, never
something a model invents: adding one here without its migration
fails `tests/infra/test_schema_parity.py`.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, Protocol, get_args

from nora.domain.memory_item import is_location_fact

__all__ = [
    "ATTACHMENT_KINDS",
    "EMBEDDED_KINDS",
    "EMBED_INPUTS",
    "EMBED_OUTCOMES",
    "EPISODE_KINDS",
    "HUB_KINDS",
    "KINDS_BY_CATEGORY",
    "MEMORY_KINDS",
    "NODE_KINDS",
    "NODE_STATES",
    "PERSON_RELATIONS",
    "PERSON_SCOPED_KINDS",
    "PRESENCE_KINDS",
    "PROJECTION_OUTCOMES",
    "RELATIONS",
    "SEARCH_STATUSES",
    "SOCIAL_KINDS",
    "EmbedInput",
    "EmbedOutcome",
    "ExactTerm",
    "IndexState",
    "IndexedNode",
    "MemoryFilter",
    "MemoryIndexRead",
    "MemoryKind",
    "MemoryScope",
    "Neighbour",
    "NodeKind",
    "ProjectionOutcome",
    "QueryEmbedder",
    "Relation",
    "SearchStatus",
    "Seed",
    "embeds_bytes",
    "exact_holds",
    "exact_pattern",
    "in_scope",
    "is_latin",
    "narrowed",
    "nothing_to_embed",
    "place_named",
    "social_label",
]

#: Every node kind; a new one is a CHECK migration. `media` is
#: one attachment, `(message, sha256)`, never the bytes. `social` is one
#: social item (a story frame, a post, a reel), `(network, item_id)`, and
#: `social_media` one medium of it, `(item, sha256)`: a carousel is several.
#: `social_location` is the place an item is tagged with, keyed like its item,
#: apart from the item so that a read can gate it alone (`PRESENCE_KINDS`).
#: A medium of a social item is its own kind, not a `media`, because
#: everything about an attachment is read off its message (its room, its type
#: in `messages.media_json`, its drop with the message) and a social item has
#: no message and no room. `persona_line` is one Persona account line
#: (design/unified-memory/45 §1), keyed by its row id.
NodeKind = Literal[
    "message", "media", "note", "memo", "event", "lesson", "character",
    "outing", "stay", "trip", "person", "room", "unit", "nora", "place",
    "social", "social_media", "social_location", "persona_line",
]
NODE_KINDS: tuple[str, ...] = get_args(NodeKind)

#: The entities: a node a hop may reach only as a direct neighbour, never
#: fan out from (a place the question names is the one exception: the walk
#: anchors on it, inward along `located_in`, `nora.agent.memory_search`).
#: They are the only nodes allowed no
#: scope of their own; every other node carries a unit, a room or a person.
HUB_KINDS: tuple[str, ...] = ("person", "room", "unit", "nora", "place")

#: The closed set of edge relations.
Relation = Literal[
    "authored_by", "in_room", "shared_by", "attached_to", "during", "about",
    "cites", "supersedes", "tracks", "closes_into", "leaves_behind",
    "draws_on", "informed_by", "disputes", "feedback_on", "depicts",
    "mentions", "located_in", "outcome_of",
]
RELATIONS: tuple[str, ...] = get_args(Relation)

NODE_STATES: tuple[str, ...] = ("live", "retired", "superseded")

#: The kinds that get a vector: every
#: kind with text worth matching by meaning. Never a hub (a hub never seeds),
#: and never a kind that is lexical only: the Rollup episodes
#: (outing, stay, trip), whose text is a kind and an interval. Disputes and
#: feedback, also lexical only there, have no node kind yet.
EMBEDDED_KINDS: tuple[str, ...] = (
    "message", "media", "note", "memo", "event", "lesson", "character",
    "social", "social_media", "social_location", "persona_line",
)

#: The kinds that are one medium, keyed by the `sha256` of its bytes: an
#: attachment and a social item's medium. An image of either is embedded
#: from its bytes (`embeds_bytes`), and a reading of those bytes is its text.
ATTACHMENT_KINDS: tuple[str, ...] = ("media", "social_media")

#: The social stream's kinds (design/cognition-loop/22). A person's
#: `social = false` (`identity.toml`) keeps every one of theirs out of every
#: read (`MemoryScope.social_off`), including those recorded before the switch.
SOCIAL_KINDS: tuple[str, ...] = ("social", "social_media", "social_location")

#: `nora_memory_embed_total`'s `outcome`, one per node the embedding worker
#: tried. `ok` stored a vector; `unavailable` the embedder (or the
#: blob store holding an image's bytes) did not answer, and the tick stops;
#: `rejected` the embedder refused this input, or its bytes would not
#: decode; `empty` the node had nothing to embed (no text, no image bytes);
#: `error` anything else went wrong with this one node. Every failure leaves
#: the node's vector NULL, counts an attempt and backs the node off.
#: `given_up` is counted IN ADDITION when a failure is the node's last
#: allowed attempt: it is never picked again until its input changes or the
#: operator clears vectors (`nora memory rebuild --embeddings-only`).
EmbedOutcome = Literal["ok", "unavailable", "rejected", "empty", "error", "given_up"]
EMBED_OUTCOMES: tuple[str, ...] = get_args(EmbedOutcome)

#: What a node's vector, or its last failed attempt, was made from
#: (`memory_node.embed_input`): its `text`, or the `bytes` its `sha256` names.
#: A change to that input, and only that input, clears the vector.
EmbedInput = Literal["text", "bytes"]
EMBED_INPUTS: tuple[str, ...] = get_args(EmbedInput)


def embeds_bytes(media_type: str) -> bool:
    """Whether an attachment of `media_type` is embedded from its bytes
    rather than its reading: an image, a sticker or a GIF is; a
    video, a voice note or a document is embedded from its reading's text."""
    return media_type.strip().lower().startswith("image/")


#: `nora_memory_projection_total`'s `outcome`. `ok` projected (or dropped) in
#: the store's own transaction; `queued` failed, and the source is on
#: `memory_projection_queue` for the next pass; `lost` failed AND the queue
#: write failed, so only a rebuild finds it. In every case the store's own
#: write stands.
ProjectionOutcome = Literal["ok", "queued", "lost"]
PROJECTION_OUTCOMES: tuple[str, ...] = get_args(ProjectionOutcome)


# --- the read ----------------------------------------------------------------

#: `nora_memory_search_total`'s `status`, and what `memory_search` answers while
#: `[memory_index] enabled`: `partial` while a rebuild has not
#: completed, the projection queue is not empty, the search fell back to the
#: lexical seed (no embedder, or it failed), or embeddable nodes have waited
#: past a fresh line's window for a vector of the current model (a backlog,
#: `IndexState.fresh`), never `nothing_found`;
#: `unavailable` on any error (fail closed).
SearchStatus = Literal["ok", "nothing_found", "partial", "unavailable"]
SEARCH_STATUSES: tuple[str, ...] = get_args(SearchStatus)

#: The Rollup episodes, each carrying a presence lineage the reading room must
#: pass (R-31, DEC-222).
EPISODE_KINDS: tuple[str, ...] = ("outing", "stay", "trip")

#: The person-only kinds: no unit and no room, readable where a room lists
#: their person. Any other node with neither a unit nor a room is
#: never in scope. A social item, its media and its location are their
#: author's: no unit column, so which units may read one is the rooms that
#: list the author.
PERSON_SCOPED_KINDS: tuple[str, ...] = (
    "stay", "trip", "social", "social_media", "social_location",
)

#: The kinds that say where a person was: the Rollup episodes, and the place
#: a social item is tagged with. A node of one is in scope only where every
#: person of it passes presence (`MemoryScope.presence_persons`: the room's
#: `location` flag AND the live DisclosureGuard). A tagged place is one the
#: author published, and still their whereabouts, so it is gated as a stay
#: is; the item itself is not, and stays findable by its caption and its
#: media wherever its author is listed.
PRESENCE_KINDS: tuple[str, ...] = (*EPISODE_KINDS, "social_location")


#: `memory_search`'s `kinds` (the tool's `Literal`): five plain categories a
#: model names reliably, each the node kinds it covers (`KINDS_BY_CATEGORY`).
#: Grouped by what a person would ask for, not by store: `notes` is everything
#: she wrote down herself (a note, a memo, a lesson, a Character account),
#: `places` is where people went (an outing, a stay, a trip), and `media`
#: holds a member's social items with the pictures and videos shared in
#: rooms (an item's tagged place with it). Hubs are never a category: they
#: are never returned.
MemoryKind = Literal["messages", "media", "notes", "events", "places"]
MEMORY_KINDS: tuple[str, ...] = get_args(MemoryKind)
KINDS_BY_CATEGORY: Mapping[str, tuple[str, ...]] = {
    "messages": ("message",),
    "media": ("media", "social", "social_media", "social_location"),
    "notes": ("note", "memo", "lesson", "character"),
    "events": ("event",),
    "places": ("outing", "stay", "trip"),
}

#: The relations that tie a node to the person a `memory_search` names: an
#: entry `about` them, a message `authored_by` them or `mentions` of them (an
#: @-mention resolved at ingest, 25 §2), an attachment `shared_by` them.
#: `depicts` joins when something writes it; until then it would match
#: nothing.
PERSON_RELATIONS: tuple[str, ...] = ("about", "authored_by", "shared_by", "mentions")


@dataclass(frozen=True, slots=True)
class MemoryFilter:
    """The question's own narrowing (`memory_search`'s `kinds`, `since`,
    `until` and `person`), carried on the scope (`MemoryScope.only`) so every
    read applies it INSIDE its scan, exactly where it applies the scope: a
    node outside it is never read, never returned, never a bridge for the
    walk and never takes a slot. Not a disclosure boundary; it only narrows
    what the scope already admits.

    - `kinds`: node kinds (`KINDS_BY_CATEGORY`'s values); empty is every kind.
    - `since_ms`, `until_ms`: the node's `at_ms` within them, inclusive. A
      node with no time is outside any window.
    - `person`: a node `about`, `authored_by` or `shared_by` that person
      (`PERSON_RELATIONS`), or one whose `persons` lists them; and, the
      `about` seed's own rule, only where the reading room lists them
      and the node was said in a room that lists them too, or in no room.
    """

    kinds: tuple[str, ...] = ()
    since_ms: int | None = None
    until_ms: int | None = None
    person: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """What one read may reach, computed per read from the live policy and
    the reading room's live roster. The unit is the
    boundary: a node is in scope when it belongs to the reading room's unit
    or to one of that unit's rooms (or, for a stay or trip, to a person this
    room lists), every person it is readable only to is listed here, and it
    names nobody excluded from here.

    - `units`, `rooms`: the reading room's unit and every room key of it.
    - `listed_here`: the persons the reading room lists (empty for a `*` room).
    - `people_here`: who is here, listed or in the live roster: an
      `exclude_persons` naming any of them keeps a node out.
    - `exclude_strict`: the room cannot vouch for who is in it (a `*` room,
      an unmapped account, an unreadable or degraded roster): any node with
      an `exclude_persons` is out (`nora.agent.lineage.excluded`'s rule).
    - `presence_persons`: known circle persons whose presence passes here (the
      room's `location` flag AND the live DisclosureGuard): an episode, or a
      social item's tagged place (`PRESENCE_KINDS`), is in scope only if
      every person of it is one.
    - `room_persons`: each room of the unit and the persons it lists, for the
      listed-room rule (evidence about P only from rooms that list P).
    - `only`: the question's narrowing (`MemoryFilter`), applied by every
      read beside the scope; `None` narrows nothing.
    - `social_off`: persons whose `identity.toml` block sets `social = false`
      (computed per read by the caller, which holds identity): a social node
      naming any of them is never in scope, whenever it was recorded. The
      projector stays pure; the switch is read, not written.
    """

    units: tuple[str, ...]
    rooms: tuple[str, ...]
    listed_here: frozenset[str]
    people_here: frozenset[str]
    exclude_strict: bool
    presence_persons: frozenset[str]
    room_persons: Mapping[str, frozenset[str]] = field(default_factory=dict)
    only: MemoryFilter | None = None
    # Required, keyword-only and with no default: a scope that forgot the
    # opt-outs would read them all (fail open), so every builder must say.
    social_off: frozenset[str] = field(kw_only=True)
    # Read-time rollback also hides nodes projected by an earlier live rebuild.
    persona_render: bool = False

    def rooms_listing(self, person: str) -> tuple[str, ...]:
        """The scope's rooms that list `person`, for the listed-room rule."""
        return tuple(sorted(r for r, ps in self.room_persons.items() if person in ps))


@dataclass(frozen=True, slots=True)
class IndexedNode:
    """One `memory_node` row as a read returns it: a pointer and what is
    needed to find and show it, with canonical typed facts hydrated at read time."""

    kind: str
    source_id: str
    text: str = ""
    at_ms: int | None = None
    unit: str | None = None
    origin_room: str | None = None
    persons: tuple[str, ...] = ()
    exclude_persons: tuple[str, ...] = ()
    lineage: Any = None
    sha256: str | None = None
    state: str = "live"
    # Canonical typed truth is read from Persona, never reconstructed from prose.
    identity_fact: Mapping[str, Any] | None = None
    location: bool = False
    named_persons: frozenset[str] = frozenset()
    location_subjects: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.source_id)

    @property
    def is_hub(self) -> bool:
        return self.kind in HUB_KINDS


@dataclass(frozen=True, slots=True)
class Seed:
    """A node a search starts from, with its relevance in [0, 1]: the lexical
    score, the full-width cosine of a dense seed, or 1 for an exact seed.
    `near` is an exact seed's cosine to the question when it holds a vector of
    the asked model: an exact hit past the pinned ones ranks by it, as a dense
    hit would."""

    node: IndexedNode
    score: float
    near: float | None = None


@dataclass(frozen=True, slots=True)
class Neighbour:
    """`node`, reached from the frontier node `via` along `rel`: `outgoing`
    when the edge is `via -rel-> node`, else `node -rel-> via`."""

    via: tuple[str, str]
    rel: str
    outgoing: bool
    node: IndexedNode


@dataclass(frozen=True, slots=True)
class IndexState:
    """`memory_index_state.status` (`never`, `building`, `complete`) and the
    projection queue's length now; with a model fingerprint asked for, the
    vector coverage too, counted within the search's scope when one
    is given: `embedded`, the live embeddable nodes holding a vector of that
    model; `unembedded`, those still waiting for one; `given_up`, those
    the worker gave up on (`EMBED_MAX_ATTEMPTS`); and `fresh`, those of the
    `unembedded` holding a text that have owed their vector (`owed_since`)
    for less than the window the caller asked for (`index_state`'s
    `fresh_seconds`), which the worker's next ticks will embed and the
    lexical seed reads meanwhile. A given-up node waits for nothing the worker will do, so it does
    not hold answers `partial`, and a fresh one is normal operation; the
    lexical seed reads both (`MemoryIndexRead.seed`'s `unembedded_of`).
    `unembedded - fresh` is the backlog. A node that failed as `empty` (an
    attachment with no reading and no bytes to embed) waits for its input
    to change, and is in none of the four."""

    status: str
    queue: int
    embedded: int = 0
    unembedded: int = 0
    given_up: int = 0
    fresh: int = 0


# --- exact seeds -------------------------------------------------------------

#: The characters that continue a Latin word, a handle or an id: a Latin
#: term matches only where neither neighbour is one of these. ASCII letters,
#: digits and `_`, and the accented Latin letters (Latin-1 Supplement without
#: the multiplication and division signs, Latin Extended-A and -B), so
#: `Zoë` is not inside `Zoëy`. A CJK character is not one: `Kit今日` holds
#: `Kit`.
_WORDISH = "A-Za-z0-9_\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"
#: The highest code point of a Latin term (Latin Extended-B ends at U+024F).
_LATIN_MAX = 0x24F


class ExactTerm(NamedTuple):
    """One exact reference. `cased` for a person's name, which
    matches only as written (`Will` is not `will`); a URL, a handle or an id
    matches in any case."""

    text: str
    cased: bool = False


def is_latin(term: str) -> bool:
    """Whether `term` is written in Latin script only (ASCII or accented
    Latin), so it matches as a whole word; any other script (Chinese writes
    no spaces between words) matches as a substring."""
    return all(ord(c) <= _LATIN_MAX for c in term)


def exact_holds(term: str, text: str, *, cased: bool = False) -> bool:
    """Whether `text` contains `term` literally, as the exact seed matches it
    (`SqlMemoryIndex.exact` states the same in SQL). Case-insensitive unless
    `cased` (a person's name). A Latin term matches only on its own: not
    inside a longer word, handle or id (`Kit` is not in `kitchen`, `@kit` not
    in `@kitty`, `Zoë` not in `Zoëy`); a term with any other script matches
    as a substring."""
    if not term:
        return False
    if is_latin(term):
        flags = 0 if cased else re.IGNORECASE
        return re.search(exact_pattern(term), text, flags) is not None
    return term in text if cased else term.lower() in text.lower()


def exact_pattern(term: str) -> str:
    """`exact_holds`' Latin rule as a Postgres regular expression (ARE: the
    same look-arounds, and `re.escape` only escapes punctuation, which ARE
    reads as the literal character)."""
    return f"(?<![{_WORDISH}]){re.escape(term)}(?![{_WORDISH}])"


#: The shortest part of a place's name that counts as naming it, by script:
#: one Chinese character names nothing (`中`), nor do two Latin letters
#: (`MK`, which a word like `mk2` or an initialism in passing would hold).
_PLACE_NAME_MIN = 2
_PLACE_NAME_MIN_LATIN = 3


def place_named(name: str, query: str) -> bool:
    """Whether `query` names the place whose hub text is `name`: one of its
    «中文 / English» parts, held as an exact reference holds (`exact_holds`:
    a Latin part on its own, `Mong Kok` and not `Mong Kokomo`; a Chinese part
    as a substring, `旺角` in `旺角有咩食`), in any case."""
    return any(
        len(part) >= (_PLACE_NAME_MIN_LATIN if is_latin(part) else _PLACE_NAME_MIN)
        and exact_holds(part, query)
        for part in (p.strip() for p in name.split(" / "))
    )


#: How a social item's kind is said (instagram-adoption/06: «a story must be
#: SAID to be a story»).
_SOCIAL_KIND_WORDS: Mapping[str, str] = {
    "story_frame": "story", "post": "post", "reel": "reel", "thread_post": "post",
}


def nothing_to_embed(kind: str, text: str) -> bool:
    """Whether a node's text holds nothing worth a vector: no text, or a
    social item's label line alone (no caption, no reading yet). Every such
    item would get the same few-word vector, crowding dense search; it is
    recorded `empty` instead, found lexically by its label, and embedded once
    a reading lands and changes its text."""
    if not text.strip():
        return True
    return kind == "social" and not text.partition("\n")[2].strip()


def social_label(network: str, kind: str, *, collab: bool = False) -> str:
    """What a social item is, in a few words: `Instagram story`, `Instagram
    post, co-authored`. The first line of its node's text, so a search on
    «story» finds one and the answer says what it is."""
    label = f"{network.capitalize()} {_SOCIAL_KIND_WORDS.get(kind, kind.replace('_', ' '))}"
    return f"{label}, co-authored" if collab else label


def in_scope(node: IndexedNode, scope: MemoryScope) -> bool:
    """The scope predicate for a NON-hub node, in Python: the fake index's,
    and the statement of what `SqlMemoryIndex` writes in SQL (the contract
    suite holds the two to the same answers). A hub never passes: it has no
    scope, and is reached only as a direct neighbour."""
    if node.kind in HUB_KINDS or node.state != "live":
        return False
    if node.kind == "persona_line" and not scope.persona_render:
        return False
    placed = (
        (node.unit is not None and node.unit in scope.units)
        or (node.origin_room is not None and node.origin_room in scope.rooms)
        or (
            node.unit is None and node.origin_room is None
            and node.kind in PERSON_SCOPED_KINDS
        )
    )
    if not placed:
        return False
    if not set(node.persons) <= scope.listed_here:
        return False
    if node.exclude_persons and (
        scope.exclude_strict or set(node.exclude_persons) & scope.people_here
    ):
        return False
    location = node.location or (node.identity_fact is not None and is_location_fact(
        node.identity_fact.get("key"), node.identity_fact.get("value")))
    if location:
        subjects = set(node.persons or node.location_subjects)
        if not subjects or not subjects <= scope.presence_persons:
            return False
    if node.kind in SOCIAL_KINDS and set(node.persons) & scope.social_off:
        return False
    return not (
        node.kind in PRESENCE_KINDS and not set(node.persons) <= scope.presence_persons
    )


def narrowed(node: IndexedNode, scope: MemoryScope, linked: Collection[str] = ()) -> bool:
    """`scope.only` for a NON-hub node, in Python (the fake index's, and the
    statement of what `SqlMemoryIndex` writes in SQL): `linked` is every
    person the node has a `PERSON_RELATIONS` edge to. True when nothing
    narrows."""
    f = scope.only
    if f is None:
        return True
    if f.kinds and node.kind not in f.kinds:
        return False
    if f.since_ms is not None or f.until_ms is not None:
        if node.at_ms is None:
            return False
        if f.since_ms is not None and node.at_ms < f.since_ms:
            return False
        if f.until_ms is not None and node.at_ms > f.until_ms:
            return False
    if f.person is not None:
        if f.person not in scope.listed_here:
            return False
        if f.person not in node.persons and f.person not in linked:
            return False
        if node.origin_room is not None and node.origin_room not in scope.rooms_listing(
            f.person,
        ):
            return False
    return True


class QueryEmbedder(Protocol):
    """The question as a vector, with the model's query instruction
    (`nora.infra.embedder.LlamaEmbedder.embed_query`): `DIMENSIONS` floats of
    unit length. Raises on any failure; the search then falls back to the
    lexical seed and answers `partial`."""

    async def embed_query(self, text: str) -> list[float]:
        ...


class MemoryIndexRead(Protocol):
    """The read half of the memory index: `SqlMemoryIndex`, and
    `nora.testing.memory_index.FakeMemoryIndex` under the shared contract
    suite. Every method applies `scope` INSIDE its read, and its
    `only` beside it (`narrowed`): a node out of scope, or outside the
    question's narrowing, is never returned, so nothing after the read has
    to remember to drop it."""

    async def seed(
        self, terms: Sequence[str], scope: MemoryScope, *, limit: int,
        unembedded_of: str | None = None,
    ) -> list[Seed]:
        """In-scope, non-hub nodes whose text holds the terms (pg_trgm word
        similarity, or containment), best first. A hub never seeds. With
        `unembedded_of`, only the embeddable nodes holding no vector of that
        model (waiting for one, or given up on): the dense seed cannot reach
        them."""
        ...

    async def dense(
        self, vector: Sequence[float], scope: MemoryScope, *, model: str, limit: int,
    ) -> list[Seed]:
        """In-scope, non-hub nodes holding a vector of `model`, nearest to
        `vector` first, scored by their full-width cosine. A vector of any
        other model is never compared."""
        ...

    async def exact(
        self, terms: Sequence[ExactTerm], scope: MemoryScope, *, limit: int,
        vector: Sequence[float] | None = None, model: str | None = None,
    ) -> list[Seed]:
        """In-scope, non-hub nodes containing at least one of `terms`
        literally (`exact_holds`, a `cased` term in its own case), scored 1:
        those holding the most terms first, then, given a `vector`, the
        nearest by cosine among nodes of `model` (that cosine is the seed's
        `near`), then the newest."""
        ...

    async def about(
        self, person_id: str, scope: MemoryScope, *, limit: int,
    ) -> list[IndexedNode]:
        """In-scope, non-hub nodes with an `about` edge to `person_id`, newest
        first: the exact-reference seeds of a named person."""
        ...

    async def media(
        self, sha256s: Sequence[str], scope: MemoryScope, *, limit: int,
    ) -> list[IndexedNode]:
        """In-scope nodes holding one of `sha256s` (the bytes of an attached
        picture, wherever it was shared before), newest first: the
        exact-reference seeds of the media a message carries."""
        ...

    async def places(self, query: str, *, limit: int) -> list[IndexedNode]:
        """The place hubs `query` names (`place_named`): a venue, a district,
        a city. A hub carries no scope and is never returned to a reader; a
        named one only anchors a walk inward along `located_in`
        (`nora.agent.memory_search.walk`), whose every other node is
        scoped."""
        ...

    async def neighbours(
        self, frontier: Sequence[tuple[str, str]], scope: MemoryScope,
    ) -> list[Neighbour]:
        """Every node one edge from a frontier node, in either direction,
        that exists (a dangling edge drops itself) and is in scope; a hub
        node is returned as a direct neighbour whatever its scope."""
        ...

    async def index_state(
        self, *, model: str | None = None, scope: MemoryScope | None = None,
        fresh_seconds: float = 0.0,
    ) -> IndexState:
        """The index's state; with `model`, its vector coverage as well,
        within `scope` when given (else over every node), counting as
        `fresh` the waiting nodes with a text that have owed their vector
        for less than `fresh_seconds` (none at 0)."""
        ...
