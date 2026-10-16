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
  rest rank by their cosine, so the other half of the answer always goes by
  score. Ordinary words are never exact.
- **Dense**: the question embedded ONCE with the model's query instruction,
  and the nearest in-scope nodes of the configured model's vectors, scored by
  their cosine (`SqlMemoryIndex.dense`). A scan that comes back with fewer
  than the scope holds is counted (`nora_memory_dense_short_total`).
- **Lexical**, by the question's terms (`terms_of`), when there is no query
  vector: `[memory_index] embed` is off, no node in scope holds a vector of
  the current model yet, or the embedder failed. The answer is then
  `partial`. Beside a dense seed, it reads only the nodes the worker gave up
  on, which no vector reaches.
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
  on the person, not an expansion of the question.
- **The listed-room rule**: an edge to person P is followed only if
  the reading room lists P and, when the node it comes from was said in a
  room, that room lists P too. Talk about Kit in a room Kit is not in is not
  evidence about Kit.

Each step multiplies the parent's relevance by the relation's weight
(`REL_WEIGHT`). A node below `RELEVANCE_FLOOR` is not kept, and the walk
stops early when a hop keeps nothing (adaptive depth), at `NODE_BUDGET`
nodes, or at the hop count `depth` asks for, never past `HOP_CEILING`.

**4. Rank** the pinned exact hits first, then the rest, each by relevance
times age decay (`HALF_LIFE_DAYS`). **5. Return** the
best `RESULT_LIMIT` items, each with its kind, age, a handle where the kind
has one a person or a tool can use, its text, and its path; an item whose
text carries an identifier is withheld whole (`scan_identifiers`, R-18).

**Statuses** (R-27): `unavailable` on any error, counted, never an
empty answer; `partial`, whatever was found, when the index is not
`complete`, its projection queue is not empty, the seeds were lexical (the
fallback above), or live embeddable nodes IN THIS SEARCH'S SCOPE still wait
for a vector of the current model (a node the worker gave up on does
not count, or one poisoned file would hold every answer `partial` for good,
and the lexical seed reads it instead; nor does an attachment that failed
as `empty`, which waits for its reading), so an unfinished index is never
«you never said that»; `nothing_found` only from a complete index; `ok`.

The constants below are starting values, not tuned against a measured
distribution.
"""

from __future__ import annotations

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
    ATTACHMENT_KINDS,
    EPISODE_KINDS,
    ExactTerm,
    IndexedNode,
    MemoryFilter,
    MemoryIndexRead,
    MemoryScope,
    QueryEmbedder,
    SearchStatus,
    exact_holds,
)
from nora.domain.models import PersonRef
from nora.domain.prompt import stamp
from nora.domain.rollup import room_key

__all__ = [
    "DEFAULT_DEPTH",
    "EXACT_LIMIT",
    "HOPS_BY_DEPTH",
    "HOP_CEILING",
    "PINNED_LIMIT",
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
#: A node whose relevance falls below this is not kept; a hop that keeps
#: none ends the walk. Starting value: a lexical seed scores at least
#: `WORD_SIMILARITY_MIN` (0.5), so this admits about four weak hops from the
#: weakest seed and stops the walk well before noise dominates.
#: The same floor serves dense seeds, whose relevance is their full-width
#: cosine, on the same [0, 1] scale as a lexical score (a negative cosine is
#: below it): a seed under 0.1 points nearly orthogonally to the question
#: and is dropped, and a middling one (0.3) still takes the default two hops
#: through a `cites` or an `about`. What the floor does NOT do is make a
#: dense search answer `nothing_found` when nothing relevant exists: the
#: nearest vectors are returned with their low cosines. Raising it needs
#: the cosine distribution of real hits and misses; a floor above a true hit's cosine would turn
#: «found» into «you never said that», the worse error.
RELEVANCE_FLOOR = 0.1
#: Nodes kept per search, seeds included.
NODE_BUDGET = 60
#: Nodes expanded per hop: the best of the last hop's.
FRONTIER_WIDTH = 20
SEED_LIMIT = 20
#: Exact seeds per search. Fewer than `SEED_LIMIT` so a question
#: naming a much-discussed person still has dense seeds to expand from.
EXACT_LIMIT = 10
RESULT_LIMIT = 10
#: Exact hits pinned ahead of every other hit: the first of them, the most
#: terms held and then the nearest to the question. Half the answer, so a
#: literal name still never loses to a near-duplicate while the other half
#: always goes by score: were every exact hit pinned, ten literal matches
#: would fill every slot and even a dense hit at cosine 1.0 would never show.
#: An exact hit past these ranks
#: by its cosine to the question, as a dense hit does.
PINNED_LIMIT = RESULT_LIMIT // 2
#: An exact-reference seed (a node `about` the named person) has no lexical
#: score of its own; it ranks as a middling lexical hit.
PERSON_SEED_SCORE = 0.6
#: Age decay: an item this many days old ranks at half its relevance.
HALF_LIFE_DAYS = 180.0
#: Characters of one item's text in the packet.
TEXT_CHARS = 600

#: How much of the parent's relevance a relation carries. Provenance relations carry
#: most; co-occurrence least. A relation absent here carries `_DEFAULT_WEIGHT`.
REL_WEIGHT: Mapping[str, float] = {
    "cites": 0.9, "attached_to": 0.9, "supersedes": 0.8, "tracks": 0.8,
    "closes_into": 0.8, "leaves_behind": 0.8, "draws_on": 0.7, "informed_by": 0.7,
    "disputes": 0.8, "feedback_on": 0.7, "about": 0.6, "during": 0.5,
    "outcome_of": 0.6, "mentions": 0.5, "in_room": 0.3,
}
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
    target: RenderRoom, *, own_room: bool = False, social_off: Collection[str] = (),
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
    whenever it was recorded."""
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


