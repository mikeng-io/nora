"""Close open intentions when observed or computed evidence settles a condition."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from nora.memory.domain.admission import Refusal
from nora.memory.domain.conditions import (
    ConditionValue,
    Ending,
    Truth,
    decode_condition,
    decode_condition_trigger,
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
        self, intention: Mapping[str, object], condition: ConditionValue, *, trigger: bool = False,
    ) -> Evaluation: ...

    async def end(
        self, intention: Mapping[str, object], ending: Ending, witness: Witness,
    ) -> None: ...

    async def publish_due(self, intention: Mapping[str, object], witness: Witness) -> None: ...


class ConditionMatcher:
    async def observe(self, *, batch: IntentionBatch) -> None:
        for intention in await batch.open():
            ending, witness = await self._ending(batch, intention)
            if ending is not None and witness is not None:
                await batch.end(intention, ending, witness)
            elif intention["delivery_mode"] == "choose_moment":
                trigger = decode_condition_trigger(intention["trigger"])
                if trigger is not None:
                    result = await batch.evaluate(intention, trigger, trigger=True)
                    if result.truth == "true" and result.witness is not None:
                        await batch.publish_due(intention, result.witness)

    async def close(
        self, batch: IntentionBatch, intention: Mapping[str, object], witness_id: str,
    ) -> Refusal | None:
        if intention["state"] == "superseded":
            return Refusal("stale_base", "base")
        if intention["state"] != "open":
            return Refusal("not_open", "base")
        if "untyped" in (intention.get("done_when_form"), intention.get("holds_while_form")):
            return Refusal("untyped_condition", "base")
        ending, witness = await self._ending(batch, intention)
        if (ending not in ("achieved", "ceased") or witness is None
                or witness.graph_id != witness_id):
            return Refusal("witness_not_met", "witness")
        await batch.end(intention, ending, witness)
        return None

    async def _ending(
        self, batch: IntentionBatch, intention: Mapping[str, object],
    ) -> tuple[Ending | None, Witness | None]:
        done = await self._evaluate(batch, intention, "done_when")
        holds = await self._evaluate(batch, intention, "holds_while")
        drop = await self._evaluate(batch, intention, "drop_when")
        ending = ending_for(done.truth, holds.truth, drop.truth)
        if ending is None:
            return None, None
        return ending, {"achieved": done, "ceased": holds, "dropped": drop}[ending].witness

    async def _evaluate(
        self, batch: IntentionBatch, intention: Mapping[str, object], role: str,
    ) -> Evaluation:
        form = intention.get(f"{role}_form")
        condition = decode_condition(form if isinstance(form, str) else None, intention.get(role))
        if condition is None:
            return Evaluation("unknown")
        return await batch.evaluate(intention, condition)
