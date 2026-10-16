"""The ingest stimulus stage and the touch path, through `IngestPipeline`.

What each test pins:

- one stimulus per recorded person-authored item — a message, a changed edit,
  a reaction, a delete — naming the Record row it was raised by;
- **Nora's own items raise none** (the echo rule): an item from one of
  her ids, or an unattributed delete of her message;
- a person's reply to her message inside `reply_quiet_seconds` is recorded
  and marked `waits`, and outside it is not;
- reactions and deletes are recorded beside their target, never as messages,
  and one with no target on record is dropped and counted;
- `[cognition] mode = "off"` (no wiring) writes no stimulus at all.

All on the fakes the contract suites bind to production
(`FakeRecordStore`, `InMemoryStimulusStore`).
"""

from __future__ import annotations

import dataclasses

from nora.domain.models import EditTarget, NormalizedMessage, Quote, Touch
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.ingest.stages import stimulus as stimulus_stage
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeRecordStore
from nora.testing.stimuli import InMemoryStimulusStore

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
    store = InMemoryStimulusStore()
    metrics = Metrics()
    wiring = stimulus_stage.Wiring(
        store=store, unit_of=lambda _p, _c: "nora", self_ids=frozenset({NORA}),
        reply_quiet_seconds=reply_quiet_seconds, clock=lambda: NOW_S,
    ) if wired else None
    pipeline = IngestPipeline(
        audit=audit, memory=_Memory(),  # type: ignore[arg-type]
        persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), allowlist=permissive_policy(),
        metrics=metrics, spawn=lambda coro: coro.close(), stimulus_wiring=wiring,
    )
    return pipeline, audit, store, metrics


async def _nora_row(audit: FakeRecordStore, ts: int) -> int:
    return await audit.append(message=_msg("her words", sender=NORA, ts=ts), is_nora=True)


async def test_a_person_s_message_appends_one_stimulus_naming_its_row():
    pipeline, audit, store, metrics = _setup()
    await pipeline.run(message=_msg("聽日七點記低"))
    assert len(audit.rows) == 1 and len(store.stimuli) == 1
    stim = store.stimuli[0]
    assert (stim.unit, stim.kind, stim.item, stim.ref) == (
        "nora", "message", "message", f"messages:{audit.rows[0]['id']}",
    )
    assert stim.cues == ("datetime", "keep")
    assert stim.waits is False and stim.at_ms == T0
    assert stim.recorded_at_ms == int(NOW_S * 1000)
    assert metrics.get("nora_stimulus_total", kind="message", cue="datetime") == 1
    assert metrics.get("nora_stimulus_total", kind="message", cue="keep") == 1


async def test_a_stimulus_with_no_cue_is_counted_as_none():
    pipeline, _audit, store, metrics = _setup()
    await pipeline.run(message=_msg("hello"))
    assert store.stimuli[0].cues == ()
    assert metrics.get("nora_stimulus_total", kind="message", cue="none") == 1


async def test_nora_s_own_item_raises_no_stimulus():
    """The echo rule: «An item whose author is Nora raises no stimulus.» Her sends
    never reach ingest (R-12 in every parser), so the stage checks it itself;
    the positive control is the person's message beside it."""
    pipeline, audit, store, _ = _setup()
    await pipeline.run(message=_msg("from her own id", sender=NORA))
    await pipeline.run(message=_msg("from a person"))
    assert len(audit.rows) == 2                      # both recorded
    assert [s.ref for s in store.stimuli] == [f"messages:{audit.rows[1]['id']}"]


