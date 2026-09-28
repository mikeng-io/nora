"""The ingest signal stage and the touch path, through `IngestPipeline`
(design/cognition-loop/20-observe.md §1, §3; migration step 1).

What each test pins:

- one signal per recorded person-authored item — a message, a changed edit, a
  reaction, a delete — naming the Record row it was raised by;
- **Nora's own items raise none** (20 §3, the echo rule): an item from one of
  her ids, or an unattributed delete of her message;
- a person's reply to her message inside `reply_quiet_seconds` is recorded
  and marked `waits`, and outside it is not;
- reactions and deletes are recorded beside their target, never as messages,
  and one with no target on record is dropped and counted;
- `[cognition] mode = "off"` (no wiring) writes no signal at all.

All on the fakes the contract suites bind to production
(`FakeRecordStore`, `InMemorySignalStore`).
"""

from __future__ import annotations

import dataclasses

from nora.domain.models import EditTarget, NormalizedMessage, Quote, Touch
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.ingest.stages import signal as signal_stage
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeRecordStore
from nora.testing.signals import InMemorySignalStore

NORA = "+85255550000"
PERSON = "+85211112222"
NOW_S = 1_790_000_000.0
T0 = 1_790_000_000_000 - 60_000          # a minute before NOW


class _Memory:
    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, **_: object) -> None:
        return None

    async def feed(self, **_: object) -> bool:
        return True


def _msg(body: str = "hello", *, sender: str = PERSON, ts: int = T0,
         quote: Quote | None = None, message_id: str = "",
         touch: Touch | None = None, edit_of: EditTarget | None = None) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender, sender_name=None, timestamp=ts, body=body, raw={},
        quote=quote, message_id=message_id, touch=touch, edit_of=edit_of,
    )


def _setup(*, wired: bool = True, reply_quiet_seconds: float = 600.0):
    audit = FakeRecordStore()
    store = InMemorySignalStore()
    metrics = Metrics()
    wiring = signal_stage.Wiring(
        store=store, unit_of=lambda _p, _c: "nora", self_ids=frozenset({NORA}),
        reply_quiet_seconds=reply_quiet_seconds, clock=lambda: NOW_S,
    ) if wired else None
    pipeline = IngestPipeline(
        audit=audit, memory=_Memory(),  # type: ignore[arg-type]
        persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), allowlist=permissive_policy(),
        metrics=metrics, spawn=lambda coro: coro.close(), signal_wiring=wiring,
    )
    return pipeline, audit, store, metrics


async def _nora_row(audit: FakeRecordStore, ts: int) -> int:
    return await audit.append(message=_msg("her words", sender=NORA, ts=ts), is_nora=True)


async def test_a_person_s_message_appends_one_signal_naming_its_row():
    pipeline, audit, store, metrics = _setup()
    await pipeline.run(message=_msg("聽日七點記低"))
    assert len(audit.rows) == 1 and len(store.signals) == 1
    sig = store.signals[0]
    assert (sig.unit, sig.kind, sig.item, sig.ref) == (
        "nora", "message", "message", f"messages:{audit.rows[0]['id']}",
    )
    assert sig.cues == ("datetime", "keep")
    assert sig.waits is False and sig.at_ms == T0
    assert sig.recorded_at_ms == int(NOW_S * 1000)
    assert metrics.get("nora_signal_total", kind="message", cue="datetime") == 1
    assert metrics.get("nora_signal_total", kind="message", cue="keep") == 1


async def test_a_signal_with_no_cue_is_counted_as_none():
    pipeline, _audit, store, metrics = _setup()
    await pipeline.run(message=_msg("hello"))
    assert store.signals[0].cues == ()
    assert metrics.get("nora_signal_total", kind="message", cue="none") == 1


async def test_nora_s_own_item_raises_no_signal():
    """20 §3: «An item whose author is Nora raises no signal.» Her sends
    never reach ingest (R-12 in every parser), so the stage checks it itself;
    the positive control is the person's message beside it."""
    pipeline, audit, store, _ = _setup()
    await pipeline.run(message=_msg("from her own id", sender=NORA))
    await pipeline.run(message=_msg("from a person"))
    assert len(audit.rows) == 2                      # both recorded
    assert [s.ref for s in store.signals] == [f"messages:{audit.rows[1]['id']}"]


async def test_the_stage_refuses_nora_s_item_directly():
    """The stage-level pin of the same rule, with no pipeline in the way."""
    store = InMemorySignalStore()
    wiring = signal_stage.Wiring(
        store=store, unit_of=lambda _p, _c: "nora", self_ids=frozenset({NORA}),
        reply_quiet_seconds=600, clock=lambda: NOW_S,
    )
    got = await signal_stage.run(
        message=_msg(sender=NORA), item="message", row_id=7, person_id=None,
        wiring=wiring, metrics=Metrics(),
    )
    assert got is None and store.signals == []
    control = await signal_stage.run(
        message=_msg(), item="message", row_id=8, person_id=None,
        wiring=wiring, metrics=Metrics(),
    )
    assert control is not None and [s.ref for s in store.signals] == ["messages:8"]


