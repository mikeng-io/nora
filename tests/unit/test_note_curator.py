"""`NoteCuratorAdaptor` — the note tier's housekeeping pass.

The assertions here are mostly about what the curator REFUSES, because that is
where this design lives: merge eligibility is decided in SQL and the model is
asked only what a body says, so the interesting failures are all a model
reaching past the question it was given.
"""

from __future__ import annotations

from nora.domain.notes import NoteSupersedeReason
from nora.domain.ports import Proposal
from nora.language.note_curator import NoteCuratorAdaptor
from nora.obs.metrics import Metrics
from nora.testing.note_store import InMemoryNoteStore

WS = "friends"
NOW = 1755000000000
TODAY = 20261001


async def _seed(store: InMemoryNoteStore, *, title: str, anchor: int | None,
                body: str = "as it stands"):
    return await store.create(
        workspace=WS, title=title, description="one line",
        uncertainty="undecided", closing_condition="somebody decides",
        established_in="conv-1", body=body, established_by="tag:miles",
        now_ms=NOW, anchor_date=anchor,
    )


def _curator(store, metrics=None, today=TODAY):
    return NoteCuratorAdaptor(
        notes=store, today=lambda: today, max_groups_per_tick=3,
        max_expired_per_tick=10, metrics=metrics or Metrics(),
    )


async def test_inputs_is_blank_when_the_notebook_is_tidy():
    """The runtime's fire condition is "did `inputs` render anything", and a
    curator has nothing to do on the overwhelming majority of ticks. Rendering
    a polite empty form instead of nothing buys a model call every 300s per
    workspace forever — observed live on the fact adaptor 2026-08-21."""
    store = InMemoryNoteStore()
    await _seed(store, title="Wedding", anchor=20271231)

    assert await _curator(store).inputs(key=WS, batch=()) == ""


async def test_inputs_renders_a_collided_group_and_an_expired_note():
    store = InMemoryNoteStore()
    await _seed(store, title="Grant wedding", anchor=20261011)
    await _seed(store, title="Grant's wedding", anchor=20261011)
    await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                body="Nobody booked.")

    rendered = await _curator(store).inputs(key=WS, batch=())

    assert "<merge_group" in rendered
    assert "Grant wedding" in rendered and "Grant's wedding" in rendered
    assert "Nobody booked." in rendered, "an expired note's BODY is the question"
    assert "Dinner, 9 Sept" in rendered


async def test_a_verdict_about_a_note_this_run_never_showed_is_dropped():
    """A model can invent a uuid, and a retirement is a permanent removal from
    an index nobody can see the inside of."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    curator = _curator(store, metrics)
    await curator.inputs(key=WS, batch=())

    kept = await curator.accept(key=WS, items=(
        {"note_id": "note-invented", "verdict": "moot", "why": "made up"},
    ))

    assert kept == ()
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="unknown_note") == 1


async def test_a_verdict_outside_the_three_is_dropped_never_mapped():
    """"expired" is not a synonym for "moot". Guessing here is guessing about
    whether to delete somebody's note."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    curator = _curator(store, metrics)
    await curator.inputs(key=WS, batch=())

    kept = await curator.accept(key=WS, items=(
        {"note_id": note.id, "verdict": "expired", "why": "the date passed"},
    ))

    assert kept == ()
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="malformed") == 1


async def test_open_is_the_common_answer_and_writes_nothing():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    curator = _curator(store)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": note.id, "verdict": "open", "why": "still nobody booked"},
    ))

    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 1,
    )

    assert written == 0
    assert len(await store.standing(workspace=WS)) == 1


async def test_a_closed_note_is_retired_with_the_closed_reason():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    curator = _curator(store, metrics)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": note.id, "verdict": "closed", "why": "Ada booked Sai Kung"},
    ))

    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 1,
    )

    assert written == 1
    assert await store.standing(workspace=WS) == ()
    gone = await store.read(workspace=WS, note_id=note.id)
    assert gone is not None
    assert gone[0].retire_reason == NoteSupersedeReason.RETIRED_CLOSED
    assert gone[0].replaced_by is None, "a closed note is replaced by nothing"
    assert metrics.get("nora_note_curator_total", action="closed") == 1


async def test_bodies_read_is_published_as_a_level_per_workspace():
    store = InMemoryNoteStore()
    await _seed(store, title="Grant wedding", anchor=20261011)
    await _seed(store, title="Grant's wedding", anchor=20261011)
    metrics = Metrics()

    await _curator(store, metrics).inputs(key=WS, batch=())

    assert metrics.get("nora_note_curator_bodies_read", workspace=WS) == 2


