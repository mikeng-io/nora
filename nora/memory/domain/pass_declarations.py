"""Graph presentations and read policy for each cognition pass."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from nora.memory.domain.pass_inputs import (
    AmbientInput,
    CalledInput,
    DebriefInput,
    PassInput,
    ProactiveInput,
    ReviewInput,
    WakeInput,
)
from nora.memory.domain.retrieval import RetrievalBounds

PassName = Literal["called", "ambient", "proactive", "debrief", "wake", "wake_history", "review"]
Presentation = Literal["graph_profile", "graph_standing", "graph_lessons", "related"]


@dataclass(frozen=True, slots=True)
class PassDeclaration:
    input_type: type[PassInput]
    presentations: tuple[Presentation, ...]
    pull: tuple[Literal["read"], ...]
    window: tuple[int, int, int, timedelta]

    def bounds(self, at: datetime, *, clock: datetime | None = None) -> RetrievalBounds:
        depth, fan_out, rows, age = self.window
        at = clock if clock is not None else at
        return RetrievalBounds(depth, fan_out, rows, at - age, at, ceiling=clock)


# Provisional bounds, measured in phase 8.
_ENGAGEMENT = (2, 8, 40, timedelta(days=180))
_DEBRIEF = (1, 8, 30, timedelta(days=30))
_WAKE = (3, 10, 80, timedelta(days=365))
_REVIEW = (1, 10, 60, timedelta(days=365))

_PRESENTATIONS: tuple[Presentation, ...] = (
    "graph_profile", "graph_standing", "graph_lessons", "related",
)
_DECLARATIONS: dict[PassName, PassDeclaration] = {
    "called": PassDeclaration(CalledInput, _PRESENTATIONS, ("read",), _ENGAGEMENT),
    "ambient": PassDeclaration(AmbientInput, _PRESENTATIONS, ("read",), _ENGAGEMENT),
    "proactive": PassDeclaration(ProactiveInput, _PRESENTATIONS, ("read",), _ENGAGEMENT),
    "debrief": PassDeclaration(DebriefInput, ("graph_standing",), (), _DEBRIEF),
    "wake": PassDeclaration(WakeInput, _PRESENTATIONS, ("read",), _WAKE),
    "wake_history": PassDeclaration(WakeInput, _PRESENTATIONS, ("read",), _WAKE),
    "review": PassDeclaration(ReviewInput, (), ("read",), _REVIEW),
}


def declaration(pass_name: PassName) -> PassDeclaration:
    """Read the declaration of a supported pass."""
    return _DECLARATIONS[pass_name]
