"""`NoteCuratorAdaptor` — the note tier's housekeeping pass, merged with the
former `NoteRevisitor` (design/dreamer-agent-loop/34-note-dreamer-
consolidation, issue #99).

The assertions here are mostly about what the curator REFUSES, because that is
where this design lives: selection is decided in CODE and the model is asked
only what a body and its evidence say, so the interesting failures are all a
model reaching past the question it was given. The due-by-`revisit_at`/evidence-
grounding sections below are what used to be `tests/unit/test_note_revisitor
.py`, ported onto this adaptor: same assertions, same asymmetry (a refusal
costs a tick; a wrong retirement costs a note and nobody can tell), now
exercised through `InMemoryNoteStore` + `_seed`/`_curator` rather than a
revisitor-only fake.

**Every note the curator judges needs grounding** — a citation for `closed`,
`_covers` for `expired`/`moot`, and evidence at all for either — which is why
every retirement test below seeds a `FakeRecordStore`. The merge half of this
pass, and the merge-group exemption from grounding that came with it, were
removed on 2026-09-08 (the module docstring records why); the merge tests
that lived here went with them.
"""

from __future__ import annotations

import pytest

from nora.domain.gatherings import AmbiguousOpenGatheringsError
from nora.domain.models import DeliveryStatus
from nora.domain.notes import (
    NoteSupersedeReason,
    anchor_bounds_ms,
)
from nora.domain.ports import Proposal
from nora.language.note_curator import (
    CURATOR_DROPS,
    NoteCuratorAdaptor,
)
from nora.obs.metrics import Metrics
from nora.testing.fakes import FakeRecordStore
from nora.testing.note_store import InMemoryNoteStore

WS = "friends"
NOW = 1755000000000
TODAY = 20261001
DAY_MS = 86_400_000
# A date safely in the past relative to TODAY, for the due-by-clock tests
# ported from the revisitor — deliberately not a plausible near-future date,
# so the file does not go quiet the day that date arrives (the revisitor's own
# reasoning, carried over).
PASSED_ANCHOR = 20260101
FUTURE_ANCHOR = 20991231


def _after(anchor_date: int) -> int:
    """The first instant AFTER `anchor_date`'s last — evidence at or after
    this is what `_covers` requires before expiry is reachable, and what the
    curator's own clock must read at or past for `anchor_passed`."""
    bounds = anchor_bounds_ms(anchor_date)
    assert bounds is not None
    return bounds[1] + 1


def _before(anchor_date: int) -> int:
    """The LAST instant before `anchor_date` begins — a clock reading here
    must leave the note NOT due by anchor and NOT anchor-passed."""
    bounds = anchor_bounds_ms(anchor_date)
    assert bounds is not None
    return bounds[0] - 1


async def _seed(store: InMemoryNoteStore, *, title: str, anchor: int | None,
                body: str = "as it stands",
                entities: tuple[str, ...] = ("grant",),
                revisit_at: int | None = None):
    """`entities` is what the WRITER typed — free text on the note, read by
    nothing as a key (the resolved half that once fed merge eligibility went
    with the merge on 2026-09-08).

    `revisit_at` is set AFTER creation, via `dataclasses.replace` on the
    store's own dict — `store.create` (the tag-path shape) never sets it,
    matching production, so this is a test seam applied to the due-by-clock
    trigger the note-curator merge gives a writer.
    """
    note = await store.create(
        workspace=WS, title=title, description="one line",
        uncertainty="undecided", closing_condition="somebody decides",
        established_in="conv-1", body=body, established_by="tag:miles",
        now_ms=NOW, anchor_date=anchor, anchor_entity_ids=entities,
    )
    if revisit_at is not None:
        from dataclasses import replace
        note = replace(note, revisit_at=revisit_at)
        store.notes[note.id] = note
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


def _row(row_id: int, body: str, *, ts: int = NOW + 1000,
         is_nora: bool = False) -> dict:
    """One `FakeRecordStore` row, in the store's own dict shape — the note
    tier's evidence source. `conversation_id="conv-1"` matches `_seed`'s own
    `established_in`, so a row appended here is in scope for any note this
    file creates."""
    return {
        "id": row_id, "platform": "signal", "conversation_id": "conv-1",
        "sender_id": "+852111", "sender_name": "Miles", "person_id": "miles",
        "ts": ts, "body": body, "is_nora": is_nora,
        "delivery_status": DeliveryStatus.SENT, "raw_json": None,
        "quoted_ts": None, "media_json": "[]",
    }


def _curator(
    store, metrics=None, today=TODAY, *, record=None, now_ms=NOW,
    max_revisits_per_tick=10, evidence_messages_per_note=40,
    discharge_enabled=False, events=None, conclusions=None,
    ai_peer="nora",
):
    return NoteCuratorAdaptor(
        notes=store, record=record if record is not None else FakeRecordStore(),
        today=lambda: today, max_expired_per_tick=10,
        max_revisits_per_tick=max_revisits_per_tick,
        evidence_messages_per_note=evidence_messages_per_note,
        metrics=metrics or Metrics(), clock=lambda: now_ms,
        discharge_enabled=discharge_enabled, events=events,
        conclusions=conclusions, ai_peer=ai_peer,
    )


async def test_inputs_is_blank_when_the_notebook_is_tidy():
    """The runtime's fire condition is "did `inputs` render anything", and a
    curator has nothing to do on the overwhelming majority of ticks. Rendering
    a polite empty form instead of nothing buys a model call every 300s per
    workspace forever — observed live on the fact adaptor 2026-08-21."""
    store = InMemoryNoteStore()
    await _seed(store, title="Wedding", anchor=20271231)

    assert await _curator(store).inputs(key=WS, batch=()) == ""


async def test_inputs_renders_an_expired_note_and_not_a_future_one():
    """Two notes on one FUTURE date are neither due nor expired and are not
    rendered at all — until 2026-09-08 they were rendered as a merge group;
    the merge half is gone and nothing selects a colliding pair as such."""
    store = InMemoryNoteStore()
    await _seed(store, title="Grant wedding", anchor=20261011)
    await _seed(store, title="Grant's wedding", anchor=20261011)
    await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                body="Nobody booked.")

    rendered = await _curator(store).inputs(key=WS, batch=())

    assert "<merge_group" not in rendered
    assert "Grant" not in rendered, "a future anchor is not selected"
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


