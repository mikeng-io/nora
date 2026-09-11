"""Shared contract for `DreamAdaptor`. Every implementation runs every test.

Shipped with ONE implementation — a fake — for the reason
`tests/contract/test_dream_sink_contract.py` states for the same situation: a
port declared with nothing exercising it is a shape nobody has checked is
satisfiable. Another joins by adding one `IMPLEMENTATIONS` entry.

**THE GAP THIS FILE USED TO DECLARE IS CLOSED (2026-09-11, issue #63).** The
knowledge tier's three adaptors were the real entries here and went with that
tier, leaving one fake; the note tier's two — `NoteCandidateAdaptor` and
`NoteCuratorAdaptor` — had never been in this map at all, so the suite
measured the SHAPE and no production behaviour, which is CLAUDE.md's "if
reverting the code under test doesn't turn the test red, the test is
decorative" written as a dict with one key. All three shipped adaptors are
entries now, and `test_every_registered_implementation_is_exercised` is what
stops the map shrinking back in silence — a shrink leaves the suite green and
faster, which is the only kind nobody notices.

**The rule this file is built against, inherited from the sink suite where it
was learned four times over:** anything the shared suite needs that is not on
`DreamAdaptor` itself must be supplied PER IMPLEMENTATION, never hard-coded
once and assumed to fit whoever joins next. Two things need it here:

1. **Legal envelope items.** `accept` takes
   `Sequence[Mapping[str, object]]` — one adaptor's item shape is not
   another's, and neither is legal for the other. So an entry
   carries `make_items()` (a batch this adaptor parses cleanly) and
   `make_malformed_item()` (one it must DROP), never one shared literal.
2. **Reading back what `commit` wrote.** `DreamAdaptor` has no read verb —
   the sink is the adaptor's own collaborator and deliberately not a port
   attribute — so an entry carries `written(adaptor)` returning however many
   rows that adaptor's own sink is holding.

**What is asserted here is the port's behaviour, never a tier's vocabulary.**
`accept` drops rather than raises; `commit(key, ())` is legal and distinct from
not calling `commit` at all; `tools` narrows the read-only ceiling rather than
widening it; `objective` names a file that actually exists. A test that asserted
what a term or a predicate looked like would be the one-tier-vocabulary defect
the sink suite found in itself, rebuilt here.

**`_FakeAdaptor` stays HERE rather than in `nora/testing/`**, the same rule
that binds `_FakeDreamSink` to its suite: `nora/testing/fakes.py` stands in for
production implementations at OTHER call sites, and this one's only job is
exercising this contract.
"""

from __future__ import annotations

import contextlib
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nora.agent.dream_screen import screen
from nora.agent.dreamer import AdaptorGrant, proactive_grant
from nora.agent.registry import ToolRegistry
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.notes import anchor_bounds_ms
from nora.domain.ports import DreamAdaptor, Proposal
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.language.note_curator import NoteCuratorAdaptor
from nora.language.proactive import ProactiveAdaptor, WorkspaceRoom
from nora.obs.metrics import Metrics
from nora.testing import InMemoryNoteStore
from nora.testing.fakes import FakeRecordStore
from nora.testing.reminders import FakeReminderStore

DREAMER_DIR = Path(__file__).resolve().parents[2] / "nora" / "dreamer"

KEY = "signal:g1"
SHA = "b" * 64


# ---------------------------------------------------------------------------
# the fake: an adaptor with a value type of its own, and a sink to write it to
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Gloss:
    """The fake's own value type. It exists so `accept` has something real to
    refuse a malformed item against — an adaptor whose parse accepted anything
    would make every drop test below vacuous."""

    term: str
    sense: str


@dataclass
class _CountingSink:
    """The fake adaptor's sink. Not `_FakeDreamSink` from the sibling suite:
    that one stands for the PORT's genericity and this one stands for a tier
    that knows its own value, which is the property `commit` is tested for."""

    rows: list[Proposal] = field(default_factory=list)
    empty_commits: int = 0

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> Sequence[str]:
        if not proposals:
            # The decay path: an empty propose is a REAL call a sink may act
            # on, and counting it here is what lets the contract assert the
            # difference between "looked, found nothing" and "never looked".
            self.empty_commits += 1
            return ()
        start = len(self.rows)
        self.rows.extend(proposals)
        return tuple(f"{key}:{i}" for i in range(start, len(self.rows)))

    async def supersede(
        self, *, key: str, target: str, replaced_by: str | None,
        because: str, source: str | None, now_ms: int,
    ) -> int:
        return 0


