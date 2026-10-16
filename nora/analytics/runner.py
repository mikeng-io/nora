"""One run of the whole Rollup to a watermark, and the build lifecycle around
it.

- `run(build_id, through_ms)` folds every person's presence to the watermark
  (`MobilityRollup.run_once`), then computes every unit's rollups over the
  same build and the Record at the same watermark (`UnitRollups`), then
  records the watermark on the build. The daemon calls it every tick on the
  current build; a rebuild calls the SAME method on a fresh build — which is
  why the two agree: they are one function of the Record.
- Each run then drains the memory index's projection queue once
  (`SqlRollupStore.drain_index_queue`): what any store's projection queued is
  re-projected here, on the one loop that runs on a clock.
- `ensure_build` keeps the pointer on a build of THIS code and these
  parameters: when the current build was made by other code (a deploy) or
  other parameters (a config change), or there is none, it rebuilds from the
  Record into a new build and switches the pointer atomically. The build it
  replaces is superseded and kept.

`CODE_VERSION` names what the rows are a function of; bump it with any
change to what a run computes, so the next start rebuilds instead of
extending a build made by other code.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nora.analytics.rollup import FoldRun, MobilityRollup, RollupConfig
from nora.analytics.units import UnitConfig, UnitRollups, UnitRun
from nora.domain.mobility import Stay
from nora.domain.ports import GeocodeCacheStore
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollups import SqlRollupStore

log = logging.getLogger("nora.analytics.runner")

#: The version of what a run computes. Build 1 is `legacy` (the rows the
#: wall-clock rollup wrote).
CODE_VERSION = "rollup-2a.3"

#: RollupConfig fields that pace or bound the work and cannot change a row.
_PACING = frozenset({
    "tick_seconds", "safety_lag", "max_age_seconds", "batch_limit",
    "backfill_batch_limit", "backfill_threshold", "grace_seconds",
})


def params_sha256(rollup: RollupConfig, units: UnitConfig) -> str:
    """A digest of every parameter a row is a function of: a change to any of
    them makes the current build stale, and the next start rebuilds."""
    shape = {
        "rollup": {k: v for k, v in dataclasses.asdict(rollup).items() if k not in _PACING},
        "units": dataclasses.asdict(units),
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Subject:
    """One person the presence fold runs for: their stable subject id, every
    person id their fixes were recorded under, and their circles."""

    subject_id: str
    person_ids: tuple[str, ...]
    circle_ids: tuple[str, ...]


@dataclass(frozen=True)
class RunReport:
    build_id: int
    through_ms: int
    folds: Mapping[str, FoldRun]
    failed: tuple[str, ...]
    units: tuple[UnitRun, ...]


class RollupRunner:
    def __init__(
        self, *, mobility: SqlMobilityStore, rollups: SqlRollupStore,
        config: RollupConfig, unit_rollups: UnitRollups,
        subjects: Sequence[Subject], unit_config: UnitConfig,
        metrics: Any = None, geocode_cache: GeocodeCacheStore | None = None,
    ) -> None:
        self._mobility = mobility.unbound()
        self._rollups = rollups
        self._cfg = config
        self._units = unit_rollups
        self._subjects = tuple(subjects)
        self._params = params_sha256(config, unit_config)
        self._metrics = metrics
        self._geocode_cache = geocode_cache

    @property
    def params(self) -> str:
        return self._params

    async def run(
        self, *, build_id: int, through_ms: int, raise_stimuli: bool = True,
    ) -> RunReport:
        rollup = MobilityRollup(
            store=self._mobility.scoped(build_id), config=self._cfg,
            metrics=self._metrics, geocode_cache=self._geocode_cache,
        )
        folds: dict[str, FoldRun] = {}
        failed: list[str] = []
        for subject in self._subjects:
            try:
                folds[subject.subject_id] = await rollup.run_once(
                    subject_id=subject.subject_id, person_ids=list(subject.person_ids),
                    circle_id=subject.circle_ids[0], circle_ids=subject.circle_ids,
                    through_ms=through_ms,
                )
            except Exception:
                # Isolated: this person's fold did not commit, so the next
                # run folds the same events again from the same state.
                log.exception("presence fold failed for %s", subject.subject_id)
                failed.append(subject.subject_id)
        mark = await self._rollups.mark(through_ms=through_ms)
        anomalous: dict[str, Sequence[Stay]] = {
            s: run.anomalous for s, run in folds.items() if run.anomalous
        }
        units = await self._units.run(
            build_id=build_id, mark=mark,
            logical_now={s: run.logical_now_ms for s, run in folds.items()},
            anomalous=anomalous, raise_stimuli=raise_stimuli,
        )
        if not failed:
            await self._rollups.advance_build(build_id=build_id, through_ms=through_ms)
        try:
            # Whatever a projection queued since the last run, from any process:
            # this is the one loop that runs on a clock.
            await self._rollups.drain_index_queue()
        except Exception:
            log.exception("memory index queue drain failed — retried next run")
        return RunReport(
            build_id=build_id, through_ms=through_ms, folds=folds,
            failed=tuple(failed), units=tuple(units),
        )

    async def rebuild(self, *, through_ms: int, switch: bool = True) -> int:
        """A new build from the start of the Record to `through_ms`, switched
        in atomically when complete. Raises no stimulus: the next run on the
        new build raises whatever is current."""
        build_id = await self._rollups.create_build(
            code_version=CODE_VERSION, params_sha256=self._params,
        )
        log.info("rollup rebuild %d started, to %d", build_id, through_ms)
        try:
            report = await self.run(
                build_id=build_id, through_ms=through_ms, raise_stimuli=False,
            )
            if report.failed:
                raise RuntimeError(f"folds failed for {list(report.failed)}")
            if switch:
                # Inside the try: a switch that fails rolls back whole, and
                # the build it leaves `building` is abandoned, not orphaned.
                await self._rollups.switch_build(build_id=build_id)
                log.info("rollup build %d is current", build_id)
        except Exception:
            await self._rollups.abandon_build(build_id=build_id)
            raise
        return build_id

    async def ensure_build(self, *, through_ms: int) -> int:
        """The current build's id, after rebuilding if the current build was
        made by other code or parameters, or there is none."""
        current = await self._rollups.current_build()
        if (
            current is not None and current.code_version == CODE_VERSION
            and current.params_sha256 == self._params
        ):
            return current.id
        log.info(
            "current rollup build %s is %s/%s, this code is %s/%s: rebuilding",
            None if current is None else current.id,
            None if current is None else current.code_version,
            None if current is None else current.params_sha256[:8],
            CODE_VERSION, self._params[:8],
        )
        return await self.rebuild(through_ms=through_ms)
