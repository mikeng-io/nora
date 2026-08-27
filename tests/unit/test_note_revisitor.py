"""`nora.language.note_revisitor.NoteRevisitor` — the note tier's negative
measurement, and the only thing in the tier that retires a note.

**Every test here is written against one asymmetry.** Retirement is
irreversible in the direction that matters: a note swept away while its
question is still open is a tracked uncertainty that silently stopped being
tracked, and nothing downstream can tell that apart from a question that got
answered. So the tests are not balanced between "does it retire" and "does it
refuse" — the refusals get the weight, because a bug that retires too eagerly
costs a note and a bug that retires too little costs a tick.

The doubles reproduce the REAL collaborators' wrong-call shapes, not just their
clean ones: `_FakeNoteStore.supersede` returns 0 for an unknown target and never
raises (the store's actual contract), and `_FakeRecord.recent` can raise, which
is how a record outage reaches the pass in production.
"""

from __future__ import annotations

import pytest

from nora.domain.models import DeliveryStatus
from nora.domain.notes import Note, NoteSupersedeReason
from nora.domain.ports import Proposal
from nora.language.note_revisitor import (
    ITEM_KEYS,
    NoteRetirement,
    NoteRevisitor,
)
from nora.obs.metrics import Metrics
from nora.testing.fakes import FakeRecordStore
from tests.objectives import shipped_objectives

pytestmark = pytest.mark.asyncio

WORKSPACE = "nora"
DAY_MS = 86_400_000
# The anchor every fixture note carries, and it is deliberately a date in the
# PAST rather than a plausible-looking near-future one: `anchor_passed` is
# computed against the real clock, so a fixture dated a few weeks out would make
# the whole file go quiet the moment that date arrived — and it would go quiet
# by rendering nothing, which reads as passing.
PASSED_ANCHOR = 20260101
# The first instant after `PASSED_ANCHOR`'s last — evidence at or after this is
# what `_covers` requires before an expiry is reachable at all.
AFTER_ANCHOR = 1_767_225_600_000 + 86_400_000
FUTURE_ANCHOR = 21000101


def _note(
    note_id="0191f2c4-0000-7000-8000-000000000001",
    *,
    anchor_date: int | None = PASSED_ANCHOR,
    created_at: int = 1,
    **kwargs,
) -> Note:
    return Note(
        id=note_id, workspace=WORKSPACE, title="9 Sept dinner",
        description="Three venues on the table; nobody has picked one.",
        uncertainty="which venue, and whether the 9th still works",
        closing_condition="a venue is booked, or the date is abandoned",
        established_in="g1", created_at=created_at,
        head_revision_id=f"{note_id}-r1", anchor_date=anchor_date, **kwargs,
    )


class _FakeNoteStore:
    """Stands in for `SqlNoteStore`, and holds itself to the store's REAL
    contract on the one verb this adaptor calls:

    - `supersede` returns rows affected and **never raises** — 0 for an unknown
      or already-retired target. A fake that raised instead would hide the
      whole `result="no_rows"` branch, which is the branch that exists because
      the real store cannot raise.
    - a non-null `source` RAISES, because the note tier has no column for a
      supersession's citation. A fake that accepted it would let the adaptor
      ship a call the real store refuses.
    """

    def __init__(self, notes: tuple[Note, ...] = (), *, known: set[str] | None = None):
        self._notes = notes
        # Which targets actually resolve. Defaults to the notes handed in, so a
        # test superseding a real note gets 1 and an invented id gets 0.
        self._known = {n.id for n in notes} if known is None else known
        self._retired: set[str] = set()
        self.calls: list[dict] = []

    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
        return self._notes if workspace == WORKSPACE else ()

    async def propose(self, *, key, proposals, now_ms):  # pragma: no cover
        raise AssertionError("the revisitor must never call propose")

    async def supersede(
        self, *, key, target, replaced_by, because, source, now_ms,
    ) -> int:
        if source is not None:
            raise ValueError("note supersede takes no source")
        assert because in NoteSupersedeReason.ALL, because
        self.calls.append({
            "key": key, "target": target, "replaced_by": replaced_by,
            "because": because, "now_ms": now_ms,
        })
        # ALREADY-RETIRED returns 0, like the real store: `SqlNoteStore
        # .supersede` filters `NOTE.c.retired_at.is_(None)`, so a second
        # supersession of one note affects no rows. The docstring above used to
        # CLAIM this branch and not implement it, which is the same
        # double-diverges-from-the-real-store shape four other findings have
        # been — and it is the exact branch `result="no_rows"` exists to expose,
        # so a double that got it wrong would make any future test of that
        # counter pass while production did the opposite.
        if target not in self._known or target in self._retired:
            return 0
        self._retired.add(target)
        return 1


def _record(messages=None, *, fail: bool = False) -> FakeRecordStore:
    """The REAL `FakeRecordStore` (`nora.testing.fakes`), not a local double.

    This file previously carried its own `recent` — a FOURTH implementation of
    a port with three, covered by no contract suite, against CLAUDE.md's rule
    that a port never gains an implementation without one. It was written from
    the adaptor's mistaken belief about ordering rather than from the store's
    contract, so the adaptor and its double agreed and the whole file went
    green against the bug. Reverting either half alone reddened; reverting both
    together — which is exactly how the bug shipped — passed.

    A test that pins two things against each other pins nothing. This one is
    bound to `tests/contract/test_audit_store_contract.py::
    test_recent_returns_rows_oldest_first`, which holds it and `SqlRecordStore`
    to the SAME ordering — so the coordinated revert is no longer possible.
    `fail_on_recent` is its own outage switch; the local double's `fail=True`
    is gone with it.
    """
    store = FakeRecordStore()
    for row in messages or []:
        store.rows.append(row)
    store.fail_on_recent = fail
    return store


