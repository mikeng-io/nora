"""Shared contract for the note store. Every implementation runs every test.

Two implementations: `nora.infra.sql.notes.SqlNoteStore` over real Postgres in
a testcontainer, and `nora.testing.InMemoryNoteStore` built from
`nora.domain.notes` alone. CLAUDE.md's rule is why this file exists at all — a
port never gains a second implementation, *including a test fake*, without a
suite both pass. It is also why `InMemoryNoteStore` is not a dict that says yes:
the compare-and-swap refusal, the unit scoping and the head-only search are
asserted HERE, against both, so a fake that accepted a stale parent would be red
rather than convenient.

**What the suite needs that the store's surface does not offer, and why it is
per-implementation rather than hard-coded.** This is
`tests/contract/test_dream_sink_contract.py`'s rule applied again:

1. **Retiring a note.** `standing` must exclude retired notes, and the store has
   no `retire` verb — retirement arrives later as `supersede`
   (DEC-130), owned by the adaptor, not by this store. Rather than inventing a
   seventh method so a test can reach a state, `_Harness` carries a `retire`
   callable that reaches each implementation the way that implementation
   actually stores the flag (a raw `UPDATE` for sql, a `dataclasses.replace` for
   memory). A test that can only be written by widening production surface is a
   test asking you to widen the surface for the test's sake.

Note ids are opaque strings at the domain layer but `pg.UUID` in the database,
so every id this suite invents for a "no such note" case is a real `uuid4` —
a non-UUID string is a DIFFERENT case (asserted once, on its own) and reaching
for one by accident would make several tests pass for the wrong reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.notes import (
    Note,
    NoteRevision,
    NoteSupersedeReason,
    RetiredNoteError,
    StaleNoteHeadError,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import (
    NOTE,
    NOTE_REVISION,
)
from nora.testing import InMemoryNoteStore

NOW = 1_755_200_000_000
WS = "unit-alpha"
OTHER_WS = "unit-beta"


class _NoteStore(Protocol):
    """The surface both implementations offer, named here so the suite type-checks
    against the SHAPE rather than against whichever class the fixture happened to
    yield — a `mypy` pass that only ever saw `SqlNoteStore` would not notice the
    fake drifting off it."""

    async def create(
        self, *, unit: str, title: str, description: str, uncertainty: str,
        closing_condition: str, established_in: str, body: str,
        established_by: str, now_ms: int, anchor_date: int | None = ...,
        anchor_entity_ids: tuple[str, ...] = ...,
    ) -> Note: ...
    async def standing(self, *, unit: str) -> tuple[Note, ...]: ...
    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...
    async def append_revision(
        self, *, unit: str, note_id: str, body: str,
        parent_revision_id: str, established_by: str, now_ms: int,
    ) -> NoteRevision: ...
    async def search(
        self, *, unit: str, query: str, limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...
    async def revision_chain(
        self, *, unit: str, note_id: str,
    ) -> tuple[NoteRevision, ...]: ...
    async def successor(self, *, unit: str, note_id: str) -> Note | None: ...
    async def merged_successor_by_title(
        self, *, unit: str, title: str,
    ) -> Note | None: ...


@dataclass(frozen=True)
class _Harness:
    store: _NoteStore
    retire: Callable[[str, int], Awaitable[None]]


@pytest.fixture(scope="module")
def _notes_db(pg_admin: Any) -> Iterator[str]:
    """ONE database and ONE migration run for this whole file.

    The shared function-scoped `pg_url` creates a database, runs the full
    migration chain and drops the database **per test**, one full rebuild per
    sql-param test. Churn of that size leaves the container degraded, and the
    cost lands on unrelated tests: `tests/integration/test_latency.py` (a p95
    budget on Nora's non-model overhead, one of the few tests that writes audit
    rows to this same container) slows by an order of magnitude with such a file
    present.

    Isolation is preserved by truncating the four note tables between tests
    (see the `notes` fixture) rather than by rebuilding the world. That is
    cheaper by orders of magnitude and gives the same guarantee this suite
    actually needs: no row written by one test is visible to the next.
    """
    dbname = "t_notes_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def notes(request: Any) -> AsyncIterator[_Harness]:
    if request.param == "memory":
        memory = InMemoryNoteStore()

        async def _retire_mem(note_id: str, now_ms: int) -> None:
            memory.notes[note_id] = replace(memory.notes[note_id], retired_at=now_ms)

        yield _Harness(memory, _retire_mem)
        return

    # The ENGINE is per test (cheap); the DATABASE and its migrations are
    # module-scoped (see `_notes_db`) because that is where the cost was.
    engine = create_sql_engine(request.getfixturevalue("_notes_db"))

    async def _retire_sql(note_id: str, now_ms: int) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(NOTE).where(NOTE.c.id == note_id).values(retired_at=now_ms)
            )

    try:
        yield _Harness(SqlNoteStore(engine), _retire_sql)
    finally:
        # Truncate rather than rebuild: same isolation guarantee this suite
        # needs — no row written by one test is visible to the next — at a
        # fraction of the cost. See `_notes_db` for what the rebuild was doing
        # to an unrelated test.
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate note, note_revision, note_entity, note_candidate, "
                "note_merge_decision, fact_entity cascade"
            ))
        await engine.dispose()


async def _make(
    store: _NoteStore,
    *,
    unit: str = WS,
    title: str = "Wedding venue",
    description: str = "Where the September wedding is held is still open.",
    body: str = "Two venues on the shortlist. Nobody has called either.",
    now_ms: int = NOW,
    anchor_date: int | None = 20260919,
    anchor_entity_ids: tuple[str, ...] = (),
) -> Note:
    return await store.create(
        unit=unit,
        title=title,
        description=description,
        uncertainty="which venue, and whether the date holds",
        closing_condition="a venue is booked and confirmed",
        established_in="conv-1",
        body=body,
        established_by="tag:turn-1",
        now_ms=now_ms,
        anchor_date=anchor_date,
        anchor_entity_ids=anchor_entity_ids,
    )


# --- create -----------------------------------------------------------------


async def test_create_leaves_a_readable_head(notes: _Harness) -> None:
    """A note whose `head_revision_id` names a revision that was never written
    is unreachable: `read` joins on it, so a dangling head is a note with a
    title in the index and no body behind it. This is why `create` writes both
    rows in one transaction."""
    note = await _make(notes.store)

    got = await notes.store.read(unit=WS, note_id=note.id)

    assert got is not None
    read_note, head = got
    assert read_note.id == note.id
    assert head.id == note.head_revision_id
    assert head.note_id == note.id
    assert head.body == "Two venues on the shortlist. Nobody has called either."
    assert head.parent_revision_id is None, "the first revision has no parent"
    assert head.established_by == "tag:turn-1"
    assert head.written_at == NOW
    assert read_note.is_standing


async def test_create_returns_a_note_carrying_what_it_was_given(
    notes: _Harness,
) -> None:
    note = await _make(notes.store, anchor_entity_ids=("e-1", "e-2"))

    assert note.unit == WS
    assert note.title == "Wedding venue"
    assert note.anchor_date == 20260919
    assert set(note.anchor_entity_ids) == {"e-1", "e-2"}
    assert note.created_at == NOW
    assert note.retired_at is None


async def test_create_refuses_a_note_with_no_anchor(notes: _Harness) -> None:
    """The domain type refuses it (`nora.domain.notes.Note.__post_init__`) and
    the table refuses it (`note_has_an_anchor_check`). The store must not have a
    path that reaches neither — which is what building the dataclass BEFORE the
    insert buys, and the reason both implementations raise the same `ValueError`
    rather than one raising and the other writing a row."""
    with pytest.raises(ValueError, match="anchor"):
        await _make(notes.store, anchor_date=None, anchor_entity_ids=())

    assert await notes.store.standing(unit=WS) == ()


# --- compare-and-swap -------------------------------------------


async def test_append_revision_accepts_the_current_head_and_moves_it(
    notes: _Harness,
) -> None:
    note = await _make(notes.store)

    rev = await notes.store.append_revision(
        unit=WS,
        note_id=note.id,
        body="Called both. The Central one is held until Friday.",
        parent_revision_id=note.head_revision_id,
        established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    assert rev.parent_revision_id == note.head_revision_id
    assert rev.id != note.head_revision_id
    assert rev.written_at == NOW + 1000
    got = await notes.store.read(unit=WS, note_id=note.id)
    assert got is not None
    read_note, head = got
    assert read_note.head_revision_id == rev.id, "the head moved"
    assert head.body == "Called both. The Central one is held until Friday."


async def test_append_revision_refuses_a_stale_parent_and_writes_nothing(
    notes: _Harness,
) -> None:
    """§6.3, and the whole reason `head_revision_id` exists. The second writer
    read the same head the first did; by the time it writes, the head has moved.
    Writing on top loses the first revision's arrangement silently, which is the
    one thing this tier holds."""
    note = await _make(notes.store)
    stale_parent = note.head_revision_id
    first = await notes.store.append_revision(
        unit=WS, note_id=note.id, body="the first writer's arrangement",
        parent_revision_id=stale_parent, established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    with pytest.raises(StaleNoteHeadError) as excinfo:
        await notes.store.append_revision(
            unit=WS, note_id=note.id, body="the second writer's overwrite",
            parent_revision_id=stale_parent, established_by="tag:turn-3",
            now_ms=NOW + 2000,
        )

    assert excinfo.value.observed_head == first.id
    got = await notes.store.read(unit=WS, note_id=note.id)
    assert got is not None
    read_note, head = got
    assert read_note.head_revision_id == first.id, "the head did not move"
    assert head.body == "the first writer's arrangement", "nothing was written"


async def test_append_revision_refuses_a_note_in_another_unit(
    notes: _Harness,
) -> None:
    """A CAS whose `WHERE` omitted the unit would let a caller holding a
    head id write into a unit it may not read — worse than a leak, because
    the write is then invisible from the side that made it."""
    note = await _make(notes.store)

    with pytest.raises(StaleNoteHeadError) as excinfo:
        await notes.store.append_revision(
            unit=OTHER_WS, note_id=note.id, body="a cross-unit write",
            parent_revision_id=note.head_revision_id,
            established_by="tag:turn-2", now_ms=NOW + 1000,
        )

    assert excinfo.value.observed_head is None, (
        "from the other unit the note does not exist at all"
    )
    got = await notes.store.read(unit=WS, note_id=note.id)
    assert got is not None
    assert got[1].body == "Two venues on the shortlist. Nobody has called either."


async def test_append_revision_on_an_unknown_note_refuses(notes: _Harness) -> None:
    with pytest.raises(StaleNoteHeadError) as excinfo:
        await notes.store.append_revision(
            unit=WS, note_id=str(uuid4()), body="into the void",
            parent_revision_id=str(uuid4()), established_by="tag:turn-2",
            now_ms=NOW + 1000,
        )

    assert excinfo.value.observed_head is None


async def test_append_revision_on_a_malformed_id_refuses_rather_than_raising_a_type_error(
    notes: _Harness,
) -> None:
    """Same reason `read` tolerates one: `note.id` is `pg.UUID`, so a non-UUID
    string reaches asyncpg as a driver-level type error, which is not a refusal
    a caller can act on. `StaleNoteHeadError` with no observed head is — its
    remedy («read it again») is right for both."""
    with pytest.raises(StaleNoteHeadError):
        await notes.store.append_revision(
            unit=WS, note_id="not-a-uuid", body="into the void",
            parent_revision_id=str(uuid4()), established_by="tag:turn-2",
            now_ms=NOW + 1000,
        )


# --- standing ---------------------------------------------------------------


async def test_standing_excludes_retired_notes(notes: _Harness) -> None:
    """The injected index renders from this read. A retired note that kept
    rendering is a closed question she is asked to keep tracking, and an
    uncapped index (§5.1) has no top-N to hide it behind."""
    live = await _make(notes.store, title="Still open")
    dead = await _make(notes.store, title="Already settled")
    await notes.retire(dead.id, NOW + 5000)

    standing = await notes.store.standing(unit=WS)

    assert [n.id for n in standing] == [live.id]


async def test_standing_excludes_other_units(notes: _Harness) -> None:
    mine = await _make(notes.store, unit=WS, title="Mine")
    await _make(notes.store, unit=OTHER_WS, title="Not mine")

    standing = await notes.store.standing(unit=WS)

    assert [n.id for n in standing] == [mine.id]


async def test_standing_carries_the_head_id_a_revision_must_echo(
    notes: _Harness,
) -> None:
    """`standing` is not merely a listing: the head it reports is the value
    §6.2's read-before-write latch and §6.3's CAS both name."""
    note = await _make(notes.store)
    rev = await notes.store.append_revision(
        unit=WS, note_id=note.id, body="moved once",
        parent_revision_id=note.head_revision_id, established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    (only,) = await notes.store.standing(unit=WS)

    assert only.head_revision_id == rev.id


async def test_standing_is_ordered_oldest_first_and_deterministic(
    notes: _Harness,
) -> None:
    """The index is a rendered block on every tag. An unordered read makes the
    same notebook render in a different order turn to turn, which reads to the
    model as the notebook having changed when nothing did."""
    first = await _make(notes.store, title="First", now_ms=NOW)
    second = await _make(notes.store, title="Second", now_ms=NOW + 1)
    third = await _make(notes.store, title="Third", now_ms=NOW + 2)

    standing = await notes.store.standing(unit=WS)

    assert [n.id for n in standing] == [first.id, second.id, third.id]


async def test_standing_is_empty_when_nothing_is_standing(notes: _Harness) -> None:
    assert await notes.store.standing(unit=WS) == ()


# --- read -------------------------------------------------------------------


async def test_read_returns_none_across_a_unit_boundary(
    notes: _Harness,
) -> None:
    """Unreachable, not merely unrendered: a note from another unit must
    answer exactly as if it did not exist. Anything else lets a caller confirm
    the existence of a note it may not read."""
    note = await _make(notes.store, unit=OTHER_WS)

    assert await notes.store.read(unit=WS, note_id=note.id) is None
    assert await notes.store.read(unit=OTHER_WS, note_id=note.id) is not None


async def test_read_returns_none_for_an_unknown_id(notes: _Harness) -> None:
    assert await notes.store.read(unit=WS, note_id=str(uuid4())) is None


async def test_read_returns_none_for_a_malformed_id_rather_than_raising(
    notes: _Harness,
) -> None:
    """`note.id` is `pg.UUID`, so a non-UUID string would otherwise reach
    asyncpg as a raw type error instead of the ordinary "no such note" outcome.
    A store that raises here turns a bad tool argument into a dead turn."""
    assert await notes.store.read(unit=WS, note_id="not-a-uuid") is None


async def test_read_still_works_on_a_retired_note(notes: _Harness) -> None:
    """Retirement is a status change, not a delete (§7.4): history is retained
    so a lossy step can be re-derived. `standing` hides it; `read` does not."""
    note = await _make(notes.store)
    await notes.retire(note.id, NOW + 5000)

    got = await notes.store.read(unit=WS, note_id=note.id)

    assert got is not None
    assert got[0].retired_at == NOW + 5000
    assert got[0].is_standing is False


# --- search -----------------------------------------------------


async def test_search_never_returns_a_superseded_body(notes: _Harness) -> None:
    """The join is `note.head_revision_id = note_revision.id`, so the QUERY —
    not a partial index, which cannot express "is the head" — is what keeps a
    superseded body out. A hit on a body that is no longer the note's content
    returns something true-in-the-past as though it were current."""
    note = await _make(notes.store, body="Booked at the Peninsula, deposit paid.")
    await notes.store.append_revision(
        unit=WS, note_id=note.id,
        body="Cancelled the Peninsula. Looking at the Upper House instead.",
        parent_revision_id=note.head_revision_id, established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    assert await notes.store.search(unit=WS, query="deposit paid", limit=10) == ()
    hits = await notes.store.search(unit=WS, query="Upper House", limit=10)
    assert [n.id for n, _ in hits] == [note.id]
    assert hits[0][1] == "Cancelled the Peninsula. Looking at the Upper House instead."


async def test_search_matches_cantonese(notes: _Harness) -> None:
    """The reason the index is trigram and not `tsvector` (§5.3):
    `to_tsvector('english', …)` does not segment Chinese at all, so this body
    would be unfindable while the search reported `nothing_found` — R-27's
    failure wearing a search result's clothes. Asserted with a real CJK string,
    because that is the only input that can tell the two indexes apart."""
    note = await _make(
        notes.store,
        title="婚禮場地",
        description="九月婚禮嘅場地仲未定。",
        body="婚禮嘅場地仲未定，可能係中環嗰間酒店，但係未問過價錢。",
    )

    hits = await notes.store.search(unit=WS, query="酒店", limit=10)

    assert [n.id for n, _ in hits] == [note.id]
    assert "酒店" in hits[0][1]


async def test_search_is_case_insensitive(notes: _Harness) -> None:
    """`ilike`, not `like` — «did anyone mention a hotel?» must reach a body
    that opened its sentence with «Hotel»."""
    note = await _make(notes.store, body="Hotel shortlist: two left.")

    hits = await notes.store.search(unit=WS, query="hotel", limit=10)

    assert [n.id for n, _ in hits] == [note.id]


async def test_search_searches_bodies_never_titles(notes: _Harness) -> None:
    """§5.3: the title is already in the injected index on every turn, so
    matching it here earns nothing and hides the one thing search buys —
    reaching into a body the index structurally cannot carry."""
    await _make(
        notes.store,
        title="Hotel shortlist",
        description="Which hotel, still open.",
        body="Two left after Tuesday's call.",
    )

    assert await notes.store.search(unit=WS, query="hotel", limit=10) == ()


async def test_search_is_scoped_to_the_unit(notes: _Harness) -> None:
    await _make(notes.store, unit=OTHER_WS, body="a hotel in the other room")

    assert await notes.store.search(unit=WS, query="hotel", limit=10) == ()


async def test_search_excludes_retired_notes(notes: _Harness) -> None:
    """A retired note is absent from the injected index, so a hit on one hands
    her a note whose title she cannot see and whose question is already closed —
    the same true-in-the-past error a superseded body would be."""
    note = await _make(notes.store, body="the shortlist mentioned a hotel")
    await notes.retire(note.id, NOW + 5000)

    assert await notes.store.search(unit=WS, query="hotel", limit=10) == ()


async def test_search_respects_its_limit(notes: _Harness) -> None:
    for i in range(3):
        await _make(notes.store, body=f"hotel option {i}", now_ms=NOW + i)

    assert len(await notes.store.search(unit=WS, query="hotel", limit=2)) == 2


async def test_search_returns_nothing_for_a_blank_query(notes: _Harness) -> None:
    """`ilike '%%'` matches every body. A blank probe emptying the whole
    notebook into a tool result is not a search; it is the index again, at full
    body length, unasked for."""
    await _make(notes.store)

    assert await notes.store.search(unit=WS, query="   ", limit=10) == ()
    assert await notes.store.search(unit=WS, query="", limit=10) == ()


async def test_search_treats_wildcards_as_literal_text(notes: _Harness) -> None:
    """An unescaped `%` in a caller's query is `ilike`'s own wildcard, so
    searching for `100%` would return every note in the unit. The query is
    data, and the in-memory implementation must escape it the same way rather
    than getting the right answer by using `in` and never meeting the
    question."""
    await _make(notes.store, body="the deposit is refundable")

    assert await notes.store.search(unit=WS, query="%", limit=10) == ()
    assert await notes.store.search(unit=WS, query="_", limit=10) == ()
    assert await notes.store.search(unit=WS, query="depos%able", limit=10) == ()


async def test_search_clamps_a_non_positive_limit(notes: _Harness) -> None:
    """`list[:0]` and `list[:-1]` disagree: the second silently drops the last
    entry instead of returning nothing, and Postgres errors outright on a
    negative LIMIT. Both implementations clamp, the way `standing_facts` does."""
    await _make(notes.store, body="a hotel")

    assert await notes.store.search(unit=WS, query="hotel", limit=0) == ()
    assert await notes.store.search(unit=WS, query="hotel", limit=-1) == ()


# --- supersede: both implementations, and the refusals in particular -------
#
# The double's `supersede` is exercised against the same assertions the real
# store faces, and the emphasis is on the REFUSALS, because that is where a
# double drifts first: it passes the happy path for free and refuses only what
# something else refuses for it.


async def test_supersede_accepts_a_retirement_with_no_successor(
    notes: _Harness,
) -> None:
    """The case `SqlFactStore.supersede` RAISES on, and the entire reason
    `supersede` types `replaced_by` as `str | None`. An anchor date
    passed and the dinner happened; it is replaced by nothing."""
    note = await _make(notes.store)

    affected = await notes.store.supersede(
        key=WS, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=NOW + 9,
    )

    assert affected == 1
    assert await notes.store.standing(unit=WS) == ()


async def test_supersede_records_the_successor_and_the_reason(
    notes: _Harness,
) -> None:
    survivor = await _make(notes.store, title="Grant wedding")
    duplicate = await _make(notes.store, title="Grant's wedding")

    affected = await notes.store.supersede(
        key=WS, target=duplicate.id, replaced_by=survivor.id,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=NOW + 9,
    )

    assert affected == 1
    standing = await notes.store.standing(unit=WS)
    assert [n.id for n in standing] == [survivor.id]
    # READ THE REASON BACK. `retire_reason` is also read by the sink suite's
    # `_note_read_row`, but that runs the SQL store alone, so deleting
    # `retire_reason=because` from the double would leave the whole suite green.
    # A field written by both implementations and asserted for one is not under
    # contract.
    retired = await notes.store.read(unit=WS, note_id=duplicate.id)
    assert retired is not None
    assert retired[0].retire_reason == NoteSupersedeReason.RETIRED_CLOSED
    assert retired[0].replaced_by == survivor.id


async def test_supersede_refuses_a_source_rather_than_dropping_it(
    notes: _Harness,
) -> None:
    """`note` has no column for a supersession's citation, and answering `ok`
    while discarding one is `unavailable` wearing `ok`'s clothes."""
    note = await _make(notes.store)

    with pytest.raises(ValueError):
        await notes.store.supersede(
            key=WS, target=note.id, replaced_by=None,
            because=NoteSupersedeReason.RETIRED_CLOSED,
            source="https://example.test/warrant", now_ms=NOW,
        )