class _FakeAdaptor:
    name = "fake-gloss"
    key_kind = "scope"
    uses_watermark = True
    # A real objective file, not an invented name: the contract asserts that an
    # adaptor's objective RESOLVES, and a fake pointing at a name nobody ships
    # would make that test pass by never being able to fail.
    objective = "note-curation"
    envelope_key = "observations"
    tools = frozenset({"load_transcript", "now"})
    #: `DreamAdaptor.store_calls_per_tick` (issue #60) — the runtime reads it
    #: to bound `inputs` and `commit`. One sink call in `commit`, none in
    #: `inputs`; declared rather than defaulted because the port requires it
    #: and `test_an_implementation_satisfies_the_protocol` reads it.
    store_calls_per_tick = 1

    def __init__(self, sink: _CountingSink) -> None:
        self._sink = sink

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        return "\n".join(f"{row.id}: {row.body or ''}" for row in batch)

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        parsed: list[object] = []
        for item in items:
            term, sense = item.get("term"), item.get("sense")
            if not isinstance(term, str) or not isinstance(sense, str):
                continue                      # DROPPED, never raised
            parsed.append(_Gloss(term=term, sense=sense))
        return tuple(parsed)

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        return len(await self._sink.propose(key=key, proposals=proposals, now_ms=now_ms))


def _build_fake() -> tuple[_FakeAdaptor, _CountingSink]:
    sink = _CountingSink()
    return _FakeAdaptor(sink), sink


def _fake_items() -> tuple[Mapping[str, object], ...]:
    return (
        {"term": "big walk", "sense": "the co-op puzzle game"},
        {"term": "gourd", "sense": "a collectible in that game"},
    )


def _fake_malformed_item() -> Mapping[str, object]:
    return {"term": 7, "sense": None}         # wrong types for both fields


def _fake_written(_adaptor: object, sink: object) -> int:
    assert isinstance(sink, _CountingSink)
    return len(sink.rows)


def _fake_empty_commits(_adaptor: object, sink: object) -> int:
    assert isinstance(sink, _CountingSink)
    return sink.empty_commits


@dataclass(frozen=True)
class _Impl:
    """One implementation's entry.

    A dataclass rather than the sibling suite's positional tuple: that file's
    own comment records an entry growing from one field to six and an
    overloaded `None` having to be split out, and a named field is what makes
    the seventh addition cheap instead of a re-read of every call site.
    """

    build: Callable[[], tuple[object, object]]
    make_items: Callable[[], Sequence[Mapping[str, object]]]
    make_malformed_item: Callable[[], Mapping[str, object]]
    written: Callable[[object, object], int]
    #: `None` when this adaptor's `commit` reaches no sink at all. Not a skip
    #: and not a shrug: the deleted entity-resolution adaptors called
    #: `attach_alias` once per resolution and had no `DreamSink` to hand an
    #: empty batch to, so «the empty commit reaches the sink» was a property
    #: they could not have rather than one they failed. The field says which,
    #: so a new adaptor has to answer the question rather than inherit an
    #: answer.
    empty_commits: Callable[[object, object], int] | None = None
    #: Run after `build` and before any contract test. `accept` on an adaptor
    #: whose parse is HANDLE-BASED is meaningless without the render that
    #: assigned the handles (the deleted entity-resolution adaptors were the
    #: case).
    prepare: Callable[[object], Awaitable[None]] | None = None
    #: The adaptor's `AdaptorGrant` (design/dreamer-agent-loop/39 D3) — the
    #: SAME object the composition root hands the runtime, never a copy. The
    #: default is no grant, which is every adaptor but one.
    grant: AdaptorGrant = field(default_factory=AdaptorGrant)


# -- the proactive adaptor (design/dreamer-agent-loop/39, DEC-162) ------------
#
# The first PRODUCTION adaptor in this map. Its backing is the in-memory note
# store its sink writes wakes to; `written` counts notes woken, which is what
# `commit` returns for it — its rows are written by `remind` inside the loop,
# not by the sink. `prepare` seeds two open notes and renders `inputs` once,
# because `accept` is HANDLE-based and a handle is meaningless before the
# render that assigned it (the same reason the field exists).

_PROACTIVE_WS = KEY