async def test_nothing_is_read_and_nothing_published_when_there_is_no_work():
    """Absent, not zero — the gauge says what the LAST tick spent, and a tick
    that did no work spent nothing to say."""
    store = InMemoryNoteStore()
    metrics = Metrics()

    await _curator(store, metrics).inputs(key=WS, batch=())

    assert ("nora_note_curator_bodies_read", (("workspace", WS),)) \
        not in metrics.snapshot()


# --- the merge half, which was UNREACHABLE until 2026-08-28 ---------------
#
# `_MergedBody` was defined, `commit` checked for it, and nothing constructed
# it. Every test below fails against that version — which is the point: the
# suite was green over a merge verb that could not run, and two declared
# metrics with no reachable call site.


async def _merge_round(store, metrics, items, today=TODAY):
    curator = _curator(store, metrics, today)
    rendered = await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=items)
    written = await curator.commit(
        key=WS,
        proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ),
        now_ms=NOW + 5,
    )
    return rendered, written


async def test_a_merge_actually_merges():
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011,
                    body="Ada can't do Fridays; Sai Kung ruled out.")
    await _seed(store, title="Grant's wedding", anchor=20261011,
                body="Ben found a place in Sheung Wan.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, (
        {"note_id": a.id, "merged_body":
            "Ada can't do Fridays; Sai Kung ruled out. Ben found Sheung Wan."},
    ))

    assert written == 1
    standing = await store.standing(workspace=WS)
    assert [n.id for n in standing] == [a.id], "one note survives"
    head = await store.read(workspace=WS, note_id=a.id)
    assert head is not None
    assert "Sai Kung ruled out" in head[1].body
    assert "Sheung Wan" in head[1].body
    assert metrics.get("nora_note_curator_total", action="merge") == 1


async def test_the_model_is_shown_the_bodies_it_must_preserve():
    """The merge asks for one body that keeps everything every note knew. Until
    2026-08-28 the group branch rendered titles and closing conditions and NOT
    bodies, so the model was asked to preserve content it had never seen — and
    the first live merge would have overwritten the survivor's accumulated
    detail with text synthesised from two titles."""
    store = InMemoryNoteStore()
    await _seed(store, title="Grant wedding", anchor=20261011,
                body="Ada can't do Fridays.")
    await _seed(store, title="Grant's wedding", anchor=20261011,
                body="Ben found Sheung Wan.")

    rendered = await _curator(store).inputs(key=WS, batch=())

    assert "Ada can't do Fridays." in rendered
    assert "Ben found Sheung Wan." in rendered


async def test_a_merged_body_for_a_group_this_run_did_not_select_is_refused():
    """Merge eligibility is SQL's decision. A model proposing its own grouping
    is the one thing this design exists to prevent, and `not_shown_in_group`
    was documented and unimplemented until 2026-08-28."""
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011)
    await _seed(store, title="Grant's wedding", anchor=20261011)
    # A note that is real and shown, but is not any group's survivor.
    lone = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, (
        {"note_id": lone.id, "merged_body": "a merge nobody asked for"},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="not_shown_in_group") == 1
    assert len(await store.standing(workspace=WS)) == 3
    assert a.id in {n.id for n in await store.standing(workspace=WS)}


async def test_the_survivor_of_a_merge_is_never_retired_in_the_same_tick():
    """A model returning a merged body for a group AND `closed` for its
    survivor used to merge into it and then retire it — the whole occasion
    leaving the index, with the duplicate pointing at a retired row.

    The store's own guard cannot see this: the curator retires through a
    SEPARATE `supersede` the store has no way to associate with the merge."""
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20260101)
    b = await _seed(store, title="Grant's wedding", anchor=20260101)
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, (
        {"note_id": a.id, "merged_body": "everything both knew"},
        {"note_id": a.id, "verdict": "closed", "why": "they got married"},
        {"note_id": b.id, "verdict": "closed", "why": "duplicate"},
    ))

    standing = await store.standing(workspace=WS)
    assert [n.id for n in standing] == [a.id], (
        "the survivor stays standing; the occasion does not leave the index"
    )
    assert written == 1


async def test_a_merge_that_never_ran_does_not_suppress_its_retirements():
    """`merged_away` was computed from the SELECTED groups rather than the
    merged ones, so a group whose merge never ran had its retirements skipped
    too — a tick could end with the survivor retired and the duplicate still
    standing, inverted, silently."""
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20260101)
    b = await _seed(store, title="Grant's wedding", anchor=20260101)
    metrics = Metrics()

    # Verdicts only — no merged_body, so no merge runs for this group.
    _, written = await _merge_round(store, metrics, (
        {"note_id": a.id, "verdict": "moot", "why": "the day passed"},
        {"note_id": b.id, "verdict": "moot", "why": "the day passed"},
    ))

    assert written == 2, "both retire; neither is suppressed by an absent merge"
    assert await store.standing(workspace=WS) == ()