async def test_supersede_refuses_a_reason_outside_the_tier_vocabulary(
    notes: _Harness,
) -> None:
    """Free text would make "how many notes did the curator retire this week,
    and on what ground" unanswerable, which is the reading that justifies the
    curator existing."""
    note = await _make(notes.store)

    with pytest.raises(ValueError):
        await notes.store.supersede(
            key=WS, target=note.id, replaced_by=None,
            because="seemed stale", source=None, now_ms=NOW,
        )


async def test_superseding_an_unknown_note_reports_zero_rather_than_raising(
    notes: _Harness,
) -> None:
    """A background pass that raises on a row it does not recognise takes the
    whole tick down with it. Includes a malformed id, which is the same
    situation with the same remedy."""
    assert await notes.store.supersede(
        key=WS, target="00000000-0000-0000-0000-00000000dead", replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=NOW,
    ) == 0
    assert await notes.store.supersede(
        key=WS, target="not-a-uuid", replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=NOW,
    ) == 0


async def test_supersede_cannot_reach_another_units_note(
    notes: _Harness,
) -> None:
    note = await _make(notes.store)

    assert await notes.store.supersede(
        key="someone-elses-unit", target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=NOW,
    ) == 0
    assert [n.id for n in await notes.store.standing(unit=WS)] == [note.id]


