"""`NoteCandidateAdaptor` — the note tier's create path (issue #98).

Mirrors `tests/unit/test_note_curator.py`'s shape: built on `InMemoryNoteStore`
(a real `DreamSink` plus the create-candidate surface, exercised by the shared
contract suite) rather than an ad-hoc fake, so an assertion here is about the
adaptor's own logic and not about a double that says yes to everything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from nora.agent.dreamer import AdaptorToolError, verify_tool_subsets
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import Proposal
from nora.language.note_candidates import (
    GATE_ACTIONS,
    NoteCandidateAdaptor,
    _adds_information,
    _CreateCandidate,
    _find_anchor_match,
    _parse_candidate,
)
from nora.obs.metrics import Metrics
from nora.testing.note_store import InMemoryNoteStore

WS = "friends"
SCOPE = "signal:g1"
NOW = 1_755_000_000_000
SHA = "d" * 64


def _row(row_id: int, body: str | None, *, ts: int = 1_000) -> TranscriptRow:
    return TranscriptRow(
        id=row_id, sender_id="p1", sender_name="Ada", ts=ts, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


def _proposal(payload: object, *, established_by: str = "dream:test") -> Proposal:
    return Proposal(
        payload=payload, instruction_sha256=SHA, model="deepseek-v4-flash",
        established_by=established_by,
    )


def _item(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Wedding venue",
        "description": "Where the September wedding is held is still open.",
        "body": "Two venues on the shortlist. Nobody has called either.",
        "closing_condition": "a venue is booked and confirmed",
        "anchor_date": 20260919,
        "message_ids": [1],
    }
    base.update(overrides)
    return base


def _adaptor(
    *, notes: InMemoryNoteStore | None = None, metrics: Metrics | None = None,
    workspace_for=lambda _: WS,
) -> tuple[NoteCandidateAdaptor, InMemoryNoteStore]:
    store = notes if notes is not None else InMemoryNoteStore()
    adaptor = NoteCandidateAdaptor(
        notes=store, candidates=store, workspace_for=workspace_for,
        metrics=metrics or Metrics(),
    )
    return adaptor, store


class _ExplodingStore:
    """A `_NoteStore`/`_CandidateStore` double that raises on every call —
    R-27's three-state discipline, exercised for `inputs`: a store that
    COULD NOT CHECK must propagate as `unavailable`, never degrade to an
    empty prompt or a silent `nothing_found`."""

    async def standing(self, *, workspace: str) -> Sequence[object]:
        raise ConnectionError("the notes database is unreachable")

    async def read(self, *, workspace: str, note_id: str):
        raise ConnectionError("the notes database is unreachable")

    async def propose(self, *, key: str, proposals, now_ms: int):
        raise ConnectionError("the notes database is unreachable")

    async def supersede(self, **kwargs: object):
        raise ConnectionError("the notes database is unreachable")

    async def record_create_candidate(self, **kwargs: object):
        raise ConnectionError("the notes database is unreachable")

    async def set_create_candidate_status(self, **kwargs: object):
        raise ConnectionError("the notes database is unreachable")


class _ExplodingStandingStore(InMemoryNoteStore):
    """A REAL `InMemoryNoteStore` whose `standing` alone raises — isolates
    `commit`'s gate READ (as opposed to the staging write, which succeeds
    normally) so the R-27 propagation can be pinned to that one call."""

    async def standing(self, *, workspace: str) -> Sequence[object]:
        raise ConnectionError("the notes database is unreachable")


# ---------------------------------------------------------------------------
# tool-free construction — the model never gets a write tool
# ---------------------------------------------------------------------------


def test_the_adaptor_holds_no_tools_at_all():
    """§4.1's cost decision: `note-candidates` could hold read-only awareness
    (it is scope-keyed), but its design keeps it tool-free anyway, the fact
    tier's own Phase A shape."""
    adaptor, _ = _adaptor()
    assert adaptor.tools == frozenset()


def test_the_empty_tool_set_clears_the_dreamers_own_ceiling_check():
    """Structural, not merely a bare assertion on `.tools`: run the SAME
    startup gate the Dreamer runtime refuses to start without
    (`nora.agent.dreamer.verify_tool_subsets`) against this adaptor, with a
    ceiling that holds real act verbs — an empty declared subset must clear
    it regardless of what the ceiling contains, because there is nothing to
    escape it with."""
    adaptor, _ = _adaptor()

    class _ToolSource:
        def read_only(self) -> frozenset[str]:
            return frozenset({"now", "web_search"})

    verify_tool_subsets([adaptor], tools=_ToolSource())  # must not raise


