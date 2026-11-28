"""The called room floor and its graph IDs through the real Record and retrieval."""

from dataclasses import replace
from datetime import timedelta
from xml.etree import ElementTree

import pytest

from nora.access import IdentityAccess
from nora.agent.prefill import engagement_floor
from nora.domain.framing import TAG_HEADER
from nora.domain.models import Quote
from nora.engagement.context import TranscriptInjection
from nora.engagement.graph_context import EngagementGraph
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
    item_graph_id,
)
from nora.memory import wire
from nora.obs.metrics import Metrics
from tests.infra.test_pass_anchors import pointers
from tests.infra.test_pass_assembly import _counted
from tests.infra.test_retrieval_scope import PERSON, ROOM, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scope_scenario
from tests.infra.test_situation_staleness import intention
from tests.unit.test_engagement_pipeline import _msg, _request

scenario = scope_scenario


def elements(rendered):
    return ElementTree.fromstring(f"<context>{rendered}</context>")  # noqa: S314


async def _prompt(graph, ctx, row, now, *, unit=UNIT, catalogue=""):
    floor = engagement_floor(
        ctx, display_name="Nora", tagger_name="Fixture reader",
        context_window=10, now=now, catalogue=catalogue,
    )
    return await graph.prompt(
        "called", ctx, floor=floor, unit=unit, display_name="Nora",
        now=now, header=TAG_HEADER, row_id=row,
    )


@pytest.fixture
async def called(scenario):
    await pointers(scenario)
    await scenario.claim("sender-claim", content={"text": "Generated sender fact"})
    await scenario.claim("room-claim", content={"text": "Generated room fact"})
    await write_edge(scenario, "sender-claim", "person-here", "about")
    await write_edge(scenario, "room-claim", "room-here", "about")
    inbound = _msg(ROOM)
    index = {**scenario.persons.index, ("signal", inbound.sender_id): PERSON}
    persons = replace(scenario.persons, index=index)
    access = IdentityAccess.for_files(persons, scenario.policy)
    registration = SqlItemRegistration(
        RegistrationScope(scenario.policy, persons),
        RuntimeRevisions({}, scenario.policy, persons),
    )
    records = SqlRecordStore(scenario.engine, registration=registration)
    now = await scenario.now()
    timestamp = int((now - timedelta(seconds=3)).timestamp() * 1000)
    parent = replace(inbound, timestamp=timestamp, body="A recorded question")
    await records.append(message=parent, person_id=PERSON)
    msg = replace(inbound, timestamp=timestamp + 1, body="An addressing message",
                  quote=Quote(quoted_ts=timestamp, author_id=parent.sender_id,
                              text="Untrusted quoted copy"))
    row = await records.append(message=msg, person_id=PERSON)
    assembler = TranscriptInjection(
        audit=records, persons=persons, context_window=10,
        metrics=Metrics(), policy=scenario.policy, access=access,
    )
    ctx = await assembler.assemble(request=_request(msg))
    return access, ctx, row, await scenario.now()


async def test_called_presents_the_floor_and_scoped_profiles_in_assembled_order(scenario, called):
    access, ctx, row, now = called
    assembler, retrieval = _counted(scenario)
    graph = EngagementGraph(access, wire.message_graph_ids(scenario.engine), assembler)

    _, rendered, shown = await _prompt(
        graph, ctx, row, now, catalogue="- memory_read: Read a row.",
    )

    blocks = elements(rendered)
    assert [b.tag for b in blocks] == [
        "profile", "lessons", "profile", "profile",
        "members", "clock", "presence", "tool_catalogue", "transcript",
        "replying_to", "tag_message", "profile", "standing", "related",
    ]
    assert blocks[2].get("of") == "room-here"
    assert "Generated room fact" in (blocks[2].text or "")
    assert blocks[11].get("of") == "person-here"
    assert "Generated sender fact" in (blocks[11].text or "")
    assert {"sender-claim", "room-claim", item_graph_id("messages", (row,), UNIT)} <= shown
    assert blocks[9].text.strip() == "A recorded question"
    assert len(retrieval.reads) == 1


