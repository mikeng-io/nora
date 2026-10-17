"""The one retrieval service over the memory index: scope, seed, expand hop by
hop, rank, and an evidence packet with the path that reached each item.
`memory_search` calls it while `[memory_index] enabled`.

**1. Scope, per read, from the live policy** (`scope_for`). The unit is the
read boundary (DEC-231): the reading room's unit, every room of it,
the persons this room lists, who is here (listed or in the live roster), and
the listed persons whose presence passes here (the room's `location` flag
and the live DisclosureGuard, `nora.agent.lineage`'s own predicate), and the
listed persons whose `social = false` keeps their social items out of every
read (the caller's, from identity). Lineage mode is not consulted. The arrays go DOWN
to the index, which applies them inside every read (`SqlMemoryIndex`); the
walk never filters afterwards, so it can only step onto in-scope nodes and a
path is cut at its first out-of-scope node.

**2. Seeds**, in this order, a node seeded once:

- **Exact** (`exact_terms_of`): a node containing, literally, a handle, a
  URL or an id-like token (in any case), or the name of a person this room
  lists (as written). The first `PINNED_LIMIT` are PINNED: they rank ahead
  of every other hit in the answer, however near a dense hit is (a literal
  name must not lose to a near-duplicate); the
  rest rank by their cosine, so most of the answer always goes by score.
  Ordinary words are never exact.
- **Dense**: the question embedded ONCE with the model's query instruction,
  and the nearest in-scope nodes of the configured model's vectors, scored by
  their cosine (`SqlMemoryIndex.dense`). A scan that comes back with fewer
  than the scope holds is counted (`nora_memory_dense_short_total`).
- **Lexical**, by the question's terms (`terms_of`), when there is no query
  vector: `[memory_index] embed` is off, no node in scope holds a vector of
  the current model yet, or the embedder failed. The answer is then
  `partial`. Beside a dense seed, it reads only the embeddable nodes holding
  no vector of the current model: those waiting for the worker's next tick
  (the line that triggered the turn, said a moment ago) and those it gave
  up on. No vector reaches either, and their words still can.
- **About**: for a named person this room lists, the in-scope nodes `about`
  them (an exact reference). A person this room does not list
  seeds nothing.

A hub never seeds.

**The question's narrowing** (`memory_search`'s `kinds`, `since`, `until`
and `person`) is a `MemoryFilter` on the scope (`MemoryScope.only`), applied
inside the index's reads where it applies the scope. `since`, `until` and
`person` narrow every read, seeds and hops alike: a node outside them is
never read, never returned and never takes a slot. A named person always
narrows: to nodes about them, authored or shared by them, or listing them,
said where they are listed (the `about` seed's rule,
`nora.domain.memory_index.narrowed`). `kinds` narrows the SEEDS and the
ANSWER, not the hops: the seeds run once with
it and once without, the hops cross any kind, and only the asked kinds are
ranked and returned. An episode has no vector and its text is a kind and an
interval, so `places` is mostly reached from what was said during one.

**3. Expansion**, hop by hop, both edge directions, from the best
`FRONTIER_WIDTH` nodes of the last hop. Two rules on top of scope:

- **The hub rule**: a person, room, unit, Nora or place node
  is kept only as a direct neighbour of the node that reached it (a message's
  author, an event's venue), and never put on the next frontier. Two hops
  through a person reach everything ever said about them, which is a search
  on the person, not an expansion of the question. **Its one exception is a
  place the question names** (step 3a, `_anchors`): «near Mong Kok» anchors
  the walk at the Mong Kok place node and the places `located_in` it (a
  district's venues; a city's districts and theirs), and the first hop
  leaves from them, inward only: the event `about` a venue there, the outing
  `located_in` it. An anchor is never returned, and each node the hops reach
  passes the scope like any other.
- **The listed-room rule**: an edge to person P is followed only if
  the reading room lists P and, when the node it comes from was said in a
  room, that room lists P too. Talk about Kit in a room Kit is not in is not
  evidence about Kit.

Each step multiplies the parent's relevance by the relation's weight
(`REL_WEIGHT`). A node below the floor is not kept (`DENSE_FLOOR` for a
seed scored by its cosine, `RELEVANCE_FLOOR` for a lexical seed and every
hop), and the walk
stops early when a hop keeps nothing (adaptive depth), at `NODE_BUDGET`
nodes, or at the hop count `depth` asks for, never past `HOP_CEILING`.

**4. Rank** the pinned exact hits first, then the rest, each by relevance
times age decay (`HALF_LIFE_DAYS`, a social item's
`SOCIAL_HALF_LIFE_DAYS`). **5. Return** the
best items, cut adaptively as they are rendered (`render_packet`): the
pinned ones always, then in rank order until a token budget
(`PACKET_TOKENS`) is spent or the relevance drops by more than `SCORE_GAP`,
at least `MIN_RESULTS` and never past `RESULT_MAX`. Each item carries its
kind, age, a handle where the kind
has one a person or a tool can use, its text, and its path; an item whose
text carries an identifier is withheld whole (`scan_identifiers`, R-18).
The cut never empties a list that has an item the screen passes, so the
status stays the search's.

**Statuses** (R-27): `unavailable` on any error, counted, never an
empty answer; `partial`, whatever was found, when the index is not
`complete`, its projection queue is not empty, the seeds were lexical (the
fallback above), or the embedding worker has a BACKLOG in this search's
scope: a live embeddable node that has waited longer than
`BACKLOG_TICKS` drain periods for a vector of the current model, counted
from when its vector became owed (`owed_since`, reset only when its input
changes, never by a re-projection). A node with a text that has owed its
vector for less is normal operation, not a backlog: the next tick embeds
it, and until then the lexical seed reads it, so it is searched. An
attachment with no text is never fresh, since nothing reads it. Nor does
a node the worker gave up on count (one poisoned file would hold every
answer `partial` for good; the lexical seed reads it too), nor an
attachment that failed as `empty`, which waits for its reading. A
question with no search terms cannot reach a waiting or given-up node by
its words, so one such node then answers `partial` too. So an
unfinished index is never «you never said that»; `nothing_found` only
from a complete index in which every node in scope was searched; `ok`.

The floors, the cut and the `about` seed's score are set from the cosine
spread measured on the backfilled index
(`design/unified-memory/65-build-step-3-vectors.md` §6); the walk's other
constants are starting values.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.agent.lineage import section_permits
from nora.domain.external_files import render_file_links
from nora.domain.lineage import PRESENCE, ROW, Lineage, LineageEntry, RenderRoom
from nora.domain.media import HANDLE_CHARS
from nora.domain.memory_index import (
    EPISODE_KINDS,
    SOCIAL_KINDS,
    ExactTerm,
    IndexedNode,
    MemoryFilter,
    MemoryIndexRead,
    MemoryScope,
    QueryEmbedder,
    SearchStatus,
    Seed,
    exact_holds,
)
from nora.domain.models import PersonRef
from nora.domain.prompt import stamp
from nora.domain.rollup import room_key
from nora.language.computer_render import estimate_tokens

__all__ = [
    "BACKLOG_TICKS",
    "DEFAULT_DEPTH",
    "DENSE_FLOOR",
    "EXACT_LIMIT",
    "HOPS_BY_DEPTH",
    "HOP_CEILING",
    "MIN_RESULTS",
    "PACKET_TOKENS",
    "PINNED_LIMIT",
    "RESULT_MAX",
    "SCORE_GAP",
    "MemorySearch",
    "MemorySearchResult",
    "exact_terms_of",
    "render_packet",
    "scope_for",
    "terms_of",
]

log = logging.getLogger("nora.agent.memory_search")

_METRIC = "nora_memory_search_total"
_DENSE_SHORT = "nora_memory_dense_short_total"

#: A reasoning depth as hops. `memory_search` takes no depth, so it
#: searches at `DEFAULT_DEPTH`; the table stays for a caller that asks.
HOPS_BY_DEPTH: Mapping[str, int] = {
    "minimal": 2, "low": 2, "medium": 3, "high": 4, "max": 4,
}
#: The hard ceiling on hops.
HOP_CEILING = 4
DEFAULT_DEPTH = "low"
#: The walk's floor: of every hop, of a search with no query vector, and of
#: the lexical seed beside a dense one. A node whose relevance falls below it
#: is not kept, and a hop that keeps none ends the walk. A lexical seed
#: scores the mean over the question's terms of 1 (contained) or its word
#: similarity, so one term of three found scores about 0.33; this admits it
#: and about four weak hops from it, and stops the walk well before noise
#: dominates. A hop's relevance is its seed's times the relations' weights,
#: so a message at a median true hit's cosine (0.65, 65 §6) reaches the
#: outing it was said during (`during`, 0.5) at about 0.32: the cosine floor
#: below, applied to hops, would cut off every episode, which has no vector
#: and is reached only by a hop or an `about` seed.
RELEVANCE_FLOOR = 0.1
#: The floor of a seed scored by its cosine to the question: a dense seed
#: and an exact hit past the pinned ones (and the `about` seed, which scores
#: above it). Measured on the backfilled index (design/unified-memory/65 §6):
#: this model's cosines cluster high, the top 50's non-hits at 0.35 at the
#: 10th percentile and up to 0.78; a known item ranked in the top 20 of its
#: own query never scored under 0.387 (127 of 143 queries), and a query
#: about nothing held scored its best at 0.33 to 0.55 (median 0.46). So the
#: floor sits under every reachable true hit (a floor above one turns
#: «found» into «you never said that», the worse error) and still trims the
#: tail: three of twenty such queries seeded nothing above it, none of the
#: 143 with a hit did. Not inside a date window (`since`, `until`): a
#: question asked by time shares few words with its answer (the recall
#: cases' evidence scored 0.07 to 0.36), and the window did the narrowing,
#: so the walk's floor holds there, as before this floor existed.
DENSE_FLOOR = 0.38
#: Nodes kept per search, seeds included.
NODE_BUDGET = 60
#: Nodes expanded per hop: the best of the last hop's.
FRONTIER_WIDTH = 20
SEED_LIMIT = 20
#: Exact seeds per search. Fewer than `SEED_LIMIT` so a question
#: naming a much-discussed person still has dense seeds to expand from.
EXACT_LIMIT = 10
#: Exact hits pinned ahead of every other hit: the first of them, the most
#: terms held and then the nearest to the question. A rendered item is about
#: 60 tokens (median of 2,131 measured, 90th percentile 100, 65 §6), so five
#: take about a sixth of `PACKET_TOKENS` and the rest of the answer, about
#: fifteen items, goes by score: were every exact hit pinned, ten literal
#: matches would crowd out even a dense hit at cosine 1.0. An exact hit past
#: these ranks by its cosine to the question, as a dense hit does.
PINNED_LIMIT = 5
#: The answer, cut adaptively as it is rendered (`render_packet`): the hits in
#: rank order until the next would take the packet past `PACKET_TOKENS`, or
#: relevance drops more than `SCORE_GAP` below the hit before it with nothing
#: stronger still to come, never
#: past `RESULT_MAX`; the pinned hits always, and at least `MIN_RESULTS`
#: whatever the budget or the gap. Run on the production index for 143
#: known-item queries (65 §6), this showed the known item for 107, the old
#: cut of ten for 98 (every one of which this showed too), at a median 20
#: items and 1,930 tokens a packet against 1,020. Replaying the budget over
#: the same lists: 1,000 tokens found 97, 1,500 found 111, 2,000 found 112,
#: 2,500 found 115 for a quarter more text every call.
PACKET_TOKENS = 2_000
#: A drop in relevance from one unpinned hit past which the rest is another
#: topic. Between neighbouring non-hits in a top 20 the drop was under 0.017
#: nine times in ten (2,733 pairs, 65 §6), so 0.2 stops only after a
#: standout: read against the next hit alone, it shortened 4 of the 163
#: measured queries and cost none its hit. On relevance, not on the
#: age-decayed rank, whose drops mix age with meaning: 0.15 on that rank cost
#: seven hits. The drop is read against the best relevance still to come, not
#: the next hit's: the rank is decayed, so a recent weak hit can sit just
#: ahead of an older strong one, and 4 of the 36 misses of the run 65 §6
#: reports stopped there.
SCORE_GAP = 0.2
#: However tight the budget or steep the drop, a search that found anything
#: shows its best few: the first this many items the identifier screen
#: passes (a withheld hit is not one of them).
MIN_RESULTS = 3
#: A safety cap, not the cut: at the measured 52 tokens of a short item
#: (10th percentile) the budget would admit 38, which no question needs.
RESULT_MAX = 30
#: A seed `about` the named person (a stay, trip, outing or character) has no
#: score of its own. On this model's scale a known item scored 0.65 at the
#: median and 0.46 at the 10th percentile, and the best non-hit 0.60 (65 §6),
#: so the 0.6 this held ranked twenty such seeds level with the best dense
#: hits and above about four in ten true ones. 0.45 ranks them after nine in
#: ten true hits, above `DENSE_FLOOR`, so they still show when room is left.
PERSON_SEED_SCORE = 0.45
#: Age decay: an item this many days old ranks at half its relevance.
HALF_LIFE_DAYS = 180.0
#: The half-life of a social item (`SOCIAL_KINDS`). It is dated by when it
#: was posted, often years before the room heard of it (in the index a social
#: item is a median 491 days old, its medium 630; a message 28), so at
#: `HALF_LIFE_DAYS` a strong match ranked below the 30 kept. Replayed on 143
#: known items, sampled and asked as 65 §6 did, each with the gap read
#: above: 180 showed 115, 365 115, 730 118, 1095 120, no decay 124, against
#: 114 for 180 with the gap read against the next hit. No decay showed
#: every social item and pushed a message out of the packet; 1095 lost none
#: master showed.
SOCIAL_HALF_LIFE_DAYS = 1095.0
#: Characters of one item's text in the packet.
TEXT_CHARS = 600
#: How many drain periods (`[memory_index] drain_seconds`, the embedding
#: worker's tick) a node may wait for its vector before it is a backlog
#: that answers `partial`. The worker sleeps a period, drains, then embeds
#: one batch, the newest first (`embed_pick`), so a node written just after
#: a pick waits out that batch, a sleep and the next drain: under two
#: periods while the worker keeps up. The third is margin for a slow batch
#: (images). Past it the worker is behind (a backfill, a cleared model) or
#: failing the node, and the dense seed is missing what it should hold. By
#: age, not by a share of the scope: a share means different things in a
#: room of fifty nodes and a unit of forty thousand, while a wait past
#: three ticks is the property itself, a worker not keeping up.
BACKLOG_TICKS = 3

#: How much of the parent's relevance a relation carries. Provenance relations carry
#: most; co-occurrence least. A relation absent here carries `_DEFAULT_WEIGHT`.
REL_WEIGHT: Mapping[str, float] = {
    "cites": 0.9, "attached_to": 0.9, "supersedes": 0.8, "tracks": 0.8,
    "closes_into": 0.8, "leaves_behind": 0.8, "draws_on": 0.7, "informed_by": 0.7,
    "disputes": 0.8, "feedback_on": 0.7, "about": 0.6, "during": 0.5,
    "outcome_of": 0.6, "mentions": 0.5, "in_room": 0.3, "located_in": 0.7,
}

#: A place the question names (`MemoryIndexRead.places`) anchors the walk at
#: the relevance of a literal reference, an exact seed's 1: a named place is
#: one, as a name or a handle is.
PLACE_SEED_SCORE = 1.0
#: How far a named place descends `located_in` before the hops: a city to its
#: districts to their venues.
PLACE_DEPTH = 2
#: The most places that anchor one walk, the named ones and those inside them.
PLACE_LIMIT = 10
_DEFAULT_WEIGHT = 0.5

#: Words that carry no search: the question's grammar, not its subject.
_STOP = frozenset({
    "about", "also", "and", "any", "are", "been", "but", "can", "could", "did", "does", "for",
    "from", "had", "has", "have", "her", "him", "his", "how", "into", "its", "just", "know",
    "like", "not", "our", "remember", "said", "say", "she", "some", "tell", "than", "that",
    "the", "their", "them", "then", "there", "they", "this", "was", "were", "what", "when",
    "where", "which", "who", "whom", "why", "will", "with", "would", "you", "your"
})
#: A Latin word, or a run of CJK ideographs (the two BMP blocks).
_WORD = re.compile(
    "[a-z0-9][a-z0-9'\\-]*[a-z0-9]|[\u3400-\u9fff\uf900-\ufaff]+", re.IGNORECASE,
)
_MAX_TERMS = 8

#: Exact references. A URL with a scheme or `www.`; a handle, `@`
#: and two or more handle characters, not inside an e-mail address; an
#: id-like token, five or more letters and digits holding both, or six or
#: more digits. Trailing sentence punctuation is not part of either.
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'`]+", re.IGNORECASE)
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_](?:[A-Za-z0-9_.]*[A-Za-z0-9_])")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]")
_TRAILING = ".,;:!?)]}>\"'」』》】）。，！？"  # noqa: RUF001 — the full-width ones are the data
_MAX_EXACT = 8


#: A number with at most a short suffix: a time (`5pm`), an ordinal
#: (`100th`), a measure (`128GB`). A token made of these joined by `-`
#: (`5pm-7pm`) is a quantity, not an id.
_QUANTITY = re.compile(r"\d+[A-Za-z]{0,3}")
#: An id holds at least this many digits: `ZX4471` is one, `COVID-19` and
#: `iPhone15`, a name with a number, are not.
_ID_DIGITS = 3


def _id_like(token: str) -> bool:
    if token.isdigit():
        return len(token) >= 6
    if all(_QUANTITY.fullmatch(part) for part in token.split("-")):
        return False
    return (
        len(token) >= 5 and sum(c.isdigit() for c in token) >= _ID_DIGITS
        and any(c.isalpha() for c in token)
    )


#: Given names that are also English words written capitalised (a month, a
#: modal or a verb that opens a sentence): such a name is never an exact
#: reference, or «May I ask» pins every note about May.
#: ponytail: a short list; a person so named is still found by the `about`
#: seed and the dense seed. Upgrade: skip a name only where it opens a
#: sentence, if a live miss shows the list costs a real pin.
_COMMON_NAMES = _STOP | frozenset({
    "april", "august", "bill", "can", "frank", "grace", "hope", "jack", "joy", "june",
    "mark", "may", "might", "must", "pat", "rob", "rose", "shall", "sue", "summer",
})


def exact_terms_of(query: str, names: Sequence[str] = ()) -> list[ExactTerm]:
    """The question's exact references, in the order it says them:
    its URLs, handles and id-like tokens, matched in any case, and each of
    `names` (the names and aliases of persons this room lists) that the
    question contains as written, matched in that case (`cased`). The
    question and the names are NFKC-normalised first, so `Kit` written in
    full-width letters is `Kit`. A reference inside a longer one is dropped (`Kit` inside `Kit
    Chan`, a handle inside a URL), and so is a name shorter than two
    characters or one that is also a common word (`_COMMON_NAMES`). Nothing
    else: an ordinary word is never exact, however rare."""
    text = unicodedata.normalize("NFKC", query or "")
    found: list[tuple[int, ExactTerm]] = []
    for m in _URL.finditer(text):
        found.append((m.start(), ExactTerm(m.group(0).rstrip(_TRAILING))))
    for m in _HANDLE.finditer(text):
        found.append((m.start(), ExactTerm(m.group(0))))
    for m in _TOKEN.finditer(text):
        if _id_like(m.group(0)):
            found.append((m.start(), ExactTerm(m.group(0))))
    for name in dict.fromkeys(unicodedata.normalize("NFKC", n).strip() for n in names):
        if len(name) < 2 or name.lower() in _COMMON_NAMES:
            continue
        if exact_holds(name, text, cased=True):
            found.append((text.find(name), ExactTerm(name, cased=True)))
    terms = list(dict.fromkeys(t for _, t in sorted(found) if t.text))
    kept = [
        t for t in terms
        if not any(
            len(o.text) > len(t.text) and exact_holds(t.text, o.text, cased=t.cased)
            for o in terms
        )
    ]
    return kept[:_MAX_EXACT]


def _names(person: PersonRef) -> list[str]:
    """The names a person is pinned by: the name Nora calls them, their
    first name, their full name and every alias. Never the bare last name:
    a family name pins everything about the family, and a romanised one is
    often two letters."""
    full = " ".join(p for p in (person.first_name, person.last_name) if p)
    surname = person.last_name.strip()
    return [
        n for n in (person.name, person.first_name, full, *person.aliases)
        if n and n.strip() != surname
    ]


def terms_of(query: str) -> list[str]:
    """The question's search terms: its words of three or more letters that
    are not grammar, and each run of CJK characters whole (a Cantonese run is
    one term; pg_trgm's word similarity finds it inside a sentence)."""
    out: list[str] = []
    for m in _WORD.finditer(query or ""):
        word = m.group(0)
        if word.isascii():
            word = word.lower().removesuffix("'s")
            if len(word) < 3 or word in _STOP:
                continue
        out.append(word)
    return list(dict.fromkeys(out))[:_MAX_TERMS]


def scope_for(
    target: RenderRoom, *, social_off: Collection[str], own_room: bool = False,
) -> MemoryScope | None:
    """The reading room's scope, from the live policy (`target.rooms`) and
    its live roster (`target.live`); `None` for a room outside the policy,
    which reads nothing.

    `own_room` narrows it to the reading room alone: no other room of the
    unit and no unit-placed node (a note, memo or event of the unit may come
    from any of its rooms), so nothing said elsewhere is read. The culture
    adaptor's (DEC-316): a room's culture is written from that room's own
    speech. The listed-room and presence rules still apply; a stay or trip, which
    belongs to a person and to no room, is still read where its person
    passes presence here.

    `social_off` are the persons whose `identity.toml` block sets `social =
    false`: none of their social items is read (design/cognition-loop/22 §2),
    whenever it was recorded. Required with no default: a caller that
    forgot it would read every opted-out person's items."""
    here = target.room
    if here is None:
        return None
    unit_rooms = (
        [] if own_room else [r for r in target.rooms if here.unit and r.unit == here.unit]
    )
    if not any(r.platform == here.platform and r.ids == here.ids for r in unit_rooms):
        unit_rooms.append(here)
    room_persons = {
        room_key(r.platform, cid): r.persons or frozenset()
        for r in unit_rooms for cid in sorted(r.ids)
    }
    listed = here.persons or frozenset()
    live = target.live
    vouched = (
        here.persons is not None and live is not None and live.available
        and not live.degraded and live.unnamed == 0
    )
    presence = frozenset(
        p for p in listed
        if section_permits((LineageEntry(PRESENCE, subject=p),), target, origin=None)
    )
    return MemoryScope(
        units=(here.unit,) if here.unit and not own_room else (),
        rooms=tuple(room_persons),
        listed_here=listed,
        people_here=listed | (live.person_ids if live is not None else frozenset()),
        exclude_strict=not vouched,
        presence_persons=presence,
        room_persons=room_persons,
        social_off=frozenset(social_off),
    )


@dataclass(frozen=True, slots=True)
class Step:
    """One step of a path: the node's kind, and how it was reached from the
    step before (`None` on the seed). `how` is `matched` or `about` on a
    seed."""

    kind: str
    rel: str | None = None
    outgoing: bool = True
    how: str = ""


@dataclass(slots=True)
class Hit:
    """A node the walk kept. `pin` is non-zero on an exact seed only: it
    ranks ahead of every unpinned hit, the highest pin first."""

    node: IndexedNode
    score: float
    path: tuple[Step, ...]
    people: set[str] = field(default_factory=set)
    places: set[str] = field(default_factory=set)
    pin: int = 0


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    status: SearchStatus
    hits: tuple[Hit, ...] = ()


def _listed_room_rule(person: str, via: IndexedNode, scope: MemoryScope) -> bool:
    """An edge to `person` is followed only if this room lists them and the
    node it comes from was said in a room that lists them."""
    if person not in scope.listed_here:
        return False
    if via.origin_room is None:
        return True
    return person in scope.room_persons.get(via.origin_room, frozenset())


def _decay(node: IndexedNode, now_ms: int) -> float:
    if node.at_ms is None:
        return 0.5
    days = max(0.0, (now_ms - node.at_ms) / 86_400_000)
    half = SOCIAL_HALF_LIFE_DAYS if node.kind in SOCIAL_KINDS else HALF_LIFE_DAYS
    return math.pow(0.5, days / half)


async def walk(
    index: MemoryIndexRead, scope: MemoryScope, *, terms: Sequence[str],
    person_id: str | None, hops: int, admits: Callable[[IndexedNode], bool],
    exact: Sequence[ExactTerm] = (), vector: Sequence[float] | None = None,
    model: str | None = None, unembedded: bool = False, embedded: int = 0,
    metrics: Any = None, bridge: MemoryScope | None = None, query: str = "",
) -> list[Hit]:
    """Seed, then expand hop by hop (module docstring). `exact` are the
    literal references, the first `PINNED_LIMIT` of them pinned; with a
    `vector` (and its `model`) the dense seed runs, plus the lexical one on
    `terms` over the nodes holding no vector of `model` when `unembedded`
    (the scope holds some: waiting for one, or given up on); else the
    lexical one on `terms`. `embedded` is how many vectors the scope holds,
    so a dense scan that came back short is counted on `metrics`. `admits`
    is the per-row presence check an episode must pass (R-31); every other
    rule is the index's scope or this function's.

    `bridge` is `scope` without its `kinds`,
    given when kinds were asked. The seeds run on `scope` and then on
    `bridge`, so a node of an asked kind still seeds when a crowd of other
    kinds would outrank it, and a node of another kind can seed a path to
    one: a message reaches the outing it was said during, and an episode
    has no vector of its own. The hops run on `bridge`; the caller keeps
    only the asked kinds in the answer (`MemorySearch.search`). Dates and a
    person narrow every read of both, and only the first pass pins.

    `query` names places (`MemoryIndexRead.places`): each named place, and
    the places `located_in` it down `PLACE_DEPTH` levels, ANCHORS the walk
    (`_anchors`). An anchor is never a hit and never returned; the hops
    leave from it, so «near Mong Kok» reaches the event at a venue in Mong
    Kok and the outing located in it, each in scope or not reached."""
    reached: dict[tuple[str, str], Hit] = {}
    # A seed scored by its cosine has the cosine's floor; a lexical seed and
    # every hop keep the walk's (an episode has no vector: a hop reaches it).
    # Not inside a date window (the `DENSE_FLOOR` comment).
    only = scope.only
    windowed = only is not None and (only.since_ms is not None or only.until_ms is not None)
    dense = vector is not None and model is not None
    seed_floor = DENSE_FLOOR if dense and not windowed else RELEVANCE_FLOOR

    def keep(
        node: IndexedNode, score: float, path: tuple[Step, ...],
        least: float = RELEVANCE_FLOOR,
    ) -> bool:
        if node.key in reached or score < least:
            return False
        # Episodes only, not every `PRESENCE_KINDS`: `admits` reads a row's
        # presence lineage, and a social item's tagged place carries none (an
        # empty lineage is denied outside its origin room, and it has no
        # room), so it would never be kept. The index's scope already gates
        # it by presence.
        if node.kind in EPISODE_KINDS and not admits(node):
            return False
        reached[node.key] = Hit(node, score, path)
        return True

    for sc in (scope,) if bridge is None else (scope, bridge):
        first = sc is scope
        if exact:
            literal = await index.exact(exact, sc, limit=EXACT_LIMIT, vector=vector, model=model)
            for i, s in enumerate(literal):
                pinned = first and i < PINNED_LIMIT
                # Past the pinned ones, an exact hit competes on its cosine
                # like a dense hit; with no vector to compare, on its literal 1.
                score = s.score if pinned or s.near is None else s.near
                exact_seed = (Step(s.node.kind, how="exact"),)
                if keep(s.node, score, exact_seed, seed_floor) and pinned:
                    reached[s.node.key].pin = PINNED_LIMIT - i
        lexical: list[Seed] = []
        if vector is not None and model is not None:
            seeds = await index.dense(vector, sc, model=model, limit=SEED_LIMIT)
            if first and len(seeds) < min(SEED_LIMIT, embedded) and metrics is not None:
                metrics.inc(_DENSE_SHORT)
            if unembedded and terms:
                # The dense seed cannot reach a node with no vector yet (a
                # line said a moment ago) or one the worker gave up on; its
                # words still can, so it is not reported as «nothing». Its
                # score is lexical, so the lexical floor holds for it.
                lexical = await index.seed(terms, sc, limit=SEED_LIMIT, unembedded_of=model)
        else:
            seeds = await index.seed(terms, sc, limit=SEED_LIMIT) if terms else []
        for s in seeds:
            keep(s.node, s.score, (Step(s.node.kind, how="matched"),), seed_floor)
        for s in lexical:
            keep(s.node, s.score, (Step(s.node.kind, how="matched"),))
        if person_id and person_id in scope.listed_here:
            for node in await index.about(person_id, sc, limit=SEED_LIMIT):
                # The listed-room rule holds for an exact-reference seed too:
                # an entry about P said in a room that does not list P is not
                # evidence about P.
                if _listed_room_rule(person_id, node, scope):
                    keep(node, PERSON_SEED_SCORE, (Step(node.kind, how="about"),), seed_floor)
    hop_scope = bridge if bridge is not None else scope
    anchors = await _anchors(index, query, hop_scope) if query.strip() else {}

    frontier = [
        *sorted(reached.values(), key=lambda h: -h.score)[:FRONTIER_WIDTH],
        *anchors.values(),
    ]
    for _ in range(max(0, min(hops, HOP_CEILING))):
        if not frontier or len(reached) >= NODE_BUDGET:
            break
        found = await index.neighbours([h.node.key for h in frontier], hop_scope)
        added: list[Hit] = []
        for nb in sorted(found, key=lambda n: (n.via, n.rel, n.node.key)):
            parent = reached.get(nb.via) or anchors.get(nb.via)
            if parent is None:
                continue
            if nb.node.is_hub:
                # The hub rule: kept on the node that reached it, never
                # expanded from. A person only under the listed-room rule.
                if nb.node.kind == "person":
                    if _listed_room_rule(nb.node.source_id, parent.node, scope):
                        parent.people.add(nb.node.source_id)
                elif nb.node.kind == "place" and nb.node.text:
                    parent.places.add(nb.node.text)
                continue
            score = parent.score * REL_WEIGHT.get(nb.rel, _DEFAULT_WEIGHT)
            step = Step(nb.node.kind, nb.rel, nb.outgoing)
            if keep(nb.node, score, (*parent.path, step)):
                if parent.node.key in anchors and parent.node.text:
                    # Where it is: the place the walk reached it from.
                    reached[nb.node.key].places.add(parent.node.text)
                added.append(reached[nb.node.key])
                if len(reached) >= NODE_BUDGET:
                    break
        if not added:
            break  # adaptive: this hop found nothing above the floor
        frontier = sorted(added, key=lambda h: -h.score)[:FRONTIER_WIDTH]
    return list(reached.values())


async def _anchors(
    index: MemoryIndexRead, query: str, scope: MemoryScope,
) -> dict[tuple[str, str], Hit]:
    """The places `query` names, each at `PLACE_SEED_SCORE`, and the places
    `located_in` them, `PLACE_DEPTH` levels down, each a `located_in` weight
    below the place it lies in; at most `PLACE_LIMIT`, the named first.

    The one exception to the hub rule (DEC-313, amended for step 3a): a place
    hub is a hop's frontier here, and only INWARD, to what lies in it, never
    out to the city around it (that would widen «Mong Kok» to all of Kowloon)
    and never from a place a hop reached (that is a search on the place, not
    the question's). A hub carries no scope and an anchor is never returned:
    every node the hops reach from one passes the index's scope."""
    named = await index.places(query, limit=PLACE_LIMIT)
    level = [Hit(n, PLACE_SEED_SCORE, (Step(n.kind, how="named"),)) for n in named]
    anchors = {h.node.key: h for h in level}
    for _ in range(PLACE_DEPTH):
        if not level or len(anchors) >= PLACE_LIMIT:
            break
        found = await index.neighbours([h.node.key for h in level], scope)
        inner: list[Hit] = []
        for nb in sorted(found, key=lambda n: (n.via, n.node.key)):
            if (
                nb.rel != "located_in" or nb.outgoing or nb.node.kind != "place"
                or nb.node.key in anchors or len(anchors) >= PLACE_LIMIT
            ):
                continue
            parent = anchors[nb.via]
            hit = Hit(
                nb.node, parent.score * REL_WEIGHT["located_in"],
                (*parent.path, Step(nb.node.kind, nb.rel, nb.outgoing)),
            )
            anchors[hit.node.key] = hit
            inner.append(hit)
        level = inner
    return anchors


class MemorySearch:
    """`search` for one read. Takes the index and the metrics registry, and,
    while `[memory_index] embed`, the query embedder and the fingerprint of
    the model its vectors are stored under (`nora.infra.embedder.
    fingerprint`); without them every search is the lexical fallback.
    `drain_seconds` is the embedding worker's tick (`[memory_index]
    drain_seconds`); without it no wait is fresh, and every node waiting
    for a vector is a backlog (`partial`), the safe side."""

    def __init__(
        self, index: MemoryIndexRead, metrics: Any, *,
        embedder: QueryEmbedder | None = None, model: str | None = None,
        drain_seconds: float | None = None,
    ) -> None:
        if (embedder is None) != (model is None):
            raise ValueError("an embedder and its model fingerprint come together")
        self._index = index
        self._metrics = metrics
        self._embedder = embedder
        self._model = model
        self._fresh_seconds = BACKLOG_TICKS * drain_seconds if drain_seconds else 0.0

    def _count(self, status: SearchStatus) -> MemorySearchResult:
        self._metrics.inc(_METRIC, status=status)
        return MemorySearchResult(status)

    async def search(
        self, *, query: str, person_id: str | None, target: RenderRoom,
        depth: str = DEFAULT_DEPTH, now_ms: int, people: Sequence[PersonRef] = (),
        kinds: Sequence[str] = (), since_ms: int | None = None,
        until_ms: int | None = None, own_room: bool = False,
        social_off: Collection[str],
    ) -> MemorySearchResult:
        """`people` are the persons the caller can name here (the room's
        resolved roster); the names of those this room lists are the exact
        name references (`exact_terms_of`). `person_id` seeds the nodes about
        that person and narrows the search to them; `kinds` (node kinds),
        `since_ms` and `until_ms` narrow it too (the module docstring), all
        inside the index's reads. `own_room` reads the reading room alone
        (`scope_for`); `social_off` (required, never defaulted) are the
        persons whose social items no read may return."""
        def admits(node: IndexedNode) -> bool:
            lineage = Lineage.from_json(node.lineage)
            return section_permits(lineage.section(ROW), target, origin=None)

        try:
            scope = scope_for(target, own_room=own_room, social_off=social_off)
            if scope is None:
                return self._count("unavailable")
            only = MemoryFilter(
                kinds=tuple(dict.fromkeys(kinds)), since_ms=since_ms, until_ms=until_ms,
                person=person_id or None,
            )
            bridge: MemoryScope | None = None
            if only != MemoryFilter():
                scope = replace(scope, only=only)
                if only.kinds:
                    # The hops cross other kinds (`walk`); the answer does not.
                    bridge = replace(scope, only=replace(only, kinds=()))
            state = await self._index.index_state(
                model=self._model, scope=scope, fresh_seconds=self._fresh_seconds,
            )
            # The seed over unvectored nodes runs on the bridge pass too, so
            # it is wanted when the scope WITHOUT the kinds holds any.
            seeding = state if bridge is None else await self._index.index_state(
                model=self._model, scope=bridge,
            )
            names = [n for p in people if p.person_id in scope.listed_here for n in _names(p)]
            vector: list[float] | None = None
            # The lexical seed is the fallback: no embedder, or no node
            # holds a vector of this model yet while some wait for one.
            lexical = self._embedder is None or (state.embedded == 0 and state.unembedded > 0)
            if not lexical and self._embedder is not None and query.strip():
                try:
                    vector = await self._embedder.embed_query(query)
                except Exception as exc:  # noqa: BLE001 — any failure: the fallback
                    log.warning("memory search: no query vector (%s)", type(exc).__name__)
                    lexical = True
            terms = terms_of(query)
            hits = await walk(
                self._index, scope, terms=terms,
                person_id=person_id, hops=HOPS_BY_DEPTH.get(depth, 2), admits=admits,
                exact=exact_terms_of(query, names), vector=vector, model=self._model,
                unembedded=seeding.unembedded + seeding.given_up > 0, embedded=state.embedded,
                metrics=self._metrics,
                bridge=bridge, query=query,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed: «I couldn't check»
            log.warning("memory search failed (%s)", type(exc).__name__)
            return self._count("unavailable")
        wanted = frozenset(kinds)
        ranked = sorted(
            (h for h in hits if not wanted or h.node.kind in wanted),
            key=lambda h: (-h.pin, -h.score * _decay(h.node, now_ms), h.node.key),
        )[:RESULT_MAX]
        # A fresh node is read by its words; a backlog, or an unvectored node
        # (waiting or given up) a question with no terms cannot read, is what
        # the dense seed missed.
        backlog = state.unembedded - state.fresh
        unread = state.unembedded + state.given_up > 0 and not terms
        incomplete = (
            state.status != "complete" or state.queue > 0 or lexical
            or (vector is not None and (backlog > 0 or unread))
        )
        status: SearchStatus = (
            "partial" if incomplete else "ok" if ranked else "nothing_found"
        )
        self._metrics.inc(_METRIC, status=status)
        return MemorySearchResult(status, tuple(ranked))


def _path(path: Sequence[Step]) -> list[str]:
    """`["message (matched)", "<-cites- memo"]`: each step's kind and the
    relation that reached it, arrowed the way the edge points. No id."""
    out: list[str] = []
    for s in path:
        if s.rel is None:
            out.append(f"{s.kind} ({s.how})")
        else:
            out.append(f"-{s.rel}-> {s.kind}" if s.outgoing else f"<-{s.rel}- {s.kind}")
    return out


def _handle(node: IndexedNode) -> str | None:
    """What a person or a tool can name the item by: an attachment's media
    handle, an entry's title (its first line). A message, an episode, an
    account and a social item have none: their ids are not something said in
    a chat (R-18). Nor has a social item's medium: `media_fetch` resolves
    only this chat's media, so its handle would fail as if it were not
    there."""
    if node.kind == "media" and node.sha256:
        return node.sha256[:HANDLE_CHARS]
    if node.kind in ("note", "memo", "event"):
        return node.text.split("\n", 1)[0].strip() or None
    return None


def _shown(node: IndexedNode) -> tuple[str, str]:
    """An item's kind as the answer says it, and its text. A social item says
    what it is, its node text's first line (`social_label`: «Instagram
    story»), and the rest is its text; a medium of one says it is a picture
    or video of the network's, and its location that it is a tagged place.
    Every other kind is its kind."""
    if node.kind == "social":
        label, _, rest = node.text.partition("\n")
        return label or "social item", rest
    if node.kind == "social_media":
        network = node.source_id.partition(":")[0]
        return f"{network.capitalize()} picture or video", node.text
    if node.kind == "social_location":
        network = node.source_id.partition(":")[0]
        return f"{network.capitalize()} tagged place", node.text
    return node.kind, node.text


def render_packet(
    hits: Sequence[Hit], *, name_of: Callable[[str], str],
    room_label: Callable[[str], str], now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """The evidence packet and how many items were withheld
    for carrying an identifier. Names, room labels and handles only: never a
    person id, a room key or a row id.

    `hits` in rank order; the answer is cut here, where an item's size is
    known (the module docstring's step 5): a pinned hit is always kept, then
    each hit while the packet stays within `PACKET_TOKENS` and the unpinned
    hit before it is within `SCORE_GAP` of the best relevance from this hit
    on (`SCORE_GAP`), the first
    `MIN_RESULTS` items whatever either says, and never more than
    `RESULT_MAX`. A withheld hit costs nothing and is not counted."""
    items: list[dict[str, Any]] = []
    withheld = 0
    spent = 0
    last: float | None = None  # the previous unpinned hit's relevance
    # Screened WHOLE, before the cut: an identifier straddling `TEXT_CHARS`
    # would otherwise leave as a fragment the screen cannot recognise (R-18).
    # A withheld hit takes no part in the cut.
    screened = [bool(h.node.text and scan_identifiers(h.node.text) is not None) for h in hits]
    # The best relevance from each hit on, over the unpinned hits the screen
    # passes. The rank is age-decayed, so a recent weak hit can sit ahead of
    # an older strong one: the drop is read against the best still to come,
    # never against the next hit alone.
    ahead = [0.0] * (len(hits) + 1)
    for i in range(len(hits) - 1, -1, -1):
        h = hits[i]
        mine = h.score if not h.pin and not screened[i] else 0.0
        ahead[i] = max(mine, ahead[i + 1])
    for i, h in enumerate(hits):
        if len(items) >= RESULT_MAX:
            break
        if screened[i]:
            withheld += 1
            continue
        steep = not h.pin and last is not None and last - ahead[i] > SCORE_GAP
        if not h.pin and len(items) >= MIN_RESULTS and steep:
            break
        if not h.pin:
            last = h.score
        kind, body = _shown(h.node)
        text = render_file_links(body[:TEXT_CHARS]) if body else ""
        places = sorted(p for p in h.places if scan_identifiers(p) is None)
        item: dict[str, Any] = {"kind": kind}
        if h.node.at_ms is not None:
            item["age"] = stamp(h.node.at_ms, now)
        handle = _handle(h.node)
        if handle:
            item["handle"] = handle
        if h.node.origin_room:
            item["room"] = room_label(h.node.origin_room)
        item["text"] = text
        people = sorted({name_of(p) for p in h.people})
        if people:
            item["people"] = people
        if places:
            item["places"] = places
        item["path"] = _path(h.path)
        cost = estimate_tokens(json.dumps(item, ensure_ascii=False))
        if not h.pin and len(items) >= MIN_RESULTS and spent + cost > PACKET_TOKENS:
            break
        spent += cost
        items.append(item)
    return items, withheld
