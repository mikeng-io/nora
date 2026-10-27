"""Collective writer contracts over generated messages, without model calls."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nora.agent.dreamer import _as_tool
from nora.domain.culture import RoomMessage
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.instructions.dreamer import DreamerInstructions
from nora.language.character import CharacterRoom
from nora.language.profile_collective import (
    CollectiveEvidence,
    CollectiveKey,
    ProfileCollectiveAdaptor,
)
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence, InMemoryCultureStore

NOW = 1_790_000_000_000


class _Profile:
    async def account_state(self, *, key):
        return 0, NOW

    async def account_heads(self, *, key):
        return ()


async def _session(kind):
    key = "room-a" if kind == "room" else f"u1#{kind}"
    keys = {key: CollectiveKey(unit="u1", rooms=(
        CharacterRoom(key="room-a", platform="signal", label="Reading club"),))}
    reads = InMemoryCharacterEvidence()
    adaptor = ProfileCollectiveAdaptor(
        kind=kind, profile=_Profile(), culture=InMemoryCultureStore(),
        evidence=CollectiveEvidence(store=reads, keys=keys, kind=kind),
        reads=reads, keys=keys, name_of=lambda pid: {"ada": "Ada", "bo": "Bo"}[pid],
        clock_ms=lambda: NOW, clock=lambda: datetime.fromtimestamp(NOW / 1000, UTC),
    )
    batch = [RoomMessage(room="room-a", row=TranscriptRow(
        id=i, sender_id=f"sender-{person}", sender_name=person, person_id=person,
        ts=NOW - 1000, body=body, is_nora=False, delivery_status=DeliveryStatus.SENT,
        raw_json=None,
    )) for i, person, body in ((1, "ada", "This group is for reading together."),
                              (2, "bo", "I can join next week."))]
    rendered = await adaptor.inputs(key=key, batch=batch)
    return adaptor, key, rendered


@pytest.mark.parametrize(("kind", "owner"), [
    ("unit", "the unit's"), ("room", "the room's"), ("nora", "Nora's"),
])
async def test_collective_tool_description_names_its_account_and_recovery(kind, owner):
    adaptor, key, _ = await _session(kind)
    write, no_change = [_as_tool(v) for v in adaptor.verbs(key=key)]
    description = " ".join(write.tool_spec["description"].split())
    assert f"Change {owner} account" in description
    assert "Fix or drop the refused ops and call profile_write again" in description
    assert "If nothing valid remains, call profile_no_change" in description
    assert "Stop only after status: ok" in description
    assert "refused write leaves no valid changes" in " ".join(
        no_change.tool_spec["description"].split())


@pytest.mark.parametrize("kind", ["unit", "room", "nora"])
def test_collective_objective_explains_single_speaker_citations_and_settlement(kind):
    text = " ".join(DreamerInstructions(
        Path("nora/dreamer"), metrics=Metrics(),
    ).for_objective(f"profile-{kind}").text.split())
    assert "Every citation on that line must be a message from that same speaker" in text
    assert "including on keep/revise" in text
    assert "use `deduced` with `draws_on`" in text
    assert "Keep each line within 300 characters" in text
    assert "including the text projected from `content` slots" in text
    assert "A `refused` result settles nothing" in text
    assert "fix or drop the invalid ops, then call `profile_write` again" in text
    assert "If nothing valid remains, call `profile_no_change`" in text
    assert "stop only after one of these tools returns `status: ok`" in text


async def test_unit_told_citations_are_grounded_and_a_refusal_can_be_repaired():
    adaptor, key, rendered = await _session("unit")
    assert 'n="1" from="Ada"' in rendered
    assert 'n="2" from="Bo"' in rendered
    write, _ = adaptor.verbs(key=key)
    op = {"op": "add", "part": "facts", "kind": "purpose", "text": "Reading together",
          "fact_key": "purpose", "fact_value": "Reading together", "mode": "told",
          "told_by": "Ada", "citations": ["1", "2"]}
    refused = await write(ops=[op])
    assert refused["status"] == "refused"
    assert refused["refusals"][0]["reason"] == "told_by_not_speaker"
    assert adaptor._runs[key].staged is None
    assert await write(ops=[{**op, "citations": ["1"]}]) == {"status": "ok"}
    assert await adaptor.accept(key=key, items=())


async def test_unit_can_explicitly_settle_when_no_valid_ops_remain():
    adaptor, key, _ = await _session("unit")
    write, no_change = adaptor.verbs(key=key)
    assert (await write(ops=[{"op": "add"}]))["status"] == "refused"
    assert adaptor._runs[key].staged is None
    assert await no_change(why="The messages add no supported account change.") == {"status": "ok"}
    assert await adaptor.accept(key=key, items=())