def test_a_declared_tool_would_be_refused_at_startup():
    """The instrument check for the test above: prove `verify_tool_subsets`
    actually reddens for THIS adaptor's shape if it ever grew a tool outside
    the ceiling, so the passing test above is not vacuous."""
    adaptor, _ = _adaptor()
    object.__setattr__(adaptor, "tools", frozenset({"send_reply"}))

    class _ToolSource:
        def read_only(self) -> frozenset[str]:
            return frozenset({"now"})

    with pytest.raises(AdaptorToolError):
        verify_tool_subsets([adaptor], tools=_ToolSource())


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


async def test_inputs_is_blank_for_an_empty_batch():
    adaptor, _ = _adaptor()
    assert await adaptor.inputs(key=SCOPE, batch=()) == ""


async def test_inputs_renders_the_batch_and_the_standing_note_index():
    adaptor, store = _adaptor()
    await store.create(
        workspace=WS, title="Wedding venue", description="Venue undecided",
        uncertainty="which venue", closing_condition="a venue is booked",
        established_in="conv-0", body="two on the shortlist",
        established_by="tag:miles", now_ms=NOW, anchor_date=20260919,
    )

    rendered = await adaptor.inputs(
        key=SCOPE, batch=(_row(41, "let's do Sai Kung this time"),),
    )

    assert "41 " in rendered and "let's do Sai Kung this time" in rendered
    assert "Wedding venue" in rendered
    assert "Venue undecided" in rendered


async def test_inputs_propagates_a_store_read_failure_rather_than_hiding_it():
    """R-27: a room with a message batch but an UNREACHABLE note index must
    not silently render as if there were no notes to check against — that
    is `unavailable` wearing `nothing_found`'s clothes."""
    adaptor, _ = _adaptor(notes=_ExplodingStore())

    with pytest.raises(ConnectionError):
        await adaptor.inputs(key=SCOPE, batch=(_row(1, "hi"),))


# ---------------------------------------------------------------------------
# accept — parsing and the message-id membership guard
# ---------------------------------------------------------------------------


def test_parse_candidate_accepts_a_well_formed_item():
    candidate = _parse_candidate(_item())
    assert candidate is not None
    assert candidate.title == "Wedding venue"
    assert candidate.anchor_date == 20260919
    assert candidate.anchor_entity_ids == ()
    assert candidate.message_ids == (1,)


@pytest.mark.parametrize("missing", ["title", "description", "body", "closing_condition"])
def test_parse_candidate_drops_a_missing_required_field(missing):
    item = _item()
    del item[missing]
    assert _parse_candidate(item) is None


def test_parse_candidate_drops_an_anchor_date_that_is_not_a_calendar_day():
    assert _parse_candidate(_item(anchor_date=2026)) is None
    assert _parse_candidate(_item(anchor_date=True)) is None, "bool is an int"


def test_parse_candidate_drops_an_empty_message_ids_list():
    assert _parse_candidate(_item(message_ids=[])) is None


def test_parse_candidate_accepts_free_text_anchor_entities():
    candidate = _parse_candidate(_item(anchor_entity_ids=["Ada", "Ben"]))
    assert candidate is not None
    assert candidate.anchor_entity_ids == ("Ada", "Ben")


async def test_accept_drops_a_candidate_citing_a_message_id_outside_the_batch():
    """Same defence `FactCandidateAdaptor.accept` keeps: a claim half-attributed
    to a message that does not exist is fabricated provenance, dropped whole."""
    adaptor, _ = _adaptor()
    await adaptor.inputs(key=SCOPE, batch=(_row(1, "hi"),))

    accepted = await adaptor.accept(
        key=SCOPE, items=[_item(message_ids=[1, 999])],
    )

    assert accepted == ()


async def test_accept_keeps_a_candidate_whose_ids_are_all_in_the_batch():
    adaptor, _ = _adaptor()
    await adaptor.inputs(key=SCOPE, batch=(_row(1, "hi"), _row(2, "there")))

    accepted = await adaptor.accept(key=SCOPE, items=[_item(message_ids=[1, 2])])

    assert len(accepted) == 1
    assert isinstance(accepted[0], _CreateCandidate)


# ---------------------------------------------------------------------------
# the deterministic gate — a pure function, tested directly
# ---------------------------------------------------------------------------


