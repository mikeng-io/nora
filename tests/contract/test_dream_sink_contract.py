"""Shared contract for `DreamSink`. Every implementation runs every test.

Shipped with ONE implementation — a fake — for the reason
`tests/contract/test_artifact_ingest_contract.py` states for the same
situation: a port declared with nothing exercising it is a shape nobody has
checked is satisfiable. `nora.infra.sql.facts.SqlFactStore` is the second and
`nora.infra.sql.fact_candidates.SqlFactCandidateStore` the third; each joins
by adding an `_Impl` entry to `IMPLEMENTATIONS`.

**This file used to claim joining was "genuinely one `IMPLEMENTATIONS`
entry", verified by building a non-fact store against it, and that if that
stopped being true it was a defect. It stopped being true, and this is what
the third implementation actually cost** — recorded rather than quietly
deleted, because a docstring that drops a falsified claim teaches the next
author nothing. Adding `SqlFactCandidateStore` as one entry and running it
(2026-08-21) gave **4 passed, 9 failed**, in three groups:

- **Three assertions read back columns `fact_candidate` did not have.** One
  was a real gap in the TABLE, not in this suite:
  `test_provenance_survives_the_write` reads `instruction_sha256`/`model`,
  the DEC-125/DEC-126 pair every `Proposal` carries and that store was
  discarding. Closed by migration **`0027_fact_candidate_provenance`** (NOT
  NULL, no backfill — read its docstring before adding the next tier's
  table). The other two — a successor and a supersession warrant — are NOT
  a gap: no candidate ever succeeds another (an adjudicated one is succeeded
  by a `fact`, in a DIFFERENT table) and a supersession's citation belongs on
  that `fact` row. They became axis states below, not columns.
- **Five passed a non-null `replaced_by` (or `source`) into calls that store
  refuses**, because point 3 below was a BOOL over a three-state axis.
- **Three hard-coded `because` into a call this file assumed never reaches a
  reason check.** See the correction under point 2.

**The rule that keeps this file shared rather than fact-shaped, found by
building the second implementation and confirmed by the third:** anything the
shared suite needs that is not on `DreamSink` itself must be supplied PER
IMPLEMENTATION, never hard-coded once and assumed to fit whoever joins next.
Three things needed this, found in this order:

1. **Constructing a valid `Proposal`.** `payload` is `object` at the port —
   a fact-tier `FactVerdict` and a note-tier revision are both valid, and
   neither is valid for the other. `IMPLEMENTATIONS` therefore carries a
   `make_proposal` callable per entry, not one shared `_proposal(payload)`
   helper with a hard-coded literal.
2. **Reading a row back.** `DreamSink` has two verbs and *deliberately no
   third* (`test_the_port_has_no_delete_verb` below is the reason written as
   a test) — there is no read verb to assert through. `IMPLEMENTATIONS`
   therefore also carries a `read_row` callable per entry, returning a
   normalised view — `proposal, because, source, replaced_by, retired_at,
   written_at` — built from whatever EACH implementation actually offers
   (`_FakeDreamSink`'s own `.rows` dict for the fake, `SqlFactStore.
   fact_by_id` for the sql store, `SqlFactCandidateStore.candidate_by_id`
   for the candidate store — whose retirement reason IS its `status`
   column, which is exactly why this view keeps `because` opaque).
   `because` and `source` are opaque `str`/
   `str | None` on this view, never a tier's own enum — typing `because` as
   `FactSupersedeReason` here would make the view fact-shaped again, just
   wearing the fix's clothes; the note tier's reasons
   (`merged`/`retired-expired`/`retired-closed`) have no notion of
   `contradicted`. A tier's one exotic field (the note store's head-revision
   id, used for compare-and-swap) lives inside `payload`, which stays opaque
   to this suite by construction and never reaches the view.

   The SAME reasoning applies to *constructing* a legal `because` for a real
   (row-affecting) `supersede` call, found while wiring `"sql"` in: `fact`'s
   own `fact_supersede_reason_check` constrains `supersede_reason` to
   `stale|wrong|merged`, so a shared test's free-text reason — chosen for
   readability, not tier-legality — makes a REAL update raise
   `IntegrityError` for `"sql"` while the fake accepts it unquestioned.
   `IMPLEMENTATIONS` therefore also carries `make_because`, a per-
   implementation callable returning a reason legal for THAT store, used
   everywhere a `supersede` call is expected to actually affect a row.

   **Correction, found by the third implementation:** this file used to
   exempt calls expected to affect ZERO rows (an unknown target, a wrong
   key) and keep a descriptive literal there, reasoning that Postgres does
   not evaluate a row constraint against a row it never touches. True of a
   store that DEFERS its reason check to a CHECK; false of one that makes it
   itself. `SqlFactCandidateStore` raises `ValueError` on a non-terminal
   `because` before any row is looked at — deliberately, because
   `fact_candidate_status_check` is a WIDER set than that verb's legal one
   (it admits `pending`, which is a legal status for a row and not a legal
   reason for a retirement). So the exemption is gone: **every** `supersede`
   in this file takes its reason from `make_because()`, and a zero-row test
   says what it is varying in a comment instead of in the reason string.
3. **What each implementation promises about the two fields the PORT allows
   to be null** — `replaced_by` and `source`. `DreamSink.supersede`'s own
   docstring is explicit that this is "permissive at the port, strict at each
   implementation".

   This was a `nullable_replaced_by` BOOL, and a bool is one state short.
   Three exist and all three are now real (`NullableAxis` is named for what
   the VALUE is, so read each member as a promise about the non-null value):

   - **null-legal / `OPTIONAL`** — the note tier's `retire` (an anchor date
     passed; a dinner that happened is replaced by nothing), and the fake.
     Both a value and a null are accepted.
   - **null-refused / `REQUIRED`** — `SqlFactStore`, whose `fact` CHECK is a
     biconditional, so a supersession without a successor is refused by the
     database and by the store above it.
   - **null-required / `REFUSED`** — `SqlFactCandidateStore`, which refuses a
     NON-null `replaced_by` (and a non-null `source`) with `ValueError`,
     because it has no column to record either and accepting-then-dropping
     would report a successor recorded that nothing recorded — R-27's
     ERROR-as-`ok`, written into a column instead of returned from a call.

   So `IMPLEMENTATIONS` carries a `NullableAxis` **per field**, and that enum
   member is itself the per-implementation factory: `axis.offer(value)`
   returns the value to pass for a store that can record it and `None` for
   one that cannot, so every test that supplies a `replaced_by` or a `source`
   gets it from the entry the way `make_proposal` and `make_because` already
   work. One value per field rather than an axis plus a separate callable:
   two entries encoding one fact can disagree, and the disagreement would
   read as a store's behaviour.

   Deliberately NO skip anywhere on this axis. A test that does not run for
   an implementation is a property nobody checked; each of the three tests
   here branches on what the entry promises, so all three implementations
   run all three. `test_a_field_the_store_cannot_record_is_refused_not_dropped`
   is where "refused" is actually measured, and it runs everywhere too — for
   a store that CAN record the field it asserts the round trip instead.

`test_a_foreign_payload_is_refused_not_duck_typed` is the shared assertion
for the property `SqlFactStore.propose`'s own docstring calls out — an
`isinstance` check refusing any payload it does not own. It is NOT run
against `"fake"`: `_FakeDreamSink` declares no payload type of its own (it
stands for the port's own genericity, not for a production adaptor with a
value to refuse), so the assertion would pass VACUOUSLY there — refusing
nothing because it refuses everything indiscriminately — which is exactly
the silently-green failure this test exists to rule out. See
`FOREIGN_PAYLOAD_EXEMPT` below; an implementation is excluded only with a
stated reason, never silently.

`_FakeDreamSink` stays HERE rather than in `nora/testing/`: CLAUDE.md's rule
binds a fake to a suite, and `nora/testing/fakes.py`'s docstring enumerates
every fake there as standing in for a *production* implementation at OTHER
call sites. This one has no such job — its only purpose is exercising this
contract. (`nora.testing.InMemoryFactStore` and
`nora.testing.InMemoryFactCandidateStore` are a DIFFERENT thing: tier-specific
doubles carrying their own tier's refusals, tested by
`tests/infra/test_fact_store.py` and `tests/infra/test_fact_candidate_store.py`
alongside the SQL store each mirrors — neither joins `IMPLEMENTATIONS` here,
because this file's `"fake"` entry exists to exercise the PORT's genericity,
and a tier-shaped fake would defeat that.)

The test that matters most is `test_the_port_has_no_delete_verb`. Every other
test here checks behaviour an implementation could get right by accident; that
one checks the property the port EXISTS to enforce, and it fails the moment
someone adds the convenience method that makes «invalidate, never delete» a
habit again instead of a type.
"""

