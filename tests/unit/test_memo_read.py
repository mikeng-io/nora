"""`memo_read` (DEC-306): one kept memo of THIS turn's unit by the id `<memos>`
shows, with its `## Superseded` rendered by code from the chain. Over the
in-memory memo store; names and values are invented."""

from __future__ import annotations

from typing import Any

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeRosterSource
from nora.testing.memo_store import InMemoryMemoStore
from tests.fakes import SinkSender

GROUP = "signal:group-1"
NOW = 1_790_000_000_000
KEPT = "## Kept\n- the wifi password is hunter-two [101]"


class _NullStore:
    async def recent(self, *, conversation_id: str, limit: int) -> list[Any]:
        return []


class _Persons:
    def resolve(self, platform: str, sender_id: str) -> None:
        return None


def _tools(memos: InMemoryMemoStore | None, unit: str | None = "u") -> dict[str, Any]:
    def unit_of(_platform: str, cid: str) -> str | None:
        return unit if cid == GROUP else None

    registry = ToolRegistry(memos=memos, unit_for=unit_of)
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (frozenset({"p"}),)}),
        persons=_Persons(), tagger=None,
    )
    specs = registry.specs(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=_Persons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )
    return {spec.name: spec.call for spec in specs}


async def _seed(memos: InMemoryMemoStore, unit: str = "u", body: str = KEPT) -> Any:
    return await memos.create(
        unit=unit, platform="signal", conversation_id=GROUP, title="wifi", body=body,
        citations=("101",), mode="told", established_by="cognition:u", model=None,
        now_ms=NOW,
    )


async def test_memo_read_returns_the_head_and_the_superseded_lines_code_renders():
    memos = InMemoryMemoStore()
    memo = await _seed(memos)
    second = await memos.append(
        unit="u", memo_id=memo.id, base_revision_id=memo.head.id, title="wifi",
        body="## Kept\n- the wifi password is hunter-three [102]", citations=("102",),
        mode="told", state="kept", established_by="cognition:u", model=None,
        now_ms=NOW + 86_400_000,
    )
    got = await _tools(memos)["memo_read"](memo_id=memo.id)
    assert got["status"] == "ok" and got["title"] == "wifi"
    assert got["body"].endswith("hunter-three [102]") and got["revision_id"] == second.id
    assert got["superseded"] == ["wifi: the wifi password is hunter-two [101] (until 2026-09-22)"]
    assert "note" not in got


async def test_another_units_memo_and_a_withdrawn_one_are_nothing_found():
    memos = InMemoryMemoStore()
    other = await _seed(memos, unit="elsewhere")
    gone = await _seed(memos)
    await memos.append(
        unit="u", memo_id=gone.id, base_revision_id=gone.head.id, title="wifi", body=KEPT,
        citations=("103",), mode="told", state="withdrawn", established_by="cognition:u",
        model=None, now_ms=NOW + 1,
    )
    read = _tools(memos)["memo_read"]
    assert (await read(memo_id=other.id))["status"] == "nothing_found"
    assert (await read(memo_id=gone.id))["status"] == "nothing_found"
    assert (await read(memo_id="not-an-id"))["status"] == "nothing_found"


async def test_with_no_unit_it_is_unavailable_and_with_no_store_it_is_withheld():
    memos = InMemoryMemoStore()
    memo = await _seed(memos)
    assert (await _tools(memos, unit=None)["memo_read"](memo_id=memo.id))["status"] == (
        "unavailable")
    # Withheld from every surface without a store, as `standing_read` is.
    assert "memo_read" in ToolRegistry()._withheld
    assert "memo_read" not in ToolRegistry(memos=memos)._withheld


async def test_a_value_a_reply_cannot_carry_comes_with_a_note_saying_so():
    memos = InMemoryMemoStore()
    memo = await _seed(memos, body="## Kept\n- the router key is sk-abcdefghijklmnopqrstuvwx [101]")
    got = await _tools(memos)["memo_read"](memo_id=memo.id)
    assert got["status"] == "ok" and "not sent" in got["note"]


async def test_a_value_only_an_earlier_revision_held_still_comes_with_the_note():
    """`superseded` carries earlier Kept lines into the
    result, so the egress note reads them as well as the head."""
    memos = InMemoryMemoStore()
    memo = await _seed(memos, body="## Kept\n- the router key is sk-abcdefghijklmnopqrstuvwx [101]")
    await memos.append(
        unit="u", memo_id=memo.id, base_revision_id=memo.head.id, title="wifi",
        body="## Kept\n- the router key is on the fridge [102]", citations=("102",),
        mode="told", state="kept", established_by="cognition:u", model=None, now_ms=NOW + 1,
    )
    got = await _tools(memos)["memo_read"](memo_id=memo.id)
    assert "sk-abcdef" in got["superseded"][0] and "not sent" in got["note"]
