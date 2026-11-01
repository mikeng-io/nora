"""The F4 handoff: `profile_read`/`profile_revise` in the unified Cognition
session against the typed Profile store (27 §9, 31 §5).

The store is the shared `InMemoryProfileStore`: REAL `plan_write`/
`chain_heads` semantics, in-memory persistence. Fixtures are generated,
never production rows.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_memory import WakeMemory
from nora.domain.memory_graph import Source
from nora.domain.profile import ProfileCitable
from nora.language.profile_render import ProfileView, render_profile
from nora.testing.comprehend import InMemoryComprehendRecords
from nora.testing.profile import InMemoryProfileStore

UNIT = "u1"
PERSON = "p1"
ROOM = "room1"
AS_OF = datetime(2025, 1, 5, tzinfo=UTC)
NOW_MS = int(AS_OF.timestamp() * 1000)
DAY1 = int(datetime(2025, 1, 1, 2, tzinfo=UTC).timestamp() * 1000)   # 10:00 HKT Jan 1
DAY1B = int(datetime(2025, 1, 1, 6, tzinfo=UTC).timestamp() * 1000)  # 14:00 HKT Jan 1
DAY2 = int(datetime(2025, 1, 2, 4, tzinfo=UTC).timestamp() * 1000)   # 12:00 HKT Jan 2
WINDOW_END = int(datetime(2025, 2, 1, tzinfo=UTC).timestamp() * 1000)
PERSON_ACCOUNT_KEY = f"{UNIT}#character:{PERSON}"
ROOM_KEY = ROOM

_RECORDS = tuple(
    SimpleNamespace(
        message_id=mid, at_ms=ts, person_id=PERSON,
        room=SimpleNamespace(conversation_id=ROOM), kind="message", text="x")
    for mid, ts in ((11, DAY1), (12, DAY1B), (13, DAY2)))


def _records() -> InMemoryComprehendRecords:
    records = InMemoryComprehendRecords()
    records.persons_by_unit = {UNIT: {PERSON}}
    records.rooms_by_unit = {UNIT: [("signal", ROOM)]}
    return records


def _item(social: bool = False) -> Any:
    rows = [
        {"id": "r1", "ts": DAY1, "person_id": PERSON, "body": "a"},
        {"id": "r2", "ts": DAY1B, "person_id": PERSON, "body": "b"},
        {"id": "r3", "ts": DAY2, "person_id": PERSON, "body": "c"},
    ]
    keys = {r["id"]: ["message", str(i)]
            for r, i in zip(rows, (11, 12, 13), strict=True)}
    metadata: dict[str, Any] = {}
    if social:
        keys["r9"] = ["social", "insta:it9"]
        metadata = {"taken_at": DAY2, "account": "p1-insta", "network": "insta",
                    "kind": "post"}
    return SimpleNamespace(
        work=SimpleNamespace(id="w1", input_hash="h", as_of=AS_OF),
        snapshot={"messages": rows, "source_keys": keys, "metadata": metadata,
                  "source_provenance": {"social:insta:it9": {
                      "metadata": metadata, "author": PERSON}}},
        room=ROOM,
        sources=(),
    )


def _state(item: Any, *, relations: tuple[str, ...] = ("rel-1",),
           social: bool = False) -> Any:
    fetched = GraphFetched()
    for key in (("message", "11"), ("message", "12"), ("message", "13")):
        fetched.show(Source(key, "record", UNIT, text="x"))
    if social:
        fetched.show(Source(("social", "insta:it9"), "record", UNIT,
                            persons=(PERSON,), text="x"))
    return SimpleNamespace(
        unit=UNIT, session_id=7, now_ms=NOW_MS,
        memory_items={0: item} if item is not None else {},
        graph_fetched=fetched,
        records=_RECORDS,
        admitted_relations={r: object() for r in relations},
        admitted_evidence=[
            SimpleNamespace(relation_id="rel-1", source=("message", "11"))],
        profile_accounts={})


def _memory(store: Any, records: Any) -> WakeMemory:
    return WakeMemory(
        graph=SimpleNamespace(), records=records,
        metrics=SimpleNamespace(inc=lambda *a, **k: None),
        profile=store, listed_rooms=lambda u, p: (ROOM,))


def _induced_op() -> dict[str, Any]:
    return {
        "op": "add", "part": "traits", "kind": "habit", "mode": "induced",
        "content": {
            "statement": "keeps a steady evening pace", "context": "weeknights",
            "action": "evening walks", "rhythm": "daily",
            "setting": "in the neighbourhood"},
        "citations": ["message:11", "message:12", "message:13"],
    }


def _deduced_op(draws: list[str] | None = None) -> dict[str, Any]:
    return {
        "op": "add", "part": "traits", "kind": "habit", "mode": "deduced",
        "content": {
            "statement": "plans outings reliably", "context": "when invited",
            "action": "plans outings", "rhythm": "on weekends", "setting": "unknown"},
        "citations": ["message:11", "message:12", "message:13"],
        "draws_on": draws or ["relation:rel-1"],
    }


def _revise_op(handle: str, statement: str = "paces fast") -> dict[str, Any]:
    return {
        "op": "revise", "line": handle,
        "content": {
            "statement": statement, "context": "weeknights",
            "action": "evening walks", "rhythm": "daily",
            "setting": "in the neighbourhood"},
        "citations": ["message:11", "message:12", "message:13"],
    }


@pytest.mark.asyncio
async def test_read_shows_the_complete_typed_account() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    view = await memory.account_view(state, ["person", PERSON])
    assert view["status"] == "ok"
    assert view["account"]["entity_kind"] == "human"
    assert view["account"]["revision"] == 0
    assert view["account"]["lines"] == [] and view["account"]["disputes"] == []
    assert PERSON_ACCOUNT_KEY in state.profile_accounts


@pytest.mark.asyncio
async def test_write_then_later_turn_renders() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    result = await memory.profile(state, ["person", PERSON], [_induced_op()],
                                  act_key="k1")
    assert result["status"] == "ok", result
    await memory.account_view(state, ["person", PERSON])
    deduced = await memory.profile(state, ["person", PERSON], [_deduced_op()],
                                   act_key="k2")
    assert deduced["status"] == "ok", deduced
    heads = await store.account_heads(key=PERSON_ACCOUNT_KEY)
    assert len(heads) == 2 and all(h.state == "current" for h in heads)
    assert heads[1].mode == "deduced" and heads[1].draws_on == ("relation:rel-1",)
    assert store.now_ms_seen == int(AS_OF.timestamp() * 1000)
    assert store.by_session == "cognition:profile:7"
    block = render_profile(
        ProfileView(entity="person", of=PERSON, lines=heads),
        name_of=lambda pid: pid, now=AS_OF)
    assert block is not None and "steady evening pace" in block.body


@pytest.mark.asyncio
async def test_a_write_invalidates_the_shown_view() -> None:
    """Handle-movement regression: after a write lands, P-handles renumber —
    the session must re-read before the next write, never apply a stale P1."""
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    await memory.profile(state, ["person", PERSON], [_induced_op()], act_key="w")
    assert PERSON_ACCOUNT_KEY not in state.profile_accounts
    again = await memory.profile(state, ["person", PERSON], [_deduced_op()],
                                 act_key="w2")
    assert again["status"] == "refused" and again["reason"] == "read_first"


@pytest.mark.asyncio
async def test_correction_and_retirement_change_the_later_read() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    await memory.profile(state, ["person", PERSON], [_induced_op()], act_key="a")
    await memory.account_view(state, ["person", PERSON])
    revise = await memory.profile(state, ["person", PERSON],
                                  [_revise_op("P1")], act_key="b")
    assert revise["status"] == "ok", revise
    heads = await store.account_heads(key=PERSON_ACCOUNT_KEY)
    assert len(heads) == 1 and "paces fast" in heads[0].text
    await memory.account_view(state, ["person", PERSON])
    retire = await memory.profile(state, ["person", PERSON], [{
        "op": "retire", "line": "P1", "why": "no longer true"}], act_key="c")
    assert retire["status"] == "ok", retire
    heads = await store.account_heads(key=PERSON_ACCOUNT_KEY)
    assert len(heads) == 1 and heads[0].state == "retired"
    block = render_profile(
        ProfileView(entity="person", of=PERSON, lines=heads),
        name_of=lambda pid: pid, now=AS_OF)
    assert block is None or "paces fast" not in block.body


@pytest.mark.asyncio
async def test_revise_requires_a_shown_account() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    result = await memory.profile(state, ["person", PERSON], [_induced_op()],
                                  act_key="k")
    assert result["status"] == "refused" and result["reason"] == "read_first"
    assert store.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", [
    ["topic", "t1"], ["place", "x"], ["person", "not-in-unit"],
    ["room", "not-in-unit"], ["unit", "other-unit"], ["person"],
])
async def test_unknown_subjects_fail_closed(entity: list[str]) -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    view = await memory.account_view(state, entity)
    assert view["status"] == "refused" and view["reason"] == "unknown_account"
    assert store.writes == 0


@pytest.mark.asyncio
async def test_unshown_source_and_undeclared_relation_refuse() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    unseen = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "traits", "kind": "habit",
        "content": {"statement": "x", "context": "x", "action": "x",
                    "rhythm": "x", "setting": "x"},
        "mode": "deduced", "citations": ["message:999", "message:11"]}],
        act_key="u1")
    assert unseen["status"] == "refused", unseen
    bad_premise = await memory.profile(state, ["person", PERSON],
                                       [_deduced_op(["relation:not-admitted"])],
                                       act_key="u2")
    assert bad_premise["status"] == "refused", bad_premise
    assert {r["reason"] for r in bad_premise["refusals"]} == {"unshown"}
    bad_handle = await memory.profile(state, ["person", PERSON], [{
        "op": "revise", "line": "P9", "text": "x",
        "citations": ["message:11", "message:12"]}], act_key="u3")
    assert bad_handle["status"] == "refused", bad_handle
    assert await store.account_heads(key=PERSON_ACCOUNT_KEY) == ()


@pytest.mark.asyncio
async def test_an_unsupported_relation_premise_is_refused() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    op = _deduced_op(["relation:rel-1"])
    op["citations"] = ["message:12", "message:13"]  # not the relation's evidence
    result = await memory.profile(state, ["person", PERSON], [op], act_key="x")
    assert result["status"] == "refused" and any(
        "not supported" in r["detail"] for r in result["refusals"])


@pytest.mark.asyncio
async def test_facts_and_told_ops_are_refused_at_the_seam() -> None:
    """Facts are code-rendered from the graph and told lines are the turn
    path's own writes — neither may be authored by this session."""
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    facts = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "facts", "kind": "attribute", "mode": "deduced",
        "text": "works at Example Co",
        "fact_key": "occupation", "fact_value": {"name": "Example Co"},
        "citations": ["message:11", "message:12"]}], act_key="f")
    assert facts["status"] == "refused", facts
    told = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "traits", "kind": "habit", "mode": "told",
        "content": {"statement": "x", "context": "x", "action": "x",
                    "rhythm": "x", "setting": "x"},
        "told_by": PERSON, "citations": ["message:11"]}], act_key="t")
    assert told["status"] == "refused", told
    assert store.writes == 0
    assert await store.account_heads(key=PERSON_ACCOUNT_KEY) == ()