def test_find_anchor_match_finds_the_note_sharing_the_date():
    """The gate compares the anchor DATE alone (module docstring: the resolved
    entity half was removed with the entity registry on 2026-09-08)."""
    from nora.domain.notes import Note

    existing = Note(
        id="n1", workspace=WS, title="Wedding venue", description="d",
        uncertainty="u", closing_condition="c", established_in="conv-1",
        created_at=NOW, head_revision_id="r1", anchor_date=20260919,
    )
    other_day = replace(existing, id="n2", anchor_date=20260920)

    match = _find_anchor_match(
        anchor_date=20260919, standing=(existing, other_day),
    )

    assert match is existing


def test_find_anchor_match_never_matches_on_title_alone():
    """M10: two notes could share a title and have nothing to do with each
    other, or one could have several spellings — the key is the anchor date,
    never `title`."""
    from nora.domain.notes import Note

    same_title_different_day = Note(
        id="n1", workspace=WS, title="Wedding venue", description="d",
        uncertainty="u", closing_condition="c", established_in="conv-1",
        created_at=NOW, head_revision_id="r1", anchor_date=20261225,
    )

    match = _find_anchor_match(
        anchor_date=20260919, standing=(same_title_different_day,),
    )

    assert match is None


def test_find_anchor_match_never_reads_the_written_entities():
    """Two notes on one date match whatever free text their writers typed
    for entities — the strings are not a key in either direction (three
    spellings of one wedding; two unrelated 「wedding」s)."""
    from nora.domain.notes import Note

    existing = Note(
        id="n1", workspace=WS, title="Wedding venue", description="d",
        uncertainty="u", closing_condition="c", established_in="conv-1",
        created_at=NOW, head_revision_id="r1", anchor_date=20260919,
        anchor_entity_ids=("Gabe's wedding",),
    )

    assert _find_anchor_match(anchor_date=20260919, standing=(existing,)) is existing


def test_find_anchor_match_takes_the_oldest_note_on_the_date():
    """`standing` is oldest-first from either store, and the first match is
    the one taken — so a re-observation lands on one stable note across
    ticks rather than on whichever happened to be listed first."""
    from nora.domain.notes import Note

    older = Note(
        id="n1", workspace=WS, title="Dinner", description="d",
        uncertainty="u", closing_condition="c", established_in="conv-1",
        created_at=NOW, head_revision_id="r1", anchor_date=20260919,
    )
    newer = replace(older, id="n2", created_at=NOW + 1, head_revision_id="r2")

    assert _find_anchor_match(anchor_date=20260919, standing=(older, newer)) is older


# ---------------------------------------------------------------------------
# "adds information" — the update-vs-refuse rule
# ---------------------------------------------------------------------------


def test_adds_information_is_false_for_a_literal_restatement():
    existing = "Two venues on the shortlist. Nobody has called either."
    assert _adds_information(existing, existing) is False
    assert _adds_information(existing.upper(), existing) is False, "case-insensitive"
    assert _adds_information(
        "  Two   venues on the shortlist.   Nobody has called either.  ",
        existing,
    ) is False, "whitespace-insensitive"


def test_adds_information_is_true_for_genuinely_new_content():
    existing = "Two venues on the shortlist. Nobody has called either."
    updated = existing + " Ada ruled out the beach club — too far for her mum."
    assert _adds_information(updated, existing) is True


# ---------------------------------------------------------------------------
# commit — create, staging-before-deciding, and the gate's fail-closed limit
# ---------------------------------------------------------------------------


async def test_commit_creates_a_new_note_when_nothing_shares_its_anchor():
    adaptor, store = _adaptor()

    written = await adaptor.commit(
        key=SCOPE,
        proposals=(_proposal(_CreateCandidate(
            title="Wedding venue",
            description="Where the wedding is held is still open.",
            body="Two venues on the shortlist.",
            closing_condition="a venue is booked",
            anchor_date=20260919, anchor_entity_ids=(), message_ids=(1,),
        )),),
        now_ms=NOW,
    )

    assert written == 1
    standing = await store.standing(workspace=WS)
    assert len(standing) == 1
    assert standing[0].title == "Wedding venue"
    assert standing[0].uncertainty == standing[0].description, (
        "the widened description convention (design 33 §3) fills uncertainty too"
    )


