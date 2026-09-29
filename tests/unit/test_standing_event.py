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
import time

import pytest

from nora.domain.models import NormalizedMessage
from nora.domain.standing import STANDING_BODY_CEILING_CHARS, Standing
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages.debrief import Completion
from nora.ingest.stages import standing_event
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, span
from nora.testing import FakeRecordStore, InMemoryStandingStore

pytestmark = pytest.mark.asyncio


class _Client:
    """Records every prompt and counts every call. It answers a STANDING call
    — recognised by the max_chars sentence the fold's system prompt carries —
    with a fresh position per fold, so the prior-carrying assertion can read
    which position reached the next prompt. Anything else gets an empty
    object, which no parser in the stage accepts: the rollup makes ONE call
    per fold since the verdict was deleted (design/standing-compact/), and a
    second call is counted rather than answered helpfully."""

    def __init__(self, *, malformed: bool = False) -> None:
        self.prompts: list[str] = []
        self.calls = 0
        self._n = 0
        self._malformed = malformed
        #: What `_complete` passed as `trace_attributes` on each call, in
        #: order — see `test_the_event_folds_tag_id_is_honestly_empty` below.
        self.trace_attributes: list[dict | None] = []

    @property
    def model(self) -> str:
        return "test-model"

    async def complete(
        self, *, system: str, prompt: str, trace_attributes=None,
    ) -> Completion:
        self.trace_attributes.append(trace_attributes)
        self.calls += 1
        self.prompts.append(f"{system}\n\n{prompt}")
        self._n += 1
        if "The position you write must be at most" in system:
            # The STANDING call's prompt carries the max_chars sentence.
            if self._malformed:
                return Completion("not json", "stop")
            return Completion(
                json.dumps({"standing": f"position {self._n}"}), "stop")
        return Completion("{}", "stop")


class _Objective:
    def objective_body(self, name: str) -> str:
        assert name == standing_event.OBJECTIVE, name
        return "OBJECTIVE-TEXT"


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
    assert harness.client.calls == 1  # one fold, one call
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
    assert harness.client.calls == 3  # 3 folds, one call each
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 3
    row = await harness.read_row()
    assert row is not None
    assert row.last_folded_row_id == 500
    assert row.fold_count == 0, "rollup folds must NOT increment fold_count (D3)"
    # Each fold's prompt carries the PREVIOUS fold's position as the prior.
    standing_prompts = harness.client.prompts
    assert len(standing_prompts) == 3
    assert "no position yet" in standing_prompts[0]  # first fold: no prior
    assert "position 1" in standing_prompts[1]       # second fold: prior = fold 1
    assert "position 2" in standing_prompts[2]       # third fold: prior = fold 2


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
    assert harness.client.calls == 5

    # The next message's check continues from the watermark.
    await harness.run_one_spawn()
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 10
    row = await harness.read_row()
    assert row is not None and row.last_folded_row_id == 2000


async def test_a_rollup_fold_makes_exactly_one_model_call():
    """design/standing-compact/00: the event path used to gather a verdict
    call beside every fold and persist nothing from it — a model call per
    rollup with no reader. One fold is one call now, labelled `fold`."""
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store)
    await harness.run_one_spawn()
    assert _counter(harness.metrics, "nora_standing_rollup_folds_total") == 1
    assert [ta["nora.debrief.call"] for ta in harness.client.trace_attributes] == [
        "fold"]


async def test_the_rollup_shows_a_prior_past_the_old_ceiling_with_its_age():
    """design/standing-compact/10 §1-§2 on the event path: the prior is shown
    whatever its `fold_count` (the tag path's deleted ceiling fired at 20), and
    `<standing_prior>` says how long ago it was written, against the fold's own
    clock — the rollup has no turn to measure against."""
    harness = _Harness(record_store=FakeRecordStore())
    await harness.wiring.store.write(standing=Standing(
        platform="signal", conversation_id="g1",
        body="tomorrow at 10 I owe Miles the route", fold_count=25,
        updated_at=int(time.time() * 1000) - 3 * 86_400_000 - 60_000))
    await _seed(harness.record_store, 200)
    await harness.run_one_spawn()
    [prompt] = harness.client.prompts
    opening, rest = prompt.split("<standing_prior ", 1)[1].split(">", 1)
    assert 'written="3d ago"' in opening, opening
    assert "tomorrow at 10 I owe Miles the route" in rest.split("</standing_prior>")[0]
    assert "no position yet" not in prompt
    row = await harness.read_row()
    assert row is not None and row.body == "position 1"
    assert row.fold_count == 25, "rollup folds must NOT increment fold_count (D3)"


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