@pytest.mark.asyncio
async def test_identical_replay_writes_once() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    first = await memory.profile(state, ["person", PERSON], [_induced_op()],
                                 act_key="same")
    again = await memory.profile(state, ["person", PERSON], [_induced_op()],
                                 act_key="same")
    assert first["status"] == again["status"] == "ok"
    assert store.writes == 1
    assert again == await records.act_result("same", state.session_id)


@pytest.mark.asyncio
async def test_a_competing_revision_conflicts_then_recovers() -> None:
    """Read A -> competing write -> conflict -> reread B -> the SAME ops
    succeed; the committed replay stays once."""
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    account = state.profile_accounts[PERSON_ACCOUNT_KEY]["account"]
    from nora.domain.profile import parse_ops
    await store.profile_write(
        account=account, ops=parse_ops([_induced_op()])[0],
        citable=memory._citable_for(state, account),
        now_ms=NOW_MS, written_by_session="other", apply_decay=False)
    conflict = await memory.profile(state, ["person", PERSON], [_deduced_op()],
                                    act_key="k")
    assert conflict["status"] == "conflict"
    assert PERSON_ACCOUNT_KEY not in state.profile_accounts
    # Same ops after a fresh read — NOT a replay: the conflict was never
    # receipted, so the write lands.
    await memory.account_view(state, ["person", PERSON])
    recovered = await memory.profile(state, ["person", PERSON], [_deduced_op()],
                                     act_key="k")
    assert recovered["status"] == "ok", recovered
    replay = await memory.profile(state, ["person", PERSON], [_deduced_op()],
                                  act_key="k")
    # competing write + conflict attempt + the one landed write
    assert replay["status"] == "ok" and store.writes == 3


