"""`NoteMergeAdaptor` (design/dreamer-agent-loop/41 §7.3-7.8, DEC-202).

Every test runs against `InMemoryNoteStore` and `InMemoryDreamScheduleClock`,
the doubles `tests/contract/` binds to the SQL implementations, so a fold here
is the same compare-and-swap the database makes. The named tests of 41 §7.8
that belong to this adaptor are here; each says what reverting it reddens.
"""

from __future__ import annotations

import re
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

from nora.domain.notes import (
    MERGE_MARKER,
    NOTE_MERGE_OUTCOMES,
    Note,
    NoteSupersedeReason,
    default_revisit_at,
)
from nora.domain.ports import Proposal
from nora.language.note_merge import (
    MERGE_TOTAL_OUTCOMES,
    NoteMergeAdaptor,
    pass_clock_name,
)
from nora.obs.metric_specs import _COUNTERS
from nora.obs.metrics import Metrics
from nora.testing.dream_schedule import InMemoryDreamScheduleClock
from nora.testing.note_store import InMemoryNoteStore

WS = "friends"
HK = ZoneInfo("Asia/Hong_Kong")
#: 2026-09-25 12:00 HKT.
T0 = 1_790_308_800_000
HOUR = 3_600_000
DAY = 24 * HOUR


class _Clock:
    """A clock the test moves, so the render instant and the commit instant
    can differ the way a model call makes them differ."""

    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def _adaptor(
    store: InMemoryNoteStore, *, mode: str = "shadow",
    clock: _Clock | None = None, metrics: Metrics | None = None,
    pass_clock: InMemoryDreamScheduleClock | None = None,
    labels: dict[str, str] | None = None,
) -> NoteMergeAdaptor:
    return NoteMergeAdaptor(
        notes=store,
        clock_store=pass_clock or InMemoryDreamScheduleClock(),
        mode=mode,  # type: ignore[arg-type]
        timezone=HK,
        label_for=(labels or {"conv-1": "Friends", "conv-2": "Ridgeline"}).get,
        metrics=metrics if metrics is not None else Metrics(),
        clock=clock or _Clock(T0),
    )


async def _note(
    store: InMemoryNoteStore, title: str, *, at: int, anchor: int | None = 20260927,
    room: str = "conv-1", body: str = "Nothing is booked yet.",
) -> Note:
    return await store.create(
        unit=WS, title=title, description=f"{title}: still open",
        uncertainty="where and when", closing_condition="somebody books it",
        established_in=room, body=body, established_by="tag:miles",
        now_ms=at, anchor_date=anchor,
        anchor_entity_ids=() if anchor is not None else ("ada",),
    )


def _handle(prompt: str, title: str) -> str:
    match = re.search(rf'<open_note id="(\d+)" title="{re.escape(title)}"', prompt)
    assert match, f"{title!r} is not rendered"
    return match.group(1)


def _proposals(*groups: dict, at: int = T0) -> tuple[Proposal, ...]:
    return tuple(
        Proposal(payload=g, instruction_sha256="a" * 64, model="m",
                 established_by=f"dream:note-merge:{WS}:{at}")
        for g in groups
    )


async def _run(
    adaptor: NoteMergeAdaptor, *items: dict, commit_ms: int = T0 + 60_000,
) -> tuple[str, int]:
    prompt = await adaptor.inputs(key=WS, batch=())
    payloads = await adaptor.accept(key=WS, items=items) if prompt else ()
    written = await adaptor.commit(
        key=WS, now_ms=commit_ms,
        proposals=tuple(
            Proposal(payload=p, instruction_sha256="a" * 64, model="m",
                     established_by=f"dream:note-merge:{WS}:{commit_ms}")
            for p in payloads
        ),
    )
    return prompt, written


# --- what it reads -----------------------------------------------------------