async def test_a_created_note_carries_an_explicit_revisit_at():
    """The note-curator merge's §5.3 resolution (c), issue #99: a gate-created
    note's first wake must be a normal SCHEDULED wake, not a strike bootstrap
    — `record_wake`'s progress test finds no tag-path revision in a
    background-authored chain either way, so an unset `revisit_at` would
    charge a strike on a note nobody has had a chance to answer yet.

    Asserted against `nora.domain.notes.default_revisit_at` itself, not a
    hand-computed literal — the two callers (this adaptor and
    `NoteCuratorAdaptor.commit`) must agree on what "next revisit" means for
    one anchor shape, and a literal here could drift from that function
    without this test ever seeing it.
    """
    from nora.domain.notes import default_revisit_at

    adaptor, store = _adaptor()

    await adaptor.commit(
        key=SCOPE,
        proposals=(_proposal(_CreateCandidate(
            title="Wedding venue",
            description="Where the wedding is held is still open.",
            body="Two venues on the shortlist.",
            closing_condition="a venue is booked",
            anchor_date=20260919, anchor_entity_ids=(), message_ids=(1,),
        )),),
        now_ms=NOW,
    )

    [note] = await store.standing(workspace=WS)
    assert note.revisit_at == default_revisit_at(anchor_date=20260919, now_ms=NOW)
    assert note.revisit_at is not None


async def test_commit_stages_the_candidate_row_before_deciding():
    """The gate's first act is always to STAGE — the row exists and is
    settled to a terminal status, whatever the gate then decides."""
    adaptor, store = _adaptor()

    await adaptor.commit(
        key=SCOPE,
        proposals=(_proposal(_CreateCandidate(
            title="Wedding venue", description="d", body="b",
            closing_condition="c", anchor_date=20260919,
            anchor_entity_ids=(), message_ids=(1,),
        )),),
        now_ms=NOW,
    )

    [row] = store.create_candidates.values()
    assert row.status == "created"


async def test_commit_updates_the_standing_note_sharing_its_date():
    """A candidate on a date a standing note already carries becomes a
    REVISION of that note, not a second note — the gate compares the anchor
    date alone (2026-09-08), and this body adds something the head lacks.
    Until then this test pinned the opposite: the gate's entity half was
    always empty, it failed closed on that, and every candidate was a
    create."""
    adaptor, store = _adaptor()
    await store.create(
        workspace=WS, title="Wedding venue", description="d",
        uncertainty="u", closing_condition="c", established_in="conv-1",
        body="original arrangement", established_by="tag:miles", now_ms=NOW,
        anchor_date=20260919,
    )

    written = await adaptor.commit(
        key=SCOPE,
        proposals=(_proposal(_CreateCandidate(
            title="Wedding venue (again)", description="d2",
            body="a second sighting of the same date",
            closing_condition="c", anchor_date=20260919,
            anchor_entity_ids=(), message_ids=(2,),
        )),),
        now_ms=NOW + 1,
    )

    assert written == 1
    standing = await store.standing(workspace=WS)
    assert len(standing) == 1, "same date -> a revision, never a second note"
    [note] = standing
    head = await store.read(workspace=WS, note_id=note.id)
    assert head is not None
    assert head[1].body == "a second sighting of the same date"
    [row] = store.create_candidates.values()
    assert row.status == "created"


async def test_commit_raises_for_an_unresolvable_scope():
    adaptor, _ = _adaptor(workspace_for=lambda _: None)

    with pytest.raises(LookupError):
        await adaptor.commit(
            key=SCOPE,
            proposals=(_proposal(_CreateCandidate(
                title="t", description="d", body="b", closing_condition="c",
                anchor_date=20260919, anchor_entity_ids=(), message_ids=(1,),
            )),),
            now_ms=NOW,
        )


async def test_commit_propagates_a_store_failure_as_unavailable_not_nothing_found():
    """R-27, at `commit`'s own grain: a store that could not be READ during
    the gate must not be swallowed into "nothing written" — it must reach
    the runtime as a raised failure, which is what turns into `unavailable`
    one layer up."""
    adaptor, _ = _adaptor(notes=_ExplodingStandingStore())

    with pytest.raises(ConnectionError):
        await adaptor.commit(
            key=SCOPE,
            proposals=(_proposal(_CreateCandidate(
                title="t", description="d", body="b", closing_condition="c",
                anchor_date=20260919, anchor_entity_ids=(), message_ids=(1,),
            )),),
            now_ms=NOW,
        )


