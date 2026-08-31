"""The standing EVENT trigger and its rollup (design/standing-watermark/10,
30) — the event-driven fold path, gated on `[standing] event_enabled`.

These tests drive the stage through its PUBLIC entry (`run`), with a fake
client that counts calls and records prompts, the in-memory standing store,
and the real `ConversationSlots`. The properties from 40-verification.md:

- the trigger fires only when the accumulator reaches fold_batch (a check with
  no rows since the watermark calls the model ZERO times);
- the rollup converges: 500 rows / no prior -> 3 folds (200+200+100), each with
  the previous position as prior, final watermark = 500, fold_count UNCHANGED
  (rollup folds do not increment it — D3);
- the rollup is bounded: 2000 new rows -> at most rollup_max_folds (5) folds
  per spawn, and the NEXT spawn continues;
- a refused fold advances neither body nor watermark (the malformed case here;
  the atomic write itself is pinned against both stores by the contract suite).
"""

from __future__ import annotations

import json

import pytest

from nora.domain.models import NormalizedMessage
from nora.domain.standing import Standing
from nora.engagement.slots import ConversationSlots
from nora.ingest.stages import standing_event
from nora.obs.metrics import Metrics
from nora.testing import FakeRecordStore, InMemoryStandingStore

pytestmark = pytest.mark.asyncio


class _Client:
    """Records every prompt and counts every call. The script answers the
    STANDING call with a fresh position per fold (so the prior-carrying
    assertion can read which position reached the next prompt) and the VERDICT
    call with a grounded verdict — the rollup makes BOTH calls (D4)."""

    def __init__(self, *, malformed: bool = False) -> None:
        self.prompts: list[str] = []
        self.calls = 0
        self._n = 0
        self._malformed = malformed

    @property
    def model(self) -> str:
        return "test-model"

    async def complete(self, *, prompt: str) -> standing_event.Completion:
        self.calls += 1
        self.prompts.append(prompt)
        self._n += 1
        if "The position you write must be at most" in prompt:
            # The STANDING call's prompt carries the max_chars sentence.
            if self._malformed:
                return standing_event.Completion("not json", "stop")
            return standing_event.Completion(
                json.dumps({"standing": f"position {self._n}"}), "stop")
        return standing_event.Completion(
            json.dumps({"grounded": True, "unsupported": []}), "stop")


class _Objective:
    def objective_body(self, name: str) -> str:
        assert name in {standing_event.OBJECTIVE, standing_event.VERDICT_OBJECTIVE}
        return "OBJECTIVE-TEXT" if name == standing_event.OBJECTIVE else "VERDICT-TEXT"


def _msg(conv: str = "g1") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="hello", raw={},
    )


class _Harness:
    """The wiring plus a spawn collector: `run` schedules the check through
    `spawn`; the check, seeing the slot FREE (these tests never hold it), spawns
    the event fold the same way. Collecting instead of backgrounding lets the
    test drive each coroutine to completion deterministically."""

    def __init__(self, *, record_store: FakeRecordStore, client: _Client | None = None,
                 rollup_max_folds: int = 5, fold_batch: int = 200) -> None:
        self.record_store = record_store
        self.spawned: list = []
        self.slots = ConversationSlots(maxlen=16, spawn=self.spawned.append)
        self.client = client if client is not None else _Client()
        self.wiring = standing_event.Wiring(
            store=InMemoryStandingStore(),
            record_store=record_store,
            client=self.client,
            objective=_Objective(),
            slots=self.slots,
            display_name="Nora",
            fold_batch=fold_batch,
            rollup_max_folds=rollup_max_folds,
            timeout_seconds=5.0,
            store_timeout_seconds=5.0,
            max_chars=1200,
        )
        self.metrics = Metrics()

    async def run_one_spawn(self) -> None:
        """`run`, then drive every coroutine the check and its folds spawn."""
        await standing_event.run(
            message=_msg(), wiring=self.wiring, metrics=self.metrics,
            spawn=self.spawned.append,
        )
        while self.spawned:
            coro = self.spawned.pop(0)
            await coro

    async def read_row(self) -> Standing | None:
        return await self.wiring.store.read(platform="signal", conversation_id="g1")


async def _seed(store: FakeRecordStore, n: int) -> None:
    for i in range(1, n + 1):
        await store.append(message=_msg(), is_nora=(i % 3 == 0))


def _counter(metrics: Metrics, name: str, **labels) -> float:
    return metrics.get(name, **labels)


