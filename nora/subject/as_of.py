"""A tag turn's blocks, rendered from a restore, against what the turn saw.

The restore-equivalence gate: a recorded
tag turn's blocks, rendered from a snapshot at its moment, equal the blocks
its Langfuse trace recorded — memory and the Live blocks excepted, the
transcript rendered through the media join as of T.

This module is the comparison, used two ways:

- **offline**, by `tests/integration/test_snapshot_renders_the_recorded_blocks.py`:
  a turn's blocks are rendered at T on a seeded source (the recording), the
  source moves on, and the same turn is rendered again on a sandbox seeded as
  of T (`nora.subject.sandbox.sandbox_as_of`); every compared block must be
  byte-identical;
- **live**, by the operator, against a real trace: `scripts/
  snapshot_block_equivalence.py` takes the block message a tag turn's
  Langfuse generation recorded as its input, the
  turn's system time, and the source (the eval clone), and runs the same
  comparison. Its docstring is the procedure.

What is excepted, by name, and why:

- **memory** — `peer_card`, `peer_history`: Honcho has no chain
  to read backwards, so a restore cannot hold what it said. Neither block
  renders on the graph path (design/cognition/context.md's migration table);
  the names stay so an old recording is excepted rather than extra.
- **Live** — `tool_catalogue` (built from the tools admitted at the turn, not
  from any store) and `presence` (her platform presence: transport health
  read at turn start).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from nora.agent.prefill import TAG_HEADER, engagement_floor
from nora.agent.room import Room, trigger_message_ids
from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagementRequest
from nora.persons import sanitize_unmapped

from .obtain import Subject
from .sandbox_roster import RecordedRoster

__all__ = [
    "LIVE_BLOCKS",
    "MEMORY_BLOCKS",
    "BlockDiff",
    "blocks_of",
    "compare_blocks",
    "render_tag_blocks",
]

MEMORY_BLOCKS: frozenset[str] = frozenset({"peer_card", "peer_history"})
LIVE_BLOCKS: frozenset[str] = frozenset({"tool_catalogue", "presence"})

#: One top-level block: its opening tag starts a line, its body is escaped
#: (so it cannot carry its own closing tag), its closing tag ends a line.
_BLOCK = re.compile(
    r"^<([a-z_]+)([^\n>]*[^/\n>])?>\n(.*?)\n</\1>$", re.MULTILINE | re.DOTALL)
#: A self-closed empty presentation (`<graph_standing ... />`) is still a
#: compared surface: its attributes carry the read's status, so a timed-out
#: read (`status="unavailable"`) and a successful empty one differ in the
#: text rather than both vanishing from the comparison.
_PRESENTATION = re.compile(r"^<([a-z_]+)([^\n>]*?)\s*/>$", re.MULTILINE)
_OF = re.compile(r'\bof="([^"]+)"')
_UNAVAILABLE = re.compile(r'\bstatus="unavailable"')


def blocks_of(prefill_text: str) -> dict[str, str]:
    """Each block of a tag turn's block message, keyed by name as rendered.

    `prefill_text` is the block message `EngagementGraph.prompt` returns —
    exactly what a tag turn's Langfuse generation records. A block rendered
    per subject (`graph_profile`) repeats its name, so its key carries the
    `of` attribute: `graph_profile of=entity:unit:nora`. Self-closed empty
    presentations count too: their attributes carry the read's status, so
    `status="unavailable"` and a successful empty block differ here. Any
    other duplicate fails closed rather than keeping one of them.
    """
    out: dict[str, str] = {}
    blocks = list(_BLOCK.finditer(prefill_text))
    spans = [m.span() for m in blocks]
    top_level = (m for m in _PRESENTATION.finditer(prefill_text)
                 if not any(start <= m.start() < end for start, end in spans))
    for match in (*blocks, *top_level):
        name, attrs = match.group(1), match.group(2) or ""
        key = name
        if name == "graph_profile" and (of := _OF.search(attrs)):
            key = f"{name} of={of.group(1)}"
        if key in out:
            raise ValueError(f"block {key!r} appears twice; this is not one prefill")
        out[key] = match.group(0)
    return out


def _opening_tag(block_text: str) -> str:
    """The block's own opening tag, where its status attribute lives."""
    end = block_text.find(">")
    return block_text if end == -1 else block_text[:end + 1]