def _build_proactive() -> tuple[ProactiveAdaptor, InMemoryNoteStore]:
    notes = InMemoryNoteStore()
    adaptor = ProactiveAdaptor(
        notes=notes, standing=None, reminders=FakeReminderStore(), memory=None,
        rooms_of=lambda ws: (
            WorkspaceRoom(platform="signal", conversation_id="g1", label="G1", home=True),
        ) if ws == _PROACTIVE_WS else (),
        ai_peer="nora", clock=lambda: datetime(2026, 9, 9, 0, 0, tzinfo=UTC),
        timezone=ZoneInfo("Asia/Hong_Kong"),
    )
    return adaptor, notes


async def _prepare_proactive(adaptor: object) -> None:
    assert isinstance(adaptor, ProactiveAdaptor)
    notes = adaptor._notes
    assert isinstance(notes, InMemoryNoteStore)
    for n, title in enumerate(("Dinner venue", "Taipei trip")):
        await notes.create(
            workspace=_PROACTIVE_WS, title=title, description="open",
            uncertainty="where", closing_condition="settled", established_in="g1",
            body="open corner", established_by="tag:miles", now_ms=1_000 + n,
            anchor_date=20260912,
        )
    await adaptor.inputs(key=_PROACTIVE_WS, batch=())


def _proactive_items() -> tuple[Mapping[str, object], ...]:
    return (
        {"note": "1", "action": "remind", "why": "Saturday is near"},
        {"note": "2", "action": "decline", "why": "nothing to raise"},
    )


def _proactive_malformed_item() -> Mapping[str, object]:
    return {"note": "9", "action": "maybe", "why": 7}


def _proactive_written(_adaptor: object, notes: object) -> int:
    assert isinstance(notes, InMemoryNoteStore)
    return sum(1 for n in notes.notes.values() if n.last_wake_revision_id is not None)


# -- the note tier's two adaptors (design/dreamer-agent-loop/33, /34) --------
#
# **They joined on 2026-09-11 and this file's own docstring had been asking for
# them since the knowledge tier went.** A contract suite exercising one fake
# measures the SHAPE and no production behaviour; worse for THIS port, the two
# properties an adaptor is most likely to get wrong — the handle scheme and the
# egress screen — are exactly the ones a fake cannot have.
#
# Both are keyed on `KEY` so the workspace an entry seeds is the workspace the
# shared tests tick. `NoteCandidateAdaptor` is scope-keyed and translates
# through `workspace_for`; `NoteCuratorAdaptor` is workspace-keyed and the two
# are the same string here, which is legal and keeps the fixtures readable.

_NOTE_WS = KEY
_NOTE_ROOM = "conv-1"
#: An anchor safely in the past, and deliberately not a plausible near date —
#: a fixture pinned to next month goes quiet the month after (the note
#: curator's own suite makes this argument and it is inherited here).
_PASSED_ANCHOR = 20260101
_CURATOR_TODAY = 20261001


def _after(anchor_date: int) -> int:
    """The first instant after `anchor_date`'s last — what `_covers` needs
    before an `expired`/`moot` verdict can ever be grounded."""
    bounds = anchor_bounds_ms(anchor_date)
    assert bounds is not None
    return bounds[1] + 1


def _evidence_row(row_id: int, body: str, *, ts: int) -> dict[str, object]:
    """One `FakeRecordStore` row in the store's own dict shape, in the room
    the seeded notes name — so it is in scope as evidence for them."""
    return {
        "id": row_id, "platform": "signal", "conversation_id": _NOTE_ROOM,
        "sender_id": "p1", "sender_name": "Ada", "person_id": "ada",
        "ts": ts, "body": body, "is_nora": False,
        "delivery_status": DeliveryStatus.SENT, "raw_json": None,
        "quoted_ts": None, "media_json": "[]",
    }


# -- note-candidates --------------------------------------------------------


def _build_note_candidates() -> tuple[NoteCandidateAdaptor, InMemoryNoteStore]:
    store = InMemoryNoteStore()
    return (
        NoteCandidateAdaptor(
            notes=store, candidates=store,
            workspace_for=lambda scope: _NOTE_WS if scope == KEY else None,
            label_for=lambda scope: "Zone 42" if scope == KEY else None,
            description_for=lambda _scope: None,
            metrics=Metrics(),
        ),
        store,
    )


async def _prepare_note_candidates(adaptor: object) -> None:
    """Render `inputs` over the batch whose ids `make_items` cites.

    `accept` drops any candidate naming a `message_ids` entry outside THIS
    run's batch — invented provenance is worse than missing provenance — so
    the render is not scene-setting, it is the half that makes the parse
    possible at all. Same reason `prepare` exists for the proactive adaptor.
    """
    assert isinstance(adaptor, NoteCandidateAdaptor)
    rows = tuple(
        TranscriptRow(
            id=n, sender_id="p1", sender_name="Ada", ts=1_000 + n,
            body=body, is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        )
        for n, body in ((1, "邊度食好"), (2, "九月十九號個婚禮仲未揀場"))
    )
    await adaptor.inputs(key=KEY, batch=rows)


