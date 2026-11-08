"""Generated shared-context journeys: assembly, unit/disclosure boundaries and states."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from nora.agent.egress import scan_reply
from nora.agent.lineage import LineagePolicy
from nora.app.cognition_context import CognitionContextReader
from nora.domain.block_types import BLOCK_TYPES
from nora.domain.blocks import assemble_declared
from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.composed import COMPOSERS, LEGACY_COMPOSERS
from nora.domain.context_data import TranscriptLine
from nora.domain.lineage import RoomFacts
from nora.domain.media_join import MediaJoin
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.domain.record_query import RecordHit
from nora.domain.self_presence import KnownRoom, UnitRooms
from nora.language.cognition_render import _BLOCKS, RenderBudget, render_agenda
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeMediaObjectStore
from tests.unit.test_media_join import SHA, _obj

NOW = 1_800_000_000_000
A = Room("signal", "alpha")
ITEM = AgendaItem(
    kind="message", room=A, summary="one change", stimulus_ids=(1,), cues=(), surprise=1
)


def row(n, pid="p1", body="hello"):
    return TranscriptRow(
        n, pid, pid, NOW - (10 - n) * 1000, body, False, DeliveryStatus.SENT, None, person_id=pid
    )


class Sources:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def context_rows(self, q, *, as_of_ms):
        self.calls.append((q.rooms, as_of_ms))
        if self.fail:
            raise RuntimeError("generated outage")
        cid = q.rooms[0][1]
        data = {
            "alpha": [
                replace(
                    row(2),
                    body="[image a3f2c1]",
                    media_json='[ {"sha256":"' + SHA + '","media_type":"image/png","size":4} ]',
                )
            ],
            "beta": [row(1, body="[image a3f2c1]"), row(3, "p2")],
            "foreign": [row(4, body="foreign secret")],
        }[cid]
        return tuple(RecordHit("signal", cid, r) for r in data), False, frozenset()

    async def account_heads(self, *, key, as_of_ms):
        assert as_of_ms == NOW
        return ()

    async def bodies(self, *, unit, as_of_ms):
        assert unit == "u"
        return ("Check the source before correcting a person.",)

    async def room_activity(self, *, conversation_id, since_ts, platform, as_of_ms):
        return SimpleNamespace(last_ts=NOW - 1000)


async def build(*, lineage_on=False, beta_people=frozenset({"p1", "p2"}), fail=False,
                foreign_agenda=False):
    p1 = PersonRef("p1", "Ada", "p1", aliases=("Ace",))
    p2 = PersonRef("p2", "Ben", "p2")
    persons = Persons({"p1": p1, "p2": p2}, {("signal", "p1"): "p1", ("signal", "p2"): "p2"})
    rooms = [
        SimpleNamespace(
            platform="signal",
            primary_id=cid,
            ids=frozenset({cid}),
            label=cid.title(),
            unit="other" if cid == "foreign" or foreign_agenda else "u",
            persons=beta_people if cid == "beta" else frozenset({"p1", "p2"}),
        )
        for cid in ("alpha", "beta", "foreign")
    ]
    facts = tuple(
        RoomFacts(
            platform=c.platform,
            ids=c.ids,
            primary_id=c.primary_id,
            unit=c.unit,
            persons=c.persons,
            label=c.label,
        )
        for c in rooms
    )
    policy = SimpleNamespace(
        conversation=lambda p, c: next(
            (r for r in rooms if (r.platform, r.primary_id) == (p, c)), None
        ),
        shows_self_to_unit=lambda u: True,
    )
    lineage = LineagePolicy(
        rooms=facts,
        mode="on" if lineage_on else "off",
        _by_id={(r.platform, r.primary_id): r for r in facts},
    )

    class Directory:
        async def rooms(self, **kw):
            assert kw["conversation_id"] == "alpha"
            return UnitRooms(
                tuple(KnownRoom("signal", c.label, c.primary_id, False, "up") for c in rooms)
            )

    class Binding:
        async def members(self):
            return SimpleNamespace(people=(p1, p2), available=True, degraded=False, unnamed=0)

        scope = members

    source = Sources(fail=fail)
    reader = CognitionContextReader(
        policy=policy,
        persons=persons,
        lineage=lineage,
        records=source,
        directory=Directory(),
        audit=source,
        profiles=source,
        lessons=source,
        media_join=MediaJoin(
            store=FakeMediaObjectStore([_obj(title="generated picture")]),
            screen=scan_reply,
            metrics=Metrics(),
        ),
        room_for=lambda *a: Binding(),
        screen=scan_reply,
        record_chars=4000,
    )
    agenda_row = replace(
        row(2),
        body="Ace sent [image a3f2c1]",
        media_json='[{"sha256":"' + SHA + '","media_type":"image/png","size":4}]',
    )
    grounding = Grounding(
        room_labels={"signal:alpha": "Alpha"},
        records={
            "messages:2": RecordItem(
                "messages:2", "message", 2, A, "Ada", agenda_row.ts, agenda_row.body, person_id="p1"
            )
        },
    )
    result = await reader.read(
        "u", [ITEM], grounding, [("signal", "alpha", agenda_row, 1)], now_ms=NOW
    )
    return result, source


async def test_reader_scope_related_profiles_and_real_assembly():
    grounding, source = await build()
    assert {v.of for v in grounding.profiles if v.entity == "person"} == {"p1"}
    assert {v.of for v in grounding.profiles if v.entity == "room"} == {"alpha"}
    assert {x.room.label for x in grounding.rooms} == {"Alpha", "Beta"}
    assert {x.row.id for x in grounding.transcript} == {1, 2}
    assert len(source.calls) == 2
    prompt = render_agenda(
        [ITEM], grounding=grounding, now_ms=NOW, budget=RenderBudget(record_chars=4000)
    )
    root = ET.fromstring("<root>" + prompt + "</root>")  # noqa: S314 - generated renderer output
    assert {x.tag for x in root} == {d.name for d in _BLOCKS}
    messages = root.findall("./transcript/m")
    assert [x.attrib["source_id"] for x in messages] == ["1", "2"]
    assert [x.attrib["room"] for x in messages] == ["Beta", "Alpha"]
    assert all(x.attrib["author_id"] == "p1" for x in messages)
    assert root.find("./members/person").attrib["id"] == "p1"
    assert root.find("./lessons/lesson") is not None
    assert prompt.count("generated picture") == 1
    assert "foreign secret" not in prompt and "person:p2" not in prompt


async def test_disclosure_negative_has_an_allowed_positive_control():
    allowed, _ = await build(lineage_on=True)
    refused, _ = await build(lineage_on=True, beta_people=frozenset({"p2"}))
    assert {x.room.label for x in allowed.rooms} == {"Alpha", "Beta"}
    assert {x.room.label for x in refused.rooms} == {"Alpha"}
    assert {x.row.id for x in refused.transcript} == {2}
    assert dict(refused.context_withheld)["rooms"] == 1


async def test_failed_transcript_read_is_unavailable_not_empty():
    grounding, _ = await build(fail=True)
    text = render_agenda([ITEM], grounding=grounding, now_ms=NOW)
    root = ET.fromstring("<root>" + text + "</root>")  # noqa: S314 - generated renderer output
    assert root.find("transcript").attrib["status"] == "unavailable"
    assert root.find("./rooms/room") is not None
    assert root.find("./lessons/lesson") is not None


def test_hard_total_interleaves_counts_and_escapes_shared_rows():
    rows = tuple(
        TranscriptLine(
            row(i, body="<forged> & " + ("x" * 600)),
            "Alpha" if i % 2 else "Beta",
            platform="signal",
            conversation_id="alpha" if i % 2 else "beta",
        )
        for i in range(1, 9)
    )
    text = render_agenda(
        [ITEM],
        grounding=Grounding(transcript=rows),
        now_ms=NOW,
        budget=RenderBudget(record_chars=1400, min_item_chars=100),
    )
    root = ET.fromstring("<root>" + text + "</root>")  # noqa: S314 - generated renderer output
    transcript = root.find("transcript")
    kept = list(transcript)
    assert kept and all(x.tag == "m" and not list(x) for x in kept)
    assert len(kept) + int(transcript.attrib["not_listed"]) == 8
    assert sum(len(ET.tostring(x, encoding="unicode")) for x in kept) <= 1400


def test_governance_declared_decisions_shared_ownership_and_no_legacy_escape():
    from pathlib import Path

    spec = Path("design/context-assembly/05-the-cognition-context.md").read_text()
    import re

    decision_table = spec.split("| Decision |", 1)[1].split("**Read but not injected**", 1)[0]
    links = set()
    decision = ""
    for row in decision_table.splitlines():
        cells = row.strip().strip("|").split("|")
        if len(cells) != 3 or not row.startswith("|"):
            continue
        decision = cells[0].strip() or decision
        for name in re.findall(r"`([a-z_]+)`", cells[2].split("(", 1)[0]):
            links.add((decision, name))
    for declaration in _BLOCKS:
        assert (declaration.decision, declaration.name) in links
        assert BLOCK_TYPES[declaration.name].store
    stores = [v.store for v in BLOCK_TYPES.values() if v.store]
    assert len(stores) == len(set(stores))
    assert not any(
        "cognition_render:" in x or "comprehend_render:render_" in x for x in LEGACY_COMPOSERS
    )
    assert "nora.language.register_render:render_register" in COMPOSERS
    with pytest.raises(ValueError, match="undeclared failed read"):
        assemble_declared(_BLOCKS, [], unavailable={"invented"})


def test_assembly_rejects_undeclared_fields_and_failed_content():
    from nora.language.context_render import render_clock

    block = render_clock(now=datetime.fromtimestamp(NOW / 1000, UTC), last_session_ms=None)
    assert assemble_declared(_BLOCKS, [block])
    with pytest.raises(ValueError, match=r"undescribed.*field"):
        assemble_declared(_BLOCKS, [replace(block, attrs=((*block.attrs, ("invented", "yes"))))])
    with pytest.raises(ValueError, match=r"failed read.*content|content.*failed read"):
        assemble_declared(_BLOCKS, [block], unavailable={"clock"})


async def test_wired_session_hands_the_shared_candidate_input_to_its_runner():
    from nora.domain.stimuli import Stimulus, UnitView
    from nora.testing.cognition_grounding import StaticGrounder
    from tests.unit.test_cognition_grounding import _Agent, _runner

    grounding, _ = await build()
    agent = _Agent()
    runner, _, ledger = _runner(agent, StaticGrounder(grounding))
    stimulus = Stimulus(
        id=1,
        unit="u",
        kind="message",
        item="message",
        ref="messages:2",
        at_ms=NOW - 8000,
        recorded_at_ms=NOW - 8000,
        platform="signal",
        conversation_id="alpha",
        cues=(),
    )
    view = UnitView(unit="u", pending=(stimulus,), last_activity_ms=None, last_wake=None)
    await runner.on_wake(unit="u", view=view, now_ms=NOW, wake_id=1)
    assert len(agent.prompts) == 1 and len(ledger.sessions) == 1
    candidate = ET.fromstring("<root>" + agent.prompts[0] + "</root>")  # noqa: S314
    assert {b.tag for b in candidate} == {d.name for d in _BLOCKS}
    assert candidate.find("./transcript/m").attrib["source_id"] == "1"
    assert candidate.find('./profile[@of="person:p1"]') is not None
    assert candidate.find("tool_catalogue").text
    assert candidate.find("graph").attrib["status"] == "unavailable"


async def test_foreign_agenda_fails_before_related_subject_or_member_reads():
    grounding, _ = await build()
    assert grounding.members and grounding.profiles  # positive scoped path
    with pytest.raises(ValueError, match="outside the unit"):
        await build(foreign_agenda=True)