async def test_every_open_note_renders_with_its_times_and_no_id():
    """The model judges «the same open question» from what it is shown, so
    each note carries its age, its last revision's age and its anchor read
    against today — and never a uuid, which the Dreamer's screen would drop
    the whole answer for (issue #63)."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - 3 * DAY, anchor=20260927)
    b = await _note(store, "Neway booking", at=T0 - 2 * HOUR, anchor=None)

    prompt = await _adaptor(store).inputs(key=WS, batch=())

    assert "<today" in prompt and "2026-09-25 Fri" in prompt
    assert 'written="3d ago"' in prompt
    assert 'written="2h ago"' in prompt
    assert 'revised="' in prompt
    assert 'anchor="2026-09-27 Sun"' in prompt and 'anchor_is="in 2 days"' in prompt
    assert 'room="Friends"' in prompt
    assert 'changed="new"' in prompt
    for note in (a, b):
        assert note.id not in prompt
        assert note.head_revision_id not in prompt


async def test_an_unlabelled_room_is_never_rendered_by_its_id():
    store = InMemoryNoteStore()
    await _note(store, "Saturday dinner", at=T0 - DAY, room="signal-group-9f")
    await _note(store, "Neway booking", at=T0 - HOUR, room="signal-group-9f")

    prompt = await _adaptor(store, labels={}).inputs(key=WS, batch=())

    assert "signal-group-9f" not in prompt
    assert prompt.count('room="room 1"') == 2


async def test_fewer_than_two_open_notes_renders_nothing():
    store = InMemoryNoteStore()
    await _note(store, "Saturday dinner", at=T0 - DAY)
    metrics = Metrics()

    assert await _adaptor(store, metrics=metrics).inputs(key=WS, batch=()) == ""
    assert metrics.get("nora_note_merge_pass_total", reason="nothing_open") == 1


# --- the pass clock (41 §7.4) --------------------------------------------------


async def test_the_pass_clock_is_stamped_with_the_render_instant():
    """The stamp is the moment `inputs` READ the notebook, not the moment
    `commit` ran. A note written while the model was thinking is newer than
    the render and must be seen by the next pass; stamped with the commit
    time, it is older than the stamp and never is.

    Falsification: stamp `record_fire` with `now_ms` instead of
    `shown.render_ms` and this goes red (both the stamp and the next pass)."""
    store = InMemoryNoteStore()
    await _note(store, "Saturday dinner", at=T0 - DAY)
    await _note(store, "Neway booking", at=T0 - HOUR)
    clock = _Clock(T0)
    pass_clock = InMemoryDreamScheduleClock()
    adaptor = _adaptor(store, clock=clock, pass_clock=pass_clock)

    prompt = await adaptor.inputs(key=WS, batch=())
    assert prompt
    # The model thinks for 40 seconds; a note lands in the middle.
    during = await _note(store, "Friday lunch", at=T0 + 20_000)
    await adaptor.commit(key=WS, proposals=(), now_ms=T0 + 40_000)

    assert await pass_clock.last_fired(
        adaptor_name=pass_clock_name("shadow"), key=WS) == T0
    clock.now = T0 + HOUR
    again = await adaptor.inputs(key=WS, batch=())
    assert 'title="Friday lunch"' in again
    assert re.search(
        r'title="Friday lunch"[^>]*changed="new"', again,
    ), "the note written during the model call is new to the next pass"
    assert during.id not in again


async def test_an_unchanged_notebook_renders_nothing_and_a_tag_revision_does():
    """The change predicate: nothing since the stamp is no call; a person's
    revision after it is a change."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - DAY)
    await _note(store, "Neway booking", at=T0 - HOUR)
    clock = _Clock(T0)
    metrics = Metrics()
    adaptor = _adaptor(store, clock=clock, metrics=metrics)
    await _run(adaptor)

    clock.now = T0 + HOUR
    assert await adaptor.inputs(key=WS, batch=()) == ""
    assert metrics.get("nora_note_merge_pass_total", reason="unchanged") == 1
    await adaptor.commit(key=WS, proposals=(), now_ms=T0 + HOUR)

    await store.append_revision(
        unit=WS, note_id=a.id, body="Ada says Neway.",
        parent_revision_id=a.head_revision_id, established_by="tag:ada",
        now_ms=T0 + HOUR + 1,
    )
    clock.now = T0 + 2 * HOUR
    prompt = await adaptor.inputs(key=WS, batch=())
    assert re.search(r'title="Saturday dinner"[^>]*changed="revised"', prompt)
    assert 'changed="new"' not in prompt


