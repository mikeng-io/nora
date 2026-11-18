"""Close open intentions when observed or computed evidence settles a condition."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from nora.memory.domain.conditions import (
    ConditionValue,
    Ending,
    Truth,
    decode_condition,
    ending_for,
)


@dataclass(frozen=True, slots=True)
class Witness:
    graph_id: str
    recorded_at: datetime
    scope: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Evaluation:
    truth: Truth
    witness: Witness | None = None


class IntentionBatch(Protocol):
    async def open(self) -> Iterable[Mapping[str, object]]: ...

    async def evaluate(
        self, intention: Mapping[str, object], condition: ConditionValue,
    ) -> Evaluation: ...

    async def end(
        self, intention: Mapping[str, object], ending: Ending, witness: Witness,
    ) -> None: ...


class ConditionMatcher:
    async def observe(self, batch: IntentionBatch) -> None:
        for intention in await batch.open():
            done = await self._evaluate(batch, intention, "done_when")
            holds = await self._evaluate(batch, intention, "holds_while")
            drop = await self._evaluate(batch, intention, "drop_when")
            ending = ending_for(done.truth, holds.truth, drop.truth)
            if ending is not None:
                witness = {"achieved": done, "ceased": holds, "dropped": drop}[ending].witness
                if witness is not None:
                    await batch.end(intention, ending, witness)

    async def _evaluate(
        self, batch: IntentionBatch, intention: Mapping[str, object], role: str,
    ) -> Evaluation:
        form = intention.get(f"{role}_form")
        condition = decode_condition(form if isinstance(form, str) else None, intention.get(role))
        if condition is None:
            return Evaluation("unknown")
        return await batch.evaluate(intention, condition)