def _note_candidate_items() -> tuple[Mapping[str, object], ...]:
    """Two candidates on DIFFERENT anchor dates.

    Same date would be one occasion by the create gate's own rule — the second
    would land as a revision to the first's note rather than a second note —
    and `test_commit_writes_what_it_was_given` would then be asserting the
    update arm while reading as the create one.
    """
    return (
        {
            "title": "Where dinner is",
            "description": "Three places named, nobody picked one.",
            "body": "Ada suggested two, Ben a third. Nobody has booked.",
            "closing_condition": "somebody names the place",
            "anchor_date": 20260919,
            "message_ids": [1],
        },
        {
            "title": "Wedding venue",
            "description": "The September wedding has no venue yet.",
            "body": "Two venues shortlisted, neither called.",
            "closing_condition": "a venue is booked and confirmed",
            "anchor_date": 20260920,
            "message_ids": [2],
        },
    )


def _note_candidate_malformed_item() -> Mapping[str, object]:
    #  `anchor_date` below the eight-digit floor AND a non-string title: the
    #  parse drops the whole item rather than filling either in.
    return {
        "title": 7, "description": None, "body": "",
        "closing_condition": "", "anchor_date": 1, "message_ids": [],
    }


def _note_candidate_written(_adaptor: object, store: object) -> int:
    assert isinstance(store, InMemoryNoteStore)
    return len(store.notes)


# -- note-curation ----------------------------------------------------------


def _build_note_curator() -> tuple[NoteCuratorAdaptor, InMemoryNoteStore]:
    store = InMemoryNoteStore()
    record = FakeRecordStore()
    record.rows.append(
        _evidence_row(1, "冇人講落", ts=_after(_PASSED_ANCHOR)),
    )
    return (
        NoteCuratorAdaptor(
            notes=store, record=record,
            today=lambda: _CURATOR_TODAY,
            max_expired_per_tick=10,
            metrics=Metrics(),
            clock=lambda: _after(_PASSED_ANCHOR),
        ),
        store,
    )


async def _prepare_note_curator(adaptor: object) -> None:
    """Two notes whose anchor has passed, then the render that hands them
    ordinal handles.

    The handles are what `make_items` speaks, and that is the whole point:
    the model is shown `### note 1`, never a uuid, because the runtime's own
    egress screen drops any item carrying one. A fixture that passed ids would
    be exercising a path production cannot take — which is exactly how this
    adaptor passed eighteen tests while every real item was rejected.
    """
    assert isinstance(adaptor, NoteCuratorAdaptor)
    notes = adaptor._notes
    assert isinstance(notes, InMemoryNoteStore)
    for n, title in enumerate(("Dinner, 1 Jan", "Wedding venue")):
        await notes.create(
            workspace=_NOTE_WS, title=title, description="one line",
            uncertainty="undecided", closing_condition="somebody decides",
            established_in=_NOTE_ROOM, body="as it stands",
            established_by="tag:miles", now_ms=1_000 + n,
            anchor_date=_PASSED_ANCHOR, anchor_entity_ids=("ada",),
        )
    await adaptor.inputs(key=_NOTE_WS, batch=())


def _note_curator_items() -> tuple[Mapping[str, object], ...]:
    return (
        {"note_id": "1", "verdict": "moot", "why": "the date has passed"},
        {"note_id": "2", "verdict": "moot", "why": "the date has passed"},
    )


def _note_curator_malformed_item() -> Mapping[str, object]:
    #  `maybe` is not one of the four verdicts, and `why` is not a string.
    return {"note_id": "1", "verdict": "maybe", "why": 7}


def _note_curator_written(_adaptor: object, store: object) -> int:
    assert isinstance(store, InMemoryNoteStore)
    return sum(1 for n in store.notes.values() if not n.is_standing)