# --- the CHECK, proven against a real migrated database --------------------


async def test_the_database_refuses_a_body_over_the_ceiling(_notes_db) -> None:
    """**Driven against real Postgres with the full migration chain applied**,
    not asserted from `schema.py`. `compare_metadata` is blind to CHECK
    constraints, so a constraint declared on the `sa.Table` and never written to
    a database is documentation: deleting one from `schema.py` alone leaves a
    schema-only suite green.

    So this writes 32001 characters straight at the table, bypassing the domain
    type entirely, and reads back the refusal. Bypassing is the point: the
    domain refuses at 8000, and what the ceiling exists for is the writer that
    never reached the domain at all."""
    engine = create_sql_engine(_notes_db)
    try:
        note_id = str(uuid4())
        rev_id = str(uuid4())
        async with engine.begin() as conn:
            await conn.execute(sa.insert(NOTE).values(
                id=note_id, unit="ceiling-probe", title="t",
                description="d", uncertainty="u", closing_condition="c",
                established_in="conv-1", created_at=NOW,
                head_revision_id=rev_id, anchor_date=20261011,
                anchor_entity_ids=[],
            ))
        with pytest.raises(Exception) as caught:
            async with engine.begin() as conn:
                await conn.execute(sa.insert(NOTE_REVISION).values(
                    id=rev_id, note_id=note_id, body="x" * 32001,
                    parent_revision_id=None, written_at=NOW,
                    established_by="tag:miles",
                    citations=[],
                ))
        assert "note_revision_body_fits_the_ceiling_check" in str(caught.value), (
            f"refused, but not by the ceiling CHECK: {caught.value}"
        )
    finally:
        await engine.dispose()