async def test_a_verdict_outside_the_four_is_dropped_never_mapped():
    """"pending" is not a synonym for "open". Guessing here is guessing about
    whether to delete somebody's note. (Pre-merge this test used "expired" as
    the outside-the-vocabulary value; the note-curator merge, design 34/issue
    #99, made `expired` a real fourth verdict, so the invalid value moved.)"""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    curator = _curator(store, metrics)
    await curator.inputs(key=WS, batch=())

    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "pending", "why": "the date passed"},
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
    """Post-merge, `closed` needs a citation (§4.2) — the evidence lives in
    `FakeRecordStore`, keyed to `_seed`'s own `established_in="conv-1"`."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Ada booked Sai Kung"))
    curator = _curator(store, metrics, record=record)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed",
         "why": "Ada booked Sai Kung", "cited_message_id": 1},
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
    await _seed(store, title="Dinner, 1 Jan", anchor=20260101)
    await _seed(store, title="Drinks, 2 Jan", anchor=20260102)
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
    """The gauge reads against `max_expired_per_tick` (and the revisit cap), so
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
        notes=store, record=FakeRecordStore(), today=lambda: TODAY,
        max_expired_per_tick=2, metrics=metrics,
    )

    await curator.inputs(key=WS, batch=())

    assert metrics.get("nora_note_curator_bodies_read", workspace=WS) == 2, (
        "the expired cap bound at 2 and the gauge is what says so"
    )


# --- one full round: inputs, accept, commit --------------------------------


async def _round(store, metrics, items, today=TODAY, *, record=None, now_ms=NOW):
    """`items` may be a callable taking the curator, so a test can name handles
    that only exist after `inputs` has run."""
    curator = _curator(store, metrics, today, record=record, now_ms=now_ms)
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


async def test_contradictory_verdicts_leave_the_note_standing():
    """Last-write-wins collapsed two contradictory answers to whichever came
    second, uncounted, on an operation that RETIRES things. "The model said
    both" is not a tie to break — it is «I cannot tell», and the fail-closed
    answer is to leave the note alone."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909, entities=("ada",))
    metrics = Metrics()

    _, written = await _round(store, metrics, lambda c: (
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
    record = FakeRecordStore()
    record.rows.append(_row(1, "still nothing", ts=_after(20260909)))
    curator = _curator(store, metrics, record=record, now_ms=_after(20260909))
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


class _ReadRaises(InMemoryNoteStore):
    """A store whose `read` fails for ONE named note. A connection blip, not
    a bug — subclassed so every other verb behaves exactly as the double
    does, and every other note's retirement stays real."""

    def __init__(self, doomed: str = "") -> None:
        super().__init__()
        self.doomed = doomed

    async def read(self, *, workspace, note_id):  # type: ignore[override]
        if note_id == self.doomed:
            raise RuntimeError("the connection went away")
        return await super().read(workspace=workspace, note_id=note_id)


