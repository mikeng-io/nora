"""The act envelope and the graph renderer hold the pass's egress boundary."""

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

from nora.agent.delivery_acts import DeliveryActs
from nora.agent.egress import internal_span
from nora.agent.guards import Deliverer
from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget
from nora.memory import ActAuthority, ActEnvelope, ActRefused, PassContext, act_event, declare_act
from nora.memory.app.acts import ActStore
from nora.memory.domain.act_executors import EXECUTORS
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeRecordStore
from tests.fakes import SinkSender
from tests.test_relay import _policy, _relay, _send
from tests.unit.test_document_tools import _doc, _service, _tools
from tests.unit.test_graph_row_render import ITEM, RENDER, render_graph_row

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = "The lighthouse committee meets beside the blue cabinet after sunrise."
PUBLIC = "The committee has an early meeting."
TARGET = ConversationTarget("signal", "room", "group")


def _authority(occasion):
    context = PassContext(
        "unit", "room", False, occasion, "fixture", "pass-1", "", "model",
        "", "", "", "", "", "",
    )
    return ActAuthority("unit", "person", room="room", pass_context=context)


def _declare(executor, *, from_pass=True):
    return declare_act(
        executor, "signal:room", event="timer-1", authority=ActAuthority("unit"),
        from_pass=from_pass,
        key="request-1" if EXECUTORS[executor].guarantee == "idempotent_effect" else None,
    )


def test_act_construction_has_one_declaration_and_one_store_reader():
    sites = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "nora").rglob("*.py")
        for call in ast.walk(ast.parse(path.read_text()))
        if isinstance(call, ast.Call) and (
            (isinstance(call.func, ast.Name) and call.func.id == "Act")
            or (isinstance(call.func, ast.Attribute) and call.func.attr == "Act")
        )
    ]
    assert sorted(sites) == [
        "nora/memory/adapters/act_store.py", "nora/memory/app/act_scope.py",
    ]


@pytest.mark.parametrize("occasion", ["debrief", "wake", "review"])
def test_non_engagement_refuses_every_executor(occasion):
    with act_event("pass-1", _authority(occasion)):
        for executor in EXECUTORS:
            with pytest.raises(ActRefused, match=r"^non_engagement_pass$"):
                _declare(executor)


@pytest.mark.parametrize("occasion", ["called", "ambient", "proactive"])
def test_engagement_admits_every_executor(occasion):
    authority = _authority(occasion)
    with act_event("pass-1", authority):
        for executor, spec in EXECUTORS.items():
            act = _declare(executor)
            assert (act.identity.executor, act.identity.ordinal) == (executor, 0)
            assert act.authority == authority
            assert (act.kind, act.guarantee) == (spec.kind, spec.guarantee)


def test_deterministic_act_inside_wake_keeps_its_own_authority():
    with act_event("pass-1", _authority("wake")):
        act = _declare("platform.otp", from_pass=False)
    assert act == _declare("platform.otp")
    assert act.identity.trigger_id == "timer-1"
    assert act.authority.pass_context is None


def _delivery(*, delivery_acts=None):
    sink = SinkSender()
    store = FakeRecordStore()
    store.delivery_acts = delivery_acts
    metrics = Metrics()
    delivery = Deliverer(
        store=store, adapter=sink, target=TARGET, doctrine=Doctrine((), "", "", "", "", 0),
        canary="", tag_id="pass-1", nora_sender_id="nora", message_cap=2,
        audience="tag", metrics=metrics, content_checks=False,
    )
    return delivery, sink, metrics


