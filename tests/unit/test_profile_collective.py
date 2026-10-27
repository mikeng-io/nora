"""Collective writer contracts over generated messages, without model calls."""
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from strands.event_loop.event_loop import _check_limits

from nora.agent.dreamer import _as_tool
from nora.config import DreamBudgetConfig, DreamerConfig
from nora.domain.culture import RoomMessage
from nora.domain.media import MediaObject
from nora.domain.media_join import MediaJoin
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import Proposal
from nora.domain.profile import Said
from nora.domain.profile_inputs import PersonSourceRevision
from nora.domain.social_stream import PersonSocialItem
from nora.instructions.dreamer import DreamerInstructions
from nora.language.character import CharacterRoom
from nora.language.profile_collective import (
    COLLECTIVE_SOURCE_CHARS,
    CollectiveEvidence,
    CollectiveKey,
    ProfileCollectiveAdaptor,
)
from nora.obs.metrics import Metrics
from nora.testing import FakeMediaObjectStore, InMemoryCharacterEvidence, InMemoryCultureStore

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


@pytest.mark.parametrize("kind", ["unit", "room", "nora"])
async def test_collective_sources_are_bounded_and_only_selected_sources_are_citable(kind):
    adaptor, key, _ = await _session(kind)
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    roots = []
    objects = []
    for i in range(1, 49):
        sha = f"{i:06x}" + "0" * 58
        roots.append(RoomMessage(room="room-a", row=TranscriptRow(
            id=100 + i, sender_id="generated-sender", sender_name="Ada", person_id="ada",
            ts=NOW - i * 1000, body=f"[image {sha[:6]}]", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
            media_json=json.dumps([{"sha256": sha}]),
        )))
        objects.append(MediaObject(sha256=sha, media_type="image/png", size=1,
            source="inbound",
            description=f"generated scene {i}: " + "reading & discussion <book> " * 160))
    sources = [PersonSourceRevision(
        domain="rollup", source=f"generated-rollup-{i}", revision=f"revision-{i}",
        subject="ada" if i % 2 else "bo", state="available", recorded_at=at.isoformat(),
        roots=pair, citable=f"rollup:generated:{i}",
        # One metadata-only overflow must not fit just because its root is short.
        properties=(("generated", "&" * 60_001 if i == 1 else "counted"),),
        evidence=((f"rollup:generated:{i}", Said(pair[0].row.ts, pair[0].row.person_id,
                                               pair[0].room, medium="act")),),
    ) for i, pair in enumerate(zip(roots[::2], roots[1::2], strict=True), 1)]
    for i, person in enumerate(("ada", "bo")):
        sources.insert(0, PersonSourceRevision(
            domain="social", source=f"generated-social-{i}", revision=f"social-revision-{i}",
            subject=person, state="available", recorded_at=at.isoformat(),
            social=PersonSocialItem(network="synthetic", item_id=f"post-{i}", kind="post",
                collab=False, taken_at=at, origin="poll", caption="generated reading group",
                recorded_us=NOW * 1000 + i),
        ))

    class _Sources:
        # Local owning-store scaffolding: exact revision acks affect subsequent reads.
        def __init__(self):
            self.acked = set()

        async def rollup(self, *, key, unit, rooms, person, limit):
            return [r for r in sources if r.domain == "rollup" and r.subject == person
                    and (key, r.source, r.revision) not in self.acked][:limit]

        async def social(self, *, key, persons, limit):
            return [r for r in sources if r.domain == "social" and r.subject in persons
                    and (key, r.source, r.revision) not in self.acked][:limit]

        async def settle(self, *, key, revisions):
            self.acked.update((key, r.source, r.revision) for r in revisions)

    source_store = _Sources()
    evidence = CollectiveEvidence(store=adaptor._reads, keys=adaptor._collective_keys,
        kind=kind, source_inputs=source_store, members={key: ("ada", "bo")})
    adaptor.evidence = evidence
    adaptor._media_join = MediaJoin(store=FakeMediaObjectStore(objects),
        screen=lambda _text: None, metrics=Metrics(), render_chars=4000)
    batch, _ = await evidence.newest(key=key, after=0, limit=100)
    assert len([r for r in batch if isinstance(r, PersonSourceRevision)]) == len(sources)
    # Exercise one combined allowance across both source kinds.
    batch = sorted(batch, key=lambda r: r.domain != "social")
    rendered = await adaptor.inputs(key=key, batch=batch)
    # Literal acceptance ceiling, independent of the implementation constant.
    assert COLLECTIVE_SOURCE_CHARS == 60_000
    assert 20_000 < len(rendered) <= 60_000
    selected = evidence._handed[key]
    omitted = [r for r in sources if r not in selected]
    assert selected and omitted
    assert any(r.domain == "social" for r in selected)
    assert any(r.domain == "rollup" for r in selected)
    assert any(r.roots and r.source != "generated-rollup-1" for r in omitted)
    assert next(r for r in sources if r.source == "generated-rollup-1") in omitted
    citable = adaptor._runs[key].citable
    assert set(citable.messages) == {m.row.id for r in selected for m in r.roots}
    assert set(citable.sources) == {c for r in selected for c, _ in r.evidence}
    assert set(citable.social) == {f"{r.social.network}:{r.social.item_id}"
                                  for r in selected if r.social is not None}
    for r in selected:
        assert r.source in rendered
        for root in r.roots:
            assert f' n="{root.row.id}"' in rendered
            assert f"generated scene {root.row.id - 100}:" in rendered
            assert "&amp; discussion &lt;book&gt;" in rendered
    for r in omitted:
        assert f'"source": "{r.source}"' not in rendered
        assert all(c not in citable.sources for c, _ in r.evidence)
        assert all(m.row.id not in citable.messages for m in r.roots)
    assert not source_store.acked  # Rendering alone never acknowledges anything.
    _, no_change = adaptor.verbs(key=key)
    assert await no_change(why="Generated evidence adds no supported change.") == {"status": "ok"}
    [staged] = await adaptor.accept(key=key, items=())

    async def commit_empty(**kwargs):
        assert kwargs["ops"] == () and kwargs["expected_revision"] == 0
        return SimpleNamespace(status="ok", written=())

    adaptor._profile.profile_write = commit_empty
    assert await adaptor.commit(key=key, proposals=[Proposal(payload=staged,
        instruction_sha256="a" * 64, model="synthetic", established_by="generated-session")],
        now_ms=NOW) == 0
    await evidence.settle(key=key)
    assert source_store.acked == {(key, r.source, r.revision) for r in selected}
    pending, _ = await evidence.newest(key=key, after=0, limit=100)
    assert {r.source for r in pending if isinstance(r, PersonSourceRevision)} == {
        r.source for r in omitted}