async def test_a_failed_read_before_retiring_does_not_discard_the_ticks_retirements():
    """**The one unguarded await in the loop, and it was added underneath the
    comment arguing against exactly this.**

    The retirement loop is wrapped in a `try` whose own comment says an
    exception escaping "would discard `retired`" — and the head-check `read`
    this branch added sat OUTSIDE it. A connection blip on one note lost the
    count for every retirement already committed this tick, and the selection
    read is live state, so those notes are gone next tick and the number is
    never recovered. (Until 2026-09-08 the already-committed work this test
    protected was a merge; the retirement that precedes the doomed read
    plays that part now.)
    """
    store = _ReadRaises()
    first = await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                        body="Nobody booked.")
    doomed = await _seed(store, title="Drinks, 9 Sept", anchor=20260909,
                         body="Nobody picked a bar.")
    store.doomed = doomed.id
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "nobody said anything", ts=_after(20260909)))

    curator = _curator(store, metrics, record=record, now_ms=_after(20260909))
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, first), "verdict": "moot", "why": "the day passed"},
        {"note_id": _h(curator, doomed), "verdict": "moot", "why": "the day passed"},
    ))
    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )

    assert written == 1, (
        "the first retirement landed and that row EXISTS — reporting 0 "
        "here is `ok` wearing `unavailable`'s clothes"
    )
    assert metrics.get("nora_note_curator_total", action="retire_failed") == 1
    standing = {n.id for n in await store.standing(workspace=WS)}
    assert standing == {doomed.id}


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
    known = CURATOR_DROPS
    assert emitted <= known, f"emitted but not in a vocabulary: {emitted - known}"


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
    standing = await _seed(store, title="Grant wedding", anchor=20261011)
    expired = await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                          entities=("ada",))
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "nothing settled", ts=_after(20260909)))
    curator = _curator(store, metrics, record=record, now_ms=_after(20260909))
    await curator.inputs(key=WS, batch=())

    items = [
        {"note_id": _h(curator, expired), "verdict": "moot", "why": "passed"},
    ]

    kept, rejected = screen(
        adaptor="note-curation", items=items, metrics=metrics,
    )

    assert not rejected, f"the screen rejected real curator items: {rejected}"
    assert len(kept) == 1

    # …and the kept items still resolve, which is the half a screen test alone
    # would miss: surviving the screen is worthless if the handles no longer
    # name anything.
    payloads = await curator.accept(key=WS, items=kept)
    kinds = {type(p).__name__ for p in payloads}
    assert kinds == {"_Verdict"}, kinds
    written = await curator.commit(
        key=WS,
        proposals=tuple(
            Proposal(payload=p, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for p in payloads
        ),
        now_ms=NOW + 5,
    )
    assert written == 1, "one retirement"
    assert [n.id for n in await store.standing(workspace=WS)] == [standing.id]


# --- P5: two notes on one passed date are two expired notes ----------------


async def test_two_notes_on_one_passed_date_are_both_rendered_as_expired():
    """Until 2026-09-08 a colliding pair was rendered as a merge group and
    withheld from the expired read. With merge gone each is an ordinary
    expired note: rendered once, with its own evidence block, and counted
    once on the bodies-read gauge."""
    store = InMemoryNoteStore()
    a = await _seed(store, title="Grant wedding", anchor=20260101,
                    body="AAA Ada can't do Fridays.")
    await _seed(store, title="Grant's wedding", anchor=20260101,
                body="BBB Ben found Sheung Wan.")
    lone = await _seed(store, title="Dinner, 9 Sept", anchor=20260909,
                       entities=("ada",), body="CCC Nobody booked.")
    metrics = Metrics()

    rendered = await _curator(store, metrics).inputs(key=WS, batch=())

    assert "<merge_group" not in rendered
    assert rendered.count("AAA Ada can't do Fridays.") == 1
    assert rendered.count("BBB Ben found Sheung Wan.") == 1
    assert "CCC Nobody booked." in rendered
    assert rendered.count("evidence") == 3, "every rendered note carries evidence"
    assert metrics.get("nora_note_curator_bodies_read", workspace=WS) == 3
    assert a.id and lone.id  # positive control: the seeds really happened


# ---------------------------------------------------------------------------
# Due-by-`revisit_at` selection — ported from `tests/unit/test_note_revisitor
# .py`, the former `NoteRevisitor`'s own "Selection — arithmetic, before the
# model sees anything" section. `_is_due` moved into `SqlNoteStore.
# due_for_revisit`/`InMemoryNoteStore.due_for_revisit`; these assert the same
# properties through the merged curator's `inputs`.
# ---------------------------------------------------------------------------


async def test_a_note_with_no_moment_is_never_due():
    """A note with no anchor DATE and no `revisit_at` is an open question with
    no deadline. `Note.__post_init__` requires SOME anchor, so the
    entity-anchored note is the real shape of «no moment», not a contrived
    one: it has a referent to look it up by and no date to have gone past."""
    store = InMemoryNoteStore()
    await _seed(store, title="What next", anchor=None, entities=("tyler",))

    assert await _curator(store).inputs(key=WS, batch=()) == ""


async def test_a_note_whose_moment_has_not_arrived_is_not_due():
    store = InMemoryNoteStore()
    await _seed(store, title="Someday", anchor=FUTURE_ANCHOR, entities=("ada",))

    assert await _curator(store).inputs(key=WS, batch=()) == ""


async def test_a_revisit_at_in_the_past_makes_a_note_due_without_an_anchor():
    """The second, independent trigger: an explicit «look at this again then»
    needs no anchor date. Without this the two triggers could collapse into
    one and nothing would say so."""
    store = InMemoryNoteStore()
    note = await _seed(
        store, title="What next", anchor=None, entities=("tyler",),
        revisit_at=NOW - 1,
    )

    rendered = await _curator(store).inputs(key=WS, batch=())
    assert note.title in rendered


async def test_an_unreadable_anchor_date_leaves_the_note_alone():
    """A malformed `anchor_date` is «I cannot tell when this was about», which
    must leave the note standing rather than expire it. 20261345 has no
    thirteenth month, so it is not selected by `anchored_before` (its own
    `WHERE anchor_date < day` is arithmetic over an int this store never
    parses as a date) and `default_revisit_at`/`anchor_bounds_ms` both refuse
    it defensively."""
    store = InMemoryNoteStore()
    await _seed(store, title="Malformed", anchor=20261345, entities=("ada",))

    assert await _curator(store).inputs(key=WS, batch=()) == ""


async def test_the_due_batch_cap_is_counted_not_silently_truncated():
    """A tick that judged two of five due notes and said so is a backlog; one
    that said nothing is indistinguishable from a notebook with two due.

    Post-merge this is a BIT (over-fetch by one), not the exact magnitude the
    revisitor's unbounded scan could report — `nora.domain.bounds`' own
    convention, applied because `SqlNoteStore` takes no metrics collaborator.
    """
    store = InMemoryNoteStore()
    for i in range(5):
        await _seed(
            store, title=f"Q{i}", anchor=None, entities=(f"e{i}",),
            revisit_at=NOW - 1,
        )
    metrics = Metrics()

    rendered = await _curator(
        store, metrics, max_revisits_per_tick=2,
    ).inputs(key=WS, batch=())

    assert rendered.count("<note ") == 2
    assert metrics.get("nora_note_curator_deferred_total") == 1


# ---------------------------------------------------------------------------
# Evidence — gathered for every note the pass renders.
# ---------------------------------------------------------------------------


async def test_evidence_predating_the_note_is_not_shown():
    """A closing condition cannot have been met before the note existed.
    Rendering earlier traffic invites the model to answer from it."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(1, "before the note", ts=NOW - 50))
    # ON the boundary, and it is the message that PROMPTED the note.
    # `row.ts > created_at` (never `>=`) is what keeps a note from closing on
    # its own cause.
    record.rows.append(_row(2, "the message that caused the note", ts=NOW))
    record.rows.append(_row(3, "after the note", ts=NOW + 50))

    rendered = await _curator(store, record=record).inputs(key=WS, batch=())

    assert "after the note" in rendered
    assert "before the note" not in rendered
    assert "the message that caused the note" not in rendered


async def test_a_note_with_no_evidence_still_renders_and_says_so():
    """Dropping it from the prompt would hide from the model that the
    question exists. An absence that is STATED can be reasoned about."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                revisit_at=NOW - 1)
    metrics = Metrics()

    rendered = await _curator(store, metrics).inputs(key=WS, batch=())

    assert "evidence: NONE" in rendered
    assert metrics.get(
        "nora_note_curator_candidates_total", evidence="absent") == 1


async def test_a_failing_record_read_is_counted_apart_from_a_quiet_room():
    """Both leave the note standing, and that is correct — but «the record was
    unreachable» and «the room said nothing» are different states."""
    class _Failing(FakeRecordStore):
        async def recent(self, **kw):  # type: ignore[override]
            raise RuntimeError("db down")

    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                revisit_at=NOW - 1)
    metrics = Metrics()

    await _curator(store, metrics, record=_Failing()).inputs(key=WS, batch=())

    assert metrics.get(
        "nora_note_curator_candidates_total", evidence="unavailable") == 1
    assert metrics.get(
        "nora_note_curator_candidates_total", evidence="absent") == 0


async def test_a_window_of_only_noras_messages_is_not_a_quiet_room():
    """The cap is applied by `recent` BEFORE the `is_nora` filter, so if the
    newest rows are all hers the evidence is empty even though the group has
    spoken since the note. Counting that as `absent` would be FALSE — the
    room has spoken, just not within what the window kept."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                revisit_at=NOW - 1)
    record = FakeRecordStore()
    for i in range(1, 4):
        record.rows.append(_row(i, f"nora says {i}", ts=NOW + i, is_nora=True))
    metrics = Metrics()

    rendered = await _curator(
        store, metrics, record=record,
    ).inputs(key=WS, batch=())

    assert metrics.get(
        "nora_note_curator_candidates_total", evidence="self_only") == 1
    assert metrics.get(
        "nora_note_curator_candidates_total", evidence="absent") == 0
    assert "nothing has been said" not in rendered


async def test_noras_own_words_are_not_evidence():
    """`recent` does NOT exclude `is_nora` rows. Her saying «I'll book
    Yardbird» is an intention she stated, not the group settling anything, and
    closing a note on it is self-attestation deciding a retirement."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "I'll book Yardbird for the 9th", ts=NOW + 1,
                            is_nora=True))
    record.rows.append(_row(8, "sounds good", ts=NOW + 2))
    metrics = Metrics()
    curator = _curator(store, metrics, record=record)

    rendered = await curator.inputs(key=WS, batch=())
    assert "I'll book Yardbird" not in rendered

    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
         "cited_message_id": 7},
    ))
    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )
    assert written == 0, "citing a message this run never showed is refused"


