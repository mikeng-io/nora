"""`NoteCuratorAdaptor` — the note tier's housekeeping pass.

The assertions here are mostly about what the curator REFUSES, because that is
where this design lives: merge eligibility is decided in SQL and the model is
asked only what a body says, so the interesting failures are all a model
reaching past the question it was given.
"""

from __future__ import annotations

import pytest

from nora.domain.notes import (
    BODY_MAX_CHARS,
    NoteSupersedeReason,
    StaleNoteHeadError,
)
from nora.domain.ports import Proposal
from nora.language.note_curator import (
    CURATOR_DROPS,
    CURATOR_GROUP_REFUSALS,
    NoteCuratorAdaptor,
)
from nora.obs.metrics import Metrics
from nora.testing.note_store import InMemoryNoteStore

WS = "friends"
NOW = 1755000000000
TODAY = 20261001


async def _seed(store: InMemoryNoteStore, *, title: str, anchor: int | None,
                body: str = "as it stands",
                entities: tuple[str, ...] = ("grant",),
                resolved: tuple[str, ...] | None = None):
    """`entities` is what the WRITER typed; `resolved` is what `note_entity`
    holds, and only the second is the merge key's second half.

    They are separate parameters because they are separate things, and
    conflating them is finding 3: 「Grant 婚禮」/「Gabe's wedding」/「婚禮」 are
    three strings and one occasion, while two unrelated notes both saying
    「wedding」 are one string and two occasions. `resolved` defaults to
    mirroring `entities` so an ordinary test gets an eligible group; a test
    about the refusal passes `resolved=()`.

    The DOUBLE does not do this mirroring — `InMemoryNoteStore.entities` is
    empty like the real table, and a double that resolved the model's own
    strings would make merge eligible in the suite and inert in production.
    """
    note = await store.create(
        workspace=WS, title=title, description="one line",
        uncertainty="undecided", closing_condition="somebody decides",
        established_in="conv-1", body=body, established_by="tag:miles",
        now_ms=NOW, anchor_date=anchor, anchor_entity_ids=entities,
    )
    ids = entities if resolved is None else resolved
    if ids:
        store.entities[note.id] = tuple(f"e-{name}" for name in ids)
    return note


def _h(curator, note) -> str:
    """The HANDLE this curator run rendered for `note`.

    Tests must speak the MODEL's language, not the store's. The model is shown
    ordinals and never an id, so a test that passes an id is exercising a path
    production cannot take — which is exactly how every test in this file
    passed while the Dreamer's screen rejected every real item.

    Per-run by construction, which is also what makes a handle from a previous
    tick unaskable.
    """
    shown = curator._shown[WS]
    return next(h for h, nid in shown.by_handle.items() if nid == note.id)


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
        {"note_id": "999", "verdict": "moot", "why": "made up"},
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
        {"note_id": _h(curator, note), "verdict": "expired", "why": "the date passed"},
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
        {"note_id": _h(curator, note), "verdict": "open", "why": "still nobody booked"},
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
        {"note_id": _h(curator, note), "verdict": "closed", "why": "Ada booked Sai Kung"},
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


async def test_bodies_read_is_published_as_zero_on_an_idle_tick():
    """**Zero, and PUBLISHED.** The gauge was set after `inputs`' early return,
    so an idle tick never wrote it and the series held the last busy tick's
    value — on a pass whose own docstring says idle is the overwhelming
    majority of ticks. A level that only ever moves up is a high-water mark
    wearing a level's help text, and an operator reading «2 bodies» a fortnight
    after the last group would be reading a fossil.

    Absence would be the honest answer to «the tier is off» — this call proves
    the tier ran, so the honest answer is 0. Asserted through `snapshot()`
    membership first, because `Metrics.get` answers 0.0 for a series that was
    never published and would pass against the defect."""
    store = InMemoryNoteStore()
    metrics = Metrics()

    await _curator(store, metrics).inputs(key=WS, batch=())

    assert ("nora_note_curator_bodies_read", (("workspace", WS),)) \
        in metrics.snapshot(), "an idle tick still says what it spent"
    assert metrics.get("nora_note_curator_bodies_read", workspace=WS) == 0


