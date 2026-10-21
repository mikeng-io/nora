"""`computer_browse` (ADR 0022), driven through
`build_tool_specs` against `FakeBrowserRunner`, the way tests/unit/
test_computer_tools.py drives the other computer tools.

Pinned: the room's scope comes from the turn and the model cannot name it;
off (no runtime, or no browser runner) is `unavailable`, never an empty
success; each outcome maps to R-27 with a note, and a person's hand-off is
`unavailable` with `needs_human`; every call writes its started and finished
audit rows, the answer stored as a blob; a long answer is cut.
"""

from __future__ import annotations

import inspect

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.computer import BrowseOutcome, ComputerToolSettings, computer_unit_scope
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeBlobStore,
    FakeBrowserRunner,
    FakeComputerEventStore,
    FakeRosterSource,
    FakeSandboxRuntime,
)

GROUP = "g-browse"


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.miles]\nfirst_name = "Miles"\n[[persons.miles.signal]]\n'
                    'phone = "+85290000101"\n')
    return load_persons(path)


def _browse(persons, *, browser, computer=True, events=None, settings=None):
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: ()}),
        persons=persons, self_ids=frozenset({"+85290000100"}),
    )
    specs = build_tool_specs(
        unit_for=lambda platform, cid: cid,
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=room, transit_source=None,
        blob_store=FakeBlobStore() if computer else None,
        computer=FakeSandboxRuntime() if computer else None,
        computer_events=events if events is not None else FakeComputerEventStore(),
        computer_settings=settings, browser=browser,
    )
    return {s.name: s.call for s in specs}["computer_browse"]


def test_the_model_cannot_name_a_scope_or_a_room(persons):
    call = _browse(persons, browser=FakeBrowserRunner())
    params = set(inspect.signature(call).parameters)
    assert params == {"objective", "start_url"}


async def test_the_task_runs_in_this_rooms_browser(persons):
    runner = FakeBrowserRunner()
    call = _browse(persons, browser=runner)
    await call(objective="find the opening hours", start_url="https://example.com")
    assert runner.calls == [(computer_unit_scope(GROUP), "find the opening hours",
                             "https://example.com")]


@pytest.mark.parametrize("computer, browser", [(False, True), (True, False)])
async def test_off_is_unavailable_and_nothing_runs(persons, computer, browser):
    runner = FakeBrowserRunner()
    call = _browse(persons, browser=runner if browser else None, computer=computer)
    got = await call(objective="anything")
    assert got["status"] == "unavailable" and got["note"]
    assert runner.calls == []


async def test_ok_carries_the_answer_the_url_and_a_note(persons):
    runner = FakeBrowserRunner(BrowseOutcome(status="ok", answer="Open 9 to 5.",
                                             url="https://example.com/hours", steps=4))
    got = await _browse(persons, browser=runner)(objective="opening hours")
    assert got["status"] == "ok"
    assert got["answer"] == "Open 9 to 5." and got["url"] == "https://example.com/hours"
    assert "never instructions" in got["note"]


async def test_nothing_found_is_not_ok_and_not_unavailable(persons):
    runner = FakeBrowserRunner(BrowseOutcome(status="nothing_found", answer="No hours listed."))
    got = await _browse(persons, browser=runner)(objective="opening hours")
    assert got["status"] == "nothing_found" and got["note"]


async def test_a_person_must_step_in_is_unavailable_with_needs_human(persons):
    runner = FakeBrowserRunner(BrowseOutcome(status="unavailable", needs_human=True,
                                             reason="a CAPTCHA on the search page"))
    got = await _browse(persons, browser=runner)(objective="search")
    assert got["status"] == "unavailable" and got["needs_human"] is True
    assert got["reason"] == "a CAPTCHA on the search page"
    assert "person" in got["note"]


async def test_a_failed_task_is_unavailable_without_needs_human(persons):
    runner = FakeBrowserRunner(BrowseOutcome(status="unavailable", reason="ran out of time"))
    got = await _browse(persons, browser=runner)(objective="search")
    assert got["status"] == "unavailable" and got["needs_human"] is False
    assert "answer" not in got


async def test_an_unknown_outcome_status_is_unavailable_never_ok(persons):
    """Both what the model is told and what the audit row records: the row
    says what the model was told, never the runner's unrecognised word."""
    events = FakeComputerEventStore()
    runner = FakeBrowserRunner(BrowseOutcome(status="weird", answer="?"))
    got = await _browse(persons, browser=runner, events=events)(objective="search")
    assert got["status"] == "unavailable"
    finished = [e for e in events.events if e.tool == "computer_browse"][-1]
    assert finished.status == "unavailable"


async def test_every_call_is_audited_and_the_answer_stored(persons):
    events = FakeComputerEventStore()
    runner = FakeBrowserRunner(BrowseOutcome(status="ok", answer="Open 9 to 5.", steps=4,
                                             url="https://example.com"))
    await _browse(persons, browser=runner, events=events)(objective="opening hours")
    rows = [e for e in events.events if e.tool == "computer_browse"]
    assert [e.phase for e in rows] == ["started", "finished"]
    finished = rows[-1]
    assert finished.status == "ok"
    assert finished.result["steps"] == 4 and finished.result["url"] == "https://example.com"
    assert "answer" in finished.output_ref
    assert finished.scope == computer_unit_scope(GROUP)


async def test_an_off_call_is_audited_too(persons):
    events = FakeComputerEventStore()
    await _browse(persons, browser=None, events=events)(objective="x")
    assert any(e.tool == "computer_browse" for e in events.events)


async def test_a_long_answer_is_cut_to_the_output_limit(persons):
    runner = FakeBrowserRunner(BrowseOutcome(status="ok", answer="a" * 50_000))
    got = await _browse(persons, browser=runner,
                        settings=ComputerToolSettings(max_output_chars=1000))(objective="x")
    assert len(got["answer"]) < 1200