@pytest.mark.asyncio
async def test_a_failed_store_write_is_unavailable_not_ok() -> None:
    records, store = _records(), InMemoryProfileStore(fail=True)
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    with pytest.raises(RuntimeError, match="profile store down"):
        await memory.profile(state, ["person", PERSON], [_induced_op()],
                             act_key="k")
    assert await store.account_heads(key=PERSON_ACCOUNT_KEY) == ()


@pytest.mark.asyncio
async def test_abduced_guess_rests_on_a_current_induced_trait() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    await memory.profile(state, ["person", PERSON], [_induced_op()], act_key="a")
    await memory.account_view(state, ["person", PERSON])
    result = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "guesses", "kind": "explanation",
        "text": "paces evenings because gym sessions run late",
        "content": {"proposed_cause": "late-evening gym visits",
                    "alternative": "unknown"},
        "mode": "abduced", "draws_on": ["P1"], "citations": ["message:13"],
        "prediction": "keeps evening gym sessions",
        "prediction_observable": "late-evening gym visits",
        "prediction_window_end": WINDOW_END}], act_key="g")
    assert result["status"] == "ok", result
    guesses = [h for h in await store.account_heads(key=PERSON_ACCOUNT_KEY)
               if h.part == "guesses"]
    assert len(guesses) == 1 and guesses[0].verdict == "open"