def _decay(at_ms: int | None, now_ms: int) -> float:
    if at_ms is None:
        return 0.5
    days = max(0.0, (now_ms - at_ms) / 86_400_000)
    return math.pow(0.5, days / HALF_LIFE_DAYS)


async def walk(
    index: MemoryIndexRead, scope: MemoryScope, *, terms: Sequence[str],
    person_id: str | None, hops: int, admits: Callable[[IndexedNode], bool],
    exact: Sequence[ExactTerm] = (), vector: Sequence[float] | None = None,
    model: str | None = None, given_up: bool = False, embedded: int = 0,
    metrics: Any = None, bridge: MemoryScope | None = None,
) -> list[Hit]:
    """Seed, then expand hop by hop (module docstring). `exact` are the
    literal references, the first `PINNED_LIMIT` of them pinned; with a
    `vector` (and its `model`) the dense seed runs, plus the lexical one on
    `terms` over the nodes the worker gave up on when `given_up`; else the
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
    person narrow every read of both, and only the first pass pins."""
    reached: dict[tuple[str, str], Hit] = {}

    def keep(node: IndexedNode, score: float, path: tuple[Step, ...]) -> bool:
        if node.key in reached or score < RELEVANCE_FLOOR:
            return False
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
                if keep(s.node, score, (Step(s.node.kind, how="exact"),)) and pinned:
                    reached[s.node.key].pin = PINNED_LIMIT - i
        if vector is not None and model is not None:
            seeds = await index.dense(vector, sc, model=model, limit=SEED_LIMIT)
            if first and len(seeds) < min(SEED_LIMIT, embedded) and metrics is not None:
                metrics.inc(_DENSE_SHORT)
            if given_up and terms:
                # The dense seed cannot reach a node the worker gave up on;
                # its words still can, so it is not reported as «nothing».)
                seeds += await index.seed(terms, sc, limit=SEED_LIMIT, given_up_of=model)
        else:
            seeds = await index.seed(terms, sc, limit=SEED_LIMIT) if terms else []
        for s in seeds:
            keep(s.node, s.score, (Step(s.node.kind, how="matched"),))
        if person_id and person_id in scope.listed_here:
            for node in await index.about(person_id, sc, limit=SEED_LIMIT):
                # The listed-room rule holds for an exact-reference seed too:
                # an entry about P said in a room that does not list P is not
                # evidence about P.
                if _listed_room_rule(person_id, node, scope):
                    keep(node, PERSON_SEED_SCORE, (Step(node.kind, how="about"),))
    hop_scope = bridge if bridge is not None else scope

    frontier = sorted(reached.values(), key=lambda h: -h.score)[:FRONTIER_WIDTH]
    for _ in range(max(0, min(hops, HOP_CEILING))):
        if not frontier or len(reached) >= NODE_BUDGET:
            break
        found = await index.neighbours([h.node.key for h in frontier], hop_scope)
        added: list[Hit] = []
        for nb in sorted(found, key=lambda n: (n.via, n.rel, n.node.key)):
            parent = reached.get(nb.via)
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
                added.append(reached[nb.node.key])
                if len(reached) >= NODE_BUDGET:
                    break
        if not added:
            break  # adaptive: this hop found nothing above the floor
        frontier = sorted(added, key=lambda h: -h.score)[:FRONTIER_WIDTH]
    return list(reached.values())