async def test_the_database_accepts_a_body_between_the_two_numbers(
    _notes_db,
) -> None:
    """**The positive control, and it is the whole design in one assertion.**
    16000 characters is over the OPERATING cap (8000) and under the CEILING
    (32000), and the database must take it.

    That gap is deliberate: 8000 says «no writer should produce that» and is
    enforced in the domain where a refusal is reviewable; 32000 says «this row
    is corrupt». A body between them means something reached the table without
    passing the domain type — a bug to go and find, not a row to reject when
    somebody reads it. A CHECK at 8000 would collapse the two meanings and make
    a skipped-validation write indistinguishable from a raised config."""
    engine = create_sql_engine(_notes_db)
    try:
        note_id = str(uuid4())
        rev_id = str(uuid4())
        async with engine.begin() as conn:
            await conn.execute(sa.insert(NOTE).values(
                id=note_id, unit="ceiling-probe-2", title="t",
                description="d", uncertainty="u", closing_condition="c",
                established_in="conv-1", created_at=NOW,
                head_revision_id=rev_id, anchor_date=20261011,
                anchor_entity_ids=[],
            ))
            await conn.execute(sa.insert(NOTE_REVISION).values(
                id=rev_id, note_id=note_id, body="x" * 16000,
                parent_revision_id=None, written_at=NOW,
                established_by="tag:miles",
                citations=[],
            ))
            stored = (await conn.execute(
                sa.select(sa.func.length(NOTE_REVISION.c.body))
                .where(NOTE_REVISION.c.id == rev_id)
            )).scalar_one()
        assert stored == 16000, "and it is stored whole, not truncated"
    finally:
        await engine.dispose()


