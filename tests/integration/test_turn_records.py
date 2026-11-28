"""The three real write paths retain rows in the shared trace store."""
from datetime import UTC, datetime

import pytest

from nora.agent.registry import ToolRegistry
from nora.app.fire import FirePath
from nora.domain.events import Event
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeAdapter, FakeAgentRunner, ScriptedTurn
from tests.infra.test_tag_trace_store import _tag_trace_db as _tag_trace_db
from tests.infra.test_tag_trace_store import tag_trace_engine as tag_trace_engine
from tests.unit.test_engagement_pipeline import _build_pipeline, _drain, _request


def _policy():
    convo = Conversation(platform="signal", ids=frozenset({"room-a"}),
                         primary_id="room-a", unit="u1", persons=None)
    return Policy(conversations=(convo,), _by_id={("signal", "room-a"): convo})


def _agent():
    return FakeAgentRunner([ScriptedTurn(tool_calls=[("send_reply", {"text": "checked"})])])


@pytest.mark.parametrize("occasion", ["tag", "fire"])
async def test_tag_and_fire_write_the_trace_and_link_their_sent_rows(
    tag_trace_engine, doctrine_store, occasion,
):
    traces = SqlTagTraceStore(tag_trace_engine)
    audit = SqlRecordStore(tag_trace_engine)
    if occasion == "tag":
        pipeline, _, _, background = _build_pipeline(
            audit=audit, runner=_agent(), tag_trace_store=traces, policy=_policy(),
        )
        msg = NormalizedMessage(
            platform="signal", conversation_id="room-a", conversation_type="group",
            sender_id="generated-account", sender_name="Member 1", timestamp=1,
            body="check", raw={},
        )
        await pipeline.run(_request(msg))
        await _drain(background)
    else:
        path = FirePath(
            persons=Persons(by_person={}, index={}), records=audit,
            doctrine_store=doctrine_store, metrics=Metrics(), runner=_agent(),
            adapters={"signal": FakeAdapter(account="self")}, registry=ToolRegistry(),
            canary="test-canary", display_name="Nora", ai_peer="nora", context_window=10,
            limits={"turns": 4, "output_tokens": 400, "total_tokens": 24000},
            timeout_seconds=5, policy=_policy(), tag_trace_store=traces,
            clock=lambda: datetime(2026, 10, 1, tzinfo=UTC),
        )
        await path.speak(Event(
            platform="signal", conversation_id="room-a", conversation_type="group",
            person_id=None, body="check", header=lambda _p, _n: "check", kind="reminder",
        ))
    rows = await traces.recent(conversation_id="room-a", limit=2)
    assert rows[0].occasion == occasion
    assert rows[0].unit == "u1"
    assert rows[0].sent_message_ids
    sent = await audit.recent(conversation_id="room-a", limit=10)
    own = [r for r in sent if r.is_nora and r.id in rows[0].sent_message_ids]
    assert own and own[-1].turn_handle == rows[0].handle
    assert own[-1].id in rows[0].sent_message_ids