async def test_its_own_merge_revision_does_not_trigger_the_next_pass():
    """A fold appends a revision to the survivor. If that counted as a
    change, every fold would buy the next pass a model call over a notebook
    nothing else touched — and a re-ask of every pair in it.

    Falsification: drop the `OWN_REVISION_PREFIX` exclusion from
    `_changed_since` and the next pass renders."""
    store = InMemoryNoteStore()
    await _note(store, "Saturday dinner", at=T0 - 2 * DAY)
    await _note(store, "Sunday brunch", at=T0 - DAY)
    await _note(store, "Neway booking", at=T0 - HOUR)
    clock = _Clock(T0)
    adaptor = _adaptor(store, mode="on", clock=clock)
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, "Saturday dinner"),
                  _handle(prompt, "Neway booking")],
        "why": "both ask where Saturday's dinner is",
    },))
    written = await adaptor.commit(
        key=WS, proposals=_proposals(*items, at=T0 + 60_000),
        now_ms=T0 + 60_000,
    )
    assert written == 1, "the fold landed (positive control)"

    clock.now = T0 + HOUR
    assert await adaptor.inputs(key=WS, batch=()) == ""


# --- what code refuses (41 §7.7, §7.8) --------------------------------------------


async def test_overlapping_groups_are_refused_whole():
    """{1,2} and {2,3} in one answer: every group naming the shared note is
    refused, and the transitive closure {1,2,3} is never taken.

    Falsification: drop the `overlap` refusal and 2 is folded twice."""
    store = InMemoryNoteStore()
    one = await _note(store, "Saturday dinner", at=T0 - 3 * DAY)
    two = await _note(store, "Neway booking", at=T0 - 2 * DAY)
    three = await _note(store, "Headcount for Saturday", at=T0 - DAY)
    metrics = Metrics()
    adaptor = _adaptor(store, mode="on", metrics=metrics)
    prompt = await adaptor.inputs(key=WS, batch=())
    h = {n.title: _handle(prompt, n.title) for n in (one, two, three)}
    items = await adaptor.accept(key=WS, items=(
        {"notes": [h["Saturday dinner"], h["Neway booking"]], "why": "a"},
        {"notes": [h["Neway booking"], h["Headcount for Saturday"]], "why": "b"},
    ))

    written = await adaptor.commit(key=WS, proposals=_proposals(*items),
                                   now_ms=T0 + 1)

    assert written == 0
    assert len(await store.standing(unit=WS)) == 3
    assert metrics.get("nora_note_merge_total", outcome="overlap") == 2
    assert {d.outcome for d in store.merge_decisions} == {"overlap"}