IMPLEMENTATIONS: dict[str, _Impl] = {
    "fake": _Impl(
        build=_build_fake,
        make_items=_fake_items,
        make_malformed_item=_fake_malformed_item,
        written=_fake_written,
        empty_commits=_fake_empty_commits,
    ),
    "proactive": _Impl(
        build=_build_proactive,
        make_items=_proactive_items,
        make_malformed_item=_proactive_malformed_item,
        written=_proactive_written,
        # No `DreamSink` behind it: the empty commit is legal and records
        # nothing, and there is no counter to reach.
        empty_commits=None,
        prepare=_prepare_proactive,
        grant=proactive_grant(),
    ),
    "note-candidates": _Impl(
        build=_build_note_candidates,
        make_items=_note_candidate_items,
        make_malformed_item=_note_candidate_malformed_item,
        written=_note_candidate_written,
        # `commit` over an empty batch reaches no sink at all: it resolves the
        # workspace and loops over nothing. The decay argument the field
        # documents does not apply — this tier's time-driven work is the
        # curator's, not this one's.
        empty_commits=None,
        prepare=_prepare_note_candidates,
    ),
    "note-curation": _Impl(
        build=_build_note_curator,
        make_items=_note_curator_items,
        make_malformed_item=_note_curator_malformed_item,
        written=_note_curator_written,
        # Same: an empty `commit` still records wakes for the notes this run
        # rendered, but it reaches no `DreamSink.propose`, so there is no
        # empty-propose counter to read.
        empty_commits=None,
        prepare=_prepare_note_curator,
    ),
}


@contextlib.asynccontextmanager
async def _open(name: str) -> AsyncGenerator[tuple[object, object, _Impl]]:
    impl = IMPLEMENTATIONS[name]
    adaptor, backing = impl.build()
    if impl.prepare is not None:
        await impl.prepare(adaptor)
    yield adaptor, backing, impl


@pytest.fixture(params=sorted(IMPLEMENTATIONS))
async def ctx(request):
    async with _open(request.param) as value:
        yield value


@pytest.fixture
def adaptor(ctx):
    return ctx[0]


@pytest.fixture
def backing(ctx):
    return ctx[1]


@pytest.fixture
def impl(ctx):
    return ctx[2]


def _proposals(payloads: Sequence[object]) -> tuple[Proposal, ...]:
    """The runtime's job, done by hand: wrap each parsed payload with the
    provenance only the runtime knows. `accept` returning payloads rather than
    `Proposal`s is a deliberate deviation from the plan's sketch — an adaptor
    cannot know the instruction digest, the model, or the run id — and this
    helper is where the contract stands in for that step."""
    return tuple(
        Proposal(
            payload=payload, instruction_sha256=SHA, model="deepseek-v4-flash",
            established_by="dream:test",
        )
        for payload in payloads
    )


# ---------------------------------------------------------------------------
# the declared fields — what the runtime reads before it builds anything
# ---------------------------------------------------------------------------


def test_an_implementation_satisfies_the_protocol(adaptor):
    assert isinstance(adaptor, DreamAdaptor)


def test_the_key_kind_is_one_the_loop_can_enumerate(adaptor):
    """`dream_loop` resolves a population per `key_kind` — `scopes()` for a
    room adaptor, `workspace_scopes()` for a workspace-keyed one. A third
    value would leave the loop with an adaptor it cannot tick, so it is
    refused here rather than discovered as a scheduling no-op."""
    assert adaptor.key_kind in {"scope", "workspace"}


def test_the_tool_subset_narrows_the_read_only_ceiling(adaptor, impl):
    """The hard line, asserted at the port rather than trusted — and, since
    DEC-162, its one named allowance.

    An adaptor declares tool NAMES because `nora.language` may not import
    `nora.agent`; the consequence is that nothing about the declaration itself
    stops it naming `send_reply`. `read_only_tools()` is the ceiling and this
    is where an adaptor is measured against it — a subset naming an act verb,
    or a tool that does not exist at all, fails HERE rather than handing a
    background loop a write.

    REWRITTEN ON PURPOSE (design/dreamer-agent-loop/39 D3): the ceiling is
    the read-only set PLUS the act verbs this implementation's `AdaptorGrant`
    names, and nothing else. The fake has no grant and is measured exactly
    as before; the proactive adaptor's `remind` clears it only through
    `proactive_grant()`, the same object the composition root hands the
    runtime — so a verb added to `PROACTIVE_TOOLS` without a grant behind it
    is red here."""
    ceiling = ToolRegistry().read_only_tools() | impl.grant.acts
    assert adaptor.tools <= ceiling, (
        f"{adaptor.name} asks for tools outside the read-only ceiling and its "
        f"grant: {sorted(adaptor.tools - ceiling)}"
    )
    # The allowance is measured too: nothing in the registry's ACT set is in
    # the subset unless the grant names it by name.
    acts = adaptor.tools - ToolRegistry().read_only_tools()
    assert acts == (adaptor.tools & impl.grant.acts)


