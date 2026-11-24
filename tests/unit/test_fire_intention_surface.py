"""The fire model receives the intention write on its composed occasions."""

from __future__ import annotations

import pytest

from nora.agent.registry import ToolRegistry
from nora.app.fire import lost_job_occasion, relay_occasion
from nora.domain.events import Event
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeAgentRunner
from tests.unit.test_fire_path import (
    MILES,
    _compute_job,
    _fire_path,
    _job_occasion,
    _send_turns,
)


@pytest.mark.parametrize("kind", ["compute", "compute_lost", "relay", "other"])
async def test_fire_grants_intention_commit_at_its_occasion(kind, doctrine_store):
    room = Conversation(
        platform="signal", ids=frozenset({"conv-1"}), persons=None,
        label="Fixture", primary_id="conv-1", unit="fixture",
    )
    events = {
        "compute": _job_occasion(),
        "compute_lost": lost_job_occasion(_compute_job()),
        "relay": relay_occasion(
            destination=room, requester=MILES, text="Dinner is at eight.", from_platform="discord",
        ),
        "other": Event(
            kind="other", platform="signal", conversation_id="conv-1", conversation_type="group",
            person_id=MILES.person_id, body="The import finished.", header=lambda *_: "",
        ),
    }
    calls = []
    path = _fire_path(
        doctrine_store=doctrine_store,
        persons=Persons(by_person={MILES.person_id: MILES}, index={}),
        metrics=Metrics(),
        policy=Policy(conversations=(room,), _by_id={("signal", "conv-1"): room}),
        registry=ToolRegistry(intention_admission=object()),
        runner=FakeAgentRunner(_send_turns("I have the update."), on_call=calls.append),
    )

    assert await path.speak(events[kind]) == "sent"
    (call,) = calls
    tools = {tool.tool_name for tool in call["tools"]}
    assert "send_reply" in tools
    assert ("intention_commit" in tools) == (kind != "other")
    assert not {"note_create", "note_revise"} & tools
