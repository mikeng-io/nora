"""`NoteMergeAdaptor` (design/dreamer-agent-loop/41 §7.3-7.8, DEC-202).

Every test runs against `InMemoryNoteStore` and `InMemoryDreamScheduleClock`,
the doubles `tests/contract/` binds to the SQL implementations, so a fold here
is the same compare-and-swap the database makes. The named tests of 41 §7.8
that belong to this adaptor are here; each says what reverting it reddens.
"""

from __future__ import annotations

import re
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
    PASS_CLOCK_NAME,
    NoteMergeAdaptor,
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
        adaptor_name=PASS_CLOCK_NAME, key=WS) == T0
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