from __future__ import annotations

import contextlib
import enum
import hashlib
import inspect
import itertools
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import FrozenInstanceError, dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.ports import DreamSink, Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.fact_candidates import SqlFactCandidateStore
from nora.infra.sql.facts import SqlFactStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import fact_candidate_proposal_factory, fact_proposal_factory

NOW = 1_755_000_000_000


@dataclass
class _Row:
    key: str
    proposal: Proposal
    written_at: int
    replaced_by: str | None = None
    retired_at: int | None = None
    because: str | None = None
    source: str | None = None


@dataclass
class _FakeDreamSink:
    """Minimum honest implementation: it really stores, really supersedes, and
    really keeps keys apart. A fake that answered every call the same way would
    make this suite decorative (CLAUDE.md)."""

    rows: dict[str, _Row] = field(default_factory=dict)
    empty_proposes: list[tuple[str, int]] = field(default_factory=list)
    _seq: int = 0

    async def propose(self, *, key, proposals, now_ms):
        if not proposals:
            # `nothing_found` — legal, and the sink MAY act on it. This is
            # where the unconditional rescore lives for the language adaptor.
            self.empty_proposes.append((key, now_ms))
            return ()
        ids = []
        for p in proposals:
            self._seq += 1
            rid = f"row-{self._seq}"
            self.rows[rid] = _Row(key=key, proposal=p, written_at=now_ms)
            ids.append(rid)
        return tuple(ids)

    async def supersede(self, *, key, target, replaced_by, because, source, now_ms):
        row = self.rows.get(target)
        if row is None or row.key != key or row.retired_at is not None:
            return 0
        row.retired_at = now_ms
        row.replaced_by = replaced_by
        row.because = because
        row.source = source
        return 1