async def test_bodies_read_counts_what_the_store_fetched_not_what_was_shown():
    """The gauge reads against `max_groups_per_tick + max_expired_per_tick`, so
    it has to count the reads that spend those caps.

    It reported `len(expired)` AFTER group members were subtracted out — but
    `anchored_before(limit=max_expired)` had already fetched every one of them.
    So the number could never reach the expired cap, and the help text's stated
    reading («sitting at the cap means the pass is reporting nothing to do
    about notes it never reached») was unreachable exactly when the cap binds,
    which is the only condition it exists for.

    Here the cap is 2 and it BINDS: three expired notes exist, two are read,
    and the gauge must say 2."""
    store = InMemoryNoteStore()
    for day in (20260901, 20260902, 20260903):
        await _seed(store, title=f"Dinner {day}", anchor=day,
                    entities=(f"e{day}",))
    metrics = Metrics()
    curator = NoteCuratorAdaptor(
        notes=store, today=lambda: TODAY, max_groups_per_tick=3,
        max_expired_per_tick=2, metrics=metrics,
    )

    await curator.inputs(key=WS, batch=())

    assert metrics.get("nora_note_curator_bodies_read", workspace=WS) == 2, (
        "the expired cap bound at 2 and the gauge is what says so"
    )


# --- the merge half, which was UNREACHABLE until 2026-08-28 ---------------
#
# `_MergedBody` was defined, `commit` checked for it, and nothing constructed
# it. Every test below fails against that version — which is the point: the
# suite was green over a merge verb that could not run, and two declared
# metrics with no reachable call site.


async def _merge_round(store, metrics, items, today=TODAY):
    """`items` may be a callable taking the curator, so a test can name handles
    that only exist after `inputs` has run."""
    curator = _curator(store, metrics, today)
    rendered = await curator.inputs(key=WS, batch=())
    kept = await curator.accept(
        key=WS, items=items(curator) if callable(items) else items,
    )
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

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body":
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
    lone = await _seed(store, title="Dinner", anchor=20260909,
                       entities=("ada",))
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, lone), "merged_body": "a merge nobody asked for"},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="not_shown_in_group") == 1
    assert len(await store.standing(workspace=WS)) == 3
    assert a.id in {n.id for n in await store.standing(workspace=WS)}


class _MergeCasLost(InMemoryNoteStore):
    """A store whose `merge` always loses the compare-and-swap.

    The tag path revising the survivor while the curator was thinking is the
    case the CAS exists for and it is NOT an error — so this is the shape of an
    ordinary tick, not of a fault.
    """

    async def merge(self, **kw):  # type: ignore[override]
        raise StaleNoteHeadError("the survivor moved", observed_head=None)


@pytest.mark.parametrize(
    "body_for,drop_reason,action,store_factory",
    [
        # An over-cap body. The objective asks for "keep every concrete thing,
        # do not summarise" over two bodies of up to the cap each, so this is
        # the INSTRUCTED outcome rather than an edge.
        (lambda: "x" * (BODY_MAX_CHARS + 1), "body_too_long", None,
         InMemoryNoteStore),
        # A body shorter than the longest input — a summary.
        (lambda: "short", "shorter_than_inputs", None, InMemoryNoteStore),
        # A well-formed body whose merge loses the CAS at the store.
        (lambda: "KEEPS BOTH: Fridays and Sheung Wan, at length",
         None, "merge_refused", _MergeCasLost),
    ],
    ids=["over_cap_body", "shorter_than_inputs", "lost_cas"],
)
async def test_a_group_the_model_asked_to_merge_is_never_retired_piecemeal(
    body_for, drop_reason, action, store_factory,
):
    """**Content loss on the NORMAL path, and the reason this branch could not
    merge.**

    `untouchable` was built from the groups that ACTUALLY MERGED, so every
    other outcome left the group unprotected and `commit` retired each member
    on its own verdict with `replaced_by=None`. Retirement is terminal in
    practice — `note_read` resolves against `standing()` and `note_search`
    filters retired rows — so both bodies became reachable only by direct
    database query, with nothing pointing at either.

    And it is the ordinary answer, not a contrived one: the objective tells the
    model to return one entry per note it was shown, and `inputs` calls a
    colliding group whose occasion has passed "the common case rather than an
    edge". `moot` on both members beside a merged body is what a compliant
    model returns.

    Parameterised over the three ways a merge fails to land, because the guard
    has to key on the model's REQUEST rather than on any of the outcomes.
    """
    store = store_factory()
    a = await _seed(store, title="Grant wedding", anchor=20260101,
                    body="Ada can't do Fridays; Sai Kung ruled out.")
    b = await _seed(store, title="Grant's wedding", anchor=20260101,
                    body="Ben found a place in Sheung Wan.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": body_for()},
        {"note_id": _h(c, a), "verdict": "moot", "why": "the day passed"},
        {"note_id": _h(c, b), "verdict": "moot", "why": "the day passed"},
    ))

    standing = {n.id for n in await store.standing(workspace=WS)}
    assert standing == {a.id, b.id}, (
        "the merge did not land, so BOTH notes stand — retiring them one at a "
        "time throws away the content the merge existed to keep"
    )
    assert written == 0
    # The positive control: the refusal was REPORTED, not silently skipped.
    if drop_reason is not None:
        assert metrics.get(
            "nora_note_curator_dropped_total", reason=drop_reason) == 1
    if action is not None:
        assert metrics.get("nora_note_curator_total", action=action) == 1