async def test_commit_defers_rather_than_settles_on_a_materialization_failure():
    """A store fault at the MATERIALIZE step (not the read) must leave the
    candidate row PENDING — not `refused` (a refusal is a decision about the
    proposal) and not `created` (nothing was written) — so the next tick
    retries it."""

    class _FailingPropose(InMemoryNoteStore):
        async def propose(self, *, key: str, proposals, now_ms: int):
            raise RuntimeError("the database is on fire")

    adaptor, store = _adaptor(notes=_FailingPropose())

    written = await adaptor.commit(
        key=SCOPE,
        proposals=(_proposal(_CreateCandidate(
            title="t", description="d", body="b", closing_condition="c",
            anchor_date=20260919, anchor_entity_ids=(), message_ids=(1,),
        )),),
        now_ms=NOW,
    )

    assert written == 0
    [row] = store.create_candidates.values()
    assert row.status == "pending", "left open for the next tick to retry"


async def test_commit_settles_a_reobserved_candidate_only_once():
    """Two ticks proposing the SAME occasion: the first materializes it, the
    second must not write a second note or re-decide the first row."""
    adaptor, store = _adaptor()
    candidate = _CreateCandidate(
        title="Wedding venue", description="d", body="b",
        closing_condition="c", anchor_date=20260919, anchor_entity_ids=(),
        message_ids=(1,),
    )

    first = await adaptor.commit(
        key=SCOPE, proposals=(_proposal(candidate),), now_ms=NOW,
    )
    second = await adaptor.commit(
        key=SCOPE, proposals=(_proposal(replace(candidate, message_ids=(2,))),),
        now_ms=NOW + 1000,
    )

    assert (first, second) == (1, 0)
    assert len(await store.standing(workspace=WS)) == 1


# ---------------------------------------------------------------------------
# the gate's action vocabulary — derived, not hand-copied
# ---------------------------------------------------------------------------


def test_gate_actions_vocabulary_matches_the_metric_help_text():
    """`nora.language.note_curator.CURATOR_ACTIONS`'s own guard, applied here:
    a hand-written vocabulary in help text drifts the moment a branch adds a
    value, so the check runs against the SET the code can actually emit."""
    import re

    from nora.obs.metric_specs import _COUNTERS

    spec = next(c for c in _COUNTERS if c.name == "nora_note_candidate_gate_total")
    for action in GATE_ACTIONS:
        assert re.search(rf"\b{re.escape(action)}\b", spec.help_text), (
            f"nora_note_candidate_gate_total{{action={action!r}}} is emitted "
            f"and the help text does not name it"
        )


# ---------------------------------------------------------------------------
# the rendered moment is the ROOM's, with its weekday (issue #118)
# ---------------------------------------------------------------------------


def test_a_message_moment_renders_in_the_rooms_zone_with_its_weekday():
    """Measured 2026-09-08: rendered as `2026-09-07 18:13:23 UTC`, the model
    resolved «今個禮拜六» to the 13th in two of ten samples. The same instant
    in the room's zone is Tuesday the 8th, and the weekday is a fact this
    process has — so it is rendered, not derived."""
    from zoneinfo import ZoneInfo

    from nora.language.note_candidates import _render_line

    row = _row(2816, "嚟緊今個禮拜六", ts=1_788_804_803_000)  # 2026-09-07T18:13:23Z

    hk = _render_line(row, room="ridgeline", tz=ZoneInfo("Asia/Hong_Kong"))
    utc = _render_line(row, room="ridgeline", tz=ZoneInfo("UTC"))

    assert hk.startswith("2816 [2026-09-08 Tue 02:13 HKT] Ada (ridgeline): "), hk
    assert utc.startswith("2816 [2026-09-07 Mon 18:13 UTC] Ada (ridgeline): "), utc


async def test_inputs_renders_moments_in_the_adaptors_zone():
    """The adaptor's own `timezone` collaborator reaches every rendered line —
    two adaptors 25 hours apart (Kiritimati +14, Niue -11) render different
    calendar days for one instant, the same property `test_composition`
    pins for the curator's `_today`."""
    from zoneinfo import ZoneInfo

    store = InMemoryNoteStore()
    row = _row(7, "食飯？", ts=1_788_804_803_000)
    ahead = NoteCandidateAdaptor(
        notes=store, candidates=store, workspace_for=lambda _: WS,
        timezone=ZoneInfo("Pacific/Kiritimati"),
    )
    behind = NoteCandidateAdaptor(
        notes=store, candidates=store, workspace_for=lambda _: WS,
        timezone=ZoneInfo("Pacific/Niue"),
    )

    a = await ahead.inputs(key=SCOPE, batch=(row,))
    b = await behind.inputs(key=SCOPE, batch=(row,))

    assert "[2026-09-08 Tue" in a, a
    assert "[2026-09-07 Mon" in b, b