_fake_seq = itertools.count(1)


def _fake_proposal_factory(*, established_by: str | None = None) -> Proposal:
    """Three distinct default values per call — `instruction_sha256`,
    `model`, `established_by` — mirroring `fact_proposal_factory`'s own
    contract, so a field swap in an assertion is visible for EITHER
    implementation, not just the real one."""
    n = next(_fake_seq)
    return Proposal(
        payload=f"fake-payload-{n}",
        instruction_sha256=hashlib.sha256(f"fake-instruction-{n}".encode()).hexdigest(),
        model=f"fake-contract-model-{n}",
        established_by=established_by or f"run-{n}",
    )


@dataclass(frozen=True)
class _ReadBack:
    """The normalised row view every `IMPLEMENTATIONS` entry's `read_row`
    returns — see the module docstring's point 2. Deliberately this small:
    nothing here is typed to one tier's vocabulary."""

    proposal: Proposal
    because: str | None
    source: str | None
    replaced_by: str | None
    retired_at: int | None
    written_at: int


async def _fake_read_row(sink: _FakeDreamSink, *, key: str, row_id: str) -> _ReadBack:
    row = sink.rows[row_id]
    return _ReadBack(
        proposal=row.proposal, because=row.because, source=row.source,
        replaced_by=row.replaced_by, retired_at=row.retired_at,
        written_at=row.written_at,
    )


async def _sql_read_row(sink: SqlFactStore, *, key: str, row_id: str) -> _ReadBack:
    fact = await sink.fact_by_id(workspace=key, fact_id=row_id)
    assert fact is not None, f"{row_id} was just written and must be readable"
    # `payload` is not reconstructed — `fact_by_id` returns the adjudicated
    # `Fact`, not the `FactVerdict` that produced it, and no shared test
    # compares `.payload`. Only the provenance columns are asserted on.
    return _ReadBack(
        proposal=Proposal(
            payload=None, instruction_sha256=fact.instruction_sha256,
            model=fact.model, established_by=fact.established_by,
        ),
        because=fact.supersede_reason, source=fact.supersede_source,
        replaced_by=fact.superseded_by, retired_at=fact.superseded_at,
        written_at=fact.created_at,
    )


_fake_because_seq = itertools.count(1)


def _fake_make_because() -> str:
    return f"fake-reason-{next(_fake_because_seq)}"


_sql_because_cycle = itertools.cycle(("stale", "wrong", "merged"))


def _sql_make_because() -> str:
    """`fact_supersede_reason_check` in `nora/infra/sql/schema.py` and
    `nora/infra/sql/migrations/versions/0025_fact_tier.py`: any OTHER string
    raises `IntegrityError` the moment a real row is touched."""
    return next(_sql_because_cycle)


async def _candidate_read_row(
    sink: SqlFactCandidateStore, *, key: str, row_id: str,
) -> _ReadBack:
    """The candidate tier's row, normalised onto the shared view.

    Three of the six fields are this tier saying something the other two do
    not, and each is a mapping rather than a gap:

    - `because` is the row's `status`. A candidate's retirement reason and
      its terminal status are the SAME value (`adjudicated`/`not_covered`) —
      which is the reason `_ReadBack.because` is an opaque `str` and not
      anyone's enum. Before a retirement the status is `pending`, which is a
      status and not a reason, so this reports `None` there rather than
      handing a test the word "pending" as if a supersession had said it.
    - `source` and `replaced_by` are always `None`: the table has no column
      for either, which is why `SqlFactCandidateStore.supersede` REFUSES a
      non-null value for them instead of accepting and dropping it. That is
      the `NullableAxis.REQUIRED` entry below, not a hole in this view.
    - `written_at` is `first_seen_at` — a re-sighting bumps `last_seen_at`,
      and "when was this row written" is the first sighting.
    """
    c = await sink.candidate_by_id(workspace=key, candidate_id=row_id)
    assert c is not None, f"{row_id} was just written and must be readable"
    return _ReadBack(
        proposal=Proposal(
            payload=None, instruction_sha256=c.instruction_sha256, model=c.model,
            established_by=c.established_by,
        ),
        because=c.status if c.last_adjudicated_at is not None else None,
        source=None,
        replaced_by=None,
        retired_at=c.last_adjudicated_at,
        written_at=c.first_seen_at,
    )