async def test_a_merged_body_naming_the_wrong_member_still_holds_its_group():
    """`not_shown_in_group` fires when the handle is not the group's SURVIVOR —
    and a non-survivor member of a real group is exactly that. The model got
    the handle wrong; it did not get the occasion wrong, and retiring the pair
    individually is still the unrecoverable answer."""
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20260101,
                    body="Ada can't do Fridays.")
    b = await _seed(store, title="Grant's wedding", anchor=20260101,
                    body="Ben found Sheung Wan.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, b), "merged_body": "everything both knew, at length"},
        {"note_id": _h(c, a), "verdict": "moot", "why": "the day passed"},
        {"note_id": _h(c, b), "verdict": "moot", "why": "the day passed"},
    ))

    assert written == 0
    assert {n.id for n in await store.standing(workspace=WS)} == {a.id, b.id}
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="not_shown_in_group") == 1


async def test_a_group_nobody_asked_to_merge_is_still_retired():
    """**The positive control for the whole guard, and it is not optional.**
    Protecting every SELECTED group would end a tick with nothing retired, and
    every assertion above would still pass. This is the case the narrow version
    of the guard was written for, kept because it is real: no merged body came
    back, so there is nothing to preserve and the verdicts stand on their own.
    """
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20260101)
    b = await _seed(store, title="Grant's wedding", anchor=20260101)
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "verdict": "moot", "why": "the day passed"},
        {"note_id": _h(c, b), "verdict": "moot", "why": "the day passed"},
    ))

    assert written == 2
    assert await store.standing(workspace=WS) == ()


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

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": "everything both knew"},
        {"note_id": _h(c, a), "verdict": "closed", "why": "they got married"},
        {"note_id": _h(c, b), "verdict": "closed", "why": "duplicate"},
    ))

    standing = await store.standing(workspace=WS)
    assert [n.id for n in standing] == [a.id], (
        "the survivor stays standing; the occasion does not leave the index"
    )
    assert written == 1
    assert metrics.get("nora_note_curator_total", action="merge") == 1, (
        "positive control: the merge really ran"
    )
    # **THE GUARD, not the race check.** This assertion is what makes the test
    # able to fail: with `untouchable` deleted the survivor still stands,
    # because the merge moved its head and the retirement's own head check
    # refuses it — so the note is safe and `retire_stale` fires. That reading
    # is a lie. `retire_stale` means the TAG PATH moved a note under the
    # curator, and an operator watching for contention would be shown a
    # collision the curator had with itself, once per merge, forever.
    assert metrics.get("nora_note_curator_total", action="retire_stale") == 0, (
        "the survivor is protected BY THE GUARD; it must not reach the "
        "retirement loop and report contention that never happened"
    )


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
    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "verdict": "moot", "why": "the day passed"},
        {"note_id": _h(c, b), "verdict": "moot", "why": "the day passed"},
    ))

    assert written == 2, "both retire; neither is suppressed by an absent merge"
    assert await store.standing(workspace=WS) == ()


async def test_a_date_only_collision_is_refused_and_counted():
    """Finding 3. An anchor is `(date, entity_set)` and BOTH halves make it an
    occasion. In production the entity half is empty — `note_entity` is
    unpopulated and `note_create` passes the model's strings through unresolved
    — so a date-only group is "everything happening on the 9th".

    27 §2 already decided this: populating `note_entity` is "the curator's real
    precondition, NOT its flag". Relying on the model to decline is not the
    alternative; that is self-attestation on a destructive operation.

    The COUNT is what makes this a reading rather than a silence — without it,
    a year of a quiet curator cannot be told from a year of no duplicates."""
    store = InMemoryNoteStore()
    # ENTITY STRINGS ARE PRESENT AND IDENTICAL, and nothing is resolved. This
    # is the live shape: `note_create(anchor_entities=["wedding"])` is a real
    # tool call and `note_entity` has no writer. A guard keyed on the raw
    # column would merge these two.
    await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                entities=("wedding",), resolved=())
    await _seed(store, title="Ada's flight to Tokyo", anchor=20260909,
                entities=("wedding",), resolved=())
    metrics = Metrics()

    rendered = await _curator(store, metrics).inputs(key=WS, batch=())

    assert "<merge_group" not in rendered, "a date is not an occasion"
    assert metrics.get(
        "nora_note_curator_groups_refused_total",
        reason="no_entity_anchor") == 1


