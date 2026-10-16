"""DEC-245: at most `deviation_stimuli_per_person_per_day` deviation stimuli
per person per local day, until a session can read the rows behind them
(`rollup_read`). Driven through `UnitRollups._raise` with the in-memory stimulus
store, whose `count_rollup` is bound to the SQL one by the store contract; only
the health proof is stubbed (it has its own test), so the cap is what is under
test.

What each test would catch if the cap were reverted is named in
`scripts/mutations/cognition-input.json`.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nora.analytics.units import PersonScope, UnitConfig, UnitRollups
from nora.domain.mobility import Stay
from nora.infra.sql.rollups import RecordMark, UnitRooms
from nora.obs.metrics import Metrics
from nora.testing.stimuli import InMemoryStimulusStore

HKT = ZoneInfo("Asia/Hong_Kong")
H = 3_600_000
UNIT = UnitRooms(unit="friends", rooms=(("signal", "g"),), members=("a", "b"), accounts={})


def _t(day: int, hour: float) -> int:
    return int(datetime(2026, 9, day, tzinfo=HKT).timestamp() * 1000 + hour * H)


def _stay(person: str, start: int, end: int) -> Stay:
    return Stay(
        subject_id=person, person_id=person, circle_id="c", started_ts=start, ended_ts=end,
        lat=22.3, lng=114.1, radius_m=40.0, fix_count=3,
    )


def _rig(cap: int = 1) -> tuple[UnitRollups, InMemoryStimulusStore, Metrics]:
    sink = InMemoryStimulusStore()
    metrics = Metrics()
    rollups = UnitRollups(
        store=None,  # type: ignore[arg-type]  # `_raise` reads no rollup table here
        config=UnitConfig(
            deviation_stimuli_per_person_per_day=cap, stimulus_horizon_seconds=6 * 3600,
        ),
        units=(UNIT,), people={"a": PersonScope(subject="a"), "b": PersonScope(subject="b")},
        stimuli=sink, metrics=metrics, clock=lambda: 0.0,
    )

    async def healthy(*_a: object, **_k: object) -> bool:
        return True

    rollups._healthy = healthy  # type: ignore[method-assign]
    return rollups, sink, metrics


async def _raise(rollups: UnitRollups, sink: InMemoryStimulusStore, *, as_of: int, anomalous):
    return await rollups._raise(
        sink, UNIT, members=("a", "b"), as_of=as_of, mark=RecordMark(as_of, 0, 0, 0),
        found=[], stays=[], books=[], anomalous=anomalous, platforms=["signal"],
    )


async def test_a_second_deviation_for_one_person_on_one_day_is_capped():
    rollups, sink, metrics = _rig()
    day = {"a": [_stay("a", _t(28, 9), _t(28, 10)), _stay("a", _t(28, 11), _t(28, 12))]}
    out = await _raise(rollups, sink, as_of=_t(28, 13), anomalous=day)
    assert [o for _, o in out] == ["raised", "capped"]
    assert [s.person_id for s in sink.stimuli] == ["a"]
    assert metrics.get("nora_rollup_stimuli_total", kind="deviation", outcome="capped") == 1


async def test_the_cap_holds_across_runs_a_deviation_raised_earlier_still_counts():
    rollups, sink, _ = _rig()
    first = {"a": [_stay("a", _t(28, 9), _t(28, 10))]}
    await _raise(rollups, sink, as_of=_t(28, 11), anomalous=first)
    later = {"a": [_stay("a", _t(28, 12), _t(28, 13))]}        # a NEW deviation, hours later
    out = await _raise(rollups, sink, as_of=_t(28, 14), anomalous=later)
    assert [o for _, o in out] == ["capped"]
    assert len(sink.stimuli) == 1


async def test_it_is_per_person_and_per_local_day():
    rollups, sink, _ = _rig()
    both = {"a": [_stay("a", _t(28, 9), _t(28, 10))], "b": [_stay("b", _t(28, 9), _t(28, 10))]}
    out = await _raise(rollups, sink, as_of=_t(28, 11), anomalous=both)
    assert [o for _, o in out] == ["raised", "raised"]          # two people, one each
    tomorrow = {"a": [_stay("a", _t(29, 9), _t(29, 10))]}
    out = await _raise(rollups, sink, as_of=_t(29, 11), anomalous=tomorrow)
    assert [o for _, o in out] == ["raised"]                     # the next local day is a new day
    assert len(sink.stimuli) == 3


async def test_a_day_is_the_local_day_not_the_utc_day():
    """23:30 HKT and 00:30 HKT the next day are 15:30 and 16:30 UTC the SAME UTC
    day: two local days, so neither is capped by the other."""
    rollups, sink, _ = _rig()
    late = {"a": [_stay("a", _t(28, 22.5), _t(28, 23.5))]}
    await _raise(rollups, sink, as_of=_t(28, 23.9), anomalous=late)
    early = {"a": [_stay("a", _t(29, 0.2), _t(29, 0.5))]}
    out = await _raise(rollups, sink, as_of=_t(29, 1), anomalous=early)
    assert [o for _, o in out] == ["raised"]


async def test_the_cap_is_a_number_and_two_per_day_lets_two_through():
    rollups, sink, _ = _rig(cap=2)
    day = {"a": [_stay("a", _t(28, 8), _t(28, 9)), _stay("a", _t(28, 10), _t(28, 11)),
                 _stay("a", _t(28, 12), _t(28, 13))]}
    out = await _raise(rollups, sink, as_of=_t(28, 14), anomalous=day)
    assert [o for _, o in out] == ["raised", "raised", "capped"]


async def test_an_unhealthy_deviation_is_withheld_before_it_can_use_up_the_cap():
    rollups, sink, _ = _rig()

    async def unhealthy(*_a: object, **_k: object) -> bool:
        return False

    rollups._healthy = unhealthy  # type: ignore[method-assign]
    day = {"a": [_stay("a", _t(28, 9), _t(28, 10))]}
    out = await _raise(rollups, sink, as_of=_t(28, 11), anomalous=day)
    assert [o for _, o in out] == ["unhealthy"] and sink.stimuli == []
    rollups._healthy = (lambda *a, **k: _true())  # type: ignore[method-assign]
    out = await _raise(rollups, sink, as_of=_t(28, 12), anomalous=day)
    assert [o for _, o in out] == ["raised"]


async def _true() -> bool:
    return True


@pytest.mark.parametrize("bad", [0, -1])
def test_a_cap_below_one_is_refused_by_config(bad):
    from pydantic import ValidationError

    from nora.config import AnalyticsConfig

    with pytest.raises(ValidationError):
        AnalyticsConfig(deviation_stimuli_per_person_per_day=bad)
