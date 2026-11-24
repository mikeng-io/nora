"""A proactive pass without a delivered graph ID can record an open matter."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nora.memory import ActAuthority, act_pass
from nora.memory.adapters.tools.intentions import _make_intention_commit
from nora.memory.domain.admission import fields
from nora.memory.domain.admission_ports import AdmissionResult, Stored
from nora.obs.metrics import Metrics
from tests.unit.test_cognitive_graph_admission import PASS

_CONTEXT = replace(PASS, occasion="proactive", writer="engagement")
_OTHER = "intention:unrelated"
_WITNESS = "item:witness"
_SHOWN = frozenset({_OTHER, _WITNESS})

_OPEN = {
    "op": "open", "form": "achieve", "owner": "nora", "owed_to": "nora",
    "condition": {"form": "untyped", "value": "The room settles the open question."},
    "deadline": "2027-01-02T10:00:00+08:00",
    "delivery": "when_it_comes_up",
}
_TIME = {"kind": "time", "at": "2027-01-01T11:00:00+08:00", "zone": "Asia/Hong_Kong"}
_SCHEDULE = {**_OPEN, "delivery": "choose_moment", "trigger": _TIME}
_CASES = (
    pytest.param({**_SCHEDULE, "rooms": ["room-2"]}, "rooms", id="target-another-room"),
    pytest.param({
        **_SCHEDULE,
        "trigger": {**_TIME, "recurrence": {"every": "daily", "count": 5}},
    }, "delivery", id="recurring-delivery"),
    pytest.param({"op": "drop", "base": _OTHER}, "op", id="drop-unrelated"),
    pytest.param({"op": "close", "base": _OTHER, "witness": _WITNESS}, "op", id="close-unrelated"),
    pytest.param({**_OPEN, "op": "revise", "base": _OTHER}, "base", id="revise-unrelated"),
)


@pytest.fixture
def tool_and_admission():
    def admit(proposals, _context, _shown):
        outcomes = tuple(Stored(fields(proposal).id) for proposal in proposals)
        return AdmissionResult(outcomes, (), ())

    admission = AsyncMock(side_effect=admit)
    tool = _make_intention_commit(SimpleNamespace(
        intention_admission=SimpleNamespace(admit=admission), metrics=Metrics(),
    ))
    return tool, admission


@pytest.mark.parametrize("operation,field", _CASES)
async def test_unbound_proactive_pass_refuses_non_passive_writes(
    operation, field, tool_and_admission,
):
    tool, admission = tool_and_admission
    shown = _SHOWN if "base" in operation else frozenset()
    with act_pass(ActAuthority(_CONTEXT.unit, pass_context=_CONTEXT), shown=shown):
        result = await tool([operation])
    assert result["status"] == "refused"
    (outcome,) = result["operations"]
    assert (outcome["reason"], outcome["field"]) == ("passive_commit_only", field)
    admission.assert_not_awaited()


@pytest.mark.parametrize("operation,_field", _CASES)
async def test_bound_proactive_pass_still_refuses_other_intentions(
    operation, _field, tool_and_admission,
):
    tool, admission = tool_and_admission
    authority = ActAuthority(_CONTEXT.unit, intention="intention:delivered", pass_context=_CONTEXT)
    with act_pass(authority, shown=_SHOWN):
        result = await tool([operation])
    assert result["status"] == "refused"
    assert result["operations"][0]["reason"] == "not_this_delivery"
    admission.assert_not_awaited()


async def test_only_the_creating_pass_can_revise_its_passive_intention(tool_and_admission):
    tool, admission = tool_and_admission
    authority = ActAuthority(_CONTEXT.unit, pass_context=_CONTEXT)
    with act_pass(authority):
        first = await tool([_OPEN])
        assert first["status"] == "ok"
        base = first["operations"][0]["id"]
        scheduled = await tool([{**_SCHEDULE, "op": "revise", "base": base}])
        assert scheduled["operations"][0]["field"] == "delivery"
        for _ in range(2):
            edited = await tool([{**_OPEN, "op": "revise", "base": base}])
            assert edited["status"] == "ok"
            base = edited["operations"][0]["id"]
    next_pass = replace(_CONTEXT, pass_id=f"{_CONTEXT.pass_id}-next")
    with act_pass(replace(authority, pass_context=next_pass), shown=frozenset({base})):
        refused = await tool([{**_OPEN, "op": "revise", "base": base}])
    assert refused["operations"][0]["reason"] == "passive_commit_only"
    assert admission.await_count == 3