async def test_a_group_becomes_eligible_only_when_note_entity_resolves():
    """**The positive control for the refusal above, and the whole of finding
    3.** Two notes with identical model-written entity strings are refused;
    the SAME two notes with resolved `note_entity` rows are one group.

    Without this pair the refusal is indistinguishable from a curator that
    never groups anything — and the failing direction is the one that matters:
    keying on `note.anchor_entity_ids`, which `note_create` stores verbatim,
    makes both halves of this test pass the first assertion and merge two
    unrelated notes that happen to say the same word.
    """
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011,
                    entities=("wedding",), resolved=())
    b = await _seed(store, title="Ada's flight", anchor=20261011,
                    entities=("wedding",), resolved=())

    assert "<merge_group" not in await _curator(store).inputs(key=WS, batch=())

    store.entities[a.id] = ("e-grant",)
    store.entities[b.id] = ("e-grant",)

    assert "<merge_group" in await _curator(store).inputs(key=WS, batch=()), (
        "resolved entities, and only resolved entities, make an occasion"
    )


async def test_entities_in_a_different_order_are_the_same_occasion():
    """Finding 4: `anchor_key` sorts and de-duplicates, so a group is not
    missed for an artifact of the order somebody listed the people."""
    store = InMemoryNoteStore()
    await _seed(store, title="Grant wedding", anchor=20261011,
                entities=("ada", "ben"))
    await _seed(store, title="Grant's wedding", anchor=20261011,
                entities=("ben", "ada"))

    rendered = await _curator(store).inputs(key=WS, batch=())

    assert "<merge_group" in rendered


async def test_contradictory_verdicts_leave_the_note_standing():
    """Last-write-wins collapsed two contradictory answers to whichever came
    second, uncounted, on an operation that RETIRES things. "The model said
    both" is not a tie to break — it is «I cannot tell», and the fail-closed
    answer is to leave the note alone."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909, entities=("ada",))
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, note), "verdict": "closed", "why": "Ada booked it"},
        {"note_id": _h(c, note), "verdict": "open", "why": "actually nobody did"},
    ))

    assert written == 0
    assert [n.id for n in await store.standing(workspace=WS)] == [note.id]
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="conflicting_verdict") == 1


# --- P1: the model is never shown a note id -------------------------------


async def test_no_note_id_reaches_the_prompt():
    """**The Dreamer screens every item an adaptor returns against R-18/R-28,
    and `scan_identifiers` matches ANY hyphenated uuid.** Note ids are uuid4,
    so a curator that asked the model about ids had every answer rejected at
    the screen: the tick fired, spent a model call, `accept` received nothing,
    `commit` wrote nothing, and the run reported OK because the envelope was
    non-empty. Silent, forever.

    **Every test in this file missed it**, because `InMemoryNoteStore` ids are
    `note-1` and the screen passes those. The double said yes on exactly the
    property that decides it — which is why this test asserts against the
    SCREEN rather than against the double.

    The screen is not exempted. It is the disclosure floor, and an adaptor
    arguing its way around it is what R-18 exists to stop."""
    from nora.agent.dream_screen import scan_identifiers

    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011)
    await _seed(store, title="Grant's wedding", anchor=20261011)
    await _seed(store, title="Dinner", anchor=20260909, entities=("ada",))

    rendered = await _curator(store).inputs(key=WS, batch=())

    assert a.id not in rendered, "no note id may reach the prompt"
    for line in rendered.splitlines():
        assert scan_identifiers(line) is None, (
            f"the disclosure screen would reject this line: {line!r}"
        )


async def test_a_handle_this_run_did_not_render_is_dropped():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=20260909, entities=("ada",))
    metrics = Metrics()
    curator = _curator(store, metrics)
    await curator.inputs(key=WS, batch=())

    assert await curator.accept(
        key=WS, items=({"note_id": "9999", "verdict": "moot", "why": "x"},),
    ) == ()
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="unknown_note") == 1


# --- P2/P3/P4: the paths that lose or corrupt content ---------------------


async def test_an_over_cap_merged_body_is_dropped_not_raised():
    """P2. This is the INSTRUCTED outcome, not an edge case: the objective says
    "keep every concrete thing, do not summarise" over two bodies of up to
    BODY_MAX_CHARS each and cannot honour both.

    Left to reach `merge`, it raises ValueError out of `commit` — discarding an
    already-committed merge's count, reported as unavailable/written=0 over
    rows that exist in Postgres — and it re-raises every tick, stalling that
    workspace's retirements permanently."""
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011)
    await _seed(store, title="Grant's wedding", anchor=20261011)
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": "x" * (BODY_MAX_CHARS + 1)},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="body_too_long") == 1
    assert len(await store.standing(workspace=WS)) == 2, "nothing was merged"