async def test_an_oversized_hold_open_reaching_the_write_is_refused_not_error(
    monkeypatch,
):
    """R-27 regression (PR #624 review): master built `Standing(...)` outside
    any `try` in this fold, the same way the tag path's debrief did — a
    domain-invariant `ValueError` is a REFUSAL, never a store failure, and
    must not be counted on the `except Exception` below as `error`. Moving
    the construction into `standing_revise` lost that discipline here too.

    This module's objective does not ask the model for `hold_open`/`i_owe`
    yet, so the invariant is reached by wrapping the REAL `standing_revise`
    and forcing an oversized `hold_open` onto the write this fold makes —
    exercising the fix's real validation and construction path end to end.
    """
    too_long = "x" * (STANDING_BODY_CEILING_CHARS + 1)
    real_standing_revise = standing_event.standing_revise

    async def _forcing_an_oversized_hold_open(**kwargs):
        kwargs["hold_open"] = too_long
        return await real_standing_revise(**kwargs)

    monkeypatch.setattr(
        standing_event, "standing_revise", _forcing_an_oversized_hold_open,
    )
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store)
    await harness.run_one_spawn()
    assert await harness.read_row() is None
    assert _counter(harness.metrics, "nora_debrief_total", outcome="error") == 0
    assert _counter(
        harness.metrics, "nora_debrief_total", outcome="refused_section_too_long",
    ) == 1


async def test_the_watermark_gap_gauge_is_set_by_the_check():
    """D7's gauge: rows behind the watermark as the accumulator read measures
    them (capped at fold_batch by the read's limit)."""
    store = FakeRecordStore()
    await _seed(store, 450)
    harness = _Harness(record_store=store)
    await harness.run_one_spawn()
    assert harness.metrics.get("nora_standing_watermark_gap") == 200


# --- tracing (issue #209) --------------------------------------------------


async def test_an_event_fold_from_inside_plumbing_exports_one_generation_root(
        exported_spans):
    """The event fold must never inherit the ingest pipeline's `plumbing`
    root, whoever dispatched it (issue #209). `nora/ingest/pipeline.py` opens
    exactly this shape — a root span declaring `nora.trace_kind=plumbing` —
    around every stage it runs, including this one's `run`/`_check`; without
    its own `attached(None)` the fold's spans would nest under that root and
    be dropped WITH it at the sampler (`_PlumbingIsNotATrace`, R-76), same as
    every event fold before this fix (issue #209's root-name census: zero
    `standing.event_fold` / `engagement.debrief.*` roots on 09-09 or 09-10).

    `exported_spans` is built with `build_sampler()` — production's own rule
    — so a regression here does not just misname a span, it makes the whole
    fold invisible to the collector.

    PROBE: delete `with attached(None)` from `_event_fold` and this test
    reddens — the plumbing root captures the fold's spans and the sampler
    drops all of them, so `exported_spans` comes back empty. See the paired
    mutation entry.
    """
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store)

    with span("ingest.some_stage", **{TRACE_KIND: "plumbing"}):
        await harness.run_one_spawn()

    spans = exported_spans.get_finished_spans()
    roots = [s for s in spans if s.parent is None]
    assert [s.name for s in roots] == ["standing.event_fold"], (
        "the event fold must export exactly ONE root of its own — never the "
        f"ingest pipeline's plumbing root; roots seen: {[s.name for s in roots]}"
    )
    root = roots[0]
    assert root.attributes["nora.trace_kind"] == "generation"
    assert root.attributes["nora.platform"] == "signal"
    assert root.attributes["nora.conversation_id"] == "g1"
    # The fold's call no longer opens its own named span (it
    # run through `nora.agent.structured.ask_text` in production, a real
    # Strands Agent invocation Strands instruments itself) — this file's
    # `_Client` fake, like `test_debrief.py`'s, never calls Strands, so no
    # child span is produced here at all. `test_the_event_folds_tag_id_is_
    # honestly_empty` below covers the trace_attributes half of this claim.


async def test_the_event_folds_tag_id_is_honestly_empty():
    """The event fold has no tag — `_complete`'s `trace_attributes` must
    carry an EMPTY `nora.tag_id`, never a borrowed or fabricated one
    (issue #209), on the one call it makes."""
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store)

    with span("ingest.some_stage", **{TRACE_KIND: "plumbing"}):
        await harness.run_one_spawn()

    assert harness.client.trace_attributes, "the fold made no calls to assert on"
    for ta in harness.client.trace_attributes:
        assert ta["nora.tag_id"] == ""
        assert ta["nora.conversation_id"] == "g1"