def _row(id: int, body: str, ts: int = 10, *, is_nora: bool = False,
         person_id: str | None = "miles") -> dict:
    """One `FakeRecordStore` row, in the store's own dict shape.

    Written directly rather than through `append` because these tests care
    about `id` and `ts` precisely, and `append` assigns ids by insertion order.
    Every key `_transcript_row` reads is present, so this cannot drift into a
    shape the fake cannot project.
    """
    return {
        "id": id, "platform": "signal", "conversation_id": "g1",
        "sender_id": "+852111", "sender_name": "Miles", "person_id": person_id,
        "ts": ts, "body": body, "is_nora": is_nora,
        "delivery_status": DeliveryStatus.SENT, "raw_json": None,
        "quoted_ts": None, "media_json": "[]",
    }


# A fixed instant every test reasons from, so a boundary case can be written
# ON the boundary rather than near it. 2026-08-28T00:00:00Z.
NOW_MS = 1_787_961_600_000


def _revisitor(notes=(), messages=None, *, fail=False, metrics=None,
               now_ms=NOW_MS, **kwargs):
    store = _FakeNoteStore(notes)
    record = _record(messages, fail=fail)
    adaptor = NoteRevisitor(
        notes=store, record=record, metrics=metrics,
        clock=lambda: now_ms, **kwargs,
    )
    return adaptor, store


def _proposals(payloads) -> list[Proposal]:
    return [
        Proposal(payload=p, instruction_sha256="a" * 64, model="m", established_by="run-1")
        for p in payloads
    ]


# ---------------------------------------------------------------------------
# Selection — arithmetic, before the model sees anything
# ---------------------------------------------------------------------------


async def test_a_note_with_no_moment_is_never_due():
    """A note with no anchor DATE and no `revisit_at` is an open question with
    no deadline. Asking a model whether an undated question has gone stale is
    asking it to invent a deadline — so such a note is not offered for
    judgement at all, and nothing downstream can retire it.

    `Note.__post_init__` requires SOME anchor, so the entity-anchored note is
    the real shape of «no moment», not a contrived one: it has a referent to
    look it up by and no date to have gone past."""
    adaptor, _ = _revisitor(
        (_note(anchor_date=None, anchor_entity_ids=("tyler",)),), [_row(1, "hi")],
    )
    assert await adaptor.inputs(key=WORKSPACE, batch=[]) == ""


async def test_a_note_whose_moment_has_not_arrived_is_not_due():
    adaptor, _ = _revisitor((_note(anchor_date=FUTURE_ANCHOR),), [_row(1, "hi")])
    assert await adaptor.inputs(key=WORKSPACE, batch=[]) == ""


async def test_a_revisit_at_in_the_past_makes_a_note_due_without_an_anchor():
    """The second, independent trigger: an explicit «look at this again then»
    needs no anchor date. Without this the two triggers could collapse into one
    and nothing would say so."""
    adaptor, _ = _revisitor(
        (_note(anchor_date=None, anchor_entity_ids=("tyler",), revisit_at=1),),
        [_row(1, "hi")],
    )
    assert "### note 1" in await adaptor.inputs(key=WORKSPACE, batch=[])


async def test_an_unreadable_anchor_date_leaves_the_note_alone():
    """A malformed `anchor_date` is «I cannot tell when this was about», which
    must leave the note standing rather than expire it. 20261345 has no
    thirteenth month."""
    adaptor, _ = _revisitor((_note(anchor_date=20261345),), [_row(1, "hi")])
    assert await adaptor.inputs(key=WORKSPACE, batch=[]) == ""


async def test_the_batch_cap_is_counted_not_silently_truncated():
    """A tick that judged two of five due notes and said so is a backlog; one
    that said nothing is indistinguishable from a notebook with two due."""
    metrics = Metrics()
    notes = tuple(_note(f"note-{i}") for i in range(5))
    adaptor, _ = _revisitor(
        notes, [_row(1, "hi")], metrics=metrics, max_revisits_per_tick=2,
    )
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert rendered.count("### note ") == 2
    assert metrics.get("nora_note_revisit_deferred_total") == 3


# ---------------------------------------------------------------------------
# Evidence — and the fact that its absence is a state, not a nuisance
# ---------------------------------------------------------------------------


async def test_evidence_predating_the_note_is_not_shown():
    """A closing condition cannot have been met before the note existed.
    Rendering earlier traffic invites the model to answer from it."""
    adaptor, _ = _revisitor(
        (_note(created_at=100),),
        [
            _row(1, "before the note", ts=50),
            # ON the boundary, and it is the message that PROMPTED the note.
            # `row.ts > created_at` mutated to `>=` widens the evidence set to
            # include it, which lets a note close by citing its own cause —
            # and it left every other test in this file green. The third
            # uncovered comparison of this shape, and the second on the
            # PERMITTING side.
            _row(2, "the message that caused the note", ts=100),
            _row(3, "after the note", ts=150),
        ],
    )
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert "after the note" in rendered
    assert "before the note" not in rendered
    assert "the message that caused the note" not in rendered


async def test_a_note_with_no_evidence_still_renders_and_says_so():
    """Dropping it from the prompt would hide from the model that the question
    exists, and hide from a reader of the trace that this tick considered it
    and could not decide. An absence that is STATED can be reasoned about."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [], metrics=metrics)
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert "### note" in rendered
    assert "evidence: NONE" in rendered
    assert metrics.get("nora_note_revisit_candidates_total", evidence="absent") == 1


async def test_a_failing_record_read_is_counted_apart_from_a_quiet_room():
    """Both leave the note standing, and that is correct — but «the record was
    unreachable» and «the room said nothing» are different states, and a single
    counter for both is `unavailable` wearing `nothing_found`."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(1, "hi")], fail=True, metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert metrics.get("nora_note_revisit_candidates_total", evidence="unavailable") == 1
    assert metrics.get("nora_note_revisit_candidates_total", evidence="absent") == 0


# ---------------------------------------------------------------------------
# The measure — every branch below leaves the note standing except two
# ---------------------------------------------------------------------------