async def test_a_note_that_moved_since_inputs_is_not_retired():
    """P3. `supersede` takes an id and no parent, so a note whose body changed
    between `inputs` and `commit` was retired on a reading that no longer
    exists — and the objective's one warning is that «we moved it to the 16th»
    leaves the question OPEN. The model would have said `open` had it seen it,
    and `replaced_by` is None so nothing points at what was lost."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                       entities=("ada",), body="Nobody booked.")
    metrics = Metrics()
    curator = _curator(store, metrics)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "moot", "why": "date passed"},
    ))

    # The room speaks between inputs and commit.
    moved = await store.append_revision(
        workspace=WS, note_id=note.id,
        body="Moved to the 16th. Still nobody has booked.",
        parent_revision_id=note.head_revision_id,
        established_by="tag:miles", now_ms=NOW + 1,
    )

    written = await curator.commit(
        key=WS,
        proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ),
        now_ms=NOW + 5,
    )

    assert written == 0
    assert [n.id for n in await store.standing(workspace=WS)] == [note.id]
    assert metrics.get("nora_note_curator_total", action="retire_stale") == 1
    assert moved.id  # the room's revision stands


async def test_two_merged_bodies_for_one_survivor_leave_the_group_alone():
    """P4. Silent last-write-wins ON CONTENT — the second body overwrote the
    first and nothing counted it. Same rule as conflicting verdicts, on the
    more destructive path."""
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011,
                    body="Ada can't do Fridays.")
    await _seed(store, title="Grant's wedding", anchor=20261011,
                body="Ben found Sheung Wan.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": "KEEPS BOTH: Fridays, Sheung Wan"},
        # Long enough to clear the `shorter_than_inputs` floor, so the reason
        # under test is the CONFLICT and not the length.
        {"note_id": _h(c, a), "merged_body": "ONLY FRIDAYS, and nothing else"},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total",
        reason="conflicting_merged_body") == 1
    head = await store.read(workspace=WS, note_id=a.id)
    assert head is not None
    assert head[1].body == "Ada can't do Fridays.", "the original stands"


async def test_a_third_body_repeating_the_first_does_not_revive_a_refused_merge():
    """**The conflict was not sticky.** `out.remove(seen)` deleted the first
    body, so a third item repeating it found nothing in the list, appended
    itself, and the merge LANDED with the very body the conflict had just been
    counted for. The instrument said refused; it merged — on the more
    destructive of the two paths, with the weaker of the two mechanisms.
    `commit`'s verdict path has had a sticky `conflicted` set all along.
    """
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011,
                    body="Ada can't do Fridays.")
    await _seed(store, title="Grant's wedding", anchor=20261011,
                body="Ben found Sheung Wan.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": "KEEPS BOTH: Fridays, Sheung Wan"},
        {"note_id": _h(c, a), "merged_body": "ONLY FRIDAYS, and nothing else"},
        {"note_id": _h(c, a), "merged_body": "KEEPS BOTH: Fridays, Sheung Wan"},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total",
        reason="conflicting_merged_body") == 1
    head = await store.read(workspace=WS, note_id=a.id)
    assert head is not None
    assert head[1].body == "Ada can't do Fridays.", (
        "the survivor's head is untouched; a repeat is not a tie-break"
    )


async def test_a_merged_body_shorter_than_its_inputs_is_refused():
    """**A code check on a property the model cannot fake.**

    `group_on_anchor_key` refuses date-only merges because relying on the model
    to decline is self-attestation on a destructive operation — and the same
    tick then accepted the merged BODY, the thing that overwrites the
    survivor's head, on the model's word alone. The only checks were non-blank
    and under the cap.

    The objective says the merged body keeps everything every note knew, so it
    cannot be shorter than the longest thing it is keeping. A body that is, is
    a summary — the one failure mode this verb has, and nothing downstream can
    undo it.
    """
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011,
                    body="Ada can't do Fridays and Sai Kung was ruled out.")
    await _seed(store, title="Grant's wedding", anchor=20261011,
                body="Ben found Sheung Wan.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": "Wedding plans, still open."},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="shorter_than_inputs") == 1
    head = await store.read(workspace=WS, note_id=a.id)
    assert head is not None
    assert head[1].body == "Ada can't do Fridays and Sai Kung was ruled out."


async def test_a_merged_body_as_long_as_its_longest_input_is_accepted():
    """The floor, on the boundary. It is a FLOOR and not an equality, and it is
    deliberately not "at least the sum" — two bodies about one occasion overlap
    and an honest merge is shorter than the concatenation. Without this the
    check could be tightened into refusing every real merge and every
    assertion above would still pass."""
    store = InMemoryNoteStore()
    longest = "Ada can't do Fridays and Sai Kung was ruled out."
    a = await _seed(store, title="Grant wedding", anchor=20261011,
                    body=longest)
    await _seed(store, title="Grant's wedding", anchor=20261011,
                body="Ben found Sheung Wan.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": "x" * len(longest)},
    ))

    assert written == 1, "exactly as long as the longest input is enough"
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="shorter_than_inputs") == 0


class _ReadRaises(InMemoryNoteStore):
    """A store whose `read` fails. A connection blip, not a bug."""

    async def read(self, **kw):  # type: ignore[override]
        raise RuntimeError("the connection went away")


async def test_a_failed_read_before_retiring_does_not_discard_the_ticks_merges():
    """**The one unguarded await in the loop, and it was added underneath the
    comment arguing against exactly this.**

    The retirement loop is wrapped in a `try` whose own comment says an
    exception escaping "would discard `retired`, INCLUDING a merge that already
    committed" — and the head-check `read` this branch added sits OUTSIDE it.
    A connection blip on the first note loses the count for merges already
    committed in Postgres, and `merge_groups` reads live state, so those groups
    are gone next tick and the number is never recovered.

    The merge here is REAL and already applied at the store, which is what
    makes the lost count a lie rather than a rounding error.
    """
    store = _ReadRaises()
    a = await _seed(store, title="Grant wedding", anchor=20260101,
                    body="Ada can't do Fridays; Sai Kung ruled out.")
    b = await _seed(store, title="Grant's wedding", anchor=20260101,
                    body="Ben found a place in Sheung Wan.")
    expired = await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                          entities=("ada",), body="Nobody booked.")
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body":
            "Ada can't do Fridays; Sai Kung ruled out. Ben found Sheung Wan."},
        {"note_id": _h(c, expired), "verdict": "moot", "why": "the day passed"},
    ))

    assert written == 1, (
        "the merge retired the duplicate and that row EXISTS — reporting 0 "
        "here is `ok` wearing `unavailable`'s clothes"
    )
    assert metrics.get("nora_note_curator_total", action="retire_failed") == 1
    standing = {n.id for n in await store.standing(workspace=WS)}
    assert standing == {a.id, expired.id}
    assert b.id not in standing


# --- the label vocabularies, derived rather than hand-copied ---------------


def _curator_vocabularies():
    """DERIVED from the adaptor's own `_VOCABULARIES` map, so a counter added
    with a vocabulary is covered by the parity test the day it lands.

    The previous test covered ONE of the three counters by name, and the two it
    left out were exactly the ones that had drifted: `conflicting_merged_body`
    was emitted by the code and named nowhere in
    `nora_note_curator_dropped_total`'s help text.
    """
    return sorted(
        (name, label, sorted(vocab))
        for name, (label, vocab) in
        NoteCuratorAdaptor._VOCABULARIES.items()
    )


@pytest.mark.parametrize(
    "metric,label,vocabulary", _curator_vocabularies(),
    ids=[n for n, _, _ in _curator_vocabularies()],
)
def test_every_label_the_code_can_emit_is_named_in_its_help_text(
    metric, label, vocabulary,
):
    """A hand-written vocabulary in help text drifts the moment a branch adds a
    value — and both of these had: `retire_failed` and `retire_stale` were
    emitted for a round before `nora_note_curator_total`'s text knew about
    them, and `conflicting_merged_body` shipped emitted and unnamed.

    **Word-boundary, not substring.** `"merge" in text` is satisfied by a text
    naming only `merge_refused`, so the weaker form passed on a help text that
    did not name the label at all — a matcher that matches too much, in the
    test written to catch drift.

    Checked at runtime rather than by types because `nora/language` is outside
    `make gate`'s mypy scope (#61)."""
    import re

    from nora.obs.metric_specs import _COUNTERS, _GAUGES

    spec = next(
        c for c in (*_COUNTERS, *_GAUGES) if c.name == metric
    )
    assert vocabulary, f"{metric} has an EMPTY vocabulary — nothing is checked"
    for value in vocabulary:
        assert re.search(rf"\b{re.escape(value)}\b", spec.help_text), (
            f"{metric}{{{label}={value!r}}} is emitted and the help text does "
            f"not name it"
        )