@pytest.mark.asyncio
async def test_a_carried_relation_premise_survives_a_later_session() -> None:
    """A line written in an earlier wake keeps its `relation:` premise; a
    later session revising it without restating draws_on is not asked to
    re-admit a relation it never saw."""
    records, store = _records(), InMemoryProfileStore()
    memory, state_a = _memory(store, records), _state(_item())
    await memory.account_view(state_a, ["person", PERSON])
    await memory.profile(state_a, ["person", PERSON], [_deduced_op()],
                         act_key="a")
    # A LATER session: the relation is not admitted or shown here.
    state_b = _state(_item(), relations=())
    state_b.admitted_evidence = []
    await memory.account_view(state_b, ["person", PERSON])
    revise = await memory.profile(state_b, ["person", PERSON],
                                  [_revise_op("P1")], act_key="b")
    assert revise["status"] == "ok", revise
    heads = await store.account_heads(key=PERSON_ACCOUNT_KEY)
    assert "relation:rel-1" in heads[0].draws_on


@pytest.mark.asyncio
async def test_social_item_resolves_author_and_taken_at() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item(social=True),
                                                  social=True)
    await memory.account_view(state, ["person", PERSON])
    account = state.profile_accounts[PERSON_ACCOUNT_KEY]["account"]
    citable = memory._citable_for(state, account)
    said = citable.social.get("insta:it9")
    assert said is not None and said.ts == DAY2 and said.person_id == PERSON
    assert said.room is None and said.medium == "social"
    # A message key with no record row stays unresolved — never blessed.
    state.graph_fetched.show(Source(("message", "99"), "record", UNIT, text="x"))
    assert "message:99" not in memory._citable_for(state, account).sources


@pytest.mark.asyncio
@pytest.mark.parametrize("entity,entity_kind", [
    (["room", ROOM], "room"), (["unit", UNIT], "unit"), (["nora", "nora"], "nora"),
])
async def test_collective_accounts_map(entity: list[str], entity_kind: str) -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    view = await memory.account_view(state, entity)
    assert view["status"] == "ok" and view["account"]["entity_kind"] == entity_kind
    assert len(state.profile_accounts) == 1


def test_profile_writer_ownership_matrix() -> None:
    """One account writer per flag combination: the Dreamer writers retire
    exactly where the unified session is wired; the verdict judge and tag
    told/dispute paths never."""
    from nora.app.composition import _unified_profile_writers_retired
    from nora.language.profile import PROFILE_PERSON_ADAPTOR_NAME
    from nora.language.profile_collective import (
        PROFILE_NORA_ADAPTOR_NAME,
        PROFILE_ROOM_ADAPTOR_NAME,
        PROFILE_UNIT_ADAPTOR_NAME,
    )
    from nora.language.profile_verdict import PROFILE_VERDICT_ADAPTOR_NAME
    coverage = {"cognition_enabled": True, "units": frozenset({UNIT}),
                "home_units": frozenset({UNIT})}
    on = _unified_profile_writers_retired(
        profile_index=True, graph_comprehend=True, **coverage)
    assert on == frozenset({PROFILE_PERSON_ADAPTOR_NAME, PROFILE_ROOM_ADAPTOR_NAME,
                            PROFILE_UNIT_ADAPTOR_NAME, PROFILE_NORA_ADAPTOR_NAME})
    assert PROFILE_VERDICT_ADAPTOR_NAME not in on
    for profile_index, comprehend in ((False, True), (True, False), (False, False)):
        assert _unified_profile_writers_retired(
            profile_index=profile_index, graph_comprehend=comprehend, **coverage) == frozenset()


