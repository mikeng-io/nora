"""Resolved input references and the reading scope of a cognition pass."""

from dataclasses import dataclass
from datetime import datetime

from nora.domain.graph_stimuli import GraphStimulus
from nora.memory.domain.retrieval import ReadingRoom


@dataclass(frozen=True, slots=True)
class PassScope:
    room: ReadingRoom
    unit: str
    as_of: datetime | None = None
    concerns: frozenset[str] = frozenset()
    nora_name: str = "Nora"
    unit_name: str | None = None


@dataclass(frozen=True, slots=True)
class AnchorItem:
    id: str
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
class ReviewInput:
    lessons: tuple[str, ...] = ()


PassInput = CalledInput | AmbientInput | ProactiveInput | WakeInput | DebriefInput | ReviewInput
