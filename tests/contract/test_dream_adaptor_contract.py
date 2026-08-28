"""Shared contract for `DreamAdaptor`. Every implementation runs every test.

Shipped with ONE implementation — a fake — for the reason
`tests/contract/test_dream_sink_contract.py` states for the same situation: a
port declared with nothing exercising it is a shape nobody has checked is
satisfiable. The two room adaptors join in Phase 3 and the fact adaptor in
Phase 4, each by adding one `IMPLEMENTATIONS` entry.

**The rule this file is built against, inherited from the sink suite where it
was learned four times over:** anything the shared suite needs that is not on
`DreamAdaptor` itself must be supplied PER IMPLEMENTATION, never hard-coded
once and assumed to fit whoever joins next. Two things need it here:

1. **Legal envelope items.** `accept` takes
   `Sequence[Mapping[str, object]]` — a room-language observation and a fact
   verdict are both legal and neither is legal for the other. So an entry
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
what a term or a predicate looked like would be the fact-vocabulary defect the
sink suite found in itself, rebuilt here.

**`_FakeAdaptor` stays HERE rather than in `nora/testing/`**, the same rule
that binds `_FakeDreamSink` to its suite: `nora/testing/fakes.py` stands in for
production implementations at OTHER call sites, and this one's only job is
exercising this contract.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nora.agent.registry import ToolRegistry
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import DreamAdaptor, Proposal
from nora.language.fact_adaptor import FactAdaptor, FactCandidateAdaptor
from nora.testing.fact_candidate_store import InMemoryFactCandidateStore
from nora.testing.fact_store import InMemoryFactStore

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
    objective = "room-language"
    envelope_key = "observations"
    tools = frozenset({"load_transcript", "now"})

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


# ---------------------------------------------------------------------------
# the fact tier: two real adaptors over the two in-memory stores
# ---------------------------------------------------------------------------


@dataclass
class _CountsEmptyProposes:
    """A thin spy over a real sink that counts `propose(key, ())` calls.

    Not a third `DreamSink` implementation — it holds no rows and decides
    nothing, it delegates every verb (`__getattr__` covers `supersede`,
    `pending`, `standing_facts`) to the in-memory store behind it, which is
    the implementation the sink contract suite already measures. It exists
    only because `test_committing_nothing_is_a_legal_call_that_reaches_the_
    sink` has to tell "reached the sink and wrote nothing" apart from "was
    never called", and a return of 0 looks identical either way. `_CountingSink`
    above counts the same thing for the fake, for the same reason.
    """

    inner: object
    empty_commits: int = 0

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> Sequence[str]:
        if not proposals:
            self.empty_commits += 1
        return await self.inner.propose(  # type: ignore[attr-defined]
            key=key, proposals=proposals, now_ms=now_ms,
        )

    def __getattr__(self, name: str) -> object:
        if name == "inner":                       # pre-`__init__` recursion guard
            raise AttributeError(name)
        return getattr(self.inner, name)


def _build_fact_candidates() -> tuple[object, object]:
    sink = _CountsEmptyProposes(InMemoryFactCandidateStore())
    # The extractor is SCOPE-keyed and the candidate table is WORKSPACE-keyed
    # (DEC-105), so it translates; the composition root builds this closure
    # from the same allowlist `language_scopes`/`workspace_scopes` read.
    adaptor = FactCandidateAdaptor(
        candidates=sink, workspace_for=lambda scope: "ws-main",
    )
    return adaptor, sink


def _fact_candidate_items() -> tuple[Mapping[str, object], ...]:
    return (
        {"subject": "big walk", "predicate": "unlocked_by",
         "object": "the four coloured towers", "message_ids": [41]},
        {"subject": "big walk", "predicate": "contains",
         "object": "a black tower", "message_ids": [42]},
    )


def _fact_candidate_malformed_item() -> Mapping[str, object]:
    # Wrong type for two triple fields and an unparseable message id. The
    # triple is half of `fact_candidate_triple_key`, so a coerced `"7"`/`"None"`
    # would be a durable key nobody can ever match back — see
    # `nora.language.fact_adaptor._text`.
    return {"subject": 7, "predicate": None, "object": "x", "message_ids": "later"}


def _build_fact_adjudication() -> tuple[object, object]:
    facts = _CountsEmptyProposes(InMemoryFactStore())
    adaptor = FactAdaptor(
        facts=facts, candidates=InMemoryFactCandidateStore(),
        max_adjudications_per_tick=10,
    )
    return adaptor, facts


def _fact_verdict_items() -> tuple[Mapping[str, object], ...]:
    """Two `supported` verdicts, and `supersedes` deliberately absent from
    both.

    A `contradicted` verdict must name a fact id THIS RUN SHOWED THE MODEL,
    and the suite calls `accept` without first calling `inputs` — so a
    contradiction here would be correctly dropped as `unknown_supersedes` and
    `test_accept_parses_a_legal_batch` would fail on working code. The
    contradiction path is `tests/unit/test_fact_adaptor.py`'s, where a run can
    be given its inputs first.
    """
    return (
        {"subject": "big walk", "predicate": "unlocked_by",
         "object": "the four coloured towers", "status": "supported",
         "addresses_claim": True,
         "source_url": "https://example.test/guide",
         "source_excerpt": "The black tower opens once all four are complete."},
        {"subject": "big walk", "predicate": "gourds_required",
         "object": "three", "status": "supported", "addresses_claim": True,
         "source_url": "https://example.test/patch-2-4",
         "source_excerpt": "Patch 2.4 reduced the gourd requirement to three."},
    )


def _fact_verdict_malformed_item() -> Mapping[str, object]:
    # Well-typed and still illegal: a `supported` verdict with no source.
    # `FactVerdict.__post_init__` refuses it by RAISING, which is right for a
    # constructor and fatal for a background tick — so this item also fixes as
    # a contract that `accept` catches that `ValueError` rather than letting it
    # reach a runtime that would report the whole batch unread.
    return {
        "subject": "big walk", "predicate": "contains", "object": "a shop",
        "status": "supported", "addresses_claim": True,
    }


def _fact_written(_adaptor: object, sink: object) -> int:
    assert isinstance(sink, _CountsEmptyProposes)
    store = sink.inner
    assert isinstance(store, InMemoryFactStore)
    return len(store._facts)


def _candidate_written(_adaptor: object, sink: object) -> int:
    assert isinstance(sink, _CountsEmptyProposes)
    store = sink.inner
    assert isinstance(store, InMemoryFactCandidateStore)
    return len(store._by_triple)


def _spy_empty_commits(_adaptor: object, sink: object) -> int:
    assert isinstance(sink, _CountsEmptyProposes)
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
    empty_commits: Callable[[object, object], int]


IMPLEMENTATIONS: dict[str, _Impl] = {
    "fake": _Impl(
        build=_build_fake,
        make_items=_fake_items,
        make_malformed_item=_fake_malformed_item,
        written=_fake_written,
        empty_commits=_fake_empty_commits,
    ),
    "fact-candidates": _Impl(
        build=_build_fact_candidates,
        make_items=_fact_candidate_items,
        make_malformed_item=_fact_candidate_malformed_item,
        written=_candidate_written,
        empty_commits=_spy_empty_commits,
    ),
    "fact-adjudication": _Impl(
        build=_build_fact_adjudication,
        make_items=_fact_verdict_items,
        make_malformed_item=_fact_verdict_malformed_item,
        written=_fact_written,
        empty_commits=_spy_empty_commits,
    ),
}


@contextlib.asynccontextmanager
async def _open(name: str) -> AsyncGenerator[tuple[object, object, _Impl]]:
    impl = IMPLEMENTATIONS[name]
    adaptor, backing = impl.build()
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


def test_the_tool_subset_narrows_the_read_only_ceiling(adaptor):
    """The hard line, asserted at the port rather than trusted.

    An adaptor declares tool NAMES because `nora.language` may not import
    `nora.agent`; the consequence is that nothing about the declaration itself
    stops it naming `send_reply`. `read_only_tools()` is the ceiling and this
    is where an adaptor is measured against it — a subset naming an act verb,
    or a tool that does not exist at all, fails HERE rather than handing a
    background loop a write."""
    ceiling = ToolRegistry().read_only_tools()
    assert adaptor.tools <= ceiling, (
        f"{adaptor.name} asks for tools outside the read-only ceiling: "
        f"{sorted(adaptor.tools - ceiling)}"
    )


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
    like — which is the confusion the test exists to rule out."""
    before = impl.empty_commits(adaptor, backing)

    written = await adaptor.commit(key=KEY, proposals=(), now_ms=1_000)

    assert written == 0
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