async def _asked(adaptor, notes, messages, **kw):
    """Run `inputs` first, because `accept` only recognises notes THIS TICK
    asked about. Skipping it would let every accept test pass against an empty
    `_asked` map for the wrong reason."""
    await adaptor.inputs(key=WORKSPACE, batch=[])


async def test_a_grounded_closed_verdict_retires_the_note():
    """The positive case, and it is first for the reason the language tier's
    suite puts its own first: a measure that only ever refuses is
    indistinguishable from a broken pass, and every other test in this file
    would still pass with `_accept_one` hard-wired to return None."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked Yardbird for the 9th")],
                            metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    [out] = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ])
    assert out.because == NoteSupersedeReason.RETIRED_CLOSED
    assert out.cited_message_id == 7
    assert metrics.get("nora_note_revisit_verdicts_total", verdict="closed") == 1


async def test_an_expired_verdict_retires_a_note_whose_moment_has_passed():
    """The evidence must SPAN the anchor, not merely exist — `_covers`. A
    message after the anchor's last instant is what makes «the moment went by
    and nothing settled it» a claim about conversation this pass actually
    read."""
    adaptor, _ = _revisitor(
        (_note(),), [_row(7, "anyway, different subject", ts=AFTER_ANCHOR)],
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    [out] = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ])
    assert out.because == NoteSupersedeReason.RETIRED_EXPIRED
    assert out.cited_message_id is None


async def test_a_note_this_tick_never_asked_about_is_refused():
    """The worst thing this adaptor can do, and it is one hallucinated
    identifier away. Refused BEFORE anything else in the item is read: nothing
    about the rest of a verdict can make retiring an unrelated note safe."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "9", "verdict": "closed", "cited_message_id": 7},
    ]) == ()
    assert metrics.get("nora_note_revisit_refused_total", reason="unknown_note") == 1


async def test_the_asked_set_does_not_survive_into_the_next_tick():
    """A note id from a PREVIOUS tick is exactly as unaskable as one the model
    invented — the notebook moved on, and the evidence that justified asking is
    gone. Without the reset in `inputs`, a stale verdict could retire a note
    against evidence from a tick that has already finished."""
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")])
    await adaptor.inputs(key=WORKSPACE, batch=[])
    # Second tick: the notebook is empty now.
    adaptor._notes = _FakeNoteStore(())
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ]) == ()


async def test_a_closed_verdict_citing_a_message_it_was_never_shown_is_refused():
    """The grounding, and the one part of the measure the model does not get to
    self-attest. Deleting the membership check turns this red and nothing
    else."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 999},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="citation_ungrounded") == 1


async def test_a_closed_verdict_with_no_citation_is_refused():
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": None},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="citation_missing") == 1


async def test_an_expired_verdict_on_a_note_whose_moment_has_not_passed_is_refused():
    """The clock decides expiry, not the model. A model that believes a note is
    stale while its anchor is still ahead is not looking at a calendar."""
    metrics = Metrics()
    adaptor, _ = _revisitor(
        (_note(anchor_date=FUTURE_ANCHOR, revisit_at=1),),
        [_row(7, "still nothing decided")], metrics=metrics,
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="anchor_not_passed") == 1


async def test_no_retirement_is_reachable_without_evidence():
    """**Silence is not an answer.** A room that has said nothing since the note
    was written cannot establish that a question went unanswered — it
    establishes that nobody spoke. Both verdicts are refused, so this is
    parametrised over both rather than pinning whichever one happened to be
    implemented first."""
    for verdict, cited in (("closed", 7), ("expired", None)):
        metrics = Metrics()
        adaptor, _ = _revisitor((_note(),), [], metrics=metrics)
        await adaptor.inputs(key=WORKSPACE, batch=[])
        assert await adaptor.accept(key=WORKSPACE, items=[
            {"note": "1", "verdict": verdict, "cited_message_id": cited},
        ]) == (), verdict
        assert metrics.get(
            "nora_note_revisit_refused_total", reason="no_evidence") == 1, verdict


async def test_unresolved_is_a_real_answer_and_is_counted_apart_from_a_refusal():
    """«Asked and could not tell» and «did not answer the question» are
    different states. Collapsing them would let a model that had stopped
    engaging hide among the honest holds."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "hmm")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "unresolved", "cited_message_id": None},
    ]) == ()
    assert metrics.get("nora_note_revisit_verdicts_total", verdict="unresolved") == 1
    assert metrics.get("nora_note_revisit_refused_total", reason="unmeasured") == 0