_candidate_because_cycle = itertools.cycle(("adjudicated", "not_covered"))


def _candidate_make_because() -> str:
    """The two TERMINAL statuses, and the reason the zero-row exemption had to
    go: `SqlFactCandidateStore.supersede` raises `ValueError` on anything else
    BEFORE looking at a row, because `fact_candidate_status_check` also admits
    `pending` and «supersede a candidate into pending» is not a retirement."""
    return next(_candidate_because_cycle)


class NullableAxis(enum.Enum):
    """What ONE implementation promises about ONE field the PORT allows to be
    null — `replaced_by` or `source`. See the module docstring's point 3: this
    replaced a `nullable_replaced_by` bool, which was one state short of the
    three that exist.

    The member is also the per-implementation factory (`offer`), so an entry
    cannot promise one thing and hand tests a value that says another.
    """

    OPTIONAL = "optional"
    """Null and non-null are both accepted (`_FakeDreamSink`; the note tier's
    `retire` for `replaced_by`)."""

    REQUIRED = "required"
    """A null is REFUSED — the store's own rows need the value
    (`SqlFactStore`'s `(superseded_by is null) = (superseded_at is null)`)."""

    REFUSED = "refused"
    """A NON-null is refused — the store has no column for it and will not
    accept-then-drop (`SqlFactCandidateStore` on both fields)."""

    def offer(self, value: str) -> str | None:
        """The value a test passes for this field: `value` where the store can
        record it, `None` where it cannot. Never the reverse of what the
        store promises, because the promise and the value are one object."""
        return None if self is NullableAxis.REFUSED else value

    @property
    def null_is_legal(self) -> bool:
        return self is not NullableAxis.REQUIRED


def _build_fake(_engine: AsyncEngine | None) -> _FakeDreamSink:
    return _FakeDreamSink()


def _build_sql(engine: AsyncEngine | None) -> SqlFactStore:
    assert engine is not None, "sql's build is only ever called with a real engine"
    return SqlFactStore(engine)


def _build_candidate(engine: AsyncEngine | None) -> SqlFactCandidateStore:
    assert engine is not None, "candidate's build needs a real engine"
    return SqlFactCandidateStore(engine)


@dataclass(frozen=True)
class _Impl:
    """One implementation's entry. A dataclass rather than the tuple this
    started as: the third implementation took it to seven fields, two of them
    the same type (`NullableAxis`) sitting next to each other, which is a
    positional swap nothing would have caught — `successor`/`source`
    transposed still type-checks and still looks like it works.

    `needs_engine`/`build` are what this table adds over the `IMPLEMENTATIONS`
    dicts elsewhere in this repo (`test_tool_call_log_store_contract.py`'s own
    `store` fixture, `test_room_consolidator_contract.py`'s `_backing`):
    earlier this was `store_ctor: Callable[[AsyncEngine], object] | None`,
    with `None` overloading "no engine needed" onto "build `_FakeDreamSink`"
    specifically — which would have forced `_open_sink` to be edited for a
    THIRD implementation that also needs no engine but is not the fake.
    Splitting the two removes that: `needs_engine` says whether `_open_sink`
    wires Postgres before calling `build`, and `build` says WHAT to build, so
    an engine-less non-fake store (unlikely for this tier, but the port makes
    no such promise) still joins by adding one entry. `build` always takes the
    (possibly `None`) engine positionally so both branches share one call
    shape.

    `successor` and `source` are the two `NullableAxis` values — see the
    module docstring's point 3. They are separate fields because they are
    separate promises: `SqlFactStore` REQUIRES a successor and accepts any
    citation, and nothing says a tier cannot do the opposite.
    """

    needs_engine: bool
    build: Callable[[AsyncEngine | None], object]
    make_proposal: Callable[..., Proposal]
    make_because: Callable[[], str]
    read_row: Callable[..., Awaitable[_ReadBack]]
    successor: NullableAxis
    source: NullableAxis


IMPLEMENTATIONS: dict[str, _Impl] = {
    "fake": _Impl(
        needs_engine=False, build=_build_fake,
        make_proposal=_fake_proposal_factory, make_because=_fake_make_because,
        read_row=_fake_read_row,
        successor=NullableAxis.OPTIONAL, source=NullableAxis.OPTIONAL,
    ),
    "sql": _Impl(
        needs_engine=True, build=_build_sql,
        make_proposal=fact_proposal_factory, make_because=_sql_make_because,
        read_row=_sql_read_row,
        # `fact_supersession_is_timed_check` is a biconditional: a fact is
        # either standing or superseded-with-a-successor.
        successor=NullableAxis.REQUIRED, source=NullableAxis.OPTIONAL,
    ),
    "candidate": _Impl(
        needs_engine=True, build=_build_candidate,
        make_proposal=fact_candidate_proposal_factory,
        make_because=_candidate_make_because, read_row=_candidate_read_row,
        # No candidate ever succeeds another (an adjudicated one is succeeded
        # by a `fact`, in a different table) and a supersession's warrant
        # belongs on that `fact` row. `fact_candidate` has a column for
        # neither, so both are refused rather than accepted and dropped —
        # see `SqlFactCandidateStore.supersede`'s three refusals.
        successor=NullableAxis.REFUSED, source=NullableAxis.REFUSED,
    ),
}