async def test_a_cross_room_group_is_refused():
    """v1 folds same-room groups only: the survivor's room decides where a
    proactive row goes and which room the curator reads, and a cross-room
    fold would silently change both (41 §7.7).

    Falsification: drop the `cross_room` refusal and the two fold."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - DAY, room="conv-1")
    b = await _note(store, "Saturday dinner plan", at=T0 - HOUR, room="conv-2")
    metrics = Metrics()
    adaptor = _adaptor(store, mode="on", metrics=metrics)
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, a.title), _handle(prompt, b.title)],
        "why": "same dinner",
    },))

    written = await adaptor.commit(key=WS, proposals=_proposals(*items),
                                   now_ms=T0 + 1)

    assert written == 0
    assert len(await store.standing(unit=WS)) == 2
    assert metrics.get("nora_note_merge_total", outcome="cross_room") == 1
    [row] = store.merge_decisions
    assert (row.outcome, row.survivor_id, row.duplicate_id) == (
        "cross_room", a.id, b.id)


async def test_a_group_with_nothing_changed_since_the_last_pass_is_refused():
    """One sample per pass is a pin that time would undo: a pair judged apart
    last pass, re-asked on every pass, is a union of samples. A group none of
    whose notes changed was already judged, and is refused."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - 2 * DAY)
    b = await _note(store, "Neway booking", at=T0 - DAY)
    await _note(store, "Friday lunch", at=T0 - DAY)
    clock = _Clock(T0)
    metrics = Metrics()
    adaptor = _adaptor(store, mode="on", clock=clock, metrics=metrics)
    await _run(adaptor)

    await _note(store, "Movie night", at=T0 + 1)
    clock.now = T0 + HOUR
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, a.title), _handle(prompt, b.title)],
        "why": "same dinner",
    },))
    written = await adaptor.commit(key=WS, proposals=_proposals(*items),
                                   now_ms=T0 + HOUR)

    assert written == 0
    assert metrics.get("nora_note_merge_total", outcome="not_changed") == 1


async def test_unknown_handles_groups_of_one_and_malformed_items_are_counted():
    store = InMemoryNoteStore()
    await _note(store, "Saturday dinner", at=T0 - DAY)
    await _note(store, "Neway booking", at=T0 - HOUR)
    metrics = Metrics()
    adaptor = _adaptor(store, mode="on", metrics=metrics)
    prompt = await adaptor.inputs(key=WS, batch=())
    one = _handle(prompt, "Saturday dinner")
    items = await adaptor.accept(key=WS, items=(
        {"notes": [one, "99"], "why": "x"},
        {"notes": [one, one], "why": "x"},
        {"notes": "1,2", "why": "x"},
    ))

    written = await adaptor.commit(key=WS, proposals=_proposals(*items),
                                   now_ms=T0 + 1)

    assert written == 0
    for outcome in ("unknown_ordinal", "singleton", "malformed"):
        assert metrics.get("nora_note_merge_total", outcome=outcome) == 1
    assert store.merge_decisions == []


# --- what a fold writes (41 §7.7) ------------------------------------------------