async def test_a_verdict_outside_the_three_states_is_unmeasured():
    """A fourth value is the model not answering the question. Guessing which of
    the three it meant would be the pass inventing a verdict nobody gave — and
    two of the three throw a note away."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "probably-done", "cited_message_id": 7},
    ]) == ()
    assert metrics.get("nora_note_revisit_refused_total", reason="unmeasured") == 1


async def test_the_merged_reason_is_never_this_adaptors_to_write():
    """`merged` is the curator's — merging is a judgement ACROSS notes, and this
    adaptor sees one note at a time by construction. A model asking for it is
    asking for a verb this pass does not hold."""
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")])
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "merged", "cited_message_id": 7},
    ]) == ()


# ---------------------------------------------------------------------------
# commit — and the gap between "attempted" and "moved something"
# ---------------------------------------------------------------------------


async def test_commit_supersedes_with_no_successor_and_no_source():
    """Three properties of the call the store actually accepts: `replaced_by`
    is always None (a dinner that happened is not replaced by anything),
    `source` MUST be None (the tier has no column for a supersession's
    citation, and a non-null value raises), and `because` is the tier's own
    vocabulary. `_FakeNoteStore` raises on a non-null source exactly as the real
    store does, so this cannot pass against a call the store would refuse."""
    adaptor, store = _revisitor((_note(),), [_row(7, "booked it")])
    written = await adaptor.commit(
        key=WORKSPACE, now_ms=99,
        proposals=_proposals([
            NoteRetirement(
                note_id=_note().id,
                because=NoteSupersedeReason.RETIRED_CLOSED,
                cited_message_id=7,
            ),
        ]),
    )
    assert written == 1
    [call] = store.calls
    assert call["replaced_by"] is None
    assert call["because"] == NoteSupersedeReason.RETIRED_CLOSED
    assert (call["target"], call["key"], call["now_ms"]) == (_note().id, WORKSPACE, 99)


async def test_a_retirement_that_moved_nothing_is_counted_apart_from_one_that_did():
    """`supersede` returns 0 for an unknown target and NEVER raises, so a
    retirement that silently did nothing looks exactly like one that was never
    attempted. This is the split that makes the difference visible, and it is
    the same unmeasured-versus-zero distinction the tier is built on."""
    metrics = Metrics()
    store = _FakeNoteStore((_note(),), known=set())  # nothing resolves
    adaptor = NoteRevisitor(notes=store, record=_record(), metrics=metrics)
    written = await adaptor.commit(
        key=WORKSPACE, now_ms=1,
        proposals=_proposals([
            NoteRetirement(
                note_id=_note().id,
                because=NoteSupersedeReason.RETIRED_EXPIRED,
                cited_message_id=None,
            ),
        ]),
    )
    assert written == 0
    assert metrics.get(
        "nora_note_retirements_total",
        because=NoteSupersedeReason.RETIRED_EXPIRED, result="no_rows") == 1
    assert metrics.get(
        "nora_note_retirements_total",
        because=NoteSupersedeReason.RETIRED_EXPIRED, result="applied") == 0


async def test_commit_never_calls_propose():
    """This adaptor writes no new rows — it is the tier's negative measurement.
    `propose(key, ())` would be «I looked and found nothing» about a question it
    was never asked, and the note sink has no decay pass for it to drive.
    `_FakeNoteStore.propose` raises, so a call would fail loudly rather than be
    argued about."""
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")])
    # A REAL retirement, not an empty list: with [] the loop runs zero times
    # and this test cannot fail no matter what commit does.
    assert await adaptor.commit(
        key=WORKSPACE, now_ms=1,
        proposals=_proposals([NoteRetirement(
            note_id=_note().id, because=NoteSupersedeReason.RETIRED_CLOSED,
            cited_message_id=7,
        )]),
    ) == 1


async def test_a_foreign_payload_is_refused_rather_than_retired():
    """`Proposal.payload` is `object` at the port on purpose; commit is the one
    place that knows better. A foreign payload is a wiring fault, not a note."""
    metrics = Metrics()
    adaptor, store = _revisitor((_note(),), [_row(7, "x")], metrics=metrics)
    written = await adaptor.commit(
        key=WORKSPACE, now_ms=1, proposals=_proposals(["not a retirement"]),
    )
    assert written == 0
    assert store.calls == []
    assert metrics.get(
        "nora_note_retirements_total", because="unknown", result="refused") == 1


async def test_a_boolean_citation_cannot_become_a_citation_of_message_one():
    """`isinstance(True, int)` is True in Python and `int(True) == 1`. A model
    answering `cited_message_id: true` would otherwise be read as citing
    message 1 — a REAL id, plausibly in the evidence, pointing at the wrong
    thing, and grounded enough to pass the membership check. The one bypass in
    this file that a reader would not think to look for."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(1, "booked it")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": True},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="citation_missing") == 1


async def test_a_numeric_string_citation_is_accepted():
    """The id is rendered INSIDE the prompt text, so a model re-typing it as
    "7" rather than 7 has still pointed at the right message. Refusing that
    would be a spelling test rather than a grounding check — and the grounding
    check is what actually protects the note."""
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked Yardbird")])
    await adaptor.inputs(key=WORKSPACE, batch=[])
    [out] = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": "7"},
    ])
    assert out.cited_message_id == 7


async def test_a_non_numeric_citation_is_refused():
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")])
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": "the last one"},
    ]) == ()


async def test_the_render_never_tells_the_model_why_the_evidence_is_missing():
    """An unreachable record and a quiet room read IDENTICALLY to the model, on
    purpose: its job is the same either way — decide nothing — and naming our
    infrastructure invites it to reason about that instead of about the note.
    The distinction is the operator's and lives on the counter."""
    quiet, _ = _revisitor((_note(),), [])
    broken, _ = _revisitor((_note(),), [_row(1, "hi")], fail=True)
    assert await quiet.inputs(key=WORKSPACE, batch=[]) == await broken.inputs(
        key=WORKSPACE, batch=[])


# ---------------------------------------------------------------------------
# The Dreamer's egress screen — the hop every test above skips
# ---------------------------------------------------------------------------


async def test_a_verdict_survives_the_dreamers_egress_screen():
    """**The test that was missing, and its absence made the whole tier
    inert.**

    `DreamerRuntime` runs `nora.agent.dream_screen.screen` over the model's raw
    envelope items BEFORE `accept` is called, and the screen drops the WHOLE
    item on any identifier reachable in it. An earlier revision asked the model
    to echo the note's `str(uuid4())` id back; `egress._UUID` matches any
    hyphenated UUID, so 100% of items were screened, forever — and the only
    trace was a generic `nora_dream_screened_total{reason="identifier"}`. The
    tier's own counters showed candidates climbing and verdicts flat, which
    reads as a model that stopped answering.

    Every other test in this file calls `accept` directly and therefore cannot
    see it. This one drives the item through the real screen first, in the
    order the runtime does.
    """
    from nora.agent.dream_screen import screen

    adaptor, _ = _revisitor((_note(),), [_row(7, "booked Yardbird for the 9th")])
    await adaptor.inputs(key=WORKSPACE, batch=[])
    items = [{"note": "1", "verdict": "closed", "cited_message_id": 7}]

    kept, rejected = screen(adaptor="note-revisit", items=items, metrics=Metrics())
    assert rejected == (), "the screen dropped a well-formed verdict"
    [out] = await adaptor.accept(key=WORKSPACE, items=kept)
    assert out.because == NoteSupersedeReason.RETIRED_CLOSED


