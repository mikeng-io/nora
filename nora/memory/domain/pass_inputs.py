"""Resolved input references and the reading scope of a cognition pass."""

from dataclasses import dataclass, replace
from datetime import datetime

from nora.domain.blocks import ContextBlock
from nora.domain.blocks import assemble as render_blocks
from nora.domain.graph_stimuli import GraphStimulus
from nora.memory.domain.retrieval import ReadingRoom, RetrievalStatus


@dataclass(frozen=True, slots=True)
class PassScope:
    room: ReadingRoom
    unit: str
    as_of: datetime | None = None
    concerns: frozenset[str] = frozenset()
    nora_name: str = "Nora"
    unit_name: str | None = None
    anchor_failure: str | None = None


@dataclass(frozen=True, slots=True)
class AnchorItem:
    id: str | None
    text: str
    people: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CalledInput:
    item: AnchorItem
    quotes: str | None = None


@dataclass(frozen=True, slots=True)
class AmbientInput:
    items: tuple[AnchorItem, ...]


@dataclass(frozen=True, slots=True)
class ProactiveInput:
    intention: str


@dataclass(frozen=True, slots=True)
class WakeInput:
    stimuli: tuple[GraphStimulus, ...]


@dataclass(frozen=True, slots=True)
class DebriefInput:
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewCursor:
    target: tuple[int, str]
    after_verdict: tuple[int, str] | None = None


@dataclass(frozen=True, slots=True)
class ReviewInput:
    lessons: tuple[str, ...] = ()
    after: ReviewCursor | None = None
    include_roomless: bool = False


PassInput = CalledInput | AmbientInput | ProactiveInput | WakeInput | DebriefInput | ReviewInput


@dataclass(frozen=True, slots=True)
class AssembledPass:
    blocks: tuple[ContextBlock, ...]
    status: RetrievalStatus
    cause: str | None
    shown: frozenset[str] = frozenset()
    intention_visible: bool = True
    observed: tuple[tuple[str, str], ...] = ()
    agenda_stimuli: tuple[str, ...] = ()
    review_due: tuple[tuple[str, datetime], ...] = ()
    review_next: ReviewCursor | None = None

    def render(self) -> str:
        return render_blocks(tuple(replace(block, order=i) for i, block in enumerate(self.blocks)))