async def test_shadow_records_the_fold_and_writes_nothing_to_the_notes():
    """`shadow` is the pass with every check and no write: the notes are as
    they were, and one `would_merge` row names both titles and the reason."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - DAY)
    b = await _note(store, "Neway booking", at=T0 - HOUR)
    before = {n.id: n for n in await store.standing(unit=WS)}
    metrics = Metrics()
    adaptor = _adaptor(store, mode="shadow", metrics=metrics)
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, b.title), _handle(prompt, a.title)],
        "why": "both ask where Saturday's dinner is",
    },))

    written = await adaptor.commit(key=WS, proposals=_proposals(*items),
                                   now_ms=T0 + 1)

    assert written == 0
    assert {n.id: n for n in await store.standing(unit=WS)} == before
    [row] = store.merge_decisions
    assert row.mode == "shadow" and row.outcome == "would_merge"
    assert (row.survivor_id, row.survivor_title) == (a.id, a.title), (
        "the survivor is the OLDEST, whatever order the model named them in"
    )
    assert (row.duplicate_id, row.duplicate_title) == (b.id, b.title)
    assert row.reason == "both ask where Saturday's dinner is"
    assert metrics.get("nora_note_merge_total", outcome="would_merge") == 1


async def test_the_survivor_takes_the_latest_anchor():
    """A merged 0923 booking-deadline note carrying the 0927 dinner question
    would be selected by `anchored_before` on 0924 and could be expired with
    the later question inside it. The survivor — the oldest note — takes the
    later anchor, and its `revisit_at` is recomputed from it.

    Falsification: pass the survivor's own `anchor_date` to `merge_into` and
    the survivor keeps 0923."""
    store = InMemoryNoteStore()
    deadline = await _note(store, "Neway booking deadline", at=T0 - 3 * DAY,
                           anchor=20260923)
    dinner = await _note(store, "Saturday dinner", at=T0 - DAY,
                         anchor=20260927, body="Neway at 8?")
    adaptor = _adaptor(store, mode="on")
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, deadline.title), _handle(prompt, dinner.title)],
        "why": "the booking is for Saturday's dinner",
    },))

    written = await adaptor.commit(key=WS, proposals=_proposals(*items),
                                   now_ms=T0 + 1)

    assert written == 1
    survivor, head = await store.read(unit=WS, note_id=deadline.id)
    assert survivor.anchor_date == 20260927
    assert survivor.revisit_at == default_revisit_at(
        anchor_date=20260927, now_ms=T0 + 1)
    assert MERGE_MARKER in head.body
    assert "title: Saturday dinner" in head.body and "Neway at 8?" in head.body
    assert head.established_by.startswith("dream:note-merge:")
    gone, _ = await store.read(unit=WS, note_id=dinner.id)
    assert gone.retire_reason == NoteSupersedeReason.RETIRED_MERGED
    assert gone.replaced_by == deadline.id
    assert [d.outcome for d in store.merge_decisions] == ["merged"]


async def test_a_fold_that_lost_a_race_is_stale_and_writes_nothing():
    """A person revises the duplicate while the model is thinking: the fold
    would carry a body the model never judged. `stale`, nothing written,
    and the revision itself is the change the next pass sees."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - DAY)
    b = await _note(store, "Neway booking", at=T0 - HOUR)
    metrics = Metrics()
    adaptor = _adaptor(store, mode="on", metrics=metrics)
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, a.title), _handle(prompt, b.title)],
        "why": "same dinner",
    },))
    await store.append_revision(
        unit=WS, note_id=b.id, body="Actually it is Sunday.",
        parent_revision_id=b.head_revision_id, established_by="tag:ben",
        now_ms=T0 + 10,
    )

    written = await adaptor.commit(key=WS, proposals=_proposals(*items),
                                   now_ms=T0 + 20)

    assert written == 0
    assert len(await store.standing(unit=WS)) == 2
    assert metrics.get("nora_note_merge_total", outcome="stale") == 1


async def test_a_fold_over_the_body_cap_is_refused_not_truncated():
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - DAY, body="a" * 7000)
    b = await _note(store, "Neway booking", at=T0 - HOUR, body="b" * 2000)
    metrics = Metrics()
    adaptor = _adaptor(store, mode="on", metrics=metrics)
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, a.title), _handle(prompt, b.title)],
        "why": "same dinner",
    },))

    assert await adaptor.commit(key=WS, proposals=_proposals(*items),
                                now_ms=T0 + 1) == 0
    assert metrics.get("nora_note_merge_total", outcome="too_long") == 1
    assert len(await store.standing(unit=WS)) == 2


async def _fold_named(adaptor: NoteMergeAdaptor, *titles: str, now_ms: int) -> int:
    """One pass whose model names `titles` as one group; rows written."""
    prompt = await adaptor.inputs(key=WS, batch=())
    items = await adaptor.accept(key=WS, items=({
        "notes": [_handle(prompt, t) for t in titles], "why": "one question",
    },)) if prompt else ()
    return await adaptor.commit(
        key=WS, proposals=_proposals(*items, at=now_ms), now_ms=now_ms,
    )