async def test_the_prompt_carries_no_note_id():
    """The other half, stated as a property rather than as a screen outcome:
    the UUID must not reach the prompt at all. It buys nothing the ordinal does
    not, and every identifier rendered is one the model can echo back into an
    item the screen will then drop."""
    note = _note()
    adaptor, _ = _revisitor((note,), [_row(7, "booked it")])
    assert note.id not in await adaptor.inputs(key=WORKSPACE, batch=[])


# ---------------------------------------------------------------------------
# Evidence ORDER — a wrong order retires notes in the unrecoverable direction
# ---------------------------------------------------------------------------


async def test_the_evidence_is_rendered_oldest_first_as_its_header_claims():
    """`recent` returns the newest rows OLDEST-FIRST. An earlier revision
    reversed them, so the prompt read newest-first under a header saying
    "newest last" — and the failure that causes is not cosmetic:

        evidence, newest last:
        [3] so we are back to nothing
        [2] actually Yardbird cancelled on us
        [1] booked Yardbird for the 9th

    The model reads the last line as the latest word, answers `closed` citing
    message 1, and that citation passes every grounding check in this module —
    because the message IS in the evidence. A note about a cancelled dinner
    gets retired, and nothing afterwards can tell it apart from one that was
    genuinely settled.

    Asserted on POSITION, not on membership: every message is present either
    way, so a set assertion could not fail."""
    adaptor, _ = _revisitor((_note(),), [
        _row(1, "booked Yardbird for the 9th", ts=11),
        _row(2, "actually Yardbird cancelled on us", ts=12),
        _row(3, "so we are back to nothing", ts=13),
    ])
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert rendered.index("[1] ") < rendered.index("[2] ") < rendered.index("[3] ")


async def test_noras_own_words_are_not_evidence():
    """`recent` does NOT exclude `is_nora` rows — `since` does, in the query,
    and its docstring says why. Her saying «I'll book Yardbird» is an intention
    she stated, not the group settling anything, and closing a note on it is
    self-attestation deciding a retirement.

    Asserts the refusal too, not just the absence from the prompt: a model that
    cited her anyway must fail the grounding check rather than merely be
    unlikely to."""
    adaptor, _ = _revisitor((_note(),), [
        _row(7, "I'll book Yardbird for the 9th", is_nora=True),
        _row(8, "sounds good", ts=11),
    ])
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert "I'll book Yardbird" not in rendered
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ]) == ()


async def test_the_speaker_is_the_name_the_record_actually_carries():
    """**`recent` never populates `person_name`** — neither `SqlRecordStore`
    (it is absent from `audit.py` entirely) nor `FakeRecordStore`
    (`_transcript_row` does not map it), so it is always `None` on this read.

    So `_render_note`'s `person_name or sender_name or sender_id` has a DEAD
    first branch. It is kept, not deleted, for parity with
    `observer._build_prompt`, which carries the same dead branch for the same
    reason and would render the wrong name on the day `recent` starts
    populating it. But the TEST asserts what production can actually produce —
    an earlier version constructed a row with `person_name="Tyler"`, a shape no
    implementation of this port can return, and asserted on it. A test that
    pins an impossible shape pins nothing."""
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")])
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert "[7] Miles:" in rendered


# ---------------------------------------------------------------------------
# Two workspaces, one instance — the concurrency the loop actually runs
# ---------------------------------------------------------------------------


async def test_a_sibling_workspace_cannot_wipe_what_this_one_asked():
    """`dream_loop` dispatches one task per (adaptor, key) and its `running`
    lock is PER-UNIT: one key's inputs/accept pair is non-overlapping, and
    nothing makes two DIFFERENT keys' pairs non-overlapping. The live
    `policy.toml` has two workspaces, so this is a deployed configuration, not
    a hypothetical.

    With a bare `_asked` attribute, workspace B's `inputs` wipes workspace A's
    map, and A's grounded, correct verdict is dropped and counted
    `reason="unknown_note"` — the label whose own spec text says «one
    hallucinated identifier away». A WIRING GAP reported as a MODEL FAULT,
    inside the instrument built to refuse exactly that collapse.

    Interleaved in the order the loop can actually produce: A.inputs,
    B.inputs, A.accept, B.accept."""
    metrics = Metrics()
    note_a, note_b = _note("note-a"), _note("note-b")

    class _PerWorkspace(_FakeNoteStore):
        async def standing(self, *, workspace):
            return (note_a,) if workspace == "ws-a" else (note_b,)

    per_ws = _PerWorkspace((note_a, note_b))
    adaptor = NoteRevisitor(
        notes=per_ws, record=_record([_row(7, "booked it")]),
        metrics=metrics, clock=lambda: NOW_MS,
    )

    await adaptor.inputs(key="ws-a", batch=[])
    await adaptor.inputs(key="ws-b", batch=[])
    a_out = await adaptor.accept(key="ws-a", items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ])
    b_out = await adaptor.accept(key="ws-b", items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ])

    assert [r.note_id for r in a_out] == ["note-a"], "workspace A's verdict was lost"
    assert [r.note_id for r in b_out] == ["note-b"]
    assert metrics.get("nora_note_revisit_refused_total", reason="unknown_note") == 0


# ---------------------------------------------------------------------------
# The boundary the injected clock exists to make writable
# ---------------------------------------------------------------------------


async def test_a_note_is_not_due_on_the_last_instant_of_its_own_anchor_day():
    """END of the day, not the start: a note anchored to the 9th is not stale
    at 00:00 on the 9th, and it is still not stale at 23:59:59.999. One
    millisecond later it is. Writable only because the clock is injected —
    with a wall-clock fixture this boundary cannot be expressed at all."""
    # 2026-09-09T00:00:00Z + one day - 1ms
    last_instant = 1_788_912_000_000 + DAY_MS - 1
    note = (_note(anchor_date=20260909),)
    on_boundary, _ = _revisitor(note, [_row(1, "hi")], now_ms=last_instant)
    just_after, _ = _revisitor(note, [_row(1, "hi")], now_ms=last_instant + 1)

    assert await on_boundary.inputs(key=WORKSPACE, batch=[]) == ""
    assert "### note 1" in await just_after.inputs(key=WORKSPACE, batch=[])


