"""`NoteCuratorAdaptor` — the note tier's housekeeping pass.

The assertions here are mostly about what the curator REFUSES, because that is
where this design lives: merge eligibility is decided in SQL and the model is
asked only what a body says, so the interesting failures are all a model
reaching past the question it was given.
"""

from __future__ import annotations

import pytest

from nora.domain.notes import BODY_MAX_CHARS, NoteSupersedeReason
from nora.domain.ports import Proposal
from nora.language.note_curator import (
    CURATOR_ACTIONS,
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
                entities: tuple[str, ...] = ("grant",)):
    """`entities` defaults to a real anchor because a DATE-ONLY collision is
    refused (finding 3): an anchor is `(date, entity_set)` and in production
    the entity half is empty, so grouping on the date alone would merge
    «Dinner, 9 Sept» with «Ada's flight to Tokyo»."""
    return await store.create(
        workspace=WS, title=title, description="one line",
        uncertainty="undecided", closing_condition="somebody decides",
        established_in="conv-1", body=body, established_by="tag:miles",
        now_ms=NOW, anchor_date=anchor, anchor_entity_ids=entities,
    )


def _h(note) -> str:
    """The HANDLE a curator run rendered for this note.

    Tests must speak the model's language, not the store's: the model is shown
    ordinals and never an id, so a test passing an id is testing a path
    production cannot take. Resolved from  because the mapping is
    per-run — which is also what makes a handle from a previous tick unaskable.
    """
    return next(
        h for h, nid in _LAST_SHOWN[0].by_handle.items() if nid == note.id
    )


_LAST_SHOWN: list = []


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
    await _seed(store, title="Dinner, 9 Sept", anchor=20260909, entities=())
    await _seed(store, title="Ada's flight to Tokyo", anchor=20260909,
                entities=())
    metrics = Metrics()

    rendered = await _curator(store, metrics).inputs(key=WS, batch=())

    assert "<merge_group" not in rendered, "a date is not an occasion"
    assert metrics.get(
        "nora_note_curator_groups_refused_total",
        reason="no_entity_anchor") == 1


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
        {"note_id": _h(c, a), "merged_body": "lost it"},
    ))

    assert written == 0
    assert metrics.get(
        "nora_note_curator_dropped_total",
        reason="conflicting_merged_body") == 1
    head = await store.read(workspace=WS, note_id=a.id)
    assert head is not None
    assert head[1].body == "Ada can't do Fridays.", "the original stands"


# --- the label vocabularies, derived rather than hand-copied ---------------


def test_the_help_text_names_exactly_the_actions_the_code_emits():
    """A hand-written vocabulary in help text drifts the moment a branch adds
    an action — and this one already had: `retire_failed` and `retire_stale`
    were emitted for a round before the help text knew about them.

    Bound to the frozenset the code increments against, so the help text cannot
    fall behind without this failing. Checked at runtime rather than by types
    because `nora/language` is outside `make gate`'s mypy scope (#61)."""
    from nora.obs.metric_specs import _COUNTERS

    spec = next(
        c for c in _COUNTERS if c.name == "nora_note_curator_total"
    )
    for action in CURATOR_ACTIONS:
        assert action in spec.help_text, (
            f"{action!r} is emitted and the help text does not name it"
        )


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
    assert len(payloads) == 2
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