FOREIGN_PAYLOAD_EXEMPT: dict[str, str] = {
    "fake": (
        "the fake declares no payload type of its own -- Proposal.payload "
        "is `object` at the port on purpose, and _FakeDreamSink stands for "
        "that genericity rather than for a production adaptor with a value "
        "type to refuse. Asserting a foreign payload is refused would pass "
        "vacuously here (it refuses nothing, by construction) -- exactly "
        "the silently-green failure this test exists to rule out."
    ),
}


@contextlib.asynccontextmanager
async def _open_sink(name: str, request) -> AsyncIterator[tuple[object, _Impl]]:
    """Builds the store for `name` and yields `(sink, impl)` — the store plus
    its own entry, which every fixture below reads one field out of.
    `IMPLEMENTATIONS[name].needs_engine` says whether Postgres wiring (pg_url,
    migrate, engine, dispose) happens before `build` is called; `build` says
    WHAT to build. Mirrors
    `tests/contract/test_tool_call_log_store_contract.py`'s own `store`
    fixture's "if fake: return; else: pg_url+migrate+engine" shape, with the
    class itself pulled out per-entry rather than hard-coded to one SQL
    store — see `_Impl` above for why."""
    impl = IMPLEMENTATIONS[name]
    if not impl.needs_engine:
        yield (impl.build(None), impl)
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield (impl.build(engine), impl)
    finally:
        await engine.dispose()


@pytest.fixture(params=sorted(IMPLEMENTATIONS))
async def sink_ctx(request):
    async with _open_sink(request.param, request) as ctx:
        yield ctx


@pytest.fixture
def sink(sink_ctx):
    return sink_ctx[0]


@pytest.fixture
def make_proposal(sink_ctx):
    return sink_ctx[1].make_proposal


@pytest.fixture
def make_because(sink_ctx):
    return sink_ctx[1].make_because


@pytest.fixture
def read_row(sink_ctx):
    return sink_ctx[1].read_row


@pytest.fixture
def successor(sink_ctx) -> NullableAxis:
    """This implementation's promise about `replaced_by` — see
    `NullableAxis`. Three states, and `offer()` is how a test gets a value
    that matches the promise."""
    return sink_ctx[1].successor


@pytest.fixture
def source_axis(sink_ctx) -> NullableAxis:
    """The same, for `supersede`'s `source`. Named `source_axis` rather than
    `source` so a test body never reads as though the fixture were the
    citation itself."""
    return sink_ctx[1].source


# ---------------------------------------------------------------------------
# the structural guarantee — the reason the port exists
# ---------------------------------------------------------------------------


def test_the_port_has_no_delete_verb():
    """«Invalidate, never delete» is enforced by the port's SHAPE, not by both
    sides remembering it. A `delete`/`remove`/`purge` on `DreamSink` would make
    the rule a convention again, and a convention is what gets forgotten at 3am.

    Checked on the Protocol itself, so it fails for EVERY implementation at once
    rather than once per store someone writes.
    """
    verbs = {n for n, _ in inspect.getmembers(DreamSink, inspect.isfunction)
             if not n.startswith("_")}

    assert verbs == {"propose", "supersede"}, (
        f"DreamSink must expose exactly propose+supersede; found {sorted(verbs)}"
    )


def test_an_implementation_satisfies_the_protocol(sink):
    assert isinstance(sink, DreamSink)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


async def test_propose_returns_one_id_per_proposal_in_order(sink, make_proposal):
    """Ids, not a count — `supersede` must be able to NAME what `propose`
    produced, and a count cannot. Order is what lets a caller pair the ids back
    to the proposals it sent."""
    ids = await sink.propose(
        key="room-1",
        proposals=(make_proposal(), make_proposal(), make_proposal()),
        now_ms=NOW,
    )

    assert len(ids) == 3
    assert len(set(ids)) == 3, "each written row needs its own id"


async def test_an_empty_propose_is_legal_and_returns_nothing(sink):
    """`propose(key, ())` is `nothing_found` — «I looked and there was nothing».

    It is NOT the same as never calling `propose`, which is `unavailable` —
    «I could not check». R-27's three states, expressed in the port. Treating an
    empty tuple as "skip the call" silently drops the language adaptor's
    time-driven decay, whose whole purpose is to fade a room that said nothing.
    """
    ids = await sink.propose(key="room-1", proposals=(), now_ms=NOW)

    assert tuple(ids) == ()