async def test_called_anchor_failure_leaves_the_floor_and_names_the_cause(scenario, called):
    access, ctx, row, now = called
    assembler, retrieval = _counted(scenario)
    ids = wire.message_graph_ids(scenario.engine)

    class BrokenIds:
        async def read(self, room, unit, wanted):
            if row in wanted:
                raise OSError("Generated anchor read failure")
            return await ids.read(room, unit, wanted)

    _, rendered, shown = await _prompt(
        EngagementGraph(access, BrokenIds(), assembler), ctx, row, now,
    )

    blocks = elements(rendered)
    assert blocks.find("tag_message").text.strip() == "An addressing message"
    assert "A recorded question" in blocks.find("replying_to").text
    presentations = [b for b in blocks if b.tag.startswith("graph_") or b.tag == "related"]
    assert presentations
    assert all(b.get("status") == "unavailable"
               and b.get("cause") == "message_anchor_read_failed" for b in presentations)
    assert not shown
    assert retrieval.reads == []


async def test_a_room_without_a_unit_does_not_read_message_graph_ids(scenario, called):
    access, ctx, row, now = called
    assembler, retrieval = _counted(scenario)
    reads = []
    real_ids = wire.message_graph_ids(scenario.engine)

    class CountedIds:
        async def read(self, room, unit, ids):
            reads.append((room, unit, ids))
            return await real_ids.read(room, unit, ids)

    _, rendered, shown = await _prompt(
        EngagementGraph(access, CountedIds(), assembler), ctx, row, now, unit=None,
    )

    blocks = elements(rendered)
    assert blocks.find("standing").get("cause") == "reading_room_unavailable"
    assert blocks.find("tag_message").text.strip() == "An addressing message"
    assert not shown and not reads and not retrieval.reads


async def test_a_called_pass_carries_the_graph_lesson_and_no_legacy_block(scenario, called):
    access, ctx, row, now = called
    await scenario.claim("fixture-lesson", part="lesson")
    await write_edge(scenario, "fixture-lesson", "nora-pointer", "about")
    assembler, _retrieval = _counted(scenario)

    _, rendered, shown = await _prompt(
        EngagementGraph(access, wire.message_graph_ids(scenario.engine), assembler),
        ctx, row, now,
    )

    lessons = elements(rendered).findall("lessons")
    assert len(lessons) == 1 and "fixture-lesson" in (lessons[0].text or "")
    assert "fixture-lesson" in shown


async def test_called_uses_standing_and_keeps_room_situations(scenario, called):
    access, ctx, row, now = called
    await intention(scenario, "unit-standing", origin_room=None, asserted_at=now)
    await scenario.claim("room-situation", part="situation", asserted_at=now)
    await write_edge(scenario, "room-situation", "room-here", "about")
    assembler, _retrieval = _counted(scenario)

    _, rendered, shown = await _prompt(
        EngagementGraph(access, wire.message_graph_ids(scenario.engine), assembler),
        ctx, row, now,
    )

    blocks = elements(rendered)
    standing = blocks.findall("standing")
    assert len(standing) == 1
    assert "unit-standing" in standing[0].text
    assert "room-situation" in standing[0].text
    assert {"unit-standing", "room-situation"} <= shown
    assert not {"notes", "memos"} & {block.tag for block in blocks}


@pytest.mark.parametrize("scope", [
    {"unit": "different-unit"},
    {"excluded_identities": [PERSON]},
], ids=["unit-boundary", "excluded-identity"])
async def test_called_standing_omits_unreadable_unit_intentions(scenario, called, scope):
    access, ctx, row, now = called
    await intention(scenario, "visible-standing", origin_room=None, asserted_at=now)
    await intention(scenario, "unreadable-standing", origin_room=None, asserted_at=now, **scope)
    assembler, _retrieval = _counted(scenario)

    _, rendered, shown = await _prompt(
        EngagementGraph(access, wire.message_graph_ids(scenario.engine), assembler),
        ctx, row, now,
    )

    assert "visible-standing" in elements(rendered).find("standing").text
    assert "unreadable-standing" not in rendered
    assert "unreadable-standing" not in shown