async def test_the_stage_refuses_nora_s_item_directly():
    """The stage-level pin of the same rule, with no pipeline in the way."""
    store = InMemoryStimulusStore()
    wiring = stimulus_stage.Wiring(
        store=store, unit_of=lambda _p, _c: "nora", self_ids=frozenset({NORA}),
        reply_quiet_seconds=600, clock=lambda: NOW_S,
    )
    got = await stimulus_stage.run(
        message=_msg(sender=NORA), item="message", row_id=7, person_id=None,
        wiring=wiring, metrics=Metrics(),
    )
    assert got is None and store.stimuli == []
    control = await stimulus_stage.run(
        message=_msg(), item="message", row_id=8, person_id=None,
        wiring=wiring, metrics=Metrics(),
    )
    assert control is not None and [s.ref for s in store.stimuli] == ["messages:8"]


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
    assert [s.waits for s in store.stimuli] == [True, False, False]


async def test_a_reaction_is_recorded_beside_its_target_and_raises_a_stimulus():
    pipeline, audit, store, metrics = _setup()
    await pipeline.run(message=_msg("dinner at 8", ts=5_000))
    target_id = audit.rows[0]["id"]
    result = await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=5_000), target_author=PERSON, emoji="👍",
    )))
    assert result.message is None and result.decision is None      # never a turn
    assert len(audit.rows) == 1                                      # never a message
    assert [(r["message_id"], r["emoji"]) for r in audit.reactions] == [(target_id, "👍")]
    assert store.stimuli[-1].item == "reaction"
    assert store.stimuli[-1].ref == f"message_reaction:{audit.reactions[0]['id']}"
    assert store.stimuli[-1].cues == ()
    assert metrics.get("nora_ingest_outcomes_total", platform="signal",
                       conversation_type="group", outcome="reaction_recorded") == 1
    assert metrics.get("nora_messages_total", platform="signal") == 1, \
        "a reaction is not a message"


async def test_a_reaction_on_nora_s_message_inside_the_window_waits():
    pipeline, audit, store, _ = _setup()
    await _nora_row(audit, T0 - 30_000)
    await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=T0 - 30_000), target_author=NORA,
        emoji="❤️",
    )))
    assert len(audit.reactions) == 1
    assert [s.waits for s in store.stimuli] == [True]


async def test_a_delete_is_recorded_and_raises_a_stimulus():
    pipeline, audit, store, _ = _setup()
    await pipeline.run(message=_msg("oops", ts=5_000))
    await pipeline.run(message=_msg("", touch=Touch(kind="delete",
                                                    target=EditTarget(sent_ts=5_000))))
    assert [d["message_id"] for d in audit.deletes] == [audit.rows[0]["id"]]
    assert store.stimuli[-1].item == "delete"
    assert store.stimuli[-1].ref == f"message_delete:{audit.deletes[0]['id']}"
    assert audit.hidden_ids == {audit.rows[0]["id"]}, "a delete of a recorded message hides it"


async def test_a_reaction_hides_nothing_and_a_delete_of_nothing_on_record_is_dropped():
    pipeline, audit, _store, _ = _setup()
    await pipeline.run(message=_msg("kept", ts=5_000))
    await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=5_000), emoji="👍")))
    await pipeline.run(message=_msg("", touch=Touch(
        kind="delete", target=EditTarget(sent_ts=424242))))
    assert audit.hidden_ids == set() and audit.deletes == []


async def test_an_unattributed_delete_of_nora_s_message_is_recorded_and_raises_nothing():
    """An Instagram unsend names no one; of HER message it is her own act."""
    pipeline, audit, store, _ = _setup()
    row = await audit.append(
        message=dataclasses.replace(_msg("her words", sender=NORA), message_id="mid.$her"),
        is_nora=True,
    )
    unsend = dataclasses.replace(_msg("", sender="", touch=Touch(
        kind="delete", target=EditTarget(message_id="mid.$her"))), platform="instagram")
    await pipeline.run(message=unsend)
    assert [d["message_id"] for d in audit.deletes] == [row]
    assert store.stimuli == []