async def test_a_flip_from_shadow_to_on_reconsiders_every_pair():
    """Review of #495. `shadow` judges without folding and stamps the pass
    clock; with ONE clock for both modes, a pair shadow recorded `would_merge`
    was untouched since that stamp and was refused `not_changed` for ever once
    the operator flipped to `on`. Each mode keeps its own clock, so the first
    `on` pass sees every note as new and the pair folds.

    Falsification: key the clock on `PASS_CLOCK_NAME` alone and the `on` pass
    renders nothing (or refuses the pair) and writes 0."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - DAY)
    b = await _note(store, "Neway booking", at=T0 - HOUR)
    pass_clock = InMemoryDreamScheduleClock()
    clock = _Clock(T0)
    shadow = _adaptor(store, mode="shadow", clock=clock, pass_clock=pass_clock)
    assert await _fold_named(shadow, a.title, b.title, now_ms=T0 + 1) == 0
    assert [d.outcome for d in store.merge_decisions] == ["would_merge"]

    clock.now = T0 + DAY
    on = _adaptor(store, mode="on", clock=clock, pass_clock=pass_clock)
    written = await _fold_named(on, a.title, b.title, now_ms=T0 + DAY + 1)

    assert written == 1
    assert [d.outcome for d in store.merge_decisions] == ["would_merge", "merged"]


async def test_a_group_with_one_changed_note_is_not_refused():
    """`not_changed` refuses a group NONE of whose notes changed. One changed
    note is exactly the case the pass exists for: a new note that duplicates
    an old one."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - 2 * DAY)
    await _note(store, "Friday lunch", at=T0 - DAY)
    clock = _Clock(T0)
    adaptor = _adaptor(store, mode="on", clock=clock)
    await _run(adaptor)

    b = await _note(store, "Neway booking", at=T0 + 1)
    clock.now = T0 + HOUR
    written = await _fold_named(adaptor, a.title, b.title, now_ms=T0 + HOUR)

    assert written == 1


async def test_a_persons_revision_under_a_later_fold_revision_is_a_change():
    """41 §7.4 reads REVISIONS, not heads: a person adds to a note and then
    this pass's own fold lands on top of it. The head is the pass's own, and a
    head-only check would call the note unchanged and never judge what the
    person wrote."""
    store = InMemoryNoteStore()
    a = await _note(store, "Saturday dinner", at=T0 - 2 * DAY)
    await _note(store, "Friday lunch", at=T0 - DAY)
    clock = _Clock(T0)
    adaptor = _adaptor(store, mode="on", clock=clock)
    await _run(adaptor)

    person = await store.append_revision(
        unit=WS, note_id=a.id, body="Ada says Neway.",
        parent_revision_id=a.head_revision_id, established_by="tag:ada",
        now_ms=T0 + 10,
    )
    await store.append_revision(
        unit=WS, note_id=a.id, body="Ada says Neway.\n\n--- folded ---",
        parent_revision_id=person.id,
        established_by=f"dream:note-merge:{WS}:{T0 + 20}", now_ms=T0 + 20,
    )
    clock.now = T0 + HOUR

    prompt = await adaptor.inputs(key=WS, batch=())
    assert re.search(r'title="Saturday dinner"[^>]*changed="revised"', prompt)


async def test_strikes_reset_only_when_the_duplicate_is_the_livelier_note():
    """§7.7 at the adaptor: the survivor's strikes reset when the duplicate
    was added to more recently than the survivor's head, and are kept when
    the survivor is the one the room touched last.

    Falsification: pass `reset_strikes=True` unconditionally and the kept
    case loses its strikes."""
    store = InMemoryNoteStore()
    survivor = await _note(store, "Saturday dinner", at=T0 - 3 * DAY)
    duplicate = await _note(store, "Neway booking", at=T0 - 2 * DAY)
    await store.append_revision(
        unit=WS, note_id=survivor.id, body="Still nothing booked.",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:ada", now_ms=T0 - DAY,
    )
    store.notes[survivor.id] = replace(store.notes[survivor.id], strikes=3)

    assert await _fold_named(
        _adaptor(store, mode="on"), survivor.title, duplicate.title,
        now_ms=T0 + 1,
    ) == 1
    assert (await store.read(unit=WS, note_id=survivor.id))[0].strikes == 3

    fresh = InMemoryNoteStore()
    old = await _note(fresh, "Saturday dinner", at=T0 - 3 * DAY)
    await _note(fresh, "Neway booking", at=T0 - DAY)
    fresh.notes[old.id] = replace(fresh.notes[old.id], strikes=3)
    assert await _fold_named(
        _adaptor(fresh, mode="on"), "Saturday dinner", "Neway booking",
        now_ms=T0 + 1,
    ) == 1
    assert (await fresh.read(unit=WS, note_id=old.id))[0].strikes == 0