async def test_a_minted_id_is_shaped_the_way_the_egress_screen_reads(
    notes: _Harness,
) -> None:
    """**A double may be simpler than the real store; it may not DIFFER on a
    property something downstream branches on.**

    `nora.agent.dream_screen` walks every string in a Dreamer envelope item and
    `nora.agent.egress`'s `_UUID` drops any hyphenated uuid. So «is a minted
    note id identifier-shaped» is not cosmetic — it decides whether an adaptor
    that echoes one is inert in production, and it is the property
    `tests/contract/test_dream_adaptor_contract.py::
    test_inputs_renders_nothing_the_egress_screen_would_reject` rests its whole
    power on.

    A double minting ids like `note-1` and `rev-2` would hide a defect that makes
    the whole curator inert: production rejects every item while the double's
    ids sail through. This is the gate, asserted against BOTH implementations so
    a double cannot quietly reintroduce it; the comment on `_shaped_id` records
    the shape.
    """
    from nora.agent.egress import scan_identifiers

    note = await _make(notes.store)

    assert scan_identifiers(note.id) is not None, (
        f"this store mints {note.id!r}, which the Dreamer's egress screen does "
        f"NOT recognise as an identifier. The real store mints uuid4, which it "
        f"does — so every test written over this double is measuring the wrong "
        f"branch of the one condition that decides whether an adaptor works."
    )
    assert scan_identifiers(note.head_revision_id) is not None


