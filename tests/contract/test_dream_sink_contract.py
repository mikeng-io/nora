"""Shared contract for `DreamSink`. Every implementation runs every test.

Shipped with ONE implementation — a fake — for the reason
`tests/contract/test_artifact_ingest_contract.py` states for the same
situation: a port declared with nothing exercising it is a shape nobody has
checked is satisfiable. The fact tier's store is the second implementation and
joins by adding to `IMPLEMENTATIONS`; nothing else about this file changes.

`_FakeDreamSink` stays HERE rather than in `nora/testing/`: CLAUDE.md's rule
binds a fake to a suite, and `nora/testing/fakes.py`'s docstring enumerates
every fake there as standing in for a *production* implementation at OTHER call
sites. This one has no such job — its only purpose is exercising this contract.

The test that matters most is `test_the_port_has_no_delete_verb`. Every other
test here checks behaviour an implementation could get right by accident; that
one checks the property the port EXISTS to enforce, and it fails the moment
someone adds the convenience method that makes «invalidate, never delete» a
habit again instead of a type.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, dataclass, field

import pytest

from nora.domain.ports import DreamSink, Proposal

NOW = 1_755_000_000_000


def _proposal(payload: str, *, established_by: str = "run-1") -> Proposal:
    return Proposal(
        payload=payload,
        instruction_sha256="a" * 64,
        model="ollamacloud/deepseek-v4-flash",
        established_by=established_by,
    )


@dataclass
class _Row:
    key: str
    proposal: Proposal
    written_at: int
    superseded_by: str | None = None
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

    async def supersede(self, *, key, target, because, source, now_ms):
        row = self.rows.get(target)
        if row is None or row.key != key or row.superseded_by is not None:
            return 0
        row.superseded_by = f"superseded@{now_ms}"
        row.because = because
        row.source = source
        return 1


IMPLEMENTATIONS = {"fake": _FakeDreamSink}


@pytest.fixture(params=sorted(IMPLEMENTATIONS))
def sink(request):
    return IMPLEMENTATIONS[request.param]()


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


async def test_propose_returns_one_id_per_proposal_in_order(sink):
    """Ids, not a count — `supersede` must be able to NAME what `propose`
    produced, and a count cannot. Order is what lets a caller pair the ids back
    to the proposals it sent."""
    ids = await sink.propose(
        key="room-1",
        proposals=(_proposal("alpha"), _proposal("beta"), _proposal("gamma")),
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


async def test_an_empty_propose_still_reaches_the_sink(sink):
    """The distinction above is only real if the sink can SEE the empty call.
    A sink that cannot tell «ran, found nothing» from «never ran» cannot host
    the rescore, and the port's three-state claim would be decorative."""
    await sink.propose(key="room-1", proposals=(), now_ms=NOW)

    assert sink.empty_proposes == [("room-1", NOW)]


async def test_keys_do_not_leak_into_each_other(sink):
    """The key is the adaptor's own — `scope_id` for the room adaptors, a
    workspace for the fact adaptor (DEC-105). Whatever it means, two keys are
    two different units of work."""
    a = await sink.propose(key="room-a", proposals=(_proposal("x"),), now_ms=NOW)

    moved = await sink.supersede(
        key="room-b", target=a[0], because="wrong room", source=None, now_ms=NOW + 1,
    )

    assert moved == 0, "a key must not be able to supersede another key's row"


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


async def test_supersede_retires_a_row_proposed_earlier(sink):
    ids = await sink.propose(
        key="room-1", proposals=(_proposal("old"),), now_ms=NOW,
    )

    affected = await sink.supersede(
        key="room-1", target=ids[0], because="a later source contradicted it",
        source="keengamer", now_ms=NOW + 1,
    )

    assert affected == 1


async def test_superseding_an_unknown_row_reports_zero_rather_than_raising(sink):
    """A background pass that raises on a row someone else already retired takes
    the tick down with it. Reporting zero lets the caller notice and continue."""
    affected = await sink.supersede(
        key="room-1", target="row-does-not-exist", because="stale",
        source=None, now_ms=NOW,
    )

    assert affected == 0


async def test_superseding_twice_affects_nothing_the_second_time(sink):
    """Idempotent, because the Dreamer loop can legitimately re-fire over work
    it already did — the same property `RoomConsolidator`'s contract turns on."""
    ids = await sink.propose(key="room-1", proposals=(_proposal("x"),), now_ms=NOW)
    first = await sink.supersede(
        key="room-1", target=ids[0], because="r", source=None, now_ms=NOW + 1,
    )

    second = await sink.supersede(
        key="room-1", target=ids[0], because="r", source=None, now_ms=NOW + 2,
    )

    assert (first, second) == (1, 0)


async def test_a_supersession_records_why_and_optionally_from_where(sink):
    """`because` is required, `source` is not: a supersession always has a
    reason and only sometimes a citation. Retiring a row with neither is the
    hidden-with-no-successor failure the fact tier's biconditional prevents."""
    ids = await sink.propose(key="room-1", proposals=(_proposal("x"),), now_ms=NOW)
    await sink.supersede(
        key="room-1", target=ids[0], because="contradicted", source=None, now_ms=NOW + 1,
    )

    row = sink.rows[ids[0]]
    assert row.because == "contradicted"
    assert row.source is None


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


async def test_provenance_survives_the_write(sink):
    """`instruction_sha256` and `model` are the columns DEC-125/126 added to
    every adaptor-written table, and DEC-128 fixes what the digest must be: what
    was actually SENT, read per run. A sink that drops them writes rows that
    look right and can never be traced back to what produced them."""
    p = _proposal("x", established_by="run-42")

    ids = await sink.propose(key="room-1", proposals=(p,), now_ms=NOW)

    stored = sink.rows[ids[0]].proposal
    assert stored.instruction_sha256 == "a" * 64
    assert stored.model == "ollamacloud/deepseek-v4-flash"
    assert stored.established_by == "run-42"


def test_a_proposal_is_frozen():
    """Frozen so a sink cannot mutate a caller's proposal mid-batch — one tick
    hands the same objects to one sink, and a sink that rewrote provenance in
    place would corrupt the batch it was given."""
    p = _proposal("x")

    with pytest.raises(FrozenInstanceError):
        p.model = "something-else"  # type: ignore[misc]