def test_the_label_parity_check_can_actually_fail():
    """The positive control for the matcher above. `\b` against a value that
    is a PREFIX of a named one must not match — which is the exact weakness the
    substring form had."""
    import re

    assert not re.search(r"\bmerge\b", "actions: merge_refused, merge_failed")
    assert re.search(r"\bmerge\b", "actions: merge, merge_refused")


def test_an_action_outside_the_vocabulary_raises_rather_than_forking_a_series():
    """A label typo produces a NEW SERIES rather than an error, and a new
    series is invisible until somebody notices the old one went flat. This is
    the one place that cannot be a silent drop."""
    store = InMemoryNoteStore()
    curator = _curator(store)

    with pytest.raises(ValueError, match="vocabulary"):
        curator._inc("nora_note_curator_total", action="typo")


def test_every_drop_reason_the_code_emits_is_in_its_vocabulary():
    """Asserts the SET, so a reason added without a vocabulary entry fails here
    rather than at its first increment in production.

    **Parsed with `ast`, not a regex.** The first version matched
    `reason="..."` anywhere in the file and flagged a string inside a COMMENT
    naming another metric's label — a matcher that matches too much, which is
    the family this bundle documents, reproduced in the test written to prevent
    drift. An AST walk sees calls only.
    """
    import ast
    import pathlib

    tree = ast.parse(
        pathlib.Path("nora/language/note_curator.py").read_text()
    )
    emitted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and target.attr == "_inc"):
            continue
        for kw in node.keywords:
            if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                emitted.add(kw.value.value)

    assert emitted, "the walk found no reasons at all — it is not measuring"
    known = CURATOR_DROPS | CURATOR_GROUP_REFUSALS
    assert emitted <= known, f"emitted but not in a vocabulary: {emitted - known}"