# ---------------------------------------------------------------------------
# Two verdicts, one note
# ---------------------------------------------------------------------------


async def test_two_verdicts_for_one_note_produce_one_retirement():
    """Superseding twice would fire this module's own `result="no_rows"` alarm
    on the second call — a false positive on the counter built to make a real
    silent no-op visible."""
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")])
    await adaptor.inputs(key=WORKSPACE, batch=[])
    out = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ])
    assert len(out) == 1


async def test_expiry_is_refused_on_the_last_instant_of_the_anchor_day():
    """The OTHER boundary, and the one that actually permits a retirement.

    `_is_due` and `anchor_passed` are two separate comparisons against the same
    end-of-day instant, and only the first is exercised by the due-ness test
    above — a note sitting exactly on its anchor's last instant is not due, so
    it never reaches `accept` by that route. `revisit_at` is what makes the
    state reachable: the note IS due, and its moment has still not gone by.

    Mutating `now_ms > anchor_end` to `>=` left the whole file green before
    this existed, which is exactly the shape of an untested guard on a
    destructive path."""
    last_instant = 1_788_912_000_000 + DAY_MS - 1
    metrics = Metrics()
    adaptor, _ = _revisitor(
        (_note(anchor_date=20260909, revisit_at=1),),
        [_row(7, "nothing settled")], metrics=metrics, now_ms=last_instant,
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="anchor_not_passed") == 1


# ---------------------------------------------------------------------------
# The objective's own schema — the ungated half of the F1 fix
# ---------------------------------------------------------------------------


def _schema_line() -> str:
    """The `{"revisits": [...]}` line out of the SHIPPED objective body, read
    through the real `DreamerInstructions` — not a copy of it here."""
    body = shipped_objectives().objective_body("note-revisit")
    [line] = [ln for ln in body.splitlines() if ln.strip().startswith('{"revisits"')]
    return line


def test_the_objective_asks_for_the_handle_and_never_the_note_id():
    """**The one surface where the critical defect can come back silently.**

    `nora/dreamer` is bind-mounted `:ro` from the repo and the instruction
    store reloads on mtime, so an operator edit to this markdown reaches the
    running container with no rebuild. Reinstating `note_id` puts a
    `str(uuid4())` back in every envelope item, `dream_screen` drops all of
    them before `accept`, and the tier returns to the 100%-inert state it
    shipped in — with the five counters looking exactly as they did before the
    fix.

    This is the same shape as the language tier's trap: a load-bearing property
    living in operator-owned markdown with nothing enforcing it. The difference
    is that this objective is natively authored, so the operator CAN still
    reword everything around the schema line; what he cannot do is silently
    change the contract the adaptor parses.
    """
    line = _schema_line()
    assert '"note"' in line
    assert "note_id" not in line, (
        "the objective asks for the note's id again — every item will be "
        "dropped by dream_screen before accept, and the tier goes inert"
    )


def test_the_objective_asks_for_no_free_text_field():
    """The other half of the same fix. Every model-authored string in an item
    is a chance for the screen to drop a correct verdict — a `why` quoting a
    phone number out of the evidence silently kills the retirement it was
    explaining. The keys are pinned as a SET so a new one cannot be added
    without this failing."""
    import json

    [item] = json.loads(_schema_line().replace(
        '"<the number from the entry heading>"', '"1"',
    ).replace(
        '"closed|expired|unresolved"', '"closed"',
    ).replace(
        "<int, required when verdict is closed, otherwise null>", "7",
    ))["revisits"]
    assert set(item) == {"note", "verdict", "cited_message_id"}


def test_every_key_the_objective_promises_is_one_the_adaptor_reads():
    """The parity that makes the two tests above worth having: the schema line
    is only load-bearing if the adaptor actually reads exactly those keys. A
    key renamed on one side and not the other is a verdict silently refused as
    `unmeasured`, which reads as a model that stopped answering.

    Asserted against `ITEM_KEYS`, which `_accept_one` itself reads through, so
    renaming a key in the adaptor turns this red. An earlier version listed the
    three names as literals here and therefore only ever compared the objective
    to a copy of itself — it stayed green through a rename of the very thing it
    claimed to pin."""
    line = _schema_line()
    for key in ITEM_KEYS:
        assert f'"{key}"' in line, f"the adaptor reads {key!r}; the objective does not promise it"


# ---------------------------------------------------------------------------
# Coverage — the evidence must span the moment, not merely exist
# ---------------------------------------------------------------------------


async def test_a_room_that_died_before_the_anchor_cannot_expire_the_note():
    """**The wrong retirement two internal review passes missed.**

    Before `_covers`, `expired` needed only that the room had said SOMETHING —
    anything, at any point after the note was written. Reproduced from the live
    shape: a note written on the 1st, anchored to the 9th of the next month, a
    room that talked for a fortnight and then fell silent. Every message in the
    window predates the anchor by weeks. The model answers `expired` correctly
    on what it was shown, and a note is retired with ZERO evidence about the
    moment it is about.

    A room that fell silent cannot testify about a moment it was not present
    for. Held, and counted `anchor_not_covered`."""
    metrics = Metrics()
    anchor_start = 1_767_225_600_000  # PASSED_ANCHOR at 00:00Z
    created = anchor_start - 40 * DAY_MS
    chatter = [
        _row(i, f"chat {i}", ts=created + i * DAY_MS) for i in range(1, 15)
    ]
    adaptor, _ = _revisitor(
        (_note(created_at=created),), chatter, metrics=metrics,
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="anchor_not_covered") == 1