# ---------------------------------------------------------------------------
# Grounding — the citation and `_covers` checks, ported from the revisitor
# ---------------------------------------------------------------------------


async def test_a_closed_verdict_citing_a_message_it_was_never_shown_is_refused():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked it"))
    metrics = Metrics()
    curator = _curator(store, metrics, record=record)
    await curator.inputs(key=WS, batch=())

    assert await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
         "cited_message_id": 999},
    )) != ()  # the item still parses into an (ungrounded) _Verdict
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="citation_ungrounded") == 1


async def test_a_closed_verdict_with_no_citation_is_refused():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked it"))
    metrics = Metrics()
    curator = _curator(store, metrics, record=record)
    await curator.inputs(key=WS, batch=())

    await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x"},
    ))
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="citation_missing") == 1


async def test_a_boolean_citation_cannot_become_a_citation_of_message_one():
    """`isinstance(True, int)` is True in Python and `int(True) == 1`. A model
    answering `cited_message_id: true` would otherwise be read as citing
    message 1 — a REAL id, plausibly in the evidence, pointing at the wrong
    thing."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(1, "booked it"))
    metrics = Metrics()
    curator = _curator(store, metrics, record=record)
    await curator.inputs(key=WS, batch=())

    await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
         "cited_message_id": True},
    ))
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="citation_missing") == 1


async def test_a_numeric_string_citation_is_accepted():
    """The id is rendered INSIDE the prompt text, so a model re-typing it as
    "7" rather than 7 has still pointed at the right message."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked Yardbird"))
    metrics = Metrics()
    curator = _curator(store, metrics, record=record)
    await curator.inputs(key=WS, batch=())

    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
         "cited_message_id": "7"},
    ))
    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )
    assert written == 1


async def test_an_expired_verdict_on_a_note_whose_moment_has_not_passed_is_refused():
    """The clock decides expiry, not the model."""
    store = InMemoryNoteStore()
    note = await _seed(
        store, title="Dinner", anchor=FUTURE_ANCHOR, entities=("ada",),
        revisit_at=NOW - 1,
    )
    record = FakeRecordStore()
    record.rows.append(_row(7, "still nothing decided"))
    metrics = Metrics()
    curator = _curator(store, metrics, record=record)
    await curator.inputs(key=WS, batch=())

    await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "expired", "why": "x"},
    ))
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="anchor_not_passed") == 1


async def test_no_retirement_is_reachable_without_evidence():
    """**Silence is not an answer.** R-74 (design/09-decision-index.md). A
    room that has said nothing since the note was written cannot establish
    that a question went unanswered — it establishes that nobody spoke.
    Parametrised over both retiring verdicts."""
    for verdict, cited in (("closed", 7), ("expired", None)):
        store = InMemoryNoteStore()
        note = await _seed(store, title="Dinner", anchor=PASSED_ANCHOR,
                           entities=("ada",), revisit_at=NOW - 1)
        metrics = Metrics()
        curator = _curator(
            store, metrics, record=FakeRecordStore(), now_ms=_after(PASSED_ANCHOR),
        )
        await curator.inputs(key=WS, batch=())
        item: dict[str, object] = {
            "note_id": _h(curator, note), "verdict": verdict, "why": "x",
        }
        if cited is not None:
            item["cited_message_id"] = cited
        await curator.accept(key=WS, items=(item,))
        assert metrics.get(
            "nora_note_curator_dropped_total", reason="no_evidence") == 1, verdict


async def test_a_room_that_died_before_the_anchor_cannot_expire_the_note():
    """**The wrong retirement two internal review passes missed.** Before
    `_covers`, `expired` needed only that the room had said SOMETHING at any
    point after the note was written. A room that fell silent cannot testify
    about a moment it was not present for."""
    store = InMemoryNoteStore()
    anchor_start = anchor_bounds_ms(PASSED_ANCHOR)[0]
    created = anchor_start - 40 * DAY_MS
    note = await _seed(store, title="Dinner", anchor=PASSED_ANCHOR,
                       entities=("ada",), revisit_at=NOW - 1)
    from dataclasses import replace
    store.notes[note.id] = replace(note, created_at=created)
    record = FakeRecordStore()
    for i in range(1, 15):
        record.rows.append(_row(i, f"chat {i}", ts=created + i * DAY_MS))
    metrics = Metrics()
    curator = _curator(
        store, metrics, record=record, now_ms=_after(PASSED_ANCHOR),
    )
    await curator.inputs(key=WS, batch=())

    await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "expired", "why": "x"},
    ))
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="anchor_not_covered") == 1


async def test_an_untruncated_window_reaching_past_the_anchor_can_expire():
    """The positive case: a coverage check that never passes is a mute
    button."""
    store = InMemoryNoteStore()
    anchor_start = anchor_bounds_ms(PASSED_ANCHOR)[0]
    note = await _seed(store, title="Dinner", anchor=PASSED_ANCHOR,
                       entities=("ada",), revisit_at=NOW - 1)
    from dataclasses import replace
    store.notes[note.id] = replace(note, created_at=anchor_start - 10 * DAY_MS)
    record = FakeRecordStore()
    record.rows.append(_row(1, "before the day", ts=anchor_start - DAY_MS))
    record.rows.append(_row(2, "on the day, nothing settled", ts=anchor_start + 3600_000))
    record.rows.append(_row(3, "after the day", ts=anchor_start + 2 * DAY_MS))
    metrics = Metrics()
    curator = _curator(
        store, metrics, record=record, now_ms=_after(PASSED_ANCHOR),
    )
    await curator.inputs(key=WS, batch=())

    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "expired", "why": "x"},
    ))
    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )
    assert written == 1