# --- design 41 §7.5-7.6: an append requires an open note; a merged note forwards


async def _merge_away(
    notes: _Harness, *, duplicate: Note, survivor: Note, now_ms: int = NOW + 50,
) -> None:
    """A merge's retirement half (`merge_into`, guard 2): `supersede` with
    reason `RETIRED_MERGED`."""
    affected = await notes.store.supersede(
        key=WS, target=duplicate.id, replaced_by=survivor.id,
        because=NoteSupersedeReason.RETIRED_MERGED, source=None, now_ms=now_ms,
    )
    assert affected == 1


async def test_an_append_to_a_retired_note_raises_retired_note_error(
    notes: _Harness,
) -> None:
    """Guard 1. `supersede` never touches the head, so the head a writer read
    before a retirement still matches after it, and an append would succeed into
    a note nothing reads again. It must refuse, write nothing, and say where a
    merged note went."""
    survivor = await _make(notes.store, title="Saturday dinner")
    duplicate = await _make(notes.store, title="Miles 星期六飯局細節未定")
    await _merge_away(notes, duplicate=duplicate, survivor=survivor)

    with pytest.raises(RetiredNoteError) as excinfo:
        await notes.store.append_revision(
            unit=WS, note_id=duplicate.id, body="the late update",
            parent_revision_id=duplicate.head_revision_id,
            established_by="tag:turn-9", now_ms=NOW + 60,
        )

    assert isinstance(excinfo.value, StaleNoteHeadError), (
        "every existing `except StaleNoteHeadError` must still hold"
    )
    assert excinfo.value.retire_reason == NoteSupersedeReason.RETIRED_MERGED
    assert excinfo.value.replaced_by == survivor.id
    got = await notes.store.read(unit=WS, note_id=duplicate.id)
    assert got is not None and got[1].id == duplicate.head_revision_id, (
        "nothing was written"
    )


