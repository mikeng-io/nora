"""`OAuthKeepalive`'s loop and its wiring (DEC-306), on a scripted lane.

The chain itself (rotation, locks, the state file, a real issuer) is
`tests/contract/test_oauth_lane_contract.py`; this is the keeper's schedule:
transient backoff, a terminal failure that stops hammering, a crash that is
logged and restarted, jitter that only ever pulls a refresh earlier, and the
composition that registers every lane whose token is set.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from nora.app.composition import _image_gen_wiring
from nora.app.oauth_keepalive import KeepaliveTiming, OAuthKeepalive
from nora.obs.metrics import Metrics

HOUR = 3600.0
DAY = 24 * HOUR


@dataclass(frozen=True)
class Link:
    refreshed_at: float
    expires_at: float


class ScriptedLane:
    """Answers `refresh` from a script: a reason string, an exception, or
    `"ok"` (a link minted now, valid for ten days)."""

    lane = "scripted"
    skew_seconds = HOUR
    terminal_reasons = frozenset({"auth", "state_unwritable"})

    def __init__(self, clock: Callable[[], float], script: list[Any]) -> None:
        self._clock = clock
        self._script = script
        self.calls = 0
        self.link: Link | None = None

    def snapshot(self) -> Link | None:
        return self.link

    def set_observer(self, observer: Any) -> None:
        self.observer = observer

    async def refresh(self, *, unless_refreshed_since: float) -> Link | str:
        self.calls += 1
        step = self._script.pop(0) if self._script else "ok"
        if isinstance(step, BaseException):
            raise step
        if step == "ok":
            self.link = Link(self._clock(), self._clock() + 10 * DAY)
            return self.link
        return str(step)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


async def _run(lane: ScriptedLane, clock: Clock, *, sleeps: int, metrics: Metrics | None = None,
               rand: float = 0.0) -> list[float]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) > sleeps:
            raise asyncio.CancelledError
        clock.now += seconds

    keeper = OAuthKeepalive(metrics or Metrics(), clock=clock, sleep=sleep, rand=lambda: rand)
    timing = KeepaliveTiming(interval_seconds=DAY)
    keeper.register(lane, timing)
    with pytest.raises(asyncio.CancelledError):
        await keeper.run(lane, timing)
    return slept


async def test_a_transient_failure_backs_off_exponentially_then_returns_to_plan() -> None:
    clock = Clock()
    lane = ScriptedLane(clock, ["down", "rate_limited", "cloudflare", "ok"])
    slept = await _run(lane, clock, sleeps=4)
    assert slept == [0.0, 60.0, 120.0, 240.0, DAY]
    assert lane.calls == 4


async def test_the_transient_backoff_is_capped() -> None:
    clock = Clock()
    lane = ScriptedLane(clock, ["down"] * 8)
    slept = await _run(lane, clock, sleeps=8)
    assert slept[1:] == [60.0, 120.0, 240.0, 480.0, 960.0, 1800.0, 1800.0, 1800.0]


async def test_a_terminal_failure_stops_hammering_and_stays_loud(caplog) -> None:
    """A refused refresh token does not heal by retrying: once per interval,
    an ERROR each time, never the transient backoff."""
    clock = Clock()
    lane = ScriptedLane(clock, ["auth", "auth", "auth"])
    with caplog.at_level(logging.ERROR, logger="nora.app.oauth_keepalive"):
        slept = await _run(lane, clock, sleeps=3)
    assert slept == [0.0, DAY, DAY, DAY]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 3
    assert all("scripted" in e and "auth" in e for e in errors)


async def test_a_crash_is_logged_counted_and_the_loop_restarted(caplog) -> None:
    clock = Clock()
    metrics = Metrics()
    lane = ScriptedLane(clock, [RuntimeError("boom"), "ok"])
    with caplog.at_level(logging.ERROR, logger="nora.app.oauth_keepalive"):
        slept = await _run(lane, clock, sleeps=3, metrics=metrics)
    # plan 0 -> crash -> restart backoff 60 -> plan 0 -> ok -> a day
    assert slept == [0.0, 60.0, 0.0, DAY]
    assert lane.calls == 2
    assert metrics.get("nora_oauth_keepalive_restarts_total", lane="scripted") == 1
    crashed = [r for r in caplog.records if "crashed" in r.getMessage()]
    assert crashed and crashed[0].exc_info is not None


async def test_jitter_only_pulls_a_refresh_earlier() -> None:
    """rand=1 is the most jitter there is: a day's wait loses 15 minutes
    (the cap), never gains any, so the interval stays a ceiling."""
    clock = Clock()
    lane = ScriptedLane(clock, ["ok", "ok"])
    slept = await _run(lane, clock, sleeps=2, rand=1.0)
    assert slept == [0.0, DAY - 900.0, DAY - 900.0]


# -- composition --------------------------------------------------------------


def _cfg(example_config, provider: str, *, enabled: bool = True):
    image_gen = example_config.image_gen.model_copy(
        update={"enabled": enabled, "provider": provider},
    )
    return example_config.model_copy(update={"image_gen": image_gen})


ENV = {"XAI_OAUTH_REFRESH_TOKEN": "rt-x", "OPENAI_CODEX_REFRESH_TOKEN": "rt-c"}


@pytest.mark.parametrize("provider", ["xai", "openai_codex"])
async def test_every_lane_with_a_token_is_kept_whichever_provider_is_selected(
    example_config, tmp_path, provider,
) -> None:
    generator, keeper = _image_gen_wiring(
        _cfg(example_config, provider), Metrics(), ENV, state_dir=tmp_path,
    )
    assert generator is not None
    assert keeper is not None and set(keeper.lanes) == {"xai", "openai_codex"}
    # The adapter holds the SAME lane object the keeper refreshes: one lock.
    kept = {lane.lane: lane for lane, _ in keeper._lanes}
    assert generator._oauth is kept[provider]  # type: ignore[attr-defined]


async def test_a_lane_without_a_token_or_with_keepalive_off_is_not_kept(
    example_config, tmp_path,
) -> None:
    cfg = _cfg(example_config, "xai")
    xai = cfg.image_gen.xai.model_copy(update={"keepalive": False})
    cfg = cfg.model_copy(update={"image_gen": cfg.image_gen.model_copy(update={"xai": xai})})
    _, keeper = _image_gen_wiring(cfg, Metrics(), ENV, state_dir=tmp_path)
    assert keeper is not None and keeper.lanes == ("openai_codex",)
    _, none = _image_gen_wiring(
        _cfg(example_config, "xai"), Metrics(), {"XAI_OAUTH_REFRESH_TOKEN": ""},
        state_dir=tmp_path,
    )
    assert none is None


async def test_image_generation_off_keeps_nothing_and_composing_starts_nothing(
    example_config, tmp_path,
) -> None:
    """Off: no lane, no keeper. On: composing spawns no task, because the
    eval subject composes this graph too; only `nora/__main__.py` starts it."""
    assert _image_gen_wiring(
        _cfg(example_config, "xai", enabled=False), Metrics(), ENV, state_dir=tmp_path,
    ) == (None, None)
    before = asyncio.all_tasks()
    _image_gen_wiring(_cfg(example_config, "xai"), Metrics(), ENV, state_dir=tmp_path)
    assert asyncio.all_tasks() == before