async def test_an_empty_propose_still_reaches_the_fake():
    """The distinction above is only real if the sink can SEE the empty call.
    A sink that cannot tell «ran, found nothing» from «never ran» cannot host
    the rescore, and the port's three-state claim would be decorative.

    Fake-only, deliberately: `.empty_proposes` is `_FakeDreamSink`'s own
    bookkeeping, not something `DreamSink` promises a caller can observe (no
    read verb exists to expose it). Nothing in the fact tier has a
    time-driven side effect on an empty batch the way the language adaptor's
    rescore does, so `SqlFactStore` has nothing to assert here — this test
    checks that the FAKE itself is not a decorative double for the property
    `test_an_empty_propose_is_legal_and_returns_nothing` above relies on
    actually being exercised, not a port-level guarantee.
    """
    sink = _FakeDreamSink()

    await sink.propose(key="room-1", proposals=(), now_ms=NOW)

    assert sink.empty_proposes == [("room-1", NOW)]


async def test_keys_do_not_leak_into_each_other(
    sink, make_proposal, make_because, successor,
):
    """The key is the adaptor's own — `scope_id` for the room adaptors, a
    workspace for the fact adaptor (DEC-105). Whatever it means, two keys are
    two different units of work."""
    a = await sink.propose(key="room-a", proposals=(make_proposal(),), now_ms=NOW)
    b = await sink.propose(key="room-b", proposals=(make_proposal(),), now_ms=NOW)

    # Every argument but the key is one this store accepts — the successor
    # from the axis, the reason from `make_because` — so the ONLY reason this
    # affects zero rows is the wrong key. A store that refuses one of them
    # first would otherwise pass this test without ever comparing the keys.
    moved = await sink.supersede(
        key="room-b", target=a[0], replaced_by=successor.offer(b[0]),
        because=make_because(), source=None, now_ms=NOW + 1,
    )

    assert moved == 0, "a key must not be able to supersede another key's row"


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


async def test_supersede_retires_a_row_proposed_earlier(
    sink, make_proposal, make_because, successor, source_axis,
):
    ids = await sink.propose(
        key="room-1", proposals=(make_proposal(),), now_ms=NOW,
    )
    new_ids = await sink.propose(
        key="room-1", proposals=(make_proposal(),), now_ms=NOW,
    )

    affected = await sink.supersede(
        key="room-1", target=ids[0], replaced_by=successor.offer(new_ids[0]),
        because=make_because(), source=source_axis.offer("keengamer"),
        now_ms=NOW + 1,
    )

    assert affected == 1


async def test_superseding_an_unknown_row_reports_zero_rather_than_raising(
    sink, make_proposal, make_because, successor,
):
    """A background pass that raises on a row someone else already retired takes
    the tick down with it. Reporting zero lets the caller notice and continue."""
    # Everything but the target is legal for this store — a real successor
    # where one is recordable, a reason from `make_because` — so the unknown
    # target is the only variable. (The reason is no longer a descriptive
    # literal: see the module docstring's correction under point 2.)
    real = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)

    affected = await sink.supersede(
        key="room-1", target="row-does-not-exist",
        replaced_by=successor.offer(real[0]), because=make_because(),
        source=None, now_ms=NOW,
    )

    assert affected == 0


async def test_superseding_twice_affects_nothing_the_second_time(
    sink, make_proposal, make_because, successor,
):
    """Idempotent, because the Dreamer loop can legitimately re-fire over work
    it already did — the same property `RoomConsolidator`'s contract turns on."""
    ids = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)
    new_ids = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)
    reason = make_because()
    heir = successor.offer(new_ids[0])
    first = await sink.supersede(
        key="room-1", target=ids[0], replaced_by=heir, because=reason,
        source=None, now_ms=NOW + 1,
    )

    # Identical arguments — the second call affects zero rows because the row
    # is already retired, not because anything about the call changed.
    second = await sink.supersede(
        key="room-1", target=ids[0], replaced_by=heir, because=reason,
        source=None, now_ms=NOW + 2,
    )

    assert (first, second) == (1, 0)


async def test_a_supersession_records_why_and_optionally_from_where(
    sink, make_proposal, make_because, read_row, successor,
):
    """`because` is required, `source` is not: a supersession always has a
    reason and only sometimes a citation. Asserts what THIS test passed in,
    not a fixed literal (`because`/`source` vocabulary is a tier's own —
    `contradicted` means nothing to the note tier — so the round trip is the
    portable property, not the string)."""
    old = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)
    new = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)
    reason = make_because()

    await sink.supersede(
        key="room-1", target=old[0], replaced_by=successor.offer(new[0]),
        because=reason, source=None, now_ms=NOW + 1,
    )

    row = await read_row(sink, key="room-1", row_id=old[0])
    assert row.because == reason
    assert row.source is None


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