class _MergeRaises(InMemoryNoteStore):
    """A store whose `merge` refuses one named survivor with a domain error.

    Subclassed rather than mocked so every other verb behaves exactly as the
    double does — the point is a tick in which one group merges and another
    fails, and a mock would make the successful half unreal too.
    """

    def __init__(self, doomed: str = "") -> None:
        super().__init__()
        self.doomed = doomed

    async def merge(self, **kw):  # type: ignore[override]
        if kw["survivor"] == self.doomed:
            raise ValueError("a note body is at most 8000 characters")
        return await super().merge(**kw)


async def test_one_groups_domain_failure_does_not_discard_the_ticks_merges():
    """**The path P2's first test did not reach.** `_merged_bodies` refuses an
    over-cap body before the store sees it, so the ValueError catch is the
    BRACES — reachable when the store refuses for a reason this module does not
    enumerate.

    It matters because the earlier group's merge is ALREADY COMMITTED in
    Postgres. Letting the exception escape reports written=0 over rows that
    exist, and re-raises every tick, stalling that workspace's retirements
    permanently."""
    store = _MergeRaises()
    good = await _seed(store, title="Grant wedding", anchor=20261011,
                       entities=("grant",))
    await _seed(store, title="Grant's wedding", anchor=20261011,
                entities=("grant",))
    bad = await _seed(store, title="Ada wedding", anchor=20270301,
                      entities=("ada",))
    await _seed(store, title="Ada's wedding", anchor=20270301,
                entities=("ada",))
    store.doomed = bad.id
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, good), "merged_body": "both wedding shortlists"},
        {"note_id": _h(c, bad), "merged_body": "both Ada shortlists"},
    ))

    assert written == 1, "the good merge survives the bad one"
    assert metrics.get("nora_note_curator_total", action="merge") == 1
    assert metrics.get("nora_note_curator_total", action="merge_failed") == 1
    head = await store.read(workspace=WS, note_id=good.id)
    assert head is not None
    assert head[1].body == "both wedding shortlists"