class _RecordRaises(InMemoryNoteStore):
    async def record_merge_decision(self, *, decision):  # type: ignore[override]
        raise RuntimeError("the connection went away")


async def test_a_commit_that_fails_leaves_the_pass_clock_where_it_was():
    """The stamp is the LAST write of `commit`: a commit that fails part-way
    is `unavailable` to the runtime, and the same change must be judged again
    rather than stamped as seen."""
    store = _RecordRaises()
    a = await _note(store, "Saturday dinner", at=T0 - DAY)
    b = await _note(store, "Neway booking", at=T0 - HOUR)
    pass_clock = InMemoryDreamScheduleClock()
    adaptor = _adaptor(store, mode="shadow", pass_clock=pass_clock)

    with pytest.raises(RuntimeError):
        await _fold_named(adaptor, a.title, b.title, now_ms=T0 + 1)

    assert await pass_clock.last_fired(
        adaptor_name=pass_clock_name("shadow"), key=WS) is None


async def test_a_three_note_group_folds_both_duplicates_into_the_oldest():
    """Pairwise, one transaction per duplicate, each on the head the last one
    left (§7.7)."""
    store = InMemoryNoteStore()
    oldest = await _note(store, "Saturday dinner", at=T0 - 3 * DAY)
    second = await _note(store, "Neway booking", at=T0 - 2 * DAY)
    third = await _note(store, "Saturday headcount", at=T0 - DAY)

    written = await _fold_named(
        _adaptor(store, mode="on"), third.title, oldest.title, second.title,
        now_ms=T0 + 1,
    )

    assert written == 2
    assert [n.id for n in await store.standing(unit=WS)] == [oldest.id]
    for dup in (second, third):
        note, _ = await store.read(unit=WS, note_id=dup.id)
        assert note.replaced_by == oldest.id
    head = (await store.read(unit=WS, note_id=oldest.id))[1]
    assert head.body.count(MERGE_MARKER) == 2


# --- the instrument --------------------------------------------------------------


@pytest.mark.parametrize("outcome", sorted(MERGE_TOTAL_OUTCOMES))
def test_the_help_text_names_every_outcome(outcome):
    [spec] = [c for c in _COUNTERS if c.name == "nora_note_merge_total"]
    assert f"{outcome}:" in spec.help_text or f"{outcome} " in spec.help_text


def test_every_row_outcome_is_a_counted_outcome():
    assert set(NOTE_MERGE_OUTCOMES) <= MERGE_TOTAL_OUTCOMES


def test_off_is_not_a_mode_of_a_built_adaptor():
    with pytest.raises(ValueError):
        _adaptor(InMemoryNoteStore(), mode="off")


# ---------------------------------------------------------------------------
# design 42 S3: one open_note renderer for merge and note-candidates
# ---------------------------------------------------------------------------

