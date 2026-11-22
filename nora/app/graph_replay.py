"""Replay one unit's observed graph history through the shared wake pass."""

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from itertools import batched, groupby
from typing import Protocol

from nora.domain.graph_stimuli import GraphStimulus
from nora.memory import ReplayCoverage, ReplayItem, WakeClaim, WakeGroup, WakeOutcome


class ReplayStore(Protocol):
    def unit_lock(self, unit: str) -> AbstractAsyncContextManager[None]: ...

    async def coverage(self, unit: str, until: datetime) -> ReplayCoverage: ...

    async def record(
        self, unit: str, pass_id: str, clock: datetime, inputs: dict[str, str],
    ) -> None: ...


class ReplayWake(Protocol):
    async def run(self, claimed: WakeClaim) -> WakeOutcome: ...


@dataclass(frozen=True, slots=True)
class ReplayReport:
    coverage: ReplayCoverage
    batches: tuple[WakeClaim, ...]
    closed: int = 0
    failed: str | None = None


def _batches(
    unit: str, pending: Sequence[tuple[ReplayItem, str]], batch_max: int,
) -> Iterator[WakeClaim]:
    for (origin, room), group in groupby(pending, key=lambda pair: (pair[0].origin, pair[1])):
        for items in batched((item for item, _ in group), batch_max):
            stimuli = tuple(GraphStimulus(
                unit, "new_item", item.graph_id, item.clock, item.source,
                room=item.origin, occurrence="history",
            ) for item in items)
            yield WakeClaim(WakeGroup(unit, origin), room, stimuli, items[-1].clock, "batch")


class GraphReplay:
    def __init__(self, store: ReplayStore, batch_max: int) -> None:
        self._store = store
        self._batch_max = batch_max

    async def inspect(self, unit: str, until: datetime) -> ReplayReport:
        coverage = await self._store.coverage(unit, until)
        return ReplayReport(coverage, tuple(_batches(unit, coverage.pending, self._batch_max)))

    async def run(self, unit: str, until: datetime, wake: ReplayWake) -> ReplayReport:
        async with self._store.unit_lock(unit):
            plan = await self.inspect(unit, until)
            closed = 0
            failure: str | None = None
            for batch in plan.batches:
                outcome = await wake.run(batch)
                if outcome.state == "failed":
                    failure = outcome.cause
                    break
                inputs = dict(outcome.observed)
                if tuple(inputs) != tuple(stimulus.graph_id for stimulus in batch.stimuli):
                    failure = "input_mismatch"
                    break
                await self._store.record(unit, batch.pass_id, batch.claimed, inputs)
                closed += 1
            coverage = await self._store.coverage(unit, until) if closed else plan.coverage
            return ReplayReport(coverage, plan.batches, closed, failure)


def _instant(value: str) -> datetime:
    try:
        instant = datetime.fromisoformat(value)
        if instant.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("use an ISO 8601 timestamp with an offset") from None
    return instant


async def _entry(argv: Sequence[str] | None = None) -> int:
    from nora.app.cli import CliError, _config
    from nora.app.composition_wake_history import replay_dependencies
    from nora.app.startup import StartupError
    from nora.memory import RetrievalUnavailableError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--unit", required=True)
    parser.add_argument("--until", required=True, type=_instant)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)
    try:
        async with replay_dependencies(_config(args), inspect=args.inspect) as (store, wake, count):
            replay = GraphReplay(store, count)
            report = (await replay.inspect(args.unit, args.until) if wake is None
                      else await replay.run(args.unit, args.until, wake))
    except (CliError, StartupError, RetrievalUnavailableError) as exc:
        print(f"Replay unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "batches": [{"clock": batch.claimed.isoformat(), "items": len(batch.stimuli)}
                    for batch in report.batches],
        "closed": report.closed,
        "pending": len(report.coverage.pending),
        "not_observable": dict(Counter(report.coverage.not_observable.values())),
        "failed": report.failed,
    }))
    return int(report.failed is not None or (not args.inspect and bool(report.coverage.pending)))


if __name__ == "__main__":
    sys.exit(asyncio.run(_entry()))