async def test_a_window_truncated_across_the_anchor_cannot_expire_the_note():
    """The second variant, and the one that turns an ANSWERED note into an
    expired one. The room is busier than the window, so the message that
    settled the question sits outside the newest `limit` rows and the window
    shows only later, unrelated traffic.

    Detected from TRUNCATION rather than from content — the pass cannot see
    what it did not fetch, so «the window is full and starts after the moment»
    is the only signal available, and it is enough. `config.toml`'s evidence
    cap is therefore not merely a cost knob: raising or lowering it moves this
    boundary."""
    metrics = Metrics()
    anchor_start = 1_767_225_600_000
    # Every row lands AFTER the anchor, and there are more of them than the cap
    # — so the window is full and its oldest row postdates the moment.
    rows = [
        _row(i, f"later chat {i}", ts=anchor_start + DAY_MS * 2 + i * 1000)
        for i in range(1, 9)
    ]
    adaptor, _ = _revisitor(
        (_note(created_at=anchor_start - 30 * DAY_MS),), rows,
        metrics=metrics, evidence_messages_per_note=4,
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="anchor_not_covered") == 1


async def test_an_untruncated_window_reaching_past_the_anchor_can_expire():
    """The positive case, and it has to be here: a coverage check that never
    passes is a mute button, and every test above would still pass with
    `_covers` hard-wired to `False`."""
    anchor_start = 1_767_225_600_000
    adaptor, _ = _revisitor(
        (_note(created_at=anchor_start - 10 * DAY_MS),),
        [
            _row(1, "before the day", ts=anchor_start - DAY_MS),
            _row(2, "on the day, nothing settled", ts=anchor_start + 3600_000),
            _row(3, "after the day", ts=anchor_start + 2 * DAY_MS),
        ],
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    [out] = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ])
    assert out.because == NoteSupersedeReason.RETIRED_EXPIRED


async def test_coverage_is_not_required_to_close_a_note():
    """Only `expired` reasons from an absence, so only `expired` needs the
    window to span the moment. A `closed` verdict points at a specific message
    that settled the question — that citation is its own ground, and it holds
    wherever in the window the message sits."""
    adaptor, _ = _revisitor(
        (_note(),), [_row(7, "booked Yardbird", ts=1_767_225_600_000 - DAY_MS)],
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    [out] = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ])
    assert out.because == NoteSupersedeReason.RETIRED_CLOSED


# ---------------------------------------------------------------------------
# A model that contradicts itself
# ---------------------------------------------------------------------------


async def test_contradictory_verdicts_for_one_note_retire_nothing():
    """A model saying both `closed` and `expired` about one note is the
    DEFINITION of «cannot tell which», and both answers throw the note away.

    An earlier revision de-duplicated first-come, so the list ORDER decided an
    irreversible action: `[closed, expired]` retired it as closed and the same
    two items reversed retired it as expired. Asserted in BOTH orders, because
    a single-order test cannot tell a refusal from a lucky first element."""
    for items in (
        [{"note": "1", "verdict": "closed", "cited_message_id": 7},
         {"note": "1", "verdict": "expired", "cited_message_id": None}],
        [{"note": "1", "verdict": "expired", "cited_message_id": None},
         {"note": "1", "verdict": "closed", "cited_message_id": 7}],
    ):
        metrics = Metrics()
        adaptor, _ = _revisitor(
            (_note(),), [_row(7, "booked it", ts=AFTER_ANCHOR)], metrics=metrics,
        )
        await adaptor.inputs(key=WORKSPACE, batch=[])
        assert await adaptor.accept(key=WORKSPACE, items=items) == ()
        assert metrics.get(
            "nora_note_revisit_refused_total", reason="contradictory") == 1


