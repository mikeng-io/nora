"""Doubles for `nora.app.cognition_session.Grounder`: what a session is shown
beside its agenda (`nora.domain.cognition_ground`). `EmptyGrounder` grounds on
nothing, the state every session test that is not ABOUT grounding runs in;
`StaticGrounder` returns one fixed `Grounding` and records what it was asked."""

from __future__ import annotations

from collections.abc import Sequence

from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import AgendaItem
from nora.domain.stimuli import Stimulus


class EmptyGrounder:
    async def __call__(
        self, unit: str, items: Sequence[AgendaItem], pending: Sequence[Stimulus],
    ) -> Grounding:
        return Grounding()


class StaticGrounder:
    def __init__(self, grounding: Grounding, *, fail: bool = False) -> None:
        self.grounding = grounding
        self.fail = fail
        self.calls: list[tuple[str, tuple[AgendaItem, ...], tuple[Stimulus, ...]]] = []

    async def __call__(
        self, unit: str, items: Sequence[AgendaItem], pending: Sequence[Stimulus],
    ) -> Grounding:
        self.calls.append((unit, tuple(items), tuple(pending)))
        if self.fail:
            raise RuntimeError("the Record could not be read")
        return self.grounding