# --- the trigger ----------------------------------------------------------


async def test_a_check_with_no_rows_since_the_watermark_calls_no_model():
    """40-verification's first trigger property: a conversation with nothing
    since its watermark must not spend a model call. The check reads the
    watermark and the count and stops."""
    harness = _Harness(record_store=FakeRecordStore())
    await harness.run_one_spawn()
    assert harness.client.calls == 0
    assert _counter(harness.metrics, "nora_standing_event_folds_total") == 0
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 0


async def test_a_check_below_the_accumulator_threshold_calls_no_model():
    """The accumulator threshold IS the batch (D1): 199 new rows since the
    watermark are not a fold — the check waits for the 200th message."""
    store = FakeRecordStore()
    await _seed(store, 199)
    harness = _Harness(record_store=store)
    await harness.run_one_spawn()
    assert harness.client.calls == 0
    assert await harness.read_row() is None  # nothing written, no watermark


async def test_the_accumulator_threshold_fires_one_fold():
    """Exactly fold_batch new rows -> one event fold of exactly fold_batch
    rows, and the watermark lands on the LAST row of the batch."""
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store)
    await harness.run_one_spawn()
    assert harness.client.calls == 2  # standing + verdict (D4)
    assert _counter(harness.metrics, "nora_standing_event_folds_total") == 1
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 1
    row = await harness.read_row()
    assert row is not None and row.last_folded_row_id == 200


# --- the rollup -----------------------------------------------------------


async def test_the_rollup_converges_500_rows_in_three_folds():
    """40-verification: 500 rows / no prior -> 3 folds (200+200+100), each with
    the previous position as prior, final watermark = 500. Rollup folds do NOT
    increment fold_count (D3) — the row's counter stays 0."""
    store = FakeRecordStore()
    await _seed(store, 500)
    harness = _Harness(record_store=store)
    await harness.run_one_spawn()
    assert harness.client.calls == 6  # 3 folds x (standing + verdict)
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 3
    row = await harness.read_row()
    assert row is not None
    assert row.last_folded_row_id == 500
    assert row.fold_count == 0, "rollup folds must NOT increment fold_count (D3)"
    # Each fold's standing prompt carries the PREVIOUS fold's position as the
    # prior: the standing prompts are every other call (1st, 3rd, 5th).
    standing_prompts = harness.client.prompts[0::2]
    assert len(standing_prompts) == 3
    assert "no position yet" in standing_prompts[0]  # first fold: no prior
    assert "position 1" in standing_prompts[1]       # second fold: prior = fold 1
    assert "position 3" in standing_prompts[2]       # third fold: prior = fold 2


async def test_the_rollup_is_bounded_at_rollup_max_folds_per_spawn():
    """40-verification's bound: 2000 new rows -> at most 5 folds per spawn
    (1000 rows folded, watermark = 1000); the NEXT spawn continues and drains
    the rest (5 more folds, watermark = 2000)."""
    store = FakeRecordStore()
    await _seed(store, 2000)
    harness = _Harness(record_store=store, rollup_max_folds=5)
    await harness.run_one_spawn()
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 5
    row = await harness.read_row()
    assert row is not None and row.last_folded_row_id == 1000
    assert harness.client.calls == 10

    # The next message's check continues from the watermark.
    await harness.run_one_spawn()
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 10
    row = await harness.read_row()
    assert row is not None and row.last_folded_row_id == 2000


async def test_a_refused_fold_advances_neither_body_nor_watermark():
    """A malformed standing answer writes nothing: the prior (none here) stays,
    and the watermark stays NULL — so the NEXT message's check re-reads the
    same stretch. The atomic write itself is pinned against both stores by the
    contract suite; this is the stage-level half."""
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store, client=_Client(malformed=True))
    await harness.run_one_spawn()
    assert await harness.read_row() is None
    # The rollup stops on the refusal — one fold attempted, none written.
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 0
    assert _counter(harness.metrics, "nora_debrief_total", outcome="malformed") == 1


async def test_the_watermark_gap_gauge_is_set_by_the_check():
    """D7's gauge: rows behind the watermark as the accumulator read measures
    them (capped at fold_batch by the read's limit)."""
    store = FakeRecordStore()
    await _seed(store, 450)
    harness = _Harness(record_store=store)
    await harness.run_one_spawn()
    assert harness.metrics.get("nora_standing_watermark_gap") == 200
