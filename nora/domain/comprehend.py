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
                        persons=scope.persons,
                        depth=1,
                        nodes=6,
                        fan_out=3,
                        valid_at=work.as_of,
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
    return Preview(tuple(seeds), nearby, tuple(resolved), window)


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
    "schema", "identity_pointer", "private_web_evidence", "ungrounded", "already_staged"
]
ITEM_OUTCOMES = get_args(ItemOutcome)
LANES = get_args(Lane)
PARTS = get_args(Part)
LOOKUPS = get_args(Lookup)
EGRESS_REASONS = get_args(EgressReason)
REFUSAL_REASONS = (*get_args(RefusalReason), *CLAIM_DROP_REASONS, *get_args(ExtraRefusal))