async def test_provenance_survives_the_write(sink, make_proposal, read_row):
    """`instruction_sha256` and `model` are the columns DEC-125/126 added to
    every adaptor-written table, and DEC-128 fixes what the digest must be: what
    was actually SENT, read per run. A sink that drops them writes rows that
    look right and can never be traced back to what produced them.

    Asserted against what `p` itself carries, not a hard-coded literal: the
    two implementations' factories mint different valid values, so a fixed
    constant here would be an assertion about the fake's defaults, not about
    provenance surviving the write.
    """
    p = make_proposal(established_by="run-42")

    ids = await sink.propose(key="room-1", proposals=(p,), now_ms=NOW)

    row = await read_row(sink, key="room-1", row_id=ids[0])
    assert row.proposal.instruction_sha256 == p.instruction_sha256
    assert row.proposal.model == p.model
    assert row.proposal.established_by == "run-42"


def test_a_proposal_is_frozen():
    """Frozen so a sink cannot mutate a caller's proposal mid-batch — one tick
    hands the same objects to one sink, and a sink that rewrote provenance in
    place would corrupt the batch it was given."""
    p = _fake_proposal_factory()

    with pytest.raises(FrozenInstanceError):
        p.model = "something-else"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# the successor axis — replaced_by
# ---------------------------------------------------------------------------


async def test_a_supersession_can_name_its_successor(
    sink, make_proposal, make_because, read_row, successor, source_axis,
):
    """The field two tiers needed and the port did not have.

    Without it a supersession records that something died and not what replaced
    it — the fact tier's `(superseded_by is null) = (superseded_at is null)`
    biconditional cannot be satisfied at all, and a merge in the note tier is
    unauditable. Both derived that from their own specs independently, which is
    what makes it a port defect rather than one tier's preference.

    What round-trips is `successor.offer(...)` — the successor THIS store
    accepts, which for a `REFUSED` tier is no successor at all: a candidate is
    succeeded by a `fact` in another table, never by another candidate. The
    assertion is that whatever was offered is what comes back, so a store that
    invented a successor, or silently kept a different one, fails here for
    every implementation. That a non-null offer is REFUSED rather than dropped
    is measured in
    `test_a_field_the_store_cannot_record_is_refused_not_dropped` below.
    """
    old = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)
    new = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW + 1)

    affected = await sink.supersede(
        key="room-1", target=old[0], replaced_by=successor.offer(new[0]),
        because=make_because(),
        source=source_axis.offer("https://example.test/correction"),
        now_ms=NOW + 2,
    )

    row = await read_row(sink, key="room-1", row_id=old[0])
    assert affected == 1
    assert row.replaced_by == successor.offer(new[0])


async def test_a_supersession_with_no_successor_is_legal_or_refused_by_the_store(
    sink, make_proposal, make_because, read_row, successor,
):
    """Permissive at the port, strict at each implementation.

    The note tier's `retire` has no successor — an anchor date passed, the
    uncertainty is simply over, and a dinner that happened is not replaced by
    anything. A non-nullable `replaced_by` would force it to mint a placeholder
    id to satisfy the signature, which is the invent-a-value-to-satisfy-a-schema
    move forbidden elsewhere in this codebase.

    A sink whose own rows require a successor refuses the null ITSELF — that
    biconditional is a fact-tier rule, enforced in its CHECK, not here. Which
    branch runs is the entry's own `successor` axis say-so: `OPTIONAL`
    (`"fake"`) and `REFUSED` (`"candidate"`, for which the null is the only
    legal value) both take the legal branch; `REQUIRED` (`"sql"`,
    `SqlFactStore`) raises `ValueError`.

    `because` comes from `make_because()` — this call affects a row, so the
    file's own rule applies. It used to hard-code `"retired"`, which is not a
    legal reason in the fact tier, the candidate tier or the note tier; the
    only implementation it ever ran against was the fake, which accepts
    anything.
    """
    ids = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)

    if successor.null_is_legal:
        affected = await sink.supersede(
            key="room-1", target=ids[0], replaced_by=None,
            because=make_because(), source=None, now_ms=NOW + 1,
        )
        row = await read_row(sink, key="room-1", row_id=ids[0])
        assert affected == 1
        assert row.replaced_by is None
    else:
        with pytest.raises(ValueError):
            await sink.supersede(
                key="room-1", target=ids[0], replaced_by=None,
                because=make_because(), source=None, now_ms=NOW + 1,
            )