class MemorySearch:
    """`search` for one read. Takes the index and the metrics registry, and,
    while `[memory_index] embed`, the query embedder and the fingerprint of
    the model its vectors are stored under (`nora.infra.embedder.
    fingerprint`); without them every search is the lexical fallback."""

    def __init__(
        self, index: MemoryIndexRead, metrics: Any, *,
        embedder: QueryEmbedder | None = None, model: str | None = None,
    ) -> None:
        if (embedder is None) != (model is None):
            raise ValueError("an embedder and its model fingerprint come together")
        self._index = index
        self._metrics = metrics
        self._embedder = embedder
        self._model = model

    def _count(self, status: SearchStatus) -> MemorySearchResult:
        self._metrics.inc(_METRIC, status=status)
        return MemorySearchResult(status)

    async def search(
        self, *, query: str, person_id: str | None, target: RenderRoom,
        depth: str = DEFAULT_DEPTH, now_ms: int, people: Sequence[PersonRef] = (),
        kinds: Sequence[str] = (), since_ms: int | None = None,
        until_ms: int | None = None, own_room: bool = False,
        social_off: Collection[str] = (),
    ) -> MemorySearchResult:
        """`people` are the persons the caller can name here (the room's
        resolved roster); the names of those this room lists are the exact
        name references (`exact_terms_of`). `person_id` seeds the nodes about
        that person and narrows the search to them; `kinds` (node kinds),
        `since_ms` and `until_ms` narrow it too (the module docstring), all
        inside the index's reads. `own_room` reads the reading room alone
        (`scope_for`); `social_off` are the persons whose social items no read
        may return."""
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
            state = await self._index.index_state(model=self._model, scope=scope)
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
            hits = await walk(
                self._index, scope, terms=terms_of(query),
                person_id=person_id, hops=HOPS_BY_DEPTH.get(depth, 2), admits=admits,
                exact=exact_terms_of(query, names), vector=vector, model=self._model,
                given_up=state.given_up > 0, embedded=state.embedded, metrics=self._metrics,
                bridge=bridge,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed: «I couldn't check»
            log.warning("memory search failed (%s)", type(exc).__name__)
            return self._count("unavailable")
        wanted = frozenset(kinds)
        ranked = sorted(
            (h for h in hits if not wanted or h.node.kind in wanted),
            key=lambda h: (-h.pin, -h.score * _decay(h.node.at_ms, now_ms), h.node.key),
        )[:RESULT_LIMIT]
        incomplete = (
            state.status != "complete" or state.queue > 0 or lexical
            or (vector is not None and state.unembedded > 0)
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
    """What a person or a tool can name the item by: a medium's handle (an
    attachment's or a social item's), an entry's title (its first line). A
    message, an episode, an account and a social item have none: their ids
    are not something said in a chat (R-18)."""
    if node.kind in ATTACHMENT_KINDS and node.sha256:
        return node.sha256[:HANDLE_CHARS]
    if node.kind in ("note", "memo", "event"):
        return node.text.split("\n", 1)[0].strip() or None
    return None


def _shown(node: IndexedNode) -> tuple[str, str]:
    """An item's kind as the answer says it, and its text. A social item says
    what it is, its node text's first line (`social_label`: «Instagram
    story»), and the rest is its text; a medium of one says it is a picture
    or video of the network's. Every other kind is its kind."""
    if node.kind == "social":
        label, _, rest = node.text.partition("\n")
        return label or "social item", rest
    if node.kind == "social_media":
        network = node.source_id.partition(":")[0]
        return f"{network.capitalize()} picture or video", node.text
    return node.kind, node.text


def render_packet(
    hits: Sequence[Hit], *, name_of: Callable[[str], str],
    room_label: Callable[[str], str], now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """The evidence packet and how many items were withheld
    for carrying an identifier. Names, room labels and handles only: never a
    person id, a room key or a row id."""
    items: list[dict[str, Any]] = []
    withheld = 0
    for h in hits:
        # Screened WHOLE, before the cut: an identifier straddling
        # `TEXT_CHARS` would otherwise leave as a fragment the screen cannot
        # recognise (R-18).
        if h.node.text and scan_identifiers(h.node.text) is not None:
            withheld += 1
            continue
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
        items.append(item)
    return items, withheld