async def test_delivery_act_refusal_reaches_the_tool_before_reservation():
    store = create_autospec(ActStore, instance=True, spec_set=True)
    store.reserve.side_effect = lambda act: act.identity.executor == "platform.reply"
    scope = SimpleNamespace(
        room_unit=lambda platform, room: "unit" if (platform, room) == ("signal", "room") else None,
        person_id=AsyncMock(side_effect=lambda _platform, author: author),
    )
    delivery, sink, metrics = _delivery(delivery_acts=DeliveryActs(ActEnvelope(store), scope, dict))
    persons = Persons(by_person={}, index={})
    tools = {tool.name: tool.call for tool in build_tool_specs(
        store=None, conversation_id="room", conversation_type="group", persons=persons,
        honcho=None, deliverer=delivery, metrics=metrics,
        room=Room(platform="signal", conversation_id="room", roster_source=None, persons=persons),
        transit_source=None,
    )}
    with act_event("pass-1", _authority("wake")):
        assert await tools["send_reply"](text=PUBLIC) == "unavailable"
    store.reserve.assert_not_awaited()
    assert sink.sent == []
    with act_event("pass-1", _authority("called")):
        assert await tools["send_reply"](text=PUBLIC) == "sent"
    store.reserve.assert_awaited_once()
    assert sink.sent == [(TARGET, PUBLIC)]


def _show(content=PRIVATE, *, exposure="internal"):
    return render_graph_row(replace(ITEM, content=content, exposure=exposure), **RENDER)


def test_internal_row_is_marked_and_external_row_is_not():
    assert " | exposure=internal | " in _show()
    assert "exposure=" not in _show(exposure="external")


@pytest.mark.parametrize(
    ("exposure", "draft", "sent"),
    [
        pytest.param("internal", PRIVATE, False, id="internal"),
        pytest.param("internal", PRIVATE.replace("blue", "b\u200blue"), False, id="canonical"),
        pytest.param("internal", PUBLIC, True, id="paraphrase"),
        pytest.param("external", PRIVATE, True, id="external"),
    ],
)
async def test_delivery_checks_internal_spans_independent_of_content_checks(exposure, draft, sent):
    delivery, sink, metrics = _delivery()
    with act_event("pass-1", _authority("called")):
        assert PRIVATE in _show(exposure=exposure)
        assert await delivery.deliver(draft) is sent
    assert sink.sent == ([(TARGET, draft)] if sent else [])
    assert metrics.get("nora_egress_blocks_total", kind="internal_exposure") == int(not sent)


async def test_relay_refuses_an_internal_quote_before_speaking():
    relay, fire, _ = _relay(_policy())
    with act_event("pass-1", _authority("called")):
        _show()
        assert (await _send(relay, text=PRIVATE))["status"] == "refused"
        assert (await _send(relay, text=PUBLIC))["status"] == "ok"
    assert len(fire.spoken) == 1
    assert fire.spoken[0][2].endswith(PUBLIC)


async def test_document_publish_returns_a_finding_for_an_internal_quote():
    publish = _tools(_service())["document_publish"]
    with act_event("pass-1", _authority("called")):
        _show()
        refused = await publish(spec=_doc(PRIVATE))
        assert refused["status"] == "bad_arguments"
        assert refused["findings"] == ["Remove the quoted internal material."]
        assert (await publish(spec=_doc(PUBLIC)))["status"] == "ok"


def test_internal_corpus_exists_only_in_the_pass_that_rendered_the_row():
    _show()
    assert internal_span(PRIVATE) is None
    with act_event("pass-1", _authority("called")):
        assert internal_span(PRIVATE) is None
        _show()
        assert internal_span(PRIVATE) == "internal_exposure"
        with act_event("nested", ActAuthority("unit")):
            assert internal_span(PRIVATE) is None
        assert internal_span(PRIVATE) == "internal_exposure"
    assert internal_span(PRIVATE) is None


@pytest.mark.parametrize(
    ("content", "location_visible"),
    [
        pytest.param({"lat": 22, "lng": 114}, False, id="structured"),
        pytest.param("https://geo.example/?lat=22.41&lng=114.12", False, id="url"),
        pytest.param("Meet at 22.41, 114.12.", False, id="pair"),
        pytest.param("The report costs 22.41 credits.", True, id="ordinary-number"),
    ],
)
def test_graph_render_preserves_numbers_and_masks_coordinates(content, location_visible):
    line = _show(content)
    if location_visible:
        assert content in line
        assert "[location withheld]" not in line
    else:
        assert "[location withheld]" in line
        assert "22" not in line and "114" not in line