def test_the_objective_names_a_file_that_exists(adaptor):
    """`objective` is a NAME the runtime resolves against `nora/dreamer/
    objectives/`, and an unknown name raises at assembly time — inside the
    tick, where the failure is a counted `unavailable` rather than a startup
    refusal. Checking it here moves that failure to the gate.

    Matched on the file's `name:` frontmatter rather than its filename,
    because that is what `DreamerInstructions` keys on."""
    names = set()
    for path in (DREAMER_DIR / "objectives").glob("*.md"):
        for line in path.read_text().splitlines():
            if line.startswith("name:"):
                names.add(line.removeprefix("name:").strip())
                break
    assert adaptor.objective in names, (
        f"{adaptor.name}'s objective {adaptor.objective!r} is not one of "
        f"{sorted(names)}"
    )


def test_the_envelope_key_is_declared(adaptor):
    """Declared per adaptor rather than fixed at one shared literal, because it
    is half of a promise whose other half lives in the objective text. Asserted
    as non-empty only: the two ROOM objectives disagree today — `room-language`
    names `observations` in its body and `room-exchange` names nothing at all —
    so a test binding this field to the objective's prose would fail on
    correct, shipped text. Phase 3's re-partition is what makes that binding
    checkable; until then this is the honest assertion."""
    assert isinstance(adaptor.envelope_key, str)
    assert adaptor.envelope_key


def test_the_name_is_the_watermark_identity(adaptor):
    """One field, not three that can disagree — and renaming it re-reads the
    record from the beginning (`watermark_for` returns 0 for an unknown key),
    which is why it is worth a test that it is a real, stable string."""
    assert isinstance(adaptor.name, str)
    assert adaptor.name.strip() == adaptor.name != ""


# ---------------------------------------------------------------------------
# accept — the typed parse, which DROPS and never raises
# ---------------------------------------------------------------------------


async def test_accept_parses_a_legal_batch(adaptor, impl):
    items = impl.make_items()
    assert len(await adaptor.accept(key=KEY, items=items)) == len(items)


async def test_accept_drops_a_malformed_item_and_keeps_its_siblings(adaptor, impl):
    """`_parse_item`'s discipline, fixed as a contract: one bad element must
    not discard a batch that was otherwise fine, and it must not raise into a
    runtime that would then report the whole batch unread.

    The distinction matters because the runtime maps an envelope failure to
    `unavailable` — «nobody read this batch», watermark held — while a dropped
    item is ordinary. An adaptor that raised here would turn two malformed
    words into a room that never advances."""
    items = list(impl.make_items())
    expected = len(items)
    items.insert(1, impl.make_malformed_item())

    parsed = await adaptor.accept(key=KEY, items=items)

    assert len(parsed) == expected


async def test_accept_over_nothing_returns_nothing_and_does_not_raise(adaptor):
    assert await adaptor.accept(key=KEY, items=()) == ()


async def test_accept_of_only_malformed_items_returns_nothing(adaptor, impl):
    """Empty is a legal parse of a batch that was entirely junk — and it is
    NOT the same as the envelope failing, which is the runtime's to report."""
    bad = [impl.make_malformed_item(), impl.make_malformed_item()]
    assert await adaptor.accept(key=KEY, items=bad) == ()


# ---------------------------------------------------------------------------
# commit — where R-27's three states are expressed in the port
# ---------------------------------------------------------------------------


async def test_commit_writes_what_it_was_given(adaptor, backing, impl):
    payloads = await adaptor.accept(key=KEY, items=impl.make_items())

    written = await adaptor.commit(key=KEY, proposals=_proposals(payloads), now_ms=1_000)

    assert written == impl.written(adaptor, backing)
    assert written > 0


async def test_committing_nothing_is_a_legal_call_that_reaches_the_sink(
    adaptor, backing, impl
):
    """The decay path, and the reason it cannot be optimised away.

    `commit(key, ())` is `nothing_found` — «I looked and there was nothing» —
    and a sink may act on it: `LanguageConsolidator` rescores even for an empty
    batch, because decay is time-driven and the 24h fire condition exists
    precisely to fade a dormant room's lexicon. The runtime NOT calling
    `commit` is the third state, `unavailable`. Treating an empty sequence as
    "nothing to do, skip the call" collapses them, silently drops decay, and
    turns a wiring gap into a false negative.

    Asserted through the sink's own counter rather than through the return
    value, because a return of 0 is exactly what "never called" would also look
    like — which is the confusion the test exists to rule out.

    `impl.empty_commits is None` marks an adaptor with no sink to reach (the
    deleted entity-resolution adaptors wrote an alias per resolution and had
    no `DreamSink` at all), so the decay argument above does not apply to
    it. The empty commit must still be LEGAL and still return 0 — that half
    is asserted for every implementation."""
    before = None if impl.empty_commits is None else impl.empty_commits(
        adaptor, backing)

    written = await adaptor.commit(key=KEY, proposals=(), now_ms=1_000)

    assert written == 0
    if impl.empty_commits is not None:
        assert impl.empty_commits(adaptor, backing) == before + 1


