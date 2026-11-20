"""Script a wake outcome for each expected set of stimulus IDs."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Generic, Protocol, TypeVar

from nora.domain.graph_stimuli import GraphStimulus


class _Claimed(Protocol):
    @property
    def stimuli(self) -> tuple[GraphStimulus, ...]: ...


Claimed = TypeVar("Claimed", bound=_Claimed)
Outcome = TypeVar("Outcome")


class ScriptedWakePass(Generic[Claimed, Outcome]):
    def __init__(
        self, script: Mapping[tuple[str, ...], Outcome | Callable[[], Awaitable[Outcome]]],
    ) -> None:
        self._script = dict(script)
        self.seen: list[Claimed] = []

    async def run(self, claimed: Claimed) -> Outcome:
        self.seen.append(claimed)
        outcome = self._script[tuple(s.graph_id for s in claimed.stimuli)]
        return await outcome() if callable(outcome) else outcome