async def test_an_unattributed_delete_asserts_no_person():
    """A Discord delete (maybe a moderator's) or an Instagram unsend names no
    one: the row keeps no sender AND no person, never the author's."""
    pipeline, audit, store, _ = _setup()
    await audit.append(message=dataclasses.replace(_msg("hi"), message_id="mid.$p"),
                       person_id="someone")
    unsend = dataclasses.replace(_msg("", sender="", touch=Touch(
        kind="delete", target=EditTarget(message_id="mid.$p"))), platform="instagram")
    await pipeline.run(message=unsend)
    assert [(d["sender_id"], d["person_id"]) for d in audit.deletes] == [("", None)]
    assert store.stimuli[-1].person_id is None


async def test_a_signal_delete_of_someone_else_s_message_is_refused():
    """Only a Signal message's author can remote-delete it; a delete naming
    another author's message is malformed or forged, and is refused like an
    edit by someone else. The positive control is the author's own delete."""
    pipeline, audit, store, metrics = _setup()
    await pipeline.run(message=_msg("mine", sender=PERSON, ts=5_000))
    await pipeline.run(message=_msg("", sender="+85299999999", touch=Touch(
        kind="delete", target=EditTarget(sent_ts=5_000))))
    assert audit.deletes == []
    assert metrics.get("nora_ingest_outcomes_total", platform="signal",
                       conversation_type="group", outcome="delete_refused") == 1
    await pipeline.run(message=_msg("", sender=PERSON, touch=Touch(
        kind="delete", target=EditTarget(sent_ts=5_000))))
    assert len(audit.deletes) == 1
    assert [s.item for s in store.stimuli] == ["message", "delete"]


async def test_a_touch_of_nothing_on_record_is_dropped_and_counted():
    pipeline, audit, store, metrics = _setup()
    await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=424242), emoji="👍")))
    assert audit.reactions == [] and store.stimuli == []
    assert metrics.get("nora_ingest_outcomes_total", platform="signal",
                       conversation_type="group", outcome="reaction_orphaned") == 1


async def test_an_edit_that_changes_the_text_raises_a_stimulus_with_the_new_text_s_cues():
    pipeline, audit, store, _ = _setup()
    await pipeline.run(message=_msg("dinner", ts=5_000))
    await pipeline.run(message=_msg("dinner at 8pm", ts=6_000,
                                    edit_of=EditTarget(sent_ts=5_000)))
    assert len(audit.edits) == 1
    edit = store.stimuli[-1]
    assert (edit.item, edit.ref, edit.cues) == (
        "edit", f"messages:{audit.rows[0]['id']}", ("datetime",),
    )


async def test_mode_off_writes_no_stimulus_and_still_records_everything():
    pipeline, audit, _store, _ = _setup(wired=False)
    await pipeline.run(message=_msg("聽日", ts=5_000))
    await pipeline.run(message=_msg("", touch=Touch(
        kind="reaction", target=EditTarget(sent_ts=5_000), emoji="👍")))
    assert len(audit.rows) == 1 and len(audit.reactions) == 1


async def test_a_failed_stimulus_write_keeps_the_message_and_counts_an_error():
    pipeline, audit, store, metrics = _setup()
    store.fail = True
    result = await pipeline.run(message=_msg("hello"))
    assert len(audit.rows) == 1 and result.message is not None
    assert metrics.get("nora_errors_total", stage="stimulus", platform="signal") == 1


async def test_a_stimulus_stage_that_raises_never_stops_the_turn(monkeypatch):
    """Fail-soft is the WHOLE stage, not only the write: a cue function that
    raises still lets the message be recorded, fed and answered."""
    def _boom(_text):
        raise RuntimeError("a cue pattern broke")

    monkeypatch.setattr(stimulus_stage, "cues_of", _boom)
    pipeline, audit, store, metrics = _setup()
    result = await pipeline.run(message=_msg("nora.000 are you there"))
    assert result.decision is not None and result.decision.engage is True
    assert len(audit.rows) == 1 and store.stimuli == []
    assert metrics.get("nora_errors_total", stage="stimulus", platform="signal") == 1
