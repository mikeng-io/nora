"""The clock line and the `now` tool state the same facts.

Two surfaces tell her when it is: the line at the top of every turn and the
`now` tool she may call mid-turn. Both read `nora.agent.clock.local_now` and
render through `state_time`. Pinned here by giving both ONE instant: a tool
that reads its own clock, or formats its own way, answers something else.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

from nora.agent import clock, prefill
from nora.agent import tools as tools_module
from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.obs.metrics import Metrics
from nora.persons import Persons

#: A production-shaped instant: `datetime.now().astimezone()` in the container
#: carries an offset and «HKT», never a zone key.
_INSTANT = datetime(2026, 9, 23, 23, 7, tzinfo=timezone(timedelta(hours=8), "HKT"))


def _now_tool(**kw):
    persons = Persons(by_person={}, index={})
    specs = build_tool_specs(**kw,
        store=None, conversation_id="g1", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=Room(platform="signal", conversation_id="g1", roster_source=None,
                  persons=persons),
        transit_source=None,
    )
    return next(s for s in specs if s.name == "now")


def test_the_now_tool_states_exactly_the_clock_lines_facts(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    for module in (clock, prefill, tools_module):
        if hasattr(module, "local_now"):
            monkeypatch.setattr(module, "local_now", lambda: _INSTANT)

    line = prefill._now_line(None)
    answer = asyncio.run(_now_tool().call())

    assert line == f"Right now it is {answer}.\n"
    assert answer == "Wednesday 23 September 2026, 23:07, Hong Kong time (HKT, Asia/Hong_Kong)"


def test_the_now_tool_reads_the_injected_clock_not_the_wall(monkeypatch):
    """A turn whose clock is injected (the fire path's `clock`, frozen at a
    past instant in an eval sandbox) states that instant on its clock line.
    `now` must state the same one: reading the wall instead would give a
    different day mid-turn than the clock line."""
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    frozen = datetime(2026, 10, 11, 1, 5, tzinfo=UTC)
    monkeypatch.setattr(tools_module, "local_now", lambda: _INSTANT)

    answer = asyncio.run(_now_tool(clock=lambda: frozen).call())

    assert answer == clock.state_time(frozen.astimezone())
    # The date only: the hour depends on the process zone, which `setenv`
    # alone does not change.
    assert answer.startswith("Sunday 11 October 2026")