@pytest.mark.asyncio
async def test_shadow_mode_receipts_ops_without_writing() -> None:
    records = _records()
    memory = WakeMemory(
        graph=SimpleNamespace(), records=records,
        metrics=SimpleNamespace(inc=lambda *a, **k: None))
    state = _state(_item())
    result = await memory.profile(state, ["person", PERSON], [_induced_op()])
    assert result["status"] == "ok", result
    assert records.shadows[0]["would_writes"] == [_induced_op()]
    refused = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "traits", "kind": "habit",
        "content": {"statement": "x", "context": "x", "action": "x",
                    "rhythm": "x", "setting": "x"},
        "mode": "deduced", "citations": ["message:999"]}])
    assert refused["status"] == "refused" and len(records.shadows) == 1


@pytest.mark.asyncio
async def test_explicit_relation_premises_replace_instead_of_carrying() -> None:
    records, store = _records(), InMemoryProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    assert (await memory.profile(state, ["person", PERSON], [_deduced_op()]))["status"] == "ok"
    await memory.account_view(state, ["person", PERSON])
    op = {**_revise_op("P1"), "draws_on": []}
    assert (await memory.profile(state, ["person", PERSON], [op]))["status"] == "ok"
    heads = await store.account_heads(key=PERSON_ACCOUNT_KEY)
    assert len(heads) == 1 and heads[0].draws_on == ()


def test_writers_stay_without_cognition_or_complete_unit_coverage() -> None:
    from nora.app.composition import _unified_profile_writers_retired
    flags = {"profile_index": True, "graph_comprehend": True,
             "cognition_enabled": True, "units": frozenset({"one", "two"}),
             "home_units": frozenset({"one", "two"})}
    assert len(_unified_profile_writers_retired(**flags)) == 4
    assert not _unified_profile_writers_retired(**{**flags, "cognition_enabled": False})
    assert not _unified_profile_writers_retired(**{**flags, "home_units": frozenset({"one"})})
    assert not _unified_profile_writers_retired(**{**flags, "units": frozenset()})


@pytest.mark.asyncio
async def test_no_change_keeps_the_real_store_revision() -> None:
    from nora.domain.profile import Account
    store = InMemoryProfileStore()
    result = await store.profile_write(
        account=Account(PERSON_ACCOUNT_KEY, "human", UNIT, PERSON), ops=(),
        citable=ProfileCitable(), now_ms=NOW_MS, written_by_session="other",
        apply_decay=False)
    assert result.status == "ok" and result.revision == 0
    assert (await store.account_state(key=PERSON_ACCOUNT_KEY))[0] == 0


@pytest.mark.asyncio
async def test_prepare_keeps_each_social_sources_real_provenance() -> None:
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import WorkItem
    from nora.testing.memory_graph import FakeMemoryGraph
    records, store = _records(), InMemoryProfileStore()
    for ref, stamp in (("insta:first", DAY1), ("insta:second", DAY2),
                       ("insta:third", DAY1B), ("insta:undated", None)):
        key = ("social", ref)
        records.items[key] = ComprehendItem(
            WorkItem(ref, key, UNIT, "", AS_OF),
            (Source(key, "record", UNIT, persons=(PERSON,), text="generated social item"),),
            "signal", ROOM, author=PERSON,
            snapshot={"metadata": {"taken_at": stamp}})
    memory = WakeMemory(graph=FakeMemoryGraph(), records=records,
                        metrics=SimpleNamespace(inc=lambda *a, **k: None),
                        profile=store, listed_rooms=lambda u, p: (ROOM,), content_checks=False)
    state = _state(None)
    state.historical = False
    state.shown_keys = set()
    state.items_by_index = {0: SimpleNamespace(
        stimulus_ids=(1, 2, 3, 4), room=SimpleNamespace(platform="signal", conversation_id=ROOM))}
    stimuli = {i: SimpleNamespace(kind="social", ref="social:" + ref)
               for i, ref in enumerate(
                   ("insta:first", "insta:second", "insta:third", "insta:undated"), 1)}
    await memory.prepare(state, stimuli, SimpleNamespace(records={}))
    await memory.account_view(state, ["person", PERSON])
    account = state.profile_accounts[PERSON_ACCOUNT_KEY]["account"]
    citable = memory._citable_for(state, account)
    assert citable.social["insta:first"].ts == DAY1
    assert citable.social["insta:second"].ts == DAY2
    assert citable.social["insta:first"].person_id == PERSON
    assert "insta:undated" not in citable.social
    op = {**_induced_op(),
          "citations": ["social:insta:first", "social:insta:second", "social:insta:third"]}
    result = await memory.profile(state, ["person", PERSON], [op])
    assert result["status"] == "ok", result
    assert len(await store.account_heads(key=PERSON_ACCOUNT_KEY)) == 1
