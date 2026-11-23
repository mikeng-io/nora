"""`OperationLedger` (nora/platform/bridge/answers.py): one process's operation
receipts, reserved before the effect runs and kept five minutes from settlement."""

from __future__ import annotations

import asyncio

import pytest

from nora.platform.bridge.answers import (
    InvalidOperation,
    OperationLedger,
    pending_body,
    settled_body,
)

EFFECT = {"guild_id": "100000000000000001", "generation": "gen-1"}


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _pending(target: str = "100000000000000001", trace: str = "") -> dict:
    return pending_body({"state": None}, target=target, trace=trace, started_ms=1)


def _work(runs: list[str], name: str, gate: asyncio.Event | None = None):
    async def work() -> dict:
        runs.append(name)
        if gate is not None:
            await gate.wait()
        return settled_body("complete", {"state": None}, target="", trace="", started_ms=1)

    return work


async def test_a_full_ledger_refuses_a_new_operation_before_its_effect() -> None:
    """With every slot held by an operation in flight, a new one is
    `unavailable` and its effect never runs; the one in flight is kept."""
    ledger, runs, gate = OperationLedger(bound=1), [], asyncio.Event()
    first = asyncio.ensure_future(ledger.run("op-1", "leave", EFFECT, _work(runs, "1", gate),
                                             _pending()))
    await asyncio.sleep(0)

    refused = await ledger.run("op-2", "leave", EFFECT, _work(runs, "2"), _pending())
    gate.set()
    answered = await first

    assert refused["outcome"] == "unavailable" and refused["when"]["state"] == "settled"
    assert refused["payload"]["error"]["kind"] == "ledger_full"
    assert runs == ["1"] and answered["outcome"] == "complete"
    assert ledger.get("op-1") == answered and ledger.get("op-2") is None


async def test_a_settled_receipt_is_kept_until_the_ledger_has_room() -> None:
    """A receipt inside its five minutes is never dropped to make room."""
    ledger, runs = OperationLedger(bound=1), []
    await ledger.run("op-1", "leave", EFFECT, _work(runs, "1"), _pending())

    refused = await ledger.run("op-2", "leave", EFFECT, _work(runs, "2"), _pending())

    assert refused["outcome"] == "unavailable" and runs == ["1"]
    assert ledger.get("op-1")["outcome"] == "complete"


async def test_the_five_minutes_start_at_settlement() -> None:
    clock, runs, gate = _Clock(), [], asyncio.Event()
    ledger = OperationLedger(clock=clock)
    running = asyncio.ensure_future(ledger.run("op-1", "leave", EFFECT, _work(runs, "1", gate),
                                               _pending()))
    await asyncio.sleep(0)
    clock.now = 400.0
    assert ledger.get("op-1")["outcome"] == "pending"
    gate.set()
    await running

    clock.now = 699.0
    assert ledger.get("op-1")["outcome"] == "complete"
    await ledger.run("op-1", "leave", EFFECT, _work(runs, "again"), _pending())
    assert runs == ["1"]
    clock.now = 701.0
    assert ledger.get("op-1") is None


async def test_a_pending_read_back_names_its_target_and_trace() -> None:
    ledger, gate = OperationLedger(), asyncio.Event()
    running = asyncio.ensure_future(ledger.run(
        "op-1", "speak", EFFECT, _work([], "1", gate),
        _pending(target="100000000000000007", trace="ab" * 16)))
    await asyncio.sleep(0)

    read = ledger.get("op-1")
    gate.set()
    await running

    assert (read["outcome"], read["when"]["state"]) == ("pending", "started")
    assert read["from"]["target"] == "100000000000000007" and read["trace"] == "ab" * 16
    assert read["payload"] == {"state": None}


async def test_a_duplicate_in_flight_shares_the_one_execution() -> None:
    ledger, runs, gate = OperationLedger(), [], asyncio.Event()
    first = asyncio.ensure_future(ledger.run("op-1", "leave", EFFECT, _work(runs, "1", gate),
                                             _pending()))
    second = asyncio.ensure_future(ledger.run("op-1", "leave", EFFECT, _work(runs, "2"),
                                              _pending()))
    await asyncio.sleep(0)
    gate.set()

    assert await first == await second and runs == ["1"]


async def test_a_refused_request_keeps_nothing() -> None:
    ledger, runs = OperationLedger(), []

    async def invalid() -> dict:
        runs.append("invalid")
        raise InvalidOperation("bad")

    with pytest.raises(InvalidOperation):
        await ledger.run("op-1", "leave", EFFECT, invalid, _pending())
    await ledger.run("op-1", "leave", EFFECT, _work(runs, "valid"), _pending())

    assert runs == ["invalid", "valid"]