async def test_a_queued_fold_dispatched_from_another_turns_span_still_opens_its_own_root(
        exported_spans):
    """The SECOND dispatch path (issue #209): a fold ENQUEUED behind a held
    slot (D2 — the check never `try_acquire`-skips) is later started by
    `ConversationSlots.release_and_dispatch`, which can run from inside some
    OTHER turn's span — the tag path's own `engagement` root, in production —
    rather than the ingest path's `plumbing` root the test above covers.
    `attached(None)` lives in `_event_fold` itself, not at either dispatch
    call site, so it has to cover this path too; this is proved rather than
    assumed, because a `plumbing` root and a real (`generation`-kind) root
    fail differently — nesting under `plumbing` is invisible (the whole
    subtree is dropped at the sampler), where nesting under a live turn's own
    root is exactly issue #209's OTHER failure mode: the fold surfaces, but as
    an unnamed child of somebody else's trace rather than its own. The first
    test cannot see that shape at all — its parent is dropped, so `nested`
    and `dropped` read identically there.

    PROBE: delete `with attached(None)` from `_event_fold` and the two roots
    below collapse into one — the fold's spans nest under `unrelated.turn`
    instead of opening `standing.event_fold` as a trace of its own.
    """
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store)
    key = ("signal", "g1")

    # Simulate a turn already holding this conversation's slot: the check
    # must ENQUEUE the fold rather than spawn it directly (D2).
    harness.slots.acquire(key)
    await standing_event.run(
        message=_msg(), wiring=harness.wiring, metrics=harness.metrics,
        spawn=harness.spawned.append,
    )
    await harness.spawned.pop(0)  # the check
    assert not harness.spawned, "the check must enqueue, not spawn, while held"
    assert harness.slots.depth(key) == 1

    # The holder's OWN turn finishes, from inside its OWN span — and THAT is
    # what starts the queued fold, exactly as `release_and_dispatch`'s own
    # docstring describes.
    with span("unrelated.turn", **{TRACE_KIND: "generation"}):
        harness.slots.release_and_dispatch(key)
        assert harness.spawned, "release_and_dispatch must spawn the queued fold"
        await harness.spawned.pop(0)

    spans = exported_spans.get_finished_spans()
    roots = {s.context.trace_id: s for s in spans if s.parent is None}
    assert sorted(s.name for s in roots.values()) == [
        "standing.event_fold", "unrelated.turn",
    ], (
        "the queued fold must open its OWN trace, never nest under whichever "
        "turn's span happened to be current when release_and_dispatch ran it"
    )
    # The fold's call no longer opens its own named child span
    # under `standing.event_fold` — see the plumbing-root test's own note
    # above; the root-shape claim this test exists for (two SEPARATE trace
    # ids, not one fold nested under `unrelated.turn`) is unaffected and is
    # exactly what the `sorted(...)` assertion above already proves.


async def test_the_rollups_prior_age_is_measured_from_the_last_write(monkeypatch):
    """M7 from the review (design/standing-compact/10 §2 on the event path).
    `written` is only honest if every rollup fold REWRITES `updated_at`. Two
    folds in one spawn, on a clock that advances two hours between them: the
    second fold's prior must read «2h ago» — the age since the FIRST fold's
    write, not since the row the rollup started from thirty days earlier."""
    start = 1_756_000_000.0  # seconds, as `time.time()` returns
    ticks = iter([start, start + 2 * 3600])
    monkeypatch.setattr(
        standing_event, "time", type("_Clock", (), {"time": staticmethod(
            lambda: next(ticks))}))
    harness = _Harness(record_store=FakeRecordStore())
    await harness.wiring.store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="an old position",
        fold_count=4, updated_at=int(start * 1000) - 30 * 86_400_000))
    await _seed(harness.record_store, 400)
    await harness.run_one_spawn()
    first, second = harness.client.prompts
    assert 'written="30d ago"' in first.split("<standing_prior ", 1)[1].split(">", 1)[0]
    opening = second.split("<standing_prior ", 1)[1].split(">", 1)[0]
    assert 'written="2h ago"' in opening, opening
    assert "position 1" in second
    row = await harness.read_row()
    assert row is not None and row.updated_at == int((start + 2 * 3600) * 1000)


async def test_the_event_fold_reads_the_rooms_account_by_primary_id():
    """DEC-207 on the event path: the fold resolves `(platform, thread)` to
    the room's `primary_id` through `culture_key` and renders the account in
    its prompt."""
    import dataclasses

    from nora.testing import InMemoryCultureStore

    culture = InMemoryCultureStore()
    await culture.append(
        key="room-primary", body="they decide by attrition", written_at=1,
        established_by="dream:culture:test")
    culture.reads.clear()
    store = FakeRecordStore()
    await _seed(store, 200)
    harness = _Harness(record_store=store)
    harness.wiring = dataclasses.replace(
        harness.wiring, culture=culture,
        culture_key=lambda p, c: "room-primary" if (p, c) == ("signal", "g1") else None,
    )
    await harness.run_one_spawn()

    assert culture.reads == ["room-primary"]
    assert "they decide by attrition" in harness.client.prompts[0]
