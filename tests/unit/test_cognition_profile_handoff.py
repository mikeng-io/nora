"""The F4 handoff: `profile_read`/`profile_revise` in the unified Cognition
session against the typed Profile store (27 §9, 31 §5).

The fake store applies REAL `plan_write`/`chain_heads`, so refusals,
precedence and revision conflicts are production semantics; only the
persistence is in memory. Fixtures are generated, never production rows.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_memory import WakeMemory
from nora.domain.memory_graph import Source
from nora.domain.profile import (
    Account,
    ProfileCitable,
    ProfileWriteResult,
    plan_write,
)
from nora.infra.sql.profile import chain_heads
from nora.language.profile_render import ProfileView, render_profile
from nora.testing.comprehend import InMemoryComprehendRecords

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


class FakeProfileStore:
    """`ProfileWriter` over the real domain judge: `plan_write` and
    `chain_heads` run for real; rows and revisions sit in dicts."""

    def __init__(self, *, fail: bool = False) -> None:
        self.lines: dict[str, list[Any]] = {}
        self.revisions: dict[str, int] = {}
        self.writes = 0
        self.fail = fail
        self.now_ms_seen: int | None = None
        self.by_session: str | None = None

    async def account_state(self, *, key: str) -> tuple[int, int | None]:
        return self.revisions.get(key, 0), None

    async def account_heads(self, *, key: str) -> tuple[Any, ...]:
        return chain_heads(self.lines.get(key, []))[0]

    async def open_disputes(self, *, key: str, limit: int) -> tuple[Any, ...]:
        return ()

    async def profile_write(
        self, *, account: Account, ops: Any, citable: ProfileCitable,
        now_ms: int, written_by_session: str, apply_decay: bool,
        expected_revision: int | None = None, receipt: Any = None,
    ) -> ProfileWriteResult:
        self.writes += 1
        if self.fail:
            raise RuntimeError("profile store down")
        revision = self.revisions.get(account.key, 0)
        self.now_ms_seen, self.by_session = now_ms, written_by_session
        if expected_revision is not None and revision != expected_revision:
            return ProfileWriteResult(status="conflict", revision=revision)
        heads, aliases = chain_heads(self.lines.get(account.key, []))
        plan = plan_write(
            account=account, heads=heads, ops=ops, citable=citable,
            now_ms=now_ms, written_by_session=written_by_session,
            screen=lambda _: None, apply_decay=apply_decay, aliases=aliases)
        if plan.refused:
            return ProfileWriteResult(
                status="refused", revision=revision, refusals=plan.refusals)
        self.lines.setdefault(account.key, []).extend(plan.inserts)
        self.revisions[account.key] = revision + 1
        if receipt is not None:
            await receipt(None, {
                "status": "ok", "revision": self.revisions[account.key],
                "written": [x.id for x in plan.inserts]})
        return ProfileWriteResult(
            status="ok", revision=self.revisions[account.key],
            written=tuple(x.id for x in plan.inserts))


def _records() -> InMemoryComprehendRecords:
    records = InMemoryComprehendRecords()
    records.persons_by_unit = {UNIT: {PERSON}}
    records.rooms_by_unit = {UNIT: [("signal", ROOM)]}
    return records


def _item() -> Any:
    rows = [
        {"id": "r1", "ts": DAY1, "person_id": PERSON, "body": "a"},
        {"id": "r2", "ts": DAY1B, "person_id": PERSON, "body": "b"},
        {"id": "r3", "ts": DAY2, "person_id": PERSON, "body": "c"},
    ]
    return SimpleNamespace(
        work=SimpleNamespace(id="w1", input_hash="h", as_of=AS_OF),
        snapshot={
            "messages": rows,
            "source_keys": {r["id"]: ["message", str(i)]
                            for r, i in zip(rows, (11, 12, 13), strict=True)},
        },
        room=ROOM,
        sources=(),
    )


def _state(item: Any, relations: tuple[str, ...] = ("rel-1",)) -> Any:
    fetched = GraphFetched()
    for key in (("message", "11"), ("message", "12"), ("message", "13")):
        fetched.show(Source(key, "record", UNIT, text="x"))
    return SimpleNamespace(
        unit=UNIT, session_id=7, now_ms=NOW_MS,
        memory_items={0: item} if item is not None else {},
        graph_fetched=fetched,
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


@pytest.mark.asyncio
async def test_read_shows_heads_handles_and_revision() -> None:
    records, store = _records(), FakeProfileStore()
    memory, state = _memory(store, records), _state(_item())
    view = await memory.account_view(state, ["person", PERSON])
    assert view["status"] == "ok"
    assert view["account"]["entity_kind"] == "human"
    assert view["account"]["revision"] == 0
    assert view["account"]["lines"] == [] and view["account"]["disputes"] == []
    assert PERSON_ACCOUNT_KEY in state.profile_accounts


@pytest.mark.asyncio
async def test_revise_induced_trait_draws_on_relation_then_later_turn_renders() -> None:
    records, store = _records(), FakeProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    result = await memory.profile(state, ["person", PERSON], [_induced_op()],
                                  act_key="k1")
    assert result["status"] == "ok", result
    deduced = await memory.profile(state, ["person", PERSON], [_deduced_op()],
                                   act_key="k2")
    assert deduced["status"] == "ok", deduced
    heads = await store.account_heads(key=PERSON_ACCOUNT_KEY)
    assert len(heads) == 2 and all(h.state == "current" for h in heads)
    assert heads[1].mode == "deduced" and heads[1].draws_on == ("relation:rel-1",)
    assert store.now_ms_seen == int(AS_OF.timestamp() * 1000)
    assert store.by_session == "cognition:profile:7"
    # The persisted head is what a later assembled turn renders.
    block = render_profile(
        ProfileView(entity="person", of=PERSON, lines=heads),
        name_of=lambda pid: pid, now=AS_OF)
    assert block is not None and "steady evening pace" in block.body


@pytest.mark.asyncio
async def test_correction_and_retirement_change_the_later_read() -> None:
    records, store = _records(), FakeProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    await memory.profile(state, ["person", PERSON], [_induced_op()], act_key="a")
    await memory.account_view(state, ["person", PERSON])
    handles = state.profile_accounts[PERSON_ACCOUNT_KEY]["handles"]
    revise = await memory.profile(state, ["person", PERSON], [{
        "op": "revise", "line": next(iter(handles)),
        "content": {
            "statement": "paces fast in the evenings", "context": "weeknights",
            "action": "evening walks", "rhythm": "daily",
            "setting": "in the neighbourhood"},
        "citations": ["message:11", "message:12", "message:13"]}], act_key="b")
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
    records, store = _records(), FakeProfileStore()
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
    records, store = _records(), FakeProfileStore()
    memory, state = _memory(store, records), _state(_item())
    view = await memory.account_view(state, entity)
    assert view["status"] == "refused" and view["reason"] == "unknown_account"
    assert store.writes == 0


@pytest.mark.asyncio
async def test_unshown_source_and_undeclared_relation_refuse() -> None:
    records, store = _records(), FakeProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    unseen = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "traits", "kind": "trait", "text": "x",
        "mode": "deduced", "citations": ["message:999", "message:11"]}],
        act_key="u1")
    assert unseen["status"] == "refused", unseen
    bad_premise = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "traits", "kind": "trait", "text": "x",
        "mode": "deduced", "citations": ["message:11", "message:12", "message:13"],
        "draws_on": ["relation:not-admitted"]}], act_key="u2")
    assert bad_premise["status"] == "refused", bad_premise
    assert {r["reason"] for r in bad_premise["refusals"]} == {"unshown"}
    # A relation the session DID admit but the op's own citations do not
    # support is refused the same way (the line must cite the relation's
    # evidence, 27 §9).
    unsupported = await memory.profile(state, ["person", PERSON], [{
        "op": "add", "part": "traits", "kind": "habit", "text": "x",
        "content": {"statement": "x", "context": "x", "action": "x",
                    "rhythm": "x", "setting": "x"},
        "mode": "deduced", "citations": ["message:12", "message:13"],
        "draws_on": ["relation:rel-1"]}], act_key="u2b")
    assert unsupported["status"] == "refused" and any(
        "not supported" in r["detail"] for r in unsupported["refusals"])
    bad_handle = await memory.profile(state, ["person", PERSON], [{
        "op": "revise", "line": "P9", "text": "x",
        "citations": ["message:11", "message:12"]}], act_key="u3")
    assert bad_handle["status"] == "refused", bad_handle
    assert await store.account_heads(key=PERSON_ACCOUNT_KEY) == ()


@pytest.mark.asyncio
async def test_identical_replay_writes_once() -> None:
    records, store = _records(), FakeProfileStore()
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
async def test_a_competing_revision_forces_a_reread() -> None:
    records, store = _records(), FakeProfileStore()
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    # A competing writer moves the account between the read and the revise.
    account = state.profile_accounts[PERSON_ACCOUNT_KEY]["account"]
    await store.profile_write(
        account=account, ops=(), citable=ProfileCitable(), now_ms=NOW_MS,
        written_by_session="other", apply_decay=False)
    result = await memory.profile(state, ["person", PERSON], [_induced_op()],
                                  act_key="k")
    assert result["status"] == "conflict"
    assert PERSON_ACCOUNT_KEY not in state.profile_accounts
    again = await memory.profile(state, ["person", PERSON], [_induced_op()],
                                 act_key="k2")
    assert again["status"] == "refused" and again["reason"] == "read_first"


@pytest.mark.asyncio
async def test_a_failed_store_write_is_unavailable_not_ok() -> None:
    records, store = _records(), FakeProfileStore(fail=True)
    memory, state = _memory(store, records), _state(_item())
    await memory.account_view(state, ["person", PERSON])
    with pytest.raises(RuntimeError, match="profile store down"):
        await memory.profile(state, ["person", PERSON], [_induced_op()],
                             act_key="k")
    assert await store.account_heads(key=PERSON_ACCOUNT_KEY) == ()


@pytest.mark.asyncio
async def test_abduced_guess_rests_on_a_current_induced_trait() -> None:
    records, store = _records(), FakeProfileStore()
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
@pytest.mark.parametrize("entity,entity_kind", [
    (["room", ROOM], "room"), (["unit", UNIT], "unit"), (["nora", "nora"], "nora"),
])
async def test_collective_accounts_map(entity: list[str], entity_kind: str) -> None:
    records, store = _records(), FakeProfileStore()
    memory, state = _memory(store, records), _state(_item())
    view = await memory.account_view(state, entity)
    assert view["status"] == "ok" and view["account"]["entity_kind"] == entity_kind
    assert len(state.profile_accounts) == 1


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
        "op": "add", "part": "traits", "kind": "trait", "text": "x",
        "mode": "deduced", "citations": ["message:999"]}])
    assert refused["status"] == "refused" and len(records.shadows) == 1
