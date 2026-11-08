"""Step one's frozen input, graph preview and code-rendered card facts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from nora.domain.extraction import CLAIM_DROP_REASONS
from nora.domain.memory_graph import (
    PROPOSED_KINDS,
    Key,
    MemoryGraph,
    RefusalReason,
    Source,
    Subgraph,
    Walk,
    WorkItem,
    pointer_entity,
)


@dataclass(frozen=True)
class ComprehendItem:
    work: WorkItem
    sources: tuple[Source, ...]
    platform: str
    room: str
    author: str = ""
    mentions: tuple[str, ...] = ()
    reply_parent: Key | None = None
    urls: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    faces: tuple[str, ...] = ()
    place: str = ""
    response_closed: bool = False
    # The extraction validator's numeric source slots map to these real keys.
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.sources)


@dataclass(frozen=True)
class Preview:
    seeds: tuple[Key, ...]
    graph: Subgraph
    candidates: tuple[Subgraph, ...]
    window: tuple[datetime, datetime]


def item_window(text: str, at: datetime) -> tuple[datetime, datetime]:
    """Resolve day names against the evidence clock, never the process clock."""
    local = at.astimezone(ZoneInfo("Asia/Hong_Kong"))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    lower = text.casefold()
    for weekday, name in enumerate(names):
        if name in lower:
            offset = (weekday - day.weekday()) % 7
            if f"last {name}" in lower:
                offset = -((day.weekday() - weekday) % 7 or 7)
            day += timedelta(days=offset)
            break
    else:
        if re.search(r"\b(tomorrow)\b|聽日|明天", lower):
            day += timedelta(days=1)
        elif re.search(r"\b(yesterday)\b|尋日|昨日", lower):
            day -= timedelta(days=1)
    return day, day + timedelta(days=1)


class WithheldSourceError(LookupError):
    """A source whose own recorded row says it is gone or hidden: deliberately unread.

    Distinct from a projection that has not landed yet (`NoResultFound`), which
    fails the wake so it retries; a withheld source is skipped and never read."""


def preview_seeds(item: ComprehendItem, aliases: Mapping[str, Key]) -> tuple[Key, ...]:
    """Structural and explicit anchors shared by automatic graph readers."""
    work = item.work
    text = item.text.casefold()
    seeds = [work.source, *(s.key for s in item.sources)]
    for kind, refs in (
        ("person", (item.author, *item.mentions, *item.faces)),
        ("room", (f"{item.platform}:{item.room}",)),
        ("unit", (work.unit,)),
    ):
        seeds.extend(pointer_entity(kind, work.unit, ref, ref).key for ref in refs if ref)
    if item.reply_parent:
        seeds.append(item.reply_parent)
    named = (*item.urls, *item.accounts, item.place)
    seeds.extend(
        key
        for alias, key in aliases.items()
        if alias and (alias.casefold() in text or alias in named)
    )
    return tuple(dict.fromkeys(seeds))


async def build_preview(graph: MemoryGraph, item: ComprehendItem) -> Preview:
    work = item.work
    aliases = await graph.aliases(unit=work.unit)
    seeds = preview_seeds(item, aliases)
    window = item_window(item.text, work.as_of)
    scope = Walk(
        unit=work.unit,
        persons=frozenset(p for s in item.sources for p in s.persons),
        depth=2,
        nodes=100,
        fan_out=20,
        layers=("record", "rollup", "notebook"),
        valid_at=work.as_of,
    )
    nearby = await graph.walk(tuple(seeds), scope)
    if nearby.status == "unavailable":
        raise RuntimeError("graph preview unavailable")
    resolved = await resolution_candidates(graph, item, aliases, seeds, window)
    return Preview(tuple(seeds), nearby, resolved, window)


async def resolution_candidates(
    graph: MemoryGraph, item: ComprehendItem, aliases: Mapping[str, Key],
    seeds: tuple[Key, ...], window: tuple[datetime, datetime],
) -> tuple[Subgraph, ...]:
    work = item.work
    text = item.text.casefold()
    named = (*item.urls, *item.accounts, item.place)
    resolved = []
    # ponytail: known aliases and explicit item labels seed resolution; novel names
    # remain the model's memory tools' job until the recall spike measures a gap.
    for name in dict.fromkeys(
        [a for a in aliases if a and a.casefold() in text] + [n for n in named if n]
    ):
        for kind in PROPOSED_KINDS:
            choices = await graph.candidates(
                unit=work.unit, kind=kind, name=name, near=seeds[0], window=window, limit=5
            )
            for entity in choices[:5]:
                candidate = await graph.walk(
                    (entity.key,),
                    Walk(
                        unit=work.unit,
                        persons=frozenset(p for s in item.sources for p in s.persons),
                        depth=1,
                        nodes=6,
                        fan_out=3,
                        valid_at=work.as_of,
                        known_at=work.as_of if work.historical else None,
                    ),
                )
                if candidate.status == "unavailable":
                    raise RuntimeError("resolution candidates unavailable")
                strongest = tuple(
                    sorted(
                        candidate.relations,
                        key=lambda e: (e.evidence_count, e.relation.asserted_at),
                        reverse=True,
                    )[:3]
                )
                resolved.append(
                    replace(
                        candidate,
                        relations=strongest,
                        status="partial" if len(candidate.relations) > 3 else candidate.status,
                    )
                )
    return tuple(resolved)


def card_facts(graph: Subgraph, entity: Key, *, max_chars: int = 1600) -> str:
    """FACTS only, from current relations; no model and no prompt wiring."""
    labels = {n.key: n.entity.name if n.entity else n.key[1] for n in graph.nodes}
    edges = sorted(
        (
            e
            for e in graph.relations
            if e.relation.status == "current"
            and e.relation.valid_to is None
            and entity in (e.relation.src, e.relation.dst)
        ),
        key=lambda e: (e.relation.asserted_at, e.evidence_count),
        reverse=True,
    )
    lines: list[str] = []
    for edge in edges:
        r = edge.relation
        line = (
            f"{labels.get(r.src, r.src[1])} {r.rel} {labels.get(r.dst, r.dst[1])} "
            f"[{r.asserted_at.isoformat()}; evidence={edge.evidence_count}]"
        )
        if sum(len(x) + 1 for x in lines) + len(line) > max_chars:
            continue
        lines.append(line)
    return "\n".join(lines)


ItemOutcome = Literal["settled", "failed"]
Lane = Literal["live", "historical"]
Part = Literal["write", "reading", "profile", "entity", "relation", "evidence", "correction"]
Lookup = Literal["web_search", "web_fetch"]
EgressReason = Literal["private_person", "identifier", "coordinate", "lookup_cap", "bad_arguments"]
ExtraRefusal = Literal[
    "schema", "identity_pointer", "private_web_evidence", "ungrounded",
    "already_staged", "unshown", "endpoint_not_a_handle", "unparseable_time",
    "invalid_relation", "unexpected_replacement",
]
ITEM_OUTCOMES = get_args(ItemOutcome)
LANES = get_args(Lane)
PARTS = get_args(Part)
LOOKUPS = get_args(Lookup)
EGRESS_REASONS = get_args(EgressReason)
REFUSAL_REASONS = (*get_args(RefusalReason), *CLAIM_DROP_REASONS, *get_args(ExtraRefusal))

# Each primary refusal reason's rejected field and the one repair that answers
# it — what the proposal tool returns so a refused statement can be fixed
# rather than re-guessed. Keyed on the same vocabulary the checks refuse
# with: a reason outside this map must not reach the model without its repair.
REFUSAL_REPAIR: dict[str, dict[str, str]] = {
    # A reading dropped by the provenance checks.
    "citation_outside_batch": {
        "field": "cite",
        "repair": "Cite rendered `n` rows of the items named in this call's "
                  "`items`; never a hidden or context-only row.",
    },
    "duplicate_citation": {
        "field": "cite",
        "repair": "Keep each cite.source once.",
    },
    "evidence_not_covering_citations": {
        "field": "cite",
        "repair": "Every cited row needs a quote from its body; remove "
                  "unsupported citations or supply their quotes.",
    },
    "evidence_absent": {
        "field": "cite.quote",
        "repair": "Copy cite.quote exactly from that row's shown body — "
                  "never a paraphrase, a header or a clipped tail.",
    },
    "unknown_subject": {
        "field": "about.person_id",
        "repair": "Use a person_id shown as an author or mention in these "
                  "rows; otherwise set person_id to null and keep reference.",
    },
    "event_reference_outside_citations": {
        "field": "happened_at.source",
        "repair": "Set happened_at.source to one of this statement's "
                  "cite.source rows — the row carrying its time words.",
    },
    "event_expression_absent": {
        "field": "happened_at.expression",
        "repair": "Set happened_at.expression to exact time words from the "
                  "body of happened_at.source, or null when it has none; "
                  "never copy `at` or compute a date.",
    },
    "place_absent": {
        "field": "where",
        "repair": "Copy where exactly from a cited row's body, or null when "
                  "no place was stated; do not expand it to a canonical "
                  "place name.",
    },
    # A proposal refused before or at admission.
    "vocabulary": {
        "field": "rel",
        "repair": "Use an admitted relation name or entity kind exactly as "
                  "listed; a name outside them is refused.",
    },
    "wrong_ends": {
        "field": "subject/object",
        "repair": "That verb does not join these endpoint kinds; check the "
                  "kinds its rule admits for subject and object.",
    },
    "wrong_layer": {
        "field": "rel",
        "repair": "That relation name belongs to another layer; only "
                  "notebook relations may be proposed here.",
    },
    "missing_evidence": {
        "field": "cite",
        "repair": "A relation needs at least one citation; cite the rows "
                  "that establish it.",
    },
    "unseen_source": {
        "field": "cite.source",
        "repair": "Cite only rows the agenda rendered for the named items, "
                  "or rows a read tool handed this session.",
    },
    "notebook_evidence": {
        "field": "cite.source",
        "repair": "Cite a record or rollup row; a notebook row cannot be "
                  "evidence.",
    },
    "span_not_in_source": {
        "field": "cite.quote",
        "repair": "Copy the quote exactly from the cited row's shown body.",
    },
    "scope_wider_than_evidence": {
        "field": "cite",
        "repair": "Every person the statement names must appear in its cited "
                  "rows; cite rows that carry them or drop the person.",
    },
    "orphan_entity": {
        "field": "object",
        "repair": "A new entity must be the end of an admitted relation; "
                  "relate it or drop it.",
    },
    "duplicate_entity": {
        "field": "object",
        "repair": "That entity already exists; name the shown candidate's "
                  "handle instead of creating it.",
    },
    "duplicate_relation": {
        "field": "rel",
        "repair": "That relation is already held; propose a correction on "
                  "the existing one instead of restating it.",
    },
    "unknown_relation": {
        "field": "relation",
        "repair": "Name a held relation by the id this session showed.",
    },
    "not_current": {
        "field": "relation",
        "repair": "Only a current relation can be corrected; that one is "
                  "already closed.",
    },
    "orphan_evidence": {
        "field": "cite",
        "repair": "Evidence must attach to an admitted statement; cite it on "
                  "the statement it supports.",
    },
    "missing_replacement": {
        "field": "replacement",
        "repair": "A supersede carries its replacement relation statement; "
                  "supply it.",
    },
    "schema": {
        "field": "statement",
        "repair": "Fix the named field's shape and resend the statement; "
                  "the errors list names the path.",
    },
    "identity_pointer": {
        "field": "new.kind",
        "repair": "A person, room, unit or the agent is an identity pointer; "
                  "name its @kind:id handle, never a new entity.",
    },
    "private_web_evidence": {
        "field": "cite",
        "repair": "A web page never establishes anything about a private "
                  "person; drop the person end or cite what a person said.",
    },
    "ungrounded": {
        "field": "statement",
        "repair": "The statement established nothing code could admit; "
                  "ground every field in the cited rows or withdraw it.",
    },
    "already_staged": {
        "field": "statement",
        "repair": "That statement is already staged in this session; do not "
                  "resend it.",
    },
    "unshown": {
        "field": "subject/object",
        "repair": "Name only handles and relation ids this session showed: "
                  "a preview row, a read result or an admitted write.",
    },
    "endpoint_not_a_handle": {
        "field": "subject/object",
        "repair": "Use a shown @kind:id handle, e.g. @person:<a shown person "
                  "id>, or {new:{kind,name,...}} for a new non-person entity.",
    },
    "unparseable_time": {
        "field": "valid_from/valid_to/at",
        "repair": "Write an ISO date or datetime, or null.",
    },
    "invalid_relation": {
        "field": "subject/object",
        "repair": "The endpoints do not fit the relation's admitted shape; "
                  "check each end's kind.",
    },
    "unexpected_replacement": {
        "field": "replacement",
        "repair": "Only a supersede carries a replacement; remove it or "
                  "change the correction to supersede.",
    },
}

#: An attribute part carries its own attribute name and cite and ONE generic
#: message: whether the attribute saved cannot be read back reliably from the
#: admission result, so the message claims nothing about saved state.
ATTRIBUTE_PART_REPAIR = (
    "An attribute of this statement was refused for {reason}. Read the "
    "connected state before acting on it; re-sending a statement whose "
    "relation was saved is refused."
)
