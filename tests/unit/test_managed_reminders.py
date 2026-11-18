"""The reminder tools read the selected managed authority, not a file roster."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nora.agenda.adapters.tools import build
from nora.agenda.adapters.tools.shared import _UNKNOWN_OWNER
from nora.agent.room import Room
from nora.agent.tool_deps import ToolDeps
from nora.domain.management import Scope, ScopeKind
from nora.obs.metrics import Metrics
from nora.testing import FakeRosterSource
from nora.testing.reminders import FakeReminderStore
from tests.unit.test_scoped_engagement_names import (
    _M_PERSON,
    _M_ROOM,
    _managed,
    _managed_person,
)

_T0 = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
_FOREIGN_UNIT = Scope(ScopeKind.UNIT, "generated-foreign-unit")
_SOON = (_T0 + timedelta(hours=2)).isoformat()


def _room(access, tagger=None, members=("+852111",)):
    return Room(platform="signal", conversation_id="g1", access=access, tagger=tagger,
                roster_source=FakeRosterSource(groups={"g1": (frozenset(members),)}))


def _tools(room, store):
    deps = ToolDeps(
        store=None, conversation_id="g1", conversation_type="group",
        persons=room.access.persons_view(), honcho=None, deliverer=None,
        metrics=Metrics(), room=room, transit_source=None,
        reminders=store, clock=lambda: _T0)
    return {tool.name: tool.call for tool in build(deps)}


async def test_the_reminder_owner_is_the_canonical_person_the_room_scope_names():
    bindings, access = _managed()
    await _managed_person(bindings)
    for scope, nickname in (
            (_M_ROOM, "Generated Room Nick"),
            (_FOREIGN_UNIT, "Generated Foreign Unit Nick")):
        assert (await bindings.set_scoped_name(
            person_id=_M_PERSON, scope=scope, nickname=nickname,
            operation_id=f"generated-name-{scope.id}")).status.value == "complete"

    [tagger] = (await _room(access).members()).people
    assert tagger.person_id == _M_PERSON
    # The room's ancestry answers its own nick; a foreign unit's never substitutes.
    assert tagger.name == "Generated Room Nick"

    store = FakeReminderStore()
    tools = _tools(_room(access, tagger=tagger), store)
    assert (await tools["remind"](when=_SOON, text="generated wash"))["status"] == "ok"
    [row] = await store.pending_for("g1")
    assert row.requester_person_id == _M_PERSON  # the canonical id, never the sender_id

    listed = (await tools["list_reminders"]())["reminders"]
    # managed `persons_view` is the honest empty view: an unknown owner is
    # reported, never a foreign unit's or a file name.
    assert [r["set_by"] for r in listed] == [_UNKNOWN_OWNER]
    assert "Generated Foreign Unit Nick" not in str(listed)


async def test_an_unmapped_sender_names_nobody_and_sets_no_reminder():
    _, access = _managed()
    view = await _room(access, members=("+852999",)).members()
    assert view.people == () and view.unnamed == 1

    store = FakeReminderStore()
    tools = _tools(_room(access, tagger=None, members=("+852999",)), store)
    assert (await tools["remind"](when=_SOON, text="generated wash"))["status"] == "refused"
    assert not await store.pending_for("g1")