async def test_an_over_cap_body_is_refused_before_the_store_sees_it():
    """The BELT. Its own reason, because the remedy differs from a missing
    field: an over-cap body means the model answered the RIGHT shape and the
    objective asked for something it cannot honour."""
    store = _MergeRaises()
    a = await _seed(store, title="Grant wedding", anchor=20261011)
    await _seed(store, title="Grant's wedding", anchor=20261011)
    metrics = Metrics()

    _, written = await _merge_round(store, metrics, lambda c: (
        {"note_id": _h(c, a), "merged_body": "x" * (BODY_MAX_CHARS + 1)},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="body_too_long") == 1
    assert metrics.get("nora_note_curator_total", action="merge_failed") == 0, (
        "refused at the belt, so the braces never engage"
    )


# --- through the REAL screen, in the runtime's order ------------------------


async def test_items_survive_the_real_dream_screen_in_the_runtimes_order():
    """**The test that would have caught P1**, and the reason it is written
    this way: every adaptor suite in this repo called `accept()` directly, so
    the real screen never ran and three tiers shipped inert.

    `DreamerRuntime` does `screen(...)` and hands the KEPT items to `accept`
    (`nora/agent/dreamer.py:582-587`). A test that skips that step is testing a
    path production cannot take — which is exactly how a curator asking about
    uuid4 note ids passed 18 tests while every real item was rejected.

    So this drives the real `screen`, in the real order, and asserts the items
    SURVIVE it. Not that the handles look right — that nothing is dropped."""
    from nora.agent.dream_screen import screen

    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20261011)
    b = await _seed(store, title="Grant's wedding", anchor=20261011)
    expired = await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                          entities=("ada",))
    metrics = Metrics()
    curator = _curator(store, metrics)
    await curator.inputs(key=WS, batch=())

    items = [
        {"note_id": _h(curator, a), "merged_body": "both shortlists"},
        {"note_id": _h(curator, expired), "verdict": "moot", "why": "passed"},
    ]

    kept, rejected = screen(
        adaptor="note-curation", items=items, metrics=metrics,
    )

    assert not rejected, f"the screen rejected real curator items: {rejected}"
    assert len(kept) == 2

    # …and the kept items still resolve, which is the half a screen test alone
    # would miss: surviving the screen is worthless if the handles no longer
    # name anything.
    payloads = await curator.accept(key=WS, items=kept)
    # BY KIND, not by count: `accept` also emits a `_MergeOffered` marker for
    # every group the model named, so a bare count pins an implementation
    # detail and reddens for the wrong reason the next time one is added.
    kinds = {type(p).__name__ for p in payloads}
    assert {"_MergedBody", "_Verdict"} <= kinds, kinds
    written = await curator.commit(
        key=WS,
        proposals=tuple(
            Proposal(payload=p, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for p in payloads
        ),
        now_ms=NOW + 5,
    )
    assert written == 2, "one merge, one retirement"
    assert [n.id for n in await store.standing(workspace=WS)] == [a.id]
    assert b.id not in {n.id for n in await store.standing(workspace=WS)}


# --- P5: a group member is never also rendered as expired ------------------


async def test_a_passed_date_group_is_rendered_once_not_twice():
    """**The common case, not an edge**: retiring is ABOUT past dates, so a
    colliding group whose occasion has passed appears in BOTH reads.

    Three things went wrong when it did — double tokens, a doubled
    `bodies_read` (the gauge that says whether the cap binds), and worst, the
    standalone rendering invited a `closed` verdict on a group MEMBER, so if no
    merged body came back the duplicate was retired on its own with its content
    carried nowhere. That is the merge's whole purpose defeated by its inputs.
    """
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20260101,
                    body="AAA Ada can't do Fridays.")
    await _seed(store, title="Grant's wedding", anchor=20260101,
                body="BBB Ben found Sheung Wan.")
    metrics = Metrics()

    rendered = await _curator(store, metrics).inputs(key=WS, batch=())

    assert rendered.count("AAA Ada can't do Fridays.") == 1, (
        "the group member's body is rendered once, not twice"
    )
    assert "<merge_group" in rendered, "and it is rendered AS a group"
    assert metrics.get(
        "nora_note_curator_bodies_read", workspace=WS) == 4, (
        "TWO NOTES, FOUR READS. The rendering is de-duplicated; the gauge is "
        "not, because the gauge measures the caps and both reads spent one. "
        "`merge_groups` fetched these two bodies and `anchored_before` fetched "
        "them again — reporting 2 would hide half the cost of exactly the "
        "overlap this test is about"
    )
    assert a.id  # positive control: the seed really happened


async def test_an_expired_note_outside_any_group_is_still_rendered():
    """The positive control for the subtraction. Removing group members from
    `expired` must not remove notes that were only ever expired — otherwise
    retirement stops working entirely and the test above still passes."""
    store = InMemoryNoteStore()
    await _seed(store, title="Grant wedding", anchor=20260101)
    await _seed(store, title="Grant's wedding", anchor=20260101)
    lone = await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                       entities=("ada",), body="CCC Nobody booked.")

    rendered = await _curator(store).inputs(key=WS, batch=())

    assert "CCC Nobody booked." in rendered
    assert lone.id  # the note exists; the assertion above is about rendering