async def test_successor_follows_a_merged_chain_to_the_open_note(
    notes: _Harness,
) -> None:
    """§7.5: a merged note forwards; a chain (C merged into B, B later into A)
    ends on the open note; an open note has no successor."""
    a = await _make(notes.store, title="A")
    b = await _make(notes.store, title="B")
    c = await _make(notes.store, title="C")
    await _merge_away(notes, duplicate=c, survivor=b, now_ms=NOW + 50)
    await _merge_away(notes, duplicate=b, survivor=a, now_ms=NOW + 60)

    assert (await notes.store.successor(unit=WS, note_id=c.id)).id == a.id
    assert (await notes.store.successor(unit=WS, note_id=b.id)).id == a.id
    assert await notes.store.successor(unit=WS, note_id=a.id) is None
    assert await notes.store.successor(unit="elsewhere", note_id=c.id) is None


async def test_only_a_merged_note_forwards(notes: _Harness) -> None:
    """A `replaced_by` on a closed note is provenance (written by
    hand), not a promise the successor carries the same question."""
    kept = await _make(notes.store, title="kept")
    closed = await _make(notes.store, title="closed")
    await notes.store.supersede(
        key=WS, target=closed.id, replaced_by=kept.id,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=NOW + 9,
    )

    assert await notes.store.successor(unit=WS, note_id=closed.id) is None
    assert await notes.store.merged_successor_by_title(
        unit=WS, title="closed") is None


async def test_a_merged_title_forwards_and_an_ambiguous_one_does_not(
    notes: _Harness,
) -> None:
    """§7.5: the title a turn still sees forwards to its survivor, normalised
    the way every resolver compares titles. Two merged notes of one title
    forwarding to DIFFERENT open notes answer nothing: picking one is the
    guess every resolver in this tier refuses."""
    survivor = await _make(notes.store, title="Saturday dinner")
    dup = await _make(notes.store, title="星期六 26 號飯局細節未定")
    await _merge_away(notes, duplicate=dup, survivor=survivor)

    got = await notes.store.merged_successor_by_title(
        unit=WS, title="  星期六 26 號飯局細節未定 ")
    assert got is not None and got.id == survivor.id

    other = await _make(notes.store, title="Sunday brunch")
    twin = await _make(notes.store, title="星期六 26 號飯局細節未定")
    await _merge_away(notes, duplicate=twin, survivor=other, now_ms=NOW + 70)
    assert await notes.store.merged_successor_by_title(
        unit=WS, title="星期六 26 號飯局細節未定") is None



# --- merge_into and the head-checked retire (design 41 §7.6, guards 2 and 3) --


async def _fold(
    notes: _Harness, *, survivor: Note, duplicate: Note,
    survivor_head: str | None = None, duplicate_head: str | None = None,
    body: str = "folded body", anchor_date: int | None = 20260927,
    reset_strikes: bool = True, now_ms: int = NOW + 100,
) -> NoteRevision:
    return await notes.store.merge_into(
        unit=WS, survivor_id=survivor.id,
        survivor_head=survivor_head or survivor.head_revision_id,
        duplicate_id=duplicate.id,
        duplicate_head=duplicate_head or duplicate.head_revision_id,
        body=body, anchor_date=anchor_date, revisit_at=NOW + 5_000,
        reset_strikes=reset_strikes,
        established_by="dream:note-merge:unit-alpha:1", now_ms=now_ms,
    )


async def test_merge_into_folds_the_duplicate_into_the_survivor(
    notes: _Harness,
) -> None:
    """Guard 2's happy path: the survivor gains one appended revision and the
    fold's anchor and wake; the duplicate is retired `retired-merged` naming
    the survivor, keeps its own chain, and forwards to it."""
    survivor = await _make(notes.store, title="Saturday dinner",
                           anchor_date=20260923)
    duplicate = await _make(notes.store, title="Neway booking",
                            anchor_date=20260927)

    rev = await _fold(notes, survivor=survivor, duplicate=duplicate)

    got = await notes.store.read(unit=WS, note_id=survivor.id)
    assert got is not None
    note, head = got
    assert head.id == rev.id and head.body == "folded body"
    assert head.parent_revision_id == survivor.head_revision_id
    assert note.anchor_date == 20260927
    assert note.revisit_at == NOW + 5_000
    assert note.retired_at is None
    gone = await notes.store.read(unit=WS, note_id=duplicate.id)
    assert gone is not None
    assert gone[0].retire_reason == NoteSupersedeReason.RETIRED_MERGED
    assert gone[0].replaced_by == survivor.id
    assert gone[1].id == duplicate.head_revision_id, "the chain is kept"
    assert (await notes.store.successor(
        unit=WS, note_id=duplicate.id)).id == survivor.id
    assert [n.id for n in await notes.store.standing(unit=WS)] == [survivor.id]