async def test_two_identical_verdicts_count_one_accepted_verdict():
    """The counter half of the same fix. `_accept_one` runs per ITEM, so
    incrementing there made two items for one note assert TWO accepted verdicts
    and then produce ONE retirement — the instrument disagreeing with the
    action it was measuring. A verdict is accepted when it survives resolution,
    not when it parses."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "booked it")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    out = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
        {"note": "1", "verdict": "closed", "cited_message_id": 7},
    ])
    assert len(out) == 1
    assert metrics.get("nora_note_revisit_verdicts_total", verdict="closed") == 1


# ---------------------------------------------------------------------------
# Nora exhausting her own evidence window
# ---------------------------------------------------------------------------


async def test_a_window_of_only_noras_messages_is_not_a_quiet_room():
    """The cap is applied by `recent` BEFORE the `is_nora` filter, so if the
    newest rows are all hers the evidence is empty even though the group has
    spoken since the note. Counting that as `absent` — whose spec text says
    «it has not spoken since the note was written» — and telling the model in
    words that nothing has been said are both FALSE.

    No retirement follows either way, so the direction is safe. The honesty is
    the point: a window-exhaustion state collapsed into «honest quiet room» is
    `unavailable` wearing `nothing_found`, in the counters built to keep them
    apart."""
    metrics = Metrics()
    adaptor, _ = _revisitor(
        (_note(),),
        [_row(i, f"nora says {i}", ts=100 + i, is_nora=True) for i in range(1, 4)],
        metrics=metrics,
    )
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert metrics.get(
        "nora_note_revisit_candidates_total", evidence="self_only") == 1
    assert metrics.get(
        "nora_note_revisit_candidates_total", evidence="absent") == 0
    assert "nothing has been said" not in rendered


async def test_retiring_an_already_retired_note_moves_no_rows():
    """`SqlNoteStore.supersede` filters `retired_at IS NULL`, so a second
    supersession of one note affects no rows. This is the exact branch
    `result="no_rows"` exists to expose — and the double claimed it in a
    docstring while returning 1, so any test of that counter would have passed
    against the fake while production did the opposite.

    Two commits, not two items in one accept: de-duplication already collapses
    the second item, so the only way to reach a genuine second supersession is
    a later tick acting on a note the previous one retired."""
    metrics = Metrics()
    adaptor, store = _revisitor((_note(),), [_row(7, "booked it")], metrics=metrics)
    retirement = _proposals([NoteRetirement(
        note_id=_note().id, because=NoteSupersedeReason.RETIRED_CLOSED,
        cited_message_id=7,
    )])

    assert await adaptor.commit(key=WORKSPACE, proposals=retirement, now_ms=1) == 1
    assert await adaptor.commit(key=WORKSPACE, proposals=retirement, now_ms=2) == 0
    assert len(store.calls) == 2, "the second supersession was really attempted"
    assert metrics.get(
        "nora_note_retirements_total",
        because=NoteSupersedeReason.RETIRED_CLOSED, result="applied") == 1
    assert metrics.get(
        "nora_note_retirements_total",
        because=NoteSupersedeReason.RETIRED_CLOSED, result="no_rows") == 1


async def test_an_answer_before_the_window_cannot_expire_the_note():
    """**The reference point, and the reason the first coverage check was still
    wrong at the SHIPPED default cap.**

    A closing condition can be met at any time after the note exists — that is
    what a closing condition IS. A note anchored to the 9th of next month is
    answered by «booked Yardbird for the 9th» said three weeks earlier, not on
    the day itself. The first `_covers` measured truncation against the
    ANCHOR's first instant, so a window starting well before the anchor passed
    the check while the answer sat outside it, and an ANSWERED note was retired
    as having gone unanswered.

    Built at the real default (`revisit_evidence_messages = 40`): the answer two
    days after the note, forty later messages straddling the anchor day. The
    window's oldest row predates the anchor — so the OLD check passed — and the
    answer is invisible."""
    metrics = Metrics()
    anchor_start = 1_767_225_600_000
    created = anchor_start - 39 * DAY_MS
    rows = [_row(1, "booked Yardbird for the 9th", ts=created + 2 * DAY_MS)]
    rows += [
        _row(i, f"chat {i}", ts=created + 30 * DAY_MS + (i - 2) * (DAY_MS // 2))
        for i in range(2, 42)
    ]
    adaptor, _ = _revisitor(
        (_note(created_at=created),), rows, metrics=metrics,
        now_ms=anchor_start + 12 * DAY_MS,
    )
    rendered = await adaptor.inputs(key=WORKSPACE, batch=[])
    assert "booked Yardbird" not in rendered, "the fixture must hide the answer"
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ]) == ()
    assert metrics.get(
        "nora_note_revisit_refused_total", reason="anchor_not_covered") == 1


async def test_an_untruncated_window_covers_regardless_of_where_it_starts():
    """The other half of the same reference point: when the window is NOT
    truncated there is no older traffic to hide, so where its oldest row falls
    relative to the note is irrelevant. Without this the fix would read as «the
    window must always predate the note», which would make coverage unreachable
    for any note written before its room's first message."""
    anchor_start = 1_767_225_600_000
    adaptor, _ = _revisitor(
        (_note(created_at=anchor_start - 30 * DAY_MS),),
        [
            _row(1, "chatter", ts=anchor_start - DAY_MS),
            _row(2, "nothing settled", ts=anchor_start + 2 * DAY_MS),
        ],
        now_ms=anchor_start + 5 * DAY_MS,
    )
    await adaptor.inputs(key=WORKSPACE, batch=[])
    [out] = await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "expired", "cited_message_id": None},
    ])
    assert out.because == NoteSupersedeReason.RETIRED_EXPIRED


async def test_unresolved_paired_with_a_retirement_is_a_contradiction():
    """`unresolved` used to return `None` from `_accept_one`, so it never
    entered the grouping and was invisible to the contradiction check: a model
    saying «I cannot tell» AND «it is closed» about one note retired it, in
    either order. «I cannot tell» is precisely the state this design says must
    never retire.

    Both orders and both retiring verdicts, because a single case cannot tell a
    refusal from a lucky first element."""
    anchor_start = 1_767_225_600_000
    for other, cited in (("closed", 7), ("expired", None)):
        for order in ((("unresolved", None), (other, cited)),
                      ((other, cited), ("unresolved", None))):
            metrics = Metrics()
            adaptor, _ = _revisitor(
                (_note(),),
                [_row(7, "booked it", ts=anchor_start + 2 * DAY_MS)],
                metrics=metrics, now_ms=anchor_start + 5 * DAY_MS,
            )
            await adaptor.inputs(key=WORKSPACE, batch=[])
            items = [
                {"note": "1", "verdict": v, "cited_message_id": c}
                for v, c in order
            ]
            assert await adaptor.accept(key=WORKSPACE, items=items) == (), order
            assert metrics.get(
                "nora_note_revisit_refused_total", reason="contradictory") == 1


async def test_unresolved_is_counted_once_per_note_not_once_per_item():
    """The counter half. `verdicts_total{unresolved}` still fired inside
    `_accept_one`, which runs per ITEM — the one branch that never reaches
    resolution, in the counter whose own fix says a verdict is accepted when it
    survives resolution rather than when it parses."""
    metrics = Metrics()
    adaptor, _ = _revisitor((_note(),), [_row(7, "hmm")], metrics=metrics)
    await adaptor.inputs(key=WORKSPACE, batch=[])
    assert await adaptor.accept(key=WORKSPACE, items=[
        {"note": "1", "verdict": "unresolved", "cited_message_id": None},
        {"note": "1", "verdict": "unresolved", "cited_message_id": None},
    ]) == ()
    assert metrics.get("nora_note_revisit_verdicts_total", verdict="unresolved") == 1


def test_every_evidence_state_is_described_in_the_metric_spec():
    """Label parity, and it exists because the drift already happened:
    `self_only` was added to the code and not to
    `nora_note_revisit_candidates_total`'s help text, so an operator reading
    the spec got three values while the endpoint emitted four — and nothing
    caught it, because a help string is prose that no test reads.

    Bound to `EVIDENCE_STATES`, which `_candidate` assigns from, so adding a
    fifth state without describing it fails here."""
    from nora.language.note_revisitor import EVIDENCE_STATES
    from nora.obs.metric_specs import _COUNTERS

    [spec] = [
        c for c in _COUNTERS
        if c.name == "nora_note_revisit_candidates_total"
    ]
    for state in EVIDENCE_STATES:
        assert f'evidence="{state}"' in spec.help_text, state