async def test_source_and_replaced_by_are_kept_apart(
    sink, make_proposal, make_because, read_row, successor, source_axis,
):
    """`source` is a WARRANT — a URL or equivalent pointer to evidence — and
    never the id of a row in the tier. They are two fields because they are two
    kinds of thing: overloading one field to mean "a citation" in one tier and
    "the surviving row" in another is a collision where whoever writes first
    fixes it wrong for the other.

    Each field is offered through its OWN axis, which is the point at this
    grain too: a tier that records one and refuses the other (the candidate
    store records neither; nothing stops a tier recording only one) must not
    be able to satisfy this test by putting the citation where the successor
    goes. Hence the last assertion, which holds for every implementation
    including those that record nothing here.
    """
    old = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW)
    new = await sink.propose(key="room-1", proposals=(make_proposal(),), now_ms=NOW + 1)
    citation = "https://keengamer.test/big-walk"

    await sink.supersede(
        key="room-1", target=old[0], replaced_by=successor.offer(new[0]),
        because=make_because(), source=source_axis.offer(citation),
        now_ms=NOW + 2,
    )

    row = await read_row(sink, key="room-1", row_id=old[0])
    assert row.replaced_by == successor.offer(new[0])
    assert row.source == source_axis.offer(citation)
    assert row.replaced_by != citation, (
        "a citation must never land in the successor column"
    )


async def test_a_field_the_store_cannot_record_is_refused_not_dropped(
    sink, make_proposal, make_because, read_row, successor, source_axis,
):
    """R-27 applied to a column instead of a call.

    `replaced_by` and `source` are both nullable AT THE PORT, so a store with
    nowhere to put one has exactly two options: refuse it, or accept it and
    drop it. Accepting-and-dropping reports a successor (or a citation)
    recorded that nothing recorded and nothing can ever surface — an ERROR
    wearing `ok`'s clothes, which is the shape this codebase refuses one layer
    up.

    Runs for every implementation with no skip: the entry's axis says which
    outcome is correct here, and both outcomes are asserted in one body — a
    store that CAN record the field must round-trip it, a store that cannot
    must raise AND leave the row alone. One fresh row per field, so a refusal
    on one can never be what explains the other.
    """
    citation = "https://example.test/warrant"
    ids = await sink.propose(
        key="room-1",
        proposals=(make_proposal(), make_proposal(), make_proposal()),
        now_ms=NOW,
    )
    heir, for_successor, for_source = ids

    if successor is NullableAxis.REFUSED:
        with pytest.raises(ValueError):
            await sink.supersede(
                key="room-1", target=for_successor, replaced_by=heir,
                because=make_because(), source=source_axis.offer(citation),
                now_ms=NOW + 1,
            )
        untouched = await read_row(sink, key="room-1", row_id=for_successor)
        assert untouched.retired_at is None, (
            "a refused supersession must not half-retire the row"
        )
    else:
        affected = await sink.supersede(
            key="room-1", target=for_successor, replaced_by=heir,
            because=make_because(), source=source_axis.offer(citation),
            now_ms=NOW + 1,
        )
        row = await read_row(sink, key="room-1", row_id=for_successor)
        assert (affected, row.replaced_by) == (1, heir)

    if source_axis is NullableAxis.REFUSED:
        with pytest.raises(ValueError):
            await sink.supersede(
                key="room-1", target=for_source, replaced_by=successor.offer(heir),
                because=make_because(), source=citation, now_ms=NOW + 2,
            )
        untouched = await read_row(sink, key="room-1", row_id=for_source)
        assert untouched.retired_at is None, (
            "a refused supersession must not half-retire the row"
        )
    else:
        affected = await sink.supersede(
            key="room-1", target=for_source, replaced_by=successor.offer(heir),
            because=make_because(), source=citation, now_ms=NOW + 2,
        )
        row = await read_row(sink, key="room-1", row_id=for_source)
        assert (affected, row.source) == (1, citation)


# ---------------------------------------------------------------------------
# foreign payloads — "refused, not duck-typed"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl_name", sorted(IMPLEMENTATIONS))
async def test_a_foreign_payload_is_refused_not_duck_typed(impl_name, request):
    """`Proposal.payload` is `object` at the port so the runtime never names
    an adaptor's own value type — which is exactly why nothing upstream of a
    sink can catch a wrong value arriving at it. Each implementation that
    OWNS a payload type must refuse anything else itself; this is the shared
    assertion for that, using another entry's factory output as the foreign
    value (never a hand-rolled bad object) so it stays honest as payload
    types evolve — see `FOREIGN_PAYLOAD_EXEMPT` for the one implementation
    that structurally cannot be tested this way, and why.
    """
    if impl_name in FOREIGN_PAYLOAD_EXEMPT:
        pytest.skip(FOREIGN_PAYLOAD_EXEMPT[impl_name])

    async with _open_sink(impl_name, request) as (sink, _impl):
        foreign_name = next(n for n in IMPLEMENTATIONS if n != impl_name)
        foreign_proposal = IMPLEMENTATIONS[foreign_name].make_proposal()

        with pytest.raises(TypeError):
            await sink.propose(
                key="room-1", proposals=(foreign_proposal,), now_ms=NOW,
            )