async def test_merge_into_rolls_back_when_either_head_moved(
    notes: _Harness,
) -> None:
    """Guard 2: ONE transaction with a compare-and-swap on BOTH heads. A moved
    survivor refuses before anything is written; a moved duplicate refuses
    AFTER the survivor's swap would have landed, and that half must roll
    back — no appended revision, no moved head, the duplicate still open."""
    survivor = await _make(notes.store, title="Saturday dinner")
    duplicate = await _make(notes.store, title="Neway booking")

    # (1) the survivor moved under the pass.
    moved = await notes.store.append_revision(
        unit=WS, note_id=survivor.id, body="Ada booked it",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:ada", now_ms=NOW + 10,
    )
    with pytest.raises(StaleNoteHeadError):
        await _fold(notes, survivor=survivor, duplicate=duplicate)

    # (2) the survivor is current, the DUPLICATE moved.
    dup_moved = await notes.store.append_revision(
        unit=WS, note_id=duplicate.id, body="Neway at 8",
        parent_revision_id=duplicate.head_revision_id,
        established_by="tag:ben", now_ms=NOW + 20,
    )
    with pytest.raises(StaleNoteHeadError):
        await _fold(notes, survivor=survivor, duplicate=duplicate,
                    survivor_head=moved.id)

    s_note, s_head = await notes.store.read(unit=WS, note_id=survivor.id)
    assert s_head.id == moved.id, "the survivor's half rolled back"
    assert len(await notes.store.revision_chain(
        unit=WS, note_id=survivor.id)) == 2, "no orphan revision"
    assert s_note.anchor_date == survivor.anchor_date
    d_note, d_head = await notes.store.read(unit=WS, note_id=duplicate.id)
    assert d_note.retired_at is None and d_head.id == dup_moved.id

    # Positive control: with both heads current the same call lands.
    await _fold(notes, survivor=survivor, duplicate=duplicate,
                survivor_head=moved.id, duplicate_head=dup_moved.id)
    assert (await notes.store.read(unit=WS, note_id=duplicate.id))[0].retired_at


async def test_merge_into_refuses_a_retired_survivor(notes: _Harness) -> None:
    """The survivor's swap carries `retired_at IS NULL` too: a head that did
    not move is not enough when the note was retired under the pass."""
    survivor = await _make(notes.store, title="Saturday dinner")
    duplicate = await _make(notes.store, title="Neway booking")
    await notes.retire(survivor.id, NOW + 5)

    with pytest.raises(StaleNoteHeadError):
        await _fold(notes, survivor=survivor, duplicate=duplicate)
    assert (await notes.store.read(
        unit=WS, note_id=duplicate.id))[0].retired_at is None




async def test_a_retire_naming_a_head_is_a_noop_when_the_head_moved(
    notes: _Harness,
) -> None:
    """Guard 3 at the store: `supersede(expected_head=...)` is a
    compare-and-swap. A head that moved answers 0 and the note stays open;
    the head it names answers 1."""
    note = await _make(notes.store)
    moved = await notes.store.append_revision(
        unit=WS, note_id=note.id, body="the fold's appended question",
        parent_revision_id=note.head_revision_id,
        established_by="dream:note-merge:unit-alpha:1", now_ms=NOW + 10,
    )

    stale = await notes.store.supersede(
        key=WS, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None,
        now_ms=NOW + 20, expected_head=note.head_revision_id,
    )
    assert stale == 0
    assert (await notes.store.read(unit=WS, note_id=note.id))[0].retired_at is None

    current = await notes.store.supersede(
        key=WS, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None,
        now_ms=NOW + 30, expected_head=moved.id,
    )
    assert current == 1




async def test_merge_into_refuses_a_duplicate_retired_under_an_unmoved_head(
    notes: _Harness,
) -> None:
    """The duplicate's swap carries `retired_at IS NULL`, not just its head:
    `supersede` retires without touching the head, so a duplicate the curator
    closed while the pass was thinking still names the head the pass read.
    Folding it would append a CLOSED question to the survivor and re-retire a
    note as `retired-merged`, overwriting why it was retired."""
    survivor = await _make(notes.store, title="Saturday dinner")
    duplicate = await _make(notes.store, title="Neway booking")
    await notes.retire(duplicate.id, NOW + 5)

    with pytest.raises(StaleNoteHeadError):
        await _fold(notes, survivor=survivor, duplicate=duplicate)

    _, s_head = await notes.store.read(unit=WS, note_id=survivor.id)
    assert s_head.id == survivor.head_revision_id, "the survivor rolled back"
    assert len(await notes.store.revision_chain(
        unit=WS, note_id=survivor.id)) == 1
    d_note, _ = await notes.store.read(unit=WS, note_id=duplicate.id)
    assert d_note.retired_at == NOW + 5 and d_note.replaced_by is None


async def test_the_revision_chain_is_readable_oldest_first_and_unit_scoped(
    notes: _Harness,
) -> None:
    """The chain the fire-time screen walks: oldest first, and `()` — never a
    partial chain — across a unit boundary."""
    note = await _make(notes.store)
    second = await notes.store.append_revision(
        unit=WS, note_id=note.id, body="Two venues, one call made.",
        parent_revision_id=note.head_revision_id,
        established_by="cognition:ws", now_ms=NOW + 1,
    )
    third = await notes.store.append_revision(
        unit=WS, note_id=note.id, body="Ada booked Sai Kung.",
        parent_revision_id=second.id, established_by="tag:miles", now_ms=NOW + 2,
    )

    chain = await notes.store.revision_chain(unit=WS, note_id=note.id)

    assert [r.id for r in chain] == [note.head_revision_id, second.id, third.id]
    assert chain[1].parent_revision_id == note.head_revision_id
    assert await notes.store.revision_chain(unit=OTHER_WS, note_id=note.id) == ()
    assert await notes.store.revision_chain(unit=WS, note_id="not-a-uuid") == ()
