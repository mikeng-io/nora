"""`NoteEntityResolutionAdaptor` — the note tier's judgement half of entity
resolution (21 §5, issue #100), on the shared `fact_entity` table. It was
written as a mirror of the knowledge tier's pass 6 (`FactEntityResolutionAdaptor`);
that tier was deleted on 2026-09-07 (issue #115) and this is now the table's
only judgement pass. The shape is unchanged.

**Two halves.** The
deterministic half runs in the sink, on every note create/propose —
`nora.infra.sql.notes.SqlNoteStore._resolve_names`, exact match after
normalisation against `fact_entity`'s canonical name and aliases, free, no
model — and handles the overwhelming majority of anchor-entity strings for
free. What reaches this adaptor is what exact matching could not settle: an
abbreviation, a spelling variant, a synonym the trigram channel found but
nothing could prove.

**Why this was a SEPARATE adaptor rather than the knowledge tier's reused
directly** (recorded because the reasoning outlives the sibling). Both read
and wrote the same `fact_entity` table through the same `SqlEntityStore`, but
they read DIFFERENT unresolved-name queues — its `unresolved_names` scanned
`fact_candidate`'s pending subjects; this reads
`unresolved_note_anchor_names`, which scans standing notes' own
`anchor_entity_ids`. Folding the two into one class would make "how often
does the NOTE tier's judgement pass fire" and "how often does the FACT tier's"
unanswerable from either counter alone, and would give one adaptor two
unrelated triggers to reason about. A shared HELPER would be the wrong
abstraction for two methods this thin; the shape is what is shared, not the
code.

**Its only write is attaching an ALIAS, never merging two entity rows** — the
identical irreversibility argument (21 §5): two entities wrongly kept apart
is a missing edge, fixable later by a later merge; two wrongly merged is a
corrupted node that has already absorbed both sets of edges, and unmerging
needs provenance nothing here has. So the merge-worthy operation is not
discouraged, it is ABSENT from this adaptor's surface entirely, the same as
its fact-tier sibling.

**Workspace-keyed and watermark-free**: its input is the entity table and
the note tier's own anchor strings, not a
message batch.

**Tool-free.** Deciding whether two names mean one thing is a judgement about
the two names, not research.

**Gate: rides `[notes] curator_enabled`, not a fourth flag of its own.** The
note tier already has three gates (`enabled`, `curator_enabled`,
`candidates_enabled`), each earned by a DIFFERENT risk an operator turning on
`[notes]` did not thereby sign up for: the tag path only reads, the curator
merges and retires, the create path writes new notes. This adaptor's own risk
is lower than any of those three — it never touches a note's content, never
creates a note, and never merges an entity row; its only effect is an alias
attached to the shared registry, which is by construction reversible (delete
the alias) and invisible to the room. What it is NOT is independently
useful: its sole consumer is `nora.domain.notes.group_on_anchor_key`'s merge
key, read by the CURATOR. An operator who has not turned the curator on has
no use for a background pass computing aliases nobody will read — the exact
"declared with no call site" shape CLAUDE.md warns against, one layer up from
a metric. Riding `curator_enabled` means the judgement pass runs exactly when
its output can matter, with no knob whose only legal value is "match whatever
`curator_enabled` already says". The knowledge tier's pass 6 set the
precedent the other direction — it had NO separate gate at all and rode that
tier's single enabled flag, because that tier had no split-risk gates to
begin with; this note mirrors the PRINCIPLE (ride the flag that gates the
consumer) rather than the LITERAL flag name, since the note tier's
consumer-shaped flag is `curator_enabled`, not `enabled`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from nora.domain.models import TranscriptRow
from nora.domain.ports import Proposal
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

#: Per-tick caps, inherited from the knowledge tier's pass 6: cheap enough
#: that a quiet workspace costs nothing and a busy one drains over a few
#: ticks rather than in one model call.
DEFAULT_MAX_RESOLUTIONS_PER_TICK = 8
DEFAULT_MAX_CANDIDATES_PER_NAME = 5

#: Two disjoint alphabets so a NAME handle and an ENTITY handle can never be
#: confused with each other. A handle is what the model answers WITH, and a
#: shared alphabet would let a name's digit be read as an entity's letter.
_NAME_HANDLES = "123456789"
_ENTITY_HANDLES = "abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class NoteEntityResolution:
    """One decision: this anchor-entity string IS another way of writing that
    entity. Only the positive case is a value, for `EntityResolution`'s own
    reason — a `null` verdict produces nothing, because the name simply stays
    unresolved, which is the state it was already in."""

    name: str
    entity_id: str


@dataclass(frozen=True)
class _Offered:
    """What ONE resolution run put in front of the model. `entity_ids` maps a
    LETTER to a real `fact_entity.id`; the id never appears in the prompt, so
    the model cannot echo one back for `dream_screen` to have to drop."""

    names: dict[str, str]
    entity_ids: dict[str, dict[str, str]]


class _NoteEntityResolver(Protocol):
    """The three verbs this adaptor needs off `SqlEntityStore`, narrowed on
    purpose: this adaptor never promotes and never creates an entity, so
    neither verb is in the shape it is handed. `unresolved_note_anchor_names`
    is the note tier's own source query (a note's `anchor_entity_ids`, not a
    claim queue)."""

    async def unresolved_note_anchor_names(
        self, *, workspace: str, limit: int,
    ) -> tuple[str, ...]: ...

    async def similar_entities(
        self, *, workspace: str, name: str, limit: int,
    ) -> tuple[tuple[str, str], ...]: ...

    async def attach_alias(
        self, *, workspace: str, entity_id: str, alias: str,
    ) -> bool: ...


class NoteEntityResolutionAdaptor:
    """The note tier's judgement pass — see the module docstring."""

    name = "note-entity-resolution"
    key_kind = "workspace"
    uses_watermark = False
    objective = "note-entity-resolution"
    envelope_key = "resolutions"
    tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        entities: _NoteEntityResolver,
        max_resolutions_per_tick: int = DEFAULT_MAX_RESOLUTIONS_PER_TICK,
        max_candidates_per_name: int = DEFAULT_MAX_CANDIDATES_PER_NAME,
        metrics: Metrics | None = None,
    ) -> None:
        self._entities = entities
        self._max_resolutions = max_resolutions_per_tick
        self._max_candidates = max_candidates_per_name
        self._metrics = metrics
        self._offered: dict[str, _Offered] = {}

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The unresolved anchor-entity strings, each under the entities they
        might be another way of writing. `batch` is unused — this adaptor is
        workspace-keyed and reads the store, not a message window.

        **Blank when nothing is unresolved, or when nothing resembles
        anything**: the runtime skips the model on a blank input, and a name
        with no near-misses is not offered at all, because there is nothing
        for the model to choose between.
        """
        names = await self._entities.unresolved_note_anchor_names(
            workspace=key, limit=self._max_resolutions,
        )
        offered_names: dict[str, str] = {}
        offered_entities: dict[str, dict[str, str]] = {}
        blocks: list[str] = []
        for name in names:
            handle = _NAME_HANDLES[len(offered_names)]
            candidates = await self._entities.similar_entities(
                workspace=key, name=name, limit=self._max_candidates,
            )
            if not candidates:
                continue
            offered_names[handle] = name
            by_letter = {
                _ENTITY_HANDLES[i]: entity_id
                for i, (entity_id, _canonical) in enumerate(candidates)
            }
            offered_entities[handle] = by_letter
            lines = [f"[{handle}] {name}"]
            lines += [
                f"    [{_ENTITY_HANDLES[i]}] {canonical}"
                for i, (_entity_id, canonical) in enumerate(candidates)
            ]
            blocks.append("\n".join(lines))
            if len(offered_names) >= len(_NAME_HANDLES):
                break

        self._offered[key] = _Offered(
            names=offered_names, entity_ids=offered_entities,
        )
        if not blocks:
            return ""
        self._inc(
            "nora_note_entity_resolution_offered_total", value=len(offered_names),
        )
        return "Names to resolve:\n\n" + "\n\n".join(blocks)

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `NoteEntityResolution`, dropping anything the
        model was not shown, including the "no map for this key" branch: an
        unshown handle is a guess with an
        alias attached, and the model may only name something it was shown.
        """
        offered = self._offered.get(key)
        kept: list[object] = []
        for item in items:
            resolution = _parse_resolution(item)
            if resolution is None:
                self._inc("nora_note_entity_resolutions_total", status="malformed")
                continue
            name_handle, entity_handle = resolution
            if entity_handle is None:
                self._inc("nora_note_entity_resolutions_total", status="split")
                continue
            if offered is None:
                log.warning(
                    "note entity resolution dropped: [%s]->[%s] arrived with "
                    "no record of what this run rendered (workspace=%s)",
                    name_handle, entity_handle, key,
                )
                self._inc(
                    "nora_note_entity_resolutions_total", status="unknown_handle",
                )
                continue
            name = offered.names.get(name_handle)
            entity_id = offered.entity_ids.get(name_handle, {}).get(entity_handle)
            if name is None or entity_id is None:
                log.warning(
                    "note entity resolution dropped: [%s]->[%s] names nothing "
                    "this run rendered (workspace=%s)",
                    name_handle, entity_handle, key,
                )
                self._inc(
                    "nora_note_entity_resolutions_total", status="unknown_handle",
                )
                continue
            self._inc("nora_note_entity_resolutions_total", status="merged")
            kept.append(NoteEntityResolution(name=name, entity_id=entity_id))
        return tuple(kept)

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Attach each resolved name as an alias. Returns rows changed.

        There is no `DreamSink` here — `SqlEntityStore.attach_alias` IS the
        write, and there is no sink because this pass's only write is an alias
        on a table it reaches directly through `entities`, not a proposal
        handed to a tier-owned sink.
        """
        written = 0
        for proposal in proposals:
            resolution = proposal.payload
            if not isinstance(resolution, NoteEntityResolution):
                raise TypeError(
                    f"NoteEntityResolutionAdaptor.commit only accepts a "
                    f"NoteEntityResolution payload, got "
                    f"{type(resolution).__name__}"
                )
            if await self._entities.attach_alias(
                workspace=key, entity_id=resolution.entity_id,
                alias=resolution.name,
            ):
                written += 1
        if written:
            self._inc("nora_note_entity_aliases_attached_total", value=written)
        return written

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)


def _parse_resolution(
    item: Mapping[str, object],
) -> tuple[str, str | None] | None:
    """One envelope item -> `(name_handle, entity_handle_or_None)`, or `None`
    if it is malformed: a missing `entity` key is malformed, an explicit
    `null` is a decision."""
    name = item.get("name")
    if not isinstance(name, str) or name not in _NAME_HANDLES or len(name) != 1:
        return None
    if "entity" not in item:
        return None
    entity = item["entity"]
    if entity is None:
        return name, None
    if (
        not isinstance(entity, str)
        or len(entity) != 1
        or entity not in _ENTITY_HANDLES
    ):
        return None
    return name, entity