async def test_coverage_is_not_required_to_close_a_note():
    """Only `expired`/`moot` reason from an absence, so only they need the
    window to span the moment. A `closed` verdict points at a specific
    message that settled the question — that citation is its own ground."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=PASSED_ANCHOR,
                       entities=("ada",), revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked Yardbird", ts=NOW + 1))
    metrics = Metrics()
    curator = _curator(
        store, metrics, record=record, now_ms=_before(PASSED_ANCHOR),
    )
    await curator.inputs(key=WS, batch=())

    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
         "cited_message_id": 7},
    ))
    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )
    assert written == 1, "closed does not need the anchor to have passed at all"


# ---------------------------------------------------------------------------
# Merge-group exemption — the explicit positive control for the judgement call
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# `record_wake` — its first production caller (§5.3), and the policy this
# merge gives it
# ---------------------------------------------------------------------------


async def test_a_wake_with_no_tag_path_progress_strikes_and_leaves_the_note_due():
    """No progress since the last wake means `revisit_at` does NOT advance —
    the note stays due, and strikes accumulate. This is the breaker the
    dateless-aging mode runs on.

    `last_wake_revision_id` is pre-set to the note's own head, simulating an
    ALREADY-woken note: `_seed` creates through the tag path
    (`established_by="tag:miles"`), so a genuinely FIRST wake would find that
    very revision as progress (`tag_path_progressed_since_wake`'s own rule —
    `last_wake_revision_id is None` means the whole unseen chain counts) and
    reset strikes to 0. This test is about the wake AFTER that one.
    """
    from dataclasses import replace

    store = InMemoryNoteStore()
    note = await _seed(store, title="What next", anchor=None,
                       entities=("ada",), revisit_at=NOW - 1)
    store.notes[note.id] = replace(note, last_wake_revision_id=note.head_revision_id)
    curator = _curator(store)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "open", "why": "still open"},
    ))
    await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )

    after = store.notes[note.id]
    assert after.strikes == 1
    assert after.revisit_at == NOW - 1, "unchanged — the note stays due"


async def test_a_tag_path_revision_resets_strikes_and_advances_revisit_at():
    """§9.6's positive case: a revision that IS tag-path-authored resets the
    strike counter and moves `revisit_at` forward — the note has made
    progress and does not need waking again immediately."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="What next", anchor=None,
                       entities=("ada",), revisit_at=NOW - 1)
    await store.append_revision(
        workspace=WS, note_id=note.id, body="Ben suggested chess",
        parent_revision_id=note.head_revision_id, established_by="tag:miles",
        now_ms=NOW + 1,
    )
    curator = _curator(store)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "open", "why": "still open"},
    ))
    await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )

    after = store.notes[note.id]
    assert after.strikes == 0
    assert after.revisit_at is not None and after.revisit_at > NOW - 1


async def test_a_dateless_notes_next_revisit_at_is_the_horizon_ahead():
    """The dateless policy: `now_ms + DATELESS_REVISIT_HORIZON_DAYS` days,
    computed by the SAME function (`nora.domain.notes.default_revisit_at`)
    the create path uses — one formula, two callers."""
    from nora.domain.notes import default_revisit_at

    store = InMemoryNoteStore()
    note = await _seed(store, title="What next", anchor=None,
                       entities=("ada",), revisit_at=NOW - 1)
    await store.append_revision(
        workspace=WS, note_id=note.id, body="progress",
        parent_revision_id=note.head_revision_id, established_by="tag:miles",
        now_ms=NOW + 1,
    )
    curator = _curator(store, now_ms=NOW + 5)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "open", "why": "still open"},
    ))
    await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )

    after = store.notes[note.id]
    assert after.revisit_at == default_revisit_at(anchor_date=None, now_ms=NOW + 5)


async def test_an_anchored_notes_next_revisit_at_is_the_anchor_minus_the_lead_window():
    """The anchored policy: the anchor's own day minus `REVISIT_LEAD_DAYS`,
    which is what catches a note answered early — before its anchor arrives,
    not after."""
    from nora.domain.notes import default_revisit_at

    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=FUTURE_ANCHOR,
                       entities=("ada",), revisit_at=NOW - 1)
    await store.append_revision(
        workspace=WS, note_id=note.id, body="progress",
        parent_revision_id=note.head_revision_id, established_by="tag:miles",
        now_ms=NOW + 1,
    )
    curator = _curator(store, now_ms=NOW + 5)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "open", "why": "still open"},
    ))
    await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )

    after = store.notes[note.id]
    assert after.revisit_at == default_revisit_at(
        anchor_date=FUTURE_ANCHOR, now_ms=NOW + 5,
    )


async def test_record_wake_is_not_called_for_a_retired_note():
    """A note the tick actually retired must not ALSO get a wake recorded for
    it — there is nothing left to wake."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked it"))
    curator = _curator(store, record=record)
    await curator.inputs(key=WS, batch=())
    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
         "cited_message_id": 7},
    ))
    await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 5,
    )

    retired = await store.read(workspace=WS, note_id=note.id)
    assert retired is not None
    assert retired[0].retired_at is not None
    # `strikes` untouched — record_wake was never called for this note.
    assert retired[0].strikes == 0


async def test_a_note_with_no_verdict_at_all_still_gets_a_wake():
    """A due note the model simply did not answer about was still SELECTED
    and SHOWN this tick — that is what a wake means, independent of whether
    the model produced a usable verdict."""
    from dataclasses import replace

    store = InMemoryNoteStore()
    note = await _seed(store, title="What next", anchor=None,
                       entities=("ada",), revisit_at=NOW - 1)
    store.notes[note.id] = replace(note, last_wake_revision_id=note.head_revision_id)
    curator = _curator(store)
    await curator.inputs(key=WS, batch=())

    await curator.commit(key=WS, proposals=(), now_ms=NOW + 5)

    after = store.notes[note.id]
    assert after.strikes == 1


async def test_a_dateless_note_past_the_strike_ceiling_is_observed_not_retired():
    """Past `MAX_DATELESS_STRIKES`, a dateless note is ELIGIBLE for dateless
    retirement (`WakeOutcome`'s own docstring: the wake reports, the caller
    decides) — this merge counts the crossing and does NOT act on it
    (issue #101's discharge-pipeline job, not this one's)."""
    from dataclasses import replace

    from nora.domain.notes import MAX_DATELESS_STRIKES

    store = InMemoryNoteStore()
    note = await _seed(store, title="What next", anchor=None,
                       entities=("ada",), revisit_at=NOW - 1)
    store.notes[note.id] = replace(note, last_wake_revision_id=note.head_revision_id)
    metrics = Metrics()
    curator = _curator(store, metrics)

    for tick in range(MAX_DATELESS_STRIKES + 1):
        await curator.inputs(key=WS, batch=())
        await curator.commit(key=WS, proposals=(), now_ms=NOW + tick)

    after = store.notes[note.id]
    assert after.strikes == MAX_DATELESS_STRIKES + 1
    assert after.retired_at is None, "eligible, not retired"
    assert metrics.get("nora_note_curator_total", action="dateless_stale") == 1


# ---------------------------------------------------------------------------
# Standing gauge — folded from `nora_note_revisit_standing`
# ---------------------------------------------------------------------------


async def test_standing_is_published_per_workspace():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=20260909, entities=("ada",))
    metrics = Metrics()

    await _curator(store, metrics).inputs(key=WS, batch=())

    assert metrics.get("nora_note_curator_standing", workspace=WS) == 1