async def test_commit_returns_rows_written_not_proposals_offered(
    adaptor, backing, impl
):
    """Rows WRITTEN, which is not `len(proposals)`: a fact store skips a
    `not_covered` verdict by design, so a legal commit of three can write one.
    Asserted as an identity against what the sink actually holds rather than
    against the offer, so an implementation that returns the offered count
    fails as soon as it starts skipping anything."""
    payloads = await adaptor.accept(key=KEY, items=impl.make_items())
    proposals = _proposals(payloads)

    written = await adaptor.commit(key=KEY, proposals=proposals, now_ms=1_000)

    assert written == impl.written(adaptor, backing)
    assert written <= len(proposals)


async def test_inputs_renders_a_real_batch(adaptor):
    """Exercised with a REAL `TranscriptRow` rather than a stand-in, and the
    reason is a defect this test caught while being written: the fake read
    `row.text`, which does not exist — the field is `body`, and it is
    `str | None`. Every other test here calls `inputs` with an empty batch, so
    the attribute error was invisible until a row was actually rendered.

    That is the shape of the whole file: an adaptor's `inputs` runs once per
    tick against rows nobody is watching, so an attribute that only exists in
    someone's memory of the model fails in the background, quietly, forever."""
    row = TranscriptRow(
        id=41, sender_id="p1", sender_name="A", ts=1_000, body="big walk 今晚?",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )

    rendered = await adaptor.inputs(key=KEY, batch=(row,))

    assert isinstance(rendered, str)
    # NOT asserted non-empty, and that is the port's contract rather than a
    # weakening: a BLANK return means "nothing to reason about", and the
    # runtime uses it as the fire condition — it skips the model entirely.
    # An adaptor whose input is not the message batch (the fact adjudicator
    # reads the candidate table) correctly renders nothing for any batch at
    # all, so requiring non-empty here would demand a prompt that says "there
    # is nothing to do" and pay a model call for it. That is the defect this
    # assertion used to hide (observed live 2026-08-21).


async def test_inputs_tolerates_a_row_with_no_body(adaptor):
    """`TranscriptRow.body` is `str | None` — a media-only message has no text
    at all — and an `inputs` that assumed a string would take the tick down on
    a sticker."""
    row = TranscriptRow(
        id=42, sender_id="p1", sender_name="A", ts=1_000, body=None,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )

    assert isinstance(await adaptor.inputs(key=KEY, batch=(row,)), str)


async def test_inputs_renders_an_empty_batch_without_raising(adaptor):
    """An empty batch is a REAL state, not an error: an adaptor with
    `uses_watermark = False` always gets one, and a watermarked adaptor gets
    one whenever the room was quiet. An `inputs` that raised on it would take
    the tick down on the most ordinary condition in the loop."""
    assert isinstance(await adaptor.inputs(key=KEY, batch=()), str)


# ---------------------------------------------------------------------------
# the egress screen — the step every adaptor suite in this repo used to skip
# ---------------------------------------------------------------------------
#
# `DreamerRuntime.run` does `screen(...)` and hands only the KEPT items to
# `accept`. Every unit suite called `accept` directly, so the real screen never
# ran — and `dream_screen._offence` walks every string reachable in an item
# while `egress._UUID` matches any hyphenated uuid. An adaptor whose objective
# asks the model to echo a row id back therefore has EVERY item dropped: the
# tick fires, spends a model call, `accept` receives `()`, `commit` writes 0,
# and the run reports `ok`. THREE adaptors shipped that way (issue #63), and
# eighteen tests passed over one of them.
#
# The wrong fix, stated so nobody reaches for it: the adaptor path is not
# exempted from `dream_screen`. That screen is the R-18/R-28 floor — nothing
# carrying an identifier or a coordinate reaches a store that is read back into
# every later prompt — and widening it for one tier is how the rule dies. The
# fix is the ordinal handle, and this is the gate that keeps every adaptor on
# it.