#: Merge's `<open_note>` as it rendered before note-candidates came to share
#: the renderer (captured on master `b5952d7b`), one whole and one excerpted.
#: Only the type's own `fields` attribute may differ from these, because the
#: type is shared (`form` joined it, and `revised` reads «revised» now that a
#: revision restates): the pin is everything else, byte for byte.
_MERGE_BEFORE_WHOLE = '<open_note id="1" title="Dinner &amp; drinks" room="Friends" written="6d ago" revised="36h ago" anchor="2026-10-03 Sat" anchor_is="in 7 days" changed="revised" description="one entry of this group\'s notebook that is still open, as it was written down: what it is about, what is undecided, what would settle it, and its body so far. Your own notes, written from what the group said, not anything said in a room, and not a record of what happened." fields="`id` is its handle for this pass; `title` is what the note is called; `room` is the room the note was born in; `written` is how long ago the note was first written; `revised` is how long ago anything was last added to it; `anchor` is on an entry that has one, the day it turns on; `anchor_is` is that day read against today; `changed` is `new` if it was written since this notebook was last read this way, `revised` if something was added to it since then; absent when neither">\nabout: where is open\nundecided: where\nsettled when: a place is booked\nbody:\nAda wants &lt;somewhere&gt; quiet.\n\nBen: the 3rd.\n</open_note>'  # noqa: E501
_MERGE_BEFORE_EXCERPT = '<open_note id="2" title="Dinner &amp; drinks" room="Friends" written="6d ago" revised="36h ago" anchor="2026-10-03 Sat" anchor_is="in 7 days" description="one entry of this group\'s notebook that is still open, as it was written down: what it is about, what is undecided, what would settle it, and its body so far. Your own notes, written from what the group said, not anything said in a room, and not a record of what happened." fields="`id` is its handle for this pass; `title` is what the note is called; `room` is the room the note was born in; `written` is how long ago the note was first written; `revised` is how long ago anything was last added to it; `anchor` is on an entry that has one, the day it turns on; `anchor_is` is that day read against today; `changed` is `new` if it was written since this notebook was last read this way, `revised` if something was added to it since then; absent when neither">\nabout: where is open\nundecided: where\nsettled when: a place is booked\nbody (its first 10 characters; it runs longer):\nAda wants \n</open_note>'  # noqa: E501
_ANNOTATION_BEFORE = '`id` is its handle for this pass; `title` is what the note is called; `room` is the room the note was born in; `written` is how long ago the note was first written; `revised` is how long ago anything was last added to it; `anchor` is on an entry that has one, the day it turns on; `anchor_is` is that day read against today; `changed` is `new` if it was written since this notebook was last read this way, `revised` if something was added to it since then; absent when neither'  # noqa: E501


def test_merge_renders_an_open_note_byte_identical_but_for_the_shared_fields():
    """Design 42 §8.6: «one renderer serves both passes … and a test pinning
    merge's rendered prompt byte-identical until S6 changes it on purpose»."""
    from datetime import UTC, datetime

    from nora.domain.blocks import ContextBlock
    from nora.domain.notes import Note, NoteRevision
    from nora.language.note_merge import _note_block

    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    note = Note(
        id="n1", unit="u", title="Dinner & drinks", description="where is open",
        uncertainty="where", closing_condition="a place is booked",
        established_in="c1", head_revision_id="r1", anchor_date=20261003,
        created_at=int(datetime(2026, 9, 20, tzinfo=UTC).timestamp() * 1000),
    )
    head = NoteRevision(
        id="r1", note_id="n1", body="Ada wants <somewhere> quiet.\n\nBen: the 3rd.",
        parent_revision_id=None, established_by="dream:x",
        written_at=int(datetime(2026, 9, 25, tzinfo=UTC).timestamp() * 1000),
    )
    fields_now = ContextBlock(name="open_note", body="x").annotation
    assert fields_now != _ANNOTATION_BEFORE  # the one difference is real

    def before(golden: str) -> str:
        return golden.replace(_ANNOTATION_BEFORE, fields_now)

    whole = _note_block(
        "1", note, head, room="Friends", now=now, today=now.date(),
        changed="revised", excerpt=600,
    )
    cut = _note_block(
        "2", note, head, room="Friends", now=now, today=now.date(),
        changed=None, excerpt=10,
    )
    assert whole == before(_MERGE_BEFORE_WHOLE)
    assert cut == before(_MERGE_BEFORE_EXCERPT)