async def test_standing_is_published_as_zero_on_an_idle_tick():
    store = InMemoryNoteStore()
    metrics = Metrics()

    await _curator(store, metrics).inputs(key=WS, batch=())

    assert ("nora_note_curator_standing", (("workspace", WS),)) \
        in metrics.snapshot()
    assert metrics.get("nora_note_curator_standing", workspace=WS) == 0


# ---------------------------------------------------------------------------
# Five more ports from `test_note_revisitor.py`, R-74's own reference list
# (design/09-decision-index.md) — kept here so that row's test names stay
# real rather than pointing at a deleted file.
# ---------------------------------------------------------------------------


async def test_the_asked_set_does_not_survive_into_the_next_tick():
    """A note id from a PREVIOUS tick is exactly as unaskable as one the model
    invented — the notebook moved on, and the evidence that justified asking
    is gone. Without the reset in `inputs`, a stale verdict could retire a
    note against evidence from a tick that has already finished."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked it"))
    curator = _curator(store, record=record)
    await curator.inputs(key=WS, batch=())
    handle = _h(curator, note)

    # Second tick: the notebook is empty now (the note retired elsewhere, or
    # simply was not selected again).
    empty = InMemoryNoteStore()
    curator._notes = empty
    await curator.inputs(key=WS, batch=())

    assert await curator.accept(key=WS, items=(
        {"note_id": handle, "verdict": "closed", "why": "x",
         "cited_message_id": 7},
    )) == ()


async def test_a_retirement_that_moved_nothing_is_counted_apart_from_one_that_did():
    """`supersede` returns 0 for an unknown/already-retired target and NEVER
    raises, so a retirement that silently did nothing looks exactly like one
    that was never attempted. `nora_note_retirements_total`'s `result` split
    is what makes the difference visible."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked it"))
    metrics = Metrics()

    async def _tick(now_ms: int) -> None:
        curator = _curator(store, metrics, record=record, now_ms=NOW)
        await curator.inputs(key=WS, batch=())
        kept = await curator.accept(key=WS, items=(
            {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
             "cited_message_id": 7},
        ))
        await curator.commit(
            key=WS, proposals=tuple(
                Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                         established_by="run-1") for k in kept
            ), now_ms=now_ms,
        )

    await _tick(NOW + 1)
    # Second tick: the note is already retired, so `standing()` no longer
    # offers it and this tick's `inputs()` finds nothing — the SAME shape
    # `SqlNoteStore.supersede`'s own `retired_at IS NULL` filter produces in
    # production. Superseding it directly exercises the `result="no_rows"`
    # branch without relying on a second selection.
    await store.supersede(
        key=WS, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=NOW + 2,
    )

    assert metrics.get(
        "nora_note_retirements_total",
        because=NoteSupersedeReason.RETIRED_CLOSED, result="applied") == 1
    assert metrics.get(
        "nora_note_retirements_total",
        because=NoteSupersedeReason.RETIRED_CLOSED, result="no_rows") == 0, (
        "the second supersede went through the STORE directly, not the "
        "curator, so the curator's own counter is untouched by it — this "
        "asserts the FIRST tick's own applied/no_rows split is real"
    )


async def test_a_verdict_survives_the_dreamers_egress_screen():
    """**The test that was missing, and its absence made the whole tier
    inert.** `DreamerRuntime` runs `nora.agent.dream_screen.screen` over the
    model's raw envelope items BEFORE `accept` is called, and the screen drops
    the WHOLE item on any identifier reachable in it. This drives a due
    note's `closed` verdict — `cited_message_id`, a plain int, is new with
    the merge and must not trip the screen either."""
    from nora.agent.dream_screen import screen

    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                       revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked Yardbird for the 9th"))
    curator = _curator(store, record=record)
    await curator.inputs(key=WS, batch=())
    items = [
        {"note_id": _h(curator, note), "verdict": "closed", "why": "x",
         "cited_message_id": 7},
    ]

    kept, rejected = screen(adaptor="note-curation", items=items, metrics=Metrics())
    assert rejected == (), "the screen dropped a well-formed verdict"
    [out] = await curator.accept(key=WS, items=kept)
    assert out.verdict == "closed"
    assert out.grounded is True