def _screened(adaptor, items):
    """`screen()` exactly as the runtime calls it — same function, same
    argument order, a real `Metrics` so a drop is counted rather than
    swallowed."""
    return screen(adaptor=adaptor.name, items=items, metrics=Metrics())


async def test_a_legal_batch_survives_the_runtimes_own_egress_screen(
    adaptor, backing, impl
):
    """**The test that would have caught it**, parameterised over every
    registered adaptor rather than written once per tier.

    Driven in the RUNTIME's order — screen, then `accept`, then `commit` —
    because surviving the screen is worthless if the items no longer resolve
    afterwards, and resolving is worthless if they never survive. The pair is
    the property; either alone is half a pipeline.
    """
    items = impl.make_items()

    kept, rejected = _screened(adaptor, items)

    assert not rejected, (
        f"{adaptor.name}: the egress screen rejected its own legal items "
        f"{[(r.reason, r.path) for r in rejected]} — in production this "
        f"adaptor spends a model call, writes nothing, and reports `ok`"
    )
    assert len(kept) == len(items)

    payloads = await adaptor.accept(key=KEY, items=kept)
    assert len(payloads) == len(items), (
        "items survived the screen but no longer parse — a handle that "
        "survives and does not resolve is the same inert tier one step over"
    )
    written = await adaptor.commit(
        key=KEY, proposals=_proposals(payloads), now_ms=1_000)
    assert written == impl.written(adaptor, backing)
    assert written > 0


async def test_inputs_renders_nothing_the_egress_screen_would_reject(
    adaptor, impl
):
    """The half that catches the NEXT adaptor rather than the ones here.

    `make_items` is hand-written per entry, so an author who invents a clean
    item can satisfy the test above while their objective still asks the model
    to echo a uuid. This one does not read their items at all: it reads what
    `inputs` RENDERS, because a model can only echo what it was shown. A row
    id that reaches the prompt is a row id the model will put in an envelope,
    and `screen` will drop that envelope.

    **It is only as good as the double behind it, and that is a real bound on
    this test.** `InMemoryNoteStore` mints uuid-SHAPED ids exactly because of
    this defect (`_shaped_id`'s own comment: it minted `note-1` until
    2026-08-28 and that one divergence hid the whole thing). A future adaptor
    whose store double mints `row-1` would pass here and fail in production —
    so the rule the fakes already follow is load-bearing, and this docstring is
    where it is written down for whoever adds the next one.
    """
    row = TranscriptRow(
        id=41, sender_id="p1", sender_name="Ada", ts=1_000, body="big walk 今晚?",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )

    rendered = await adaptor.inputs(key=KEY, batch=(row,))

    assert rendered.strip(), (
        f"{adaptor.name} rendered NOTHING, so screening it asserts nothing. "
        f"`screen(({{'prompt': ''}},))` rejects nothing for any adaptor at "
        f"all — an entry whose `build()` seeds an empty store and declares no "
        f"`prepare` passes this test vacuously. Seed the state this adaptor "
        f"reads."
    )

    _, rejected = _screened(adaptor, ({"prompt": rendered},))
    assert not rejected, (
        f"{adaptor.name} renders {[(r.reason, r.path) for r in rejected]} "
        f"into its prompt. Whatever the prompt shows, the model can echo — "
        f"and an echoed identifier is dropped by the SAME screen, silently, "
        f"with the run still reporting `ok`. Render an ordinal handle and "
        f"keep the id in the adaptor's own map (issue #63)."
    )


def test_the_screen_would_catch_an_identifier_if_one_were_there():
    """**The positive control, and without it the two tests above are
    decorative.** `not rejected` is also what a screen that rejects nothing
    produces — a `screen` stubbed out, an import that silently resolved to a
    no-op, a `Rejection` tuple nobody fills. So one item carrying a uuid4 must
    actually be dropped, through the same helper, before an empty rejection
    list means anything."""
    items = ({"note_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"},)

    kept, rejected = _screened(_FakeAdaptor(_CountingSink()), items)

    assert kept == ()
    assert [r.reason for r in rejected] == ["identifier"]


def test_every_registered_implementation_is_exercised():
    """The run-that-ran control for the map itself. This suite spent the
    period after the knowledge tier's deletion with ONE entry — a fake — and
    its own docstring flagged that as a known gap rather than the shipped
    state. A silently shrinking map makes every test in this file vacuous, and
    the shrink is invisible: the suite stays green, faster."""
    assert set(IMPLEMENTATIONS) >= {
        "fake", "proactive", "note-candidates", "note-curation",
    }, sorted(IMPLEMENTATIONS)