async def test_a_reply_to_nora_inside_the_window_waits_and_outside_it_does_not():
    pipeline, audit, store, _ = _setup(reply_quiet_seconds=600)
    her_ts = T0 - 60_000
    await _nora_row(audit, her_ts)
    await pipeline.run(message=_msg("ok thanks", ts=T0,
                                    quote=Quote(author_id=NORA, text="", quoted_ts=her_ts)))
    await pipeline.run(message=_msg("late reply", ts=her_ts + 601_000,
                                    quote=Quote(author_id=NORA, text="", quoted_ts=her_ts)))
    await pipeline.run(message=_msg("reply to a person", ts=T0,
                                    quote=Quote(author_id=PERSON, text="", quoted_ts=1)))
    assert [s.waits for s in store.signals] == [True, False, False]


async def test_a_reaction_is_recorded_beside_its_target_and_signalled():
    pipeline, audit, store, metrics = _setup()
    await pipeline.run(message=_msg("dinner at 8", ts=5_000))
    target_id = audit.rows[0]["id"]
    result = await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=5_000), target_author=PERSON, emoji="👍",
    )))
    assert result.message is None and result.decision is None      # never a turn
    assert len(audit.rows) == 1                                      # never a message
    assert [(r["message_id"], r["emoji"]) for r in audit.reactions] == [(target_id, "👍")]
    assert store.signals[-1].item == "reaction"
    assert store.signals[-1].ref == f"message_reaction:{audit.reactions[0]['id']}"
    assert store.signals[-1].cues == ()
    assert metrics.get("nora_ingest_outcomes_total", platform="signal",
                       conversation_type="group", outcome="reaction_recorded") == 1


async def test_a_reaction_on_nora_s_message_inside_the_window_waits():
    pipeline, audit, store, _ = _setup()
    await _nora_row(audit, T0 - 30_000)
    await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=T0 - 30_000), target_author=NORA,
        emoji="❤️",
    )))
    assert len(audit.reactions) == 1
    assert [s.waits for s in store.signals] == [True]


async def test_a_delete_is_recorded_and_signalled():
    pipeline, audit, store, _ = _setup()
    await pipeline.run(message=_msg("oops", ts=5_000))
    await pipeline.run(message=_msg("", touch=Touch(kind="delete",
                                                    target=EditTarget(sent_ts=5_000))))
    assert [d["message_id"] for d in audit.deletes] == [audit.rows[0]["id"]]
    assert store.signals[-1].item == "delete"
    assert store.signals[-1].ref == f"message_delete:{audit.deletes[0]['id']}"


async def test_an_unattributed_delete_of_nora_s_message_is_recorded_and_raises_nothing():
    """An Instagram unsend names no one; of HER message it is her own act."""
    pipeline, audit, store, _ = _setup()
    row = await audit.append(
        message=dataclasses.replace(_msg("her words", sender=NORA), message_id="mid.$her"),
        is_nora=True,
    )
    await pipeline.run(message=_msg("", sender="", touch=Touch(
        kind="delete", target=EditTarget(message_id="mid.$her"))))
    assert [d["message_id"] for d in audit.deletes] == [row]
    assert store.signals == []


async def test_a_touch_of_nothing_on_record_is_dropped_and_counted():
    pipeline, audit, store, metrics = _setup()
    await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=424242), emoji="👍")))
    assert audit.reactions == [] and store.signals == []
    assert metrics.get("nora_ingest_outcomes_total", platform="signal",
                       conversation_type="group", outcome="reaction_orphaned") == 1


async def test_an_edit_that_changes_the_text_is_signalled_with_the_new_text_s_cues():
    pipeline, audit, store, _ = _setup()
    await pipeline.run(message=_msg("dinner", ts=5_000))
    await pipeline.run(message=_msg("dinner at 8pm", ts=6_000,
                                    edit_of=EditTarget(sent_ts=5_000)))
    assert len(audit.edits) == 1
    edit = store.signals[-1]
    assert (edit.item, edit.ref, edit.cues) == (
        "edit", f"messages:{audit.rows[0]['id']}", ("datetime",),
    )


async def test_mode_off_writes_no_signal_and_still_records_everything():
    pipeline, audit, _store, _ = _setup(wired=False)
    await pipeline.run(message=_msg("聽日", ts=5_000))
    await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=5_000), emoji="👍")))
    assert len(audit.rows) == 1 and len(audit.reactions) == 1


async def test_a_failed_signal_write_keeps_the_message_and_counts_an_error():
    pipeline, audit, store, metrics = _setup()
    store.fail = True
    result = await pipeline.run(message=_msg("hello"))
    assert len(audit.rows) == 1 and result.message is not None
    assert metrics.get("nora_errors_total", stage="signal", platform="signal") == 1