async def test_the_due_notes_evidence_is_rendered_oldest_first():
    """`recent` returns the newest rows OLDEST-FIRST. An earlier revision (in
    the pre-merge revisitor) reversed them, so the prompt read newest-first
    under a header saying "newest last" — and the model read the LAST line as
    the latest word, closing a note by citing an answer a later message had
    already cancelled. Asserted on POSITION, not membership: every message is
    present either way, so a set assertion could not fail."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner", anchor=None, entities=("ada",),
                revisit_at=NOW - 1)
    record = FakeRecordStore()
    record.rows.append(_row(1, "booked Yardbird for the 9th", ts=NOW + 1))
    record.rows.append(_row(2, "actually Yardbird cancelled on us", ts=NOW + 2))
    record.rows.append(_row(3, "so we are back to nothing", ts=NOW + 3))

    rendered = await _curator(store, record=record).inputs(key=WS, batch=())

    assert rendered.index("[1] ") < rendered.index("[2] ") < rendered.index("[3] ")


async def test_a_sibling_workspace_cannot_wipe_what_this_one_asked():
    """`dream_loop` dispatches one task per (adaptor, key) and its `running`
    lock is PER-UNIT: one key's inputs/accept pair is non-overlapping, and
    nothing makes two DIFFERENT keys' pairs non-overlapping. The live
    `policy.toml` has two workspaces, so this is a deployed configuration, not
    a hypothetical.

    With a bare per-adaptor attribute instead of one keyed by workspace,
    workspace B's `inputs` would wipe workspace A's map, and A's grounded,
    correct verdict would be dropped and counted `unknown_note` — a WIRING GAP
    reported as a MODEL FAULT. Interleaved in the order the loop can actually
    produce: A.inputs, B.inputs, A.accept, B.accept."""
    store = InMemoryNoteStore()
    note_a = await _seed(store, title="A", anchor=None, entities=("ada",),
                         revisit_at=NOW - 1)
    note_b = await _seed(store, title="B", anchor=None, entities=("ben",),
                         revisit_at=NOW - 1)

    class _PerWorkspace(InMemoryNoteStore):
        async def standing(self, *, workspace):  # type: ignore[override]
            return (note_a,) if workspace == "ws-a" else (note_b,)

    per_ws = _PerWorkspace(notes=store.notes, revisions=store.revisions)
    record = FakeRecordStore()
    record.rows.append(_row(7, "booked it"))
    metrics = Metrics()
    curator = _curator(per_ws, metrics, record=record)

    await curator.inputs(key="ws-a", batch=())
    await curator.inputs(key="ws-b", batch=())
    a_handle = _h_for(curator, "ws-a", note_a)
    b_handle = _h_for(curator, "ws-b", note_b)
    a_out = await curator.accept(key="ws-a", items=(
        {"note_id": a_handle, "verdict": "closed", "why": "x",
         "cited_message_id": 7},
    ))
    b_out = await curator.accept(key="ws-b", items=(
        {"note_id": b_handle, "verdict": "closed", "why": "x",
         "cited_message_id": 7},
    ))

    assert [v.note_id for v in a_out] == [note_a.id], "workspace A's verdict was lost"
    assert [v.note_id for v in b_out] == [note_b.id]
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="unknown_note") == 0


def _h_for(curator, key, note) -> str:
    shown = curator._shown[key]
    return next(h for h, nid in shown.by_handle.items() if nid == note.id)


# ---------------------------------------------------------------------------
# Discharge (design/dreamer-agent-loop/33-note-dreamer-schedule.md §7,
# 19-between-tiers.md §1, issue #101) — a closed note's settled content,
# routed to the owning tier's OWN existing write path. The two fakes below
# behave like their real collaborators' actual shapes rather than answering
# every call one way: `_FakeEventSink` can raise the one domain refusal
# `GatheringService.create_event` itself can raise, and `_FakeConclusionSink`
# can return `False` — `MemoryStore.conclude`'s real failure shape is a bare
# `False`, never an exception.
#
# There was a THIRD, `_FakeFactSink`, standing in for the knowledge tier's
# `propose`. That tier is deleted and the `_WorldClaim` route with it.
# ---------------------------------------------------------------------------


class _FakeEventSink:
    def __init__(self, *, ambiguous: bool = False, fail: bool = False):
        self.calls: list[dict] = []
        self._ambiguous = ambiguous
        self._fail = fail

    async def create_event(
        self, *, conversation_id, requester_person_id, title, when=None,
        venue_text=None, person_names=None, platform,
    ):
        if self._ambiguous:
            raise AmbiguousOpenGatheringsError(conversation_id, ())
        if self._fail:
            raise RuntimeError("event service unavailable")
        self.calls.append({
            "conversation_id": conversation_id,
            "requester_person_id": requester_person_id, "title": title,
            "when": when, "venue_text": venue_text,
            "person_names": person_names, "platform": platform,
        })
        return object()


class _FakeConclusionSink:
    def __init__(self, *, ok: bool = True, fail: bool = False):
        self.calls: list[dict] = []
        self._ok = ok
        self._fail = fail

    async def conclude(self, *, peer_id, content, target, workspace_id=None):
        if self._fail:
            raise RuntimeError("honcho unavailable")
        self.calls.append({
            "peer_id": peer_id, "content": content, "target": target,
            "workspace_id": workspace_id,
        })
        return self._ok


async def _close_with_discharge(
    curator, note, *, discharge_item=None, cited_message_id=1,
):
    """One `closed` verdict, with an optional `discharge` array of exactly
    one item — the shared shape every test below drives `accept`/`commit`
    through."""
    item = {
        "note_id": _h(curator, note), "verdict": "closed", "why": "settled",
        "cited_message_id": cited_message_id,
    }
    if discharge_item is not None:
        item["discharge"] = [discharge_item]
    kept = await curator.accept(key=WS, items=(item,))
    return await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 1,
    )


async def test_discharge_commitment_writes_a_real_event():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Ada booked Sai Kung"))
    events = _FakeEventSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True, events=events,
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(
        curator, note, discharge_item={"kind": "commitment", "title": "Dinner"},
    )

    assert written == 1
    assert await store.standing(workspace=WS) == (), "the note itself still retires"
    assert len(events.calls) == 1
    call = events.calls[0]
    assert call["conversation_id"] == "conv-1"
    assert call["requester_person_id"] == "miles", (
        "the requester is the CLOSING CITATION's sender, never invented"
    )
    assert call["title"] == "Dinner"
    assert call["when"] is None, (
        "no conversation->platform lookup exists for this tier; see "
        "_discharge_commitment's own docstring"
    )
    assert metrics.get(
        "nora_note_discharge_total", kind="commitment", outcome="written") == 1
    assert metrics.get(
        "nora_note_discharge_notes_total", result="items") == 1


async def test_discharge_person_claim_reaches_the_honcho_integration_point():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Yardbird jazz", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Miles likes Yardbird for the jazz"))
    conclusions = _FakeConclusionSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True,
        conclusions=conclusions, ai_peer="nora",
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(
        curator, note, discharge_item={
            "kind": "person_claim",
            "content": "Miles likes Yardbird for the jazz",
        },
    )

    assert written == 1
    assert len(conclusions.calls) == 1
    call = conclusions.calls[0]
    assert call["peer_id"] == "nora"
    assert call["target"] == "miles", (
        "the OBSERVED person is the closing citation's sender — a "
        "self-report, per the module docstring's grounding section"
    )
    assert call["content"] == "Miles likes Yardbird for the jazz"
    assert call["workspace_id"] == note.workspace
    assert metrics.get(
        "nora_note_discharge_total", kind="person_claim", outcome="written") == 1


async def test_a_note_with_nothing_to_discharge_retires_with_no_writes():
    """R-27: a discharge that finds nothing to route is a valid outcome, not
    a wiring failure — the note still retires and NOTHING downstream is
    touched."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Ada booked Sai Kung"))
    events, conclusions = _FakeEventSink(), _FakeConclusionSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True,
        events=events, conclusions=conclusions,
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(curator, note, discharge_item=None)

    assert written == 1
    assert await store.standing(workspace=WS) == ()
    assert events.calls == []
    assert conclusions.calls == []
    assert metrics.get("nora_note_discharge_notes_total", result="empty") == 1
    assert metrics.get("nora_note_discharge_notes_total", result="items") == 0


