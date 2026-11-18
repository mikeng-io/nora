"""Durable references to changes in the Cognitive Graph."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

StimulusKind = Literal[
    "new_item", "strong_cue", "failed_act", "dependency", "aggregate_crossing",
    "intention_due", "review_time", "valid_to", "deadline",
    "watched_source_changed", "absence", "verdict", "presence_change",
]
STIMULUS_KINDS: tuple[str, ...] = get_args(StimulusKind)


@dataclass(frozen=True, slots=True)
class GraphStimulus:
    unit: str
    kind: StimulusKind
    graph_id: str
    eligible_at: datetime
    source: str
    room: str | None = None
    person: str | None = None
    cues: tuple[str, ...] = ()
    revision_id: str | None = None
    dependents: tuple[str, ...] = ()
    occurrence: str = ""

    def __post_init__(self) -> None:
        if not self.unit or not self.graph_id or not self.source:
            raise ValueError("stimulus requires a unit, graph id and source")
        if self.kind not in STIMULUS_KINDS or self.eligible_at.utcoffset() is None:
            raise ValueError("stimulus requires a declared kind and an aware instant")