async def render_tag_blocks(
    subject: Subject, *, message: NormalizedMessage, now: datetime,
    row_id: int | None = None,
) -> dict[str, str]:
    """The blocks a tag turn answering `message` would be handed, rendered by
    the production assembler and renderer from `subject`'s stores at `now`.

    `message` must already be in the subject's record (a tag turn reads a
    window that ends with the tagging line), and `row_id` is its `messages.id`
    — the live pass is handed the same anchor (`request.message_row_id`).
    The roster is the recorded one: who SPOKE in this room inside the
    restored window (`sandbox_roster.RecordedRoster`), the snapshot's
    equivalent of the platform's live membership list. The tool catalogue is
    not rendered — it is a Live block and compared by nobody.
    """
    ctx = await subject.deps.assembler.assemble(request=EngagementRequest(
        message=message, decision=EngageDecision(engage=True, reason="tag"),
        tag_id=f"as-of-{uuid.uuid4().hex[:12]}",
    ))
    display_name = subject.cfg.doctrine.display_name
    tagger_name = (ctx.tagger.name if ctx.tagger is not None
                   else message.sender_name or sanitize_unmapped(message.sender_id))
    floor = engagement_floor(
        ctx, display_name=display_name, tagger_name=tagger_name,
        context_window=subject.cfg.store.context_window, now=now, catalogue="",
    )
    room = Room(
        platform=message.platform, conversation_id=message.conversation_id,
        roster_source=RecordedRoster(
            platform=message.platform,
            database_url=subject.database_url, now=now),
        access=subject.deps.access,
        tagger=ctx.tagger,
        self_ids=frozenset(getattr(
            subject.deps.registry.identity_for(message.platform), "aliases", ())),
        trigger_message_ids=trigger_message_ids(
            ctx.transcript, sender_id=message.sender_id,
            timestamp=message.timestamp),
    )
    _prefill, tag_message, _shown = await subject.deps.engagement.called_pass_prompt(
        ctx, floor=floor, display_name=display_name, now=now,
        header=TAG_HEADER, row_id=row_id, room=room,
    )
    return blocks_of(tag_message)


@dataclass(frozen=True, slots=True)
class BlockDiff:
    """What differs, block by block. Empty `differs`, `missing`, `extra` and
    `unavailable` is the gate passing; `compared` is what it passed ON, so a
    comparison of nothing cannot read as a pass. `unavailable` names blocks
    whose own status says the read did not happen — an unavailable render is
    a gate verdict of its own, never an equality and never an absence."""

    compared: tuple[str, ...]
    excepted: tuple[str, ...]
    differs: tuple[str, ...]
    #: recorded at the turn, absent from the restore
    missing: tuple[str, ...]
    #: rendered from the restore, absent from the turn
    extra: tuple[str, ...]
    #: blocks that report `status="unavailable"` on either side
    unavailable: tuple[str, ...]

    @property
    def equal(self) -> bool:
        return (bool(self.compared)
                and not (self.differs or self.missing or self.extra or self.unavailable))


def compare_blocks(recorded: Mapping[str, str], restored: Mapping[str, str]) -> BlockDiff:
    """`recorded` (the turn) against `restored` (a render from the snapshot),
    memory and Live blocks excepted."""
    excepted = MEMORY_BLOCKS | LIVE_BLOCKS
    names = (set(recorded) | set(restored)) - excepted
    return BlockDiff(
        compared=tuple(sorted(n for n in names if n in recorded and n in restored)),
        excepted=tuple(sorted((set(recorded) | set(restored)) & excepted)),
        differs=tuple(sorted(
            n for n in names if n in recorded and n in restored and recorded[n] != restored[n]
        )),
        missing=tuple(sorted(n for n in names if n in recorded and n not in restored)),
        extra=tuple(sorted(n for n in names if n in restored and n not in recorded)),
        unavailable=tuple(sorted(
            n for n in names
            if (n in recorded and _UNAVAILABLE.search(_opening_tag(recorded[n])))
            or (n in restored and _UNAVAILABLE.search(_opening_tag(restored[n]))))),
    )