@pytest.mark.parametrize("kind", ["unit", "room", "nora"])
def test_collective_budgets_admit_bounded_first_call_and_one_repair(kind):
    config = DreamerConfig()
    budget = config.budgets.get(f"profile-{kind}")
    assert budget is not None  # composition consumes the map, not budget_for
    assert budget == config.budget_for(f"profile-{kind}")
    assert DreamerConfig(budgets={"culture": DreamBudgetConfig()}).budgets.get(
        f"profile-{kind}") == budget
    # 60k input tokens allows the ~50k bounded evidence plus instructions/held
    # lines. The repair replays that input plus the first 6k output/refusals.
    first_input, first_output, refusal = 60_000, 6_000, 2_000
    repair_input, repair_output = first_input + first_output + refusal, 6_000
    assert budget.total_tokens > first_input + first_output + repair_input + repair_output
    assert budget.output_tokens > first_output + repair_output
    assert budget.turns >= 3  # first write, repair write, terminal response
    assert budget.timeout_seconds >= 2 * 120 + 30  # two calls plus tool time
    # Use the SDK's actual >= cap check: neither the first result nor the repair
    # may terminate the loop before it can continue after the tool's result.
    limits = {name: getattr(budget, name) for name in (
        "turns", "total_tokens", "output_tokens")}
    for turns, total, output in (
        (1, first_input + first_output, first_output),
        (2, first_input + first_output + repair_input + repair_output,
         first_output + repair_output),
    ):
        agent = SimpleNamespace(event_loop_metrics=SimpleNamespace(
            latest_agent_invocation=SimpleNamespace(cycles=[None] * turns,
                usage={"totalTokens": total, "outputTokens": output})))
        assert _check_limits(agent, limits) is None
    # An explicit deployment override continues to win, including partial blocks.
    override = DreamBudgetConfig(total_tokens=200_000)
    assert DreamerConfig(budgets={f"profile-{kind}": override}).budget_for(
        f"profile-{kind}") == override
    assert DreamerConfig().budget_for("profile-person") == DreamBudgetConfig()