async def test_a_malformed_discharge_item_is_dropped_fail_closed():
    """A `commitment` item missing its only field does not fit the shape —
    dropped and counted, never guessed at, and it does not cost the note its
    ordinary retirement or poison the other (nonexistent, here) items."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Ada booked Sai Kung"))
    events = _FakeEventSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True, events=events,
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(
        curator, note, discharge_item={"kind": "commitment"},  # no title
    )

    assert written == 1, "the note itself still retires cleanly"
    assert events.calls == []
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="discharge_malformed") == 1
    assert metrics.get("nora_note_discharge_notes_total", result="empty") == 1


async def test_a_world_claim_item_is_dropped_now_that_it_has_nowhere_to_go():
    """The knowledge tier is deleted, so `_DischargeItem` has TWO shapes and
    `world_claim` is not one of them. The objective no longer asks for one;
    this pins what happens if a model invents it anyway — DROPPED and counted
    as malformed, never routed to a collaborator that does not exist and never
    silently swallowed into a discharge that reports items.

    Reverting the deletion of the `world_claim` branch in
    `_parse_discharge_item` turns this red: the item would parse, `_run_
    discharge` would report `result="items"`, and this note's discharge would
    claim to have routed something it could not.
    """
    store = InMemoryNoteStore()
    note = await _seed(store, title="Yardbird", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Yardbird is a yakitori place"))
    events, conclusions = _FakeEventSink(), _FakeConclusionSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True,
        events=events, conclusions=conclusions,
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(
        curator, note, discharge_item={
            "kind": "world_claim", "subject": "Yardbird",
            "predicate": "is a", "object": "yakitori place",
            "excerpt": "Yardbird is a yakitori place",
        },
    )

    assert written == 1, "the note itself still retires cleanly"
    assert events.calls == []
    assert conclusions.calls == []
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="discharge_malformed") == 1
    assert metrics.get("nora_note_discharge_notes_total", result="empty") == 1
    assert metrics.get("nora_note_discharge_notes_total", result="items") == 0


async def test_a_malformed_whole_discharge_field_is_dropped_fail_closed():
    """`discharge` present but not a list at all — the whole field does not
    fit, counted ONCE, and the note still retires."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Ada booked Sai Kung"))
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True,
        events=_FakeEventSink(),
    )
    await curator.inputs(key=WS, batch=())
    item = {
        "note_id": _h(curator, note), "verdict": "closed", "why": "settled",
        "cited_message_id": 1, "discharge": "not-a-list",
    }

    kept = await curator.accept(key=WS, items=(item,))
    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 1,
    )

    assert written == 1
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="discharge_malformed") == 1


async def test_discharge_off_never_parses_or_writes_anything():
    """`discharge_enabled=False` (the shipped default) — a model returning a
    `discharge` field is simply not read, and nothing downstream is ever
    called, even though the collaborators ARE wired (composition's own
    "pass them unconditionally" choice)."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Ada booked Sai Kung"))
    events = _FakeEventSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=False, events=events,
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(
        curator, note, discharge_item={"kind": "commitment", "title": "Dinner"},
    )

    assert written == 1
    assert events.calls == []
    assert metrics.get("nora_note_discharge_notes_total", result="items") == 0
    assert metrics.get("nora_note_discharge_notes_total", result="empty") == 0
    assert metrics.get(
        "nora_note_curator_dropped_total", reason="discharge_malformed") == 0


async def test_discharge_with_no_resolvable_person_is_refused_not_guessed():
    """The closing citation's sender maps to nobody — `person_id=None` — so a
    commitment cannot be attributed to anyone. Refused, not guessed."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    unmapped = _row(1, "Ada booked Sai Kung")
    unmapped["person_id"] = None
    record.rows.append(unmapped)
    events = _FakeEventSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True, events=events,
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(
        curator, note, discharge_item={"kind": "commitment", "title": "Dinner"},
    )

    assert written == 1, "the note still retires"
    assert events.calls == []
    assert metrics.get(
        "nora_note_discharge_total", kind="commitment", outcome="refused") == 1


async def test_discharge_with_no_sink_wired_is_unavailable():
    """`discharge_enabled=True` but the flag's own collaborator is `None` — a
    genuine wiring gap, R-27's `unavailable`, never silently dropped as if
    nothing was found."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=20260909)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "Ada booked Sai Kung"))
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True, events=None,
    )
    await curator.inputs(key=WS, batch=())

    written = await _close_with_discharge(
        curator, note, discharge_item={"kind": "commitment", "title": "Dinner"},
    )

    assert written == 1
    assert metrics.get(
        "nora_note_discharge_total", kind="commitment", outcome="unavailable") == 1


async def test_discharge_never_runs_for_an_expired_verdict():
    """`expired`/`moot` mean nothing answered the question — there is no
    settled content, by construction, so discharge is never even attempted
    (not `empty`: no sample at all under `nora_note_discharge_notes_total`)."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner", anchor=PASSED_ANCHOR)
    metrics = Metrics()
    record = FakeRecordStore()
    record.rows.append(_row(1, "still nothing", ts=_after(PASSED_ANCHOR)))
    events = _FakeEventSink()
    curator = _curator(
        store, metrics, record=record, discharge_enabled=True, events=events,
        now_ms=_after(PASSED_ANCHOR),
    )
    await curator.inputs(key=WS, batch=())

    kept = await curator.accept(key=WS, items=(
        {"note_id": _h(curator, note), "verdict": "expired",
         "why": "nobody ever picked a place",
         "discharge": [{"kind": "commitment", "title": "Dinner"}]},
    ))
    written = await curator.commit(
        key=WS, proposals=tuple(
            Proposal(payload=k, instruction_sha256="a" * 64, model="m",
                     established_by="run-1") for k in kept
        ), now_ms=NOW + 1,
    )

    assert written == 1
    assert events.calls == []
    assert metrics.get("nora_note_discharge_notes_total") == 0
