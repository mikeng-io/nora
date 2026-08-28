"""Shared contract for the note store. Every implementation runs every test.

Two implementations: `nora.infra.sql.notes.SqlNoteStore` over real Postgres in
a testcontainer, and `nora.testing.InMemoryNoteStore` built from
`nora.domain.notes` alone. CLAUDE.md's rule is why this file exists at all — a
port never gains a second implementation, *including a test fake*, without a
suite both pass. It is also why `InMemoryNoteStore` is not a dict that says yes:
the compare-and-swap refusal, the workspace scoping and the head-only search are
asserted HERE, against both, so a fake that accepted a stale parent would be red
rather than convenient.

**What the suite needs that the store's surface does not offer, and why it is
per-implementation rather than hard-coded.** This is
`tests/contract/test_dream_sink_contract.py`'s rule applied again:

1. **Retiring a note.** `standing` must exclude retired notes, and the store has
   no `retire` verb — retirement arrives later as `DreamSink.supersede`
   (DEC-130), owned by the adaptor, not by this store. Rather than inventing a
   seventh method so a test can reach a state, `_Harness` carries a `retire`
   callable that reaches each implementation the way that implementation
   actually stores the flag (a raw `UPDATE` for sql, a `dataclasses.replace` for
   memory). A test that can only be written by widening production surface is a
   test asking you to widen the surface for the test's sake.
2. **Reading a `note_candidate` row back.** `record_miss` returns the new count
   and nothing else, but the behaviour that matters — `observed_in` ACCUMULATES
   distinct conversations rather than growing one entry per call — is invisible
   through that int. `_Harness.read_candidate` returns a normalised
   `(occurrence_count, observed_in, first_seen_at, last_seen_at)` view so the
   assertion is about the row, not about the return value that would pass either
   way.

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
    NoteCreationProposal,
    NoteRevision,
    NoteRevisionProposal,
    NoteSupersedeReason,
    StaleNoteHeadError,
)
from nora.domain.ports import Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import NOTE, NOTE_CANDIDATE
from nora.testing import InMemoryNoteStore

NOW = 1_755_200_000_000
WS = "workspace-alpha"
OTHER_WS = "workspace-beta"


class _NoteStore(Protocol):
    """The surface both implementations offer, named here so the suite type-checks
    against the SHAPE rather than against whichever class the fixture happened to
    yield — a `mypy` pass that only ever saw `SqlNoteStore` would not notice the
    fake drifting off it."""

    async def create(
        self, *, workspace: str, title: str, description: str, uncertainty: str,
        closing_condition: str, established_in: str, body: str,
        established_by: str, now_ms: int, anchor_date: int | None = ...,
        anchor_entity_ids: tuple[str, ...] = ...,
    ) -> Note: ...
    async def standing(self, *, workspace: str) -> tuple[Note, ...]: ...
    async def read(
        self, *, workspace: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...
    async def append_revision(
        self, *, workspace: str, note_id: str, body: str,
        parent_revision_id: str, established_by: str, now_ms: int,
    ) -> NoteRevision: ...
    async def search(
        self, *, workspace: str, query: str, limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...
    async def record_miss(
        self, *, workspace: str, probe: str, conversation_id: str, now_ms: int,
    ) -> int: ...


@dataclass(frozen=True)
class _Candidate:
    occurrence_count: int
    observed_in: tuple[str, ...]
    first_seen_at: int
    last_seen_at: int


@dataclass(frozen=True)
class _Harness:
    store: _NoteStore
    retire: Callable[[str, int], Awaitable[None]]
    read_candidate: Callable[[str, str], Awaitable[_Candidate | None]]


@pytest.fixture(scope="module")
def _notes_db(pg_admin: Any) -> Iterator[str]:
    """ONE database and ONE migration run for this whole file.

    The obvious fixture is the shared function-scoped `pg_url`, and this suite
    used it until 2026-08-21. It creates a database, runs the full 26-migration
    chain and drops the database **per test** — 31 sql-param tests, so 31 full
    rebuilds. That is correct and, for a small suite, affordable.

    It stopped being affordable here, and the way it showed up is worth
    recording: `tests/integration/test_latency.py` — a p95 budget on Nora's
    non-model overhead, and one of the few tests that writes audit rows to this
    same container — went from **0.032s in-suite on master to 0.346s in-suite
    with this file present**, a 10x regression that reproduced four times and
    was *absent* when the latency test ran standalone. The container was being
    left degraded by the churn, and the cost landed on an unrelated test rather
    than on this one, which is why it read as somebody else's flake for most of
    a morning.

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

        async def _candidate_mem(workspace: str, probe: str) -> _Candidate | None:
            row = memory.candidates.get((workspace, probe))
            if row is None:
                return None
            return _Candidate(
                occurrence_count=row.occurrence_count,
                observed_in=row.observed_in,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
            )

        yield _Harness(memory, _retire_mem, _candidate_mem)
        return

    # The ENGINE is per test (cheap); the DATABASE and its migrations are
    # module-scoped (see `_notes_db`) because that is where the cost was.
    engine = create_sql_engine(request.getfixturevalue("_notes_db"))

    async def _retire_sql(note_id: str, now_ms: int) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(NOTE).where(NOTE.c.id == note_id).values(retired_at=now_ms)
            )

    async def _candidate_sql(workspace: str, probe: str) -> _Candidate | None:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(NOTE_CANDIDATE).where(
                        NOTE_CANDIDATE.c.workspace == workspace,
                        NOTE_CANDIDATE.c.probe == probe,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return _Candidate(
            occurrence_count=row.occurrence_count,
            observed_in=tuple(row.observed_in),
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
        )

    try:
        yield _Harness(SqlNoteStore(engine), _retire_sql, _candidate_sql)
    finally:
        # Truncate rather than rebuild: same isolation guarantee this suite
        # needs — no row written by one test is visible to the next — at a
        # fraction of the cost. See `_notes_db` for what the rebuild was doing
        # to an unrelated test.
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "truncate note, note_revision, note_entity, note_candidate cascade"
            ))
        await engine.dispose()


async def _make(
    store: _NoteStore,
    *,
    workspace: str = WS,
    title: str = "Wedding venue",
    description: str = "Where the September wedding is held is still open.",
    body: str = "Two venues on the shortlist. Nobody has called either.",
    now_ms: int = NOW,
    anchor_date: int | None = 20260919,
    anchor_entity_ids: tuple[str, ...] = (),
) -> Note:
    return await store.create(
        workspace=workspace,
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

    got = await notes.store.read(workspace=WS, note_id=note.id)

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

    assert note.workspace == WS
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

    assert await notes.store.standing(workspace=WS) == ()


# --- compare-and-swap (spec §6.3) -------------------------------------------


async def test_append_revision_accepts_the_current_head_and_moves_it(
    notes: _Harness,
) -> None:
    note = await _make(notes.store)

    rev = await notes.store.append_revision(
        workspace=WS,
        note_id=note.id,
        body="Called both. The Central one is held until Friday.",
        parent_revision_id=note.head_revision_id,
        established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    assert rev.parent_revision_id == note.head_revision_id
    assert rev.id != note.head_revision_id
    assert rev.written_at == NOW + 1000
    got = await notes.store.read(workspace=WS, note_id=note.id)
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
        workspace=WS, note_id=note.id, body="the first writer's arrangement",
        parent_revision_id=stale_parent, established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    with pytest.raises(StaleNoteHeadError) as excinfo:
        await notes.store.append_revision(
            workspace=WS, note_id=note.id, body="the second writer's overwrite",
            parent_revision_id=stale_parent, established_by="tag:turn-3",
            now_ms=NOW + 2000,
        )

    assert excinfo.value.observed_head == first.id
    got = await notes.store.read(workspace=WS, note_id=note.id)
    assert got is not None
    read_note, head = got
    assert read_note.head_revision_id == first.id, "the head did not move"
    assert head.body == "the first writer's arrangement", "nothing was written"


async def test_append_revision_refuses_a_note_in_another_workspace(
    notes: _Harness,
) -> None:
    """A CAS whose `WHERE` omitted the workspace would let a caller holding a
    head id write into a workspace it may not read — worse than a leak, because
    the write is then invisible from the side that made it."""
    note = await _make(notes.store)

    with pytest.raises(StaleNoteHeadError) as excinfo:
        await notes.store.append_revision(
            workspace=OTHER_WS, note_id=note.id, body="a cross-workspace write",
            parent_revision_id=note.head_revision_id,
            established_by="tag:turn-2", now_ms=NOW + 1000,
        )

    assert excinfo.value.observed_head is None, (
        "from the other workspace the note does not exist at all"
    )
    got = await notes.store.read(workspace=WS, note_id=note.id)
    assert got is not None
    assert got[1].body == "Two venues on the shortlist. Nobody has called either."


async def test_append_revision_on_an_unknown_note_refuses(notes: _Harness) -> None:
    with pytest.raises(StaleNoteHeadError) as excinfo:
        await notes.store.append_revision(
            workspace=WS, note_id=str(uuid4()), body="into the void",
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
            workspace=WS, note_id="not-a-uuid", body="into the void",
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

    standing = await notes.store.standing(workspace=WS)

    assert [n.id for n in standing] == [live.id]


async def test_standing_excludes_other_workspaces(notes: _Harness) -> None:
    mine = await _make(notes.store, workspace=WS, title="Mine")
    await _make(notes.store, workspace=OTHER_WS, title="Not mine")

    standing = await notes.store.standing(workspace=WS)

    assert [n.id for n in standing] == [mine.id]


async def test_standing_carries_the_head_id_a_revision_must_echo(
    notes: _Harness,
) -> None:
    """`standing` is not merely a listing: the head it reports is the value
    §6.2's read-before-write latch and §6.3's CAS both name."""
    note = await _make(notes.store)
    rev = await notes.store.append_revision(
        workspace=WS, note_id=note.id, body="moved once",
        parent_revision_id=note.head_revision_id, established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    (only,) = await notes.store.standing(workspace=WS)

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

    standing = await notes.store.standing(workspace=WS)

    assert [n.id for n in standing] == [first.id, second.id, third.id]


async def test_standing_is_empty_when_nothing_is_standing(notes: _Harness) -> None:
    assert await notes.store.standing(workspace=WS) == ()


# --- read -------------------------------------------------------------------


async def test_read_returns_none_across_a_workspace_boundary(
    notes: _Harness,
) -> None:
    """Unreachable, not merely unrendered: a note from another workspace must
    answer exactly as if it did not exist. Anything else lets a caller confirm
    the existence of a note it may not read."""
    note = await _make(notes.store, workspace=OTHER_WS)

    assert await notes.store.read(workspace=WS, note_id=note.id) is None
    assert await notes.store.read(workspace=OTHER_WS, note_id=note.id) is not None


async def test_read_returns_none_for_an_unknown_id(notes: _Harness) -> None:
    assert await notes.store.read(workspace=WS, note_id=str(uuid4())) is None


async def test_read_returns_none_for_a_malformed_id_rather_than_raising(
    notes: _Harness,
) -> None:
    """`note.id` is `pg.UUID`, so a non-UUID string would otherwise reach
    asyncpg as a raw type error instead of the ordinary "no such note" outcome.
    A store that raises here turns a bad tool argument into a dead turn."""
    assert await notes.store.read(workspace=WS, note_id="not-a-uuid") is None


async def test_read_still_works_on_a_retired_note(notes: _Harness) -> None:
    """Retirement is a status change, not a delete (§7.4): history is retained
    so a lossy step can be re-derived. `standing` hides it; `read` does not."""
    note = await _make(notes.store)
    await notes.retire(note.id, NOW + 5000)

    got = await notes.store.read(workspace=WS, note_id=note.id)

    assert got is not None
    assert got[0].retired_at == NOW + 5000
    assert got[0].is_standing is False


# --- search (spec §5.3) -----------------------------------------------------


async def test_search_never_returns_a_superseded_body(notes: _Harness) -> None:
    """The join is `note.head_revision_id = note_revision.id`, so the QUERY —
    not a partial index, which cannot express "is the head" — is what keeps a
    superseded body out. A hit on a body that is no longer the note's content
    returns something true-in-the-past as though it were current."""
    note = await _make(notes.store, body="Booked at the Peninsula, deposit paid.")
    await notes.store.append_revision(
        workspace=WS, note_id=note.id,
        body="Cancelled the Peninsula. Looking at the Upper House instead.",
        parent_revision_id=note.head_revision_id, established_by="tag:turn-2",
        now_ms=NOW + 1000,
    )

    assert await notes.store.search(workspace=WS, query="deposit paid", limit=10) == ()
    hits = await notes.store.search(workspace=WS, query="Upper House", limit=10)
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

    hits = await notes.store.search(workspace=WS, query="酒店", limit=10)

    assert [n.id for n, _ in hits] == [note.id]
    assert "酒店" in hits[0][1]


async def test_search_is_case_insensitive(notes: _Harness) -> None:
    """`ilike`, not `like` — «did anyone mention a hotel?» must reach a body
    that opened its sentence with «Hotel»."""
    note = await _make(notes.store, body="Hotel shortlist: two left.")

    hits = await notes.store.search(workspace=WS, query="hotel", limit=10)

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

    assert await notes.store.search(workspace=WS, query="hotel", limit=10) == ()


async def test_search_is_scoped_to_the_workspace(notes: _Harness) -> None:
    await _make(notes.store, workspace=OTHER_WS, body="a hotel in the other room")

    assert await notes.store.search(workspace=WS, query="hotel", limit=10) == ()


async def test_search_excludes_retired_notes(notes: _Harness) -> None:
    """A retired note is absent from the injected index, so a hit on one hands
    her a note whose title she cannot see and whose question is already closed —
    the same true-in-the-past error a superseded body would be."""
    note = await _make(notes.store, body="the shortlist mentioned a hotel")
    await notes.retire(note.id, NOW + 5000)

    assert await notes.store.search(workspace=WS, query="hotel", limit=10) == ()


async def test_search_respects_its_limit(notes: _Harness) -> None:
    for i in range(3):
        await _make(notes.store, body=f"hotel option {i}", now_ms=NOW + i)

    assert len(await notes.store.search(workspace=WS, query="hotel", limit=2)) == 2


async def test_search_returns_nothing_for_a_blank_query(notes: _Harness) -> None:
    """`ilike '%%'` matches every body. A blank probe emptying the whole
    notebook into a tool result is not a search; it is the index again, at full
    body length, unasked for."""
    await _make(notes.store)

    assert await notes.store.search(workspace=WS, query="   ", limit=10) == ()
    assert await notes.store.search(workspace=WS, query="", limit=10) == ()


async def test_search_treats_wildcards_as_literal_text(notes: _Harness) -> None:
    """An unescaped `%` in a caller's query is `ilike`'s own wildcard, so
    searching for `100%` would return every note in the workspace. The query is
    data, and the in-memory implementation must escape it the same way rather
    than getting the right answer by using `in` and never meeting the
    question."""
    await _make(notes.store, body="the deposit is refundable")

    assert await notes.store.search(workspace=WS, query="%", limit=10) == ()
    assert await notes.store.search(workspace=WS, query="_", limit=10) == ()
    assert await notes.store.search(workspace=WS, query="depos%able", limit=10) == ()


async def test_search_clamps_a_non_positive_limit(notes: _Harness) -> None:
    """`list[:0]` and `list[:-1]` disagree: the second silently drops the last
    entry instead of returning nothing, and Postgres errors outright on a
    negative LIMIT. Both implementations clamp, the way `standing_facts` does
    (2026-08-21 review)."""
    await _make(notes.store, body="a hotel")

    assert await notes.store.search(workspace=WS, query="hotel", limit=0) == ()
    assert await notes.store.search(workspace=WS, query="hotel", limit=-1) == ()


# --- record_miss (the negative cache, spec §4.5) ----------------------------


async def test_record_miss_accumulates_rather_than_duplicating(
    notes: _Harness,
) -> None:
    """`fact_candidate`'s shape and its reason: the SECOND miss on the same
    ground is evidence rather than a guess. An insert-per-miss would make one
    gap look like N unrelated gaps and nothing would ever cross a threshold."""
    first = await notes.store.record_miss(
        workspace=WS, probe="who is driving", conversation_id="conv-1", now_ms=NOW,
    )
    second = await notes.store.record_miss(
        workspace=WS, probe="who is driving", conversation_id="conv-2",
        now_ms=NOW + 1000,
    )

    assert (first, second) == (1, 2)
    row = await notes.read_candidate(WS, "who is driving")
    assert row is not None
    assert row.occurrence_count == 2
    assert set(row.observed_in) == {"conv-1", "conv-2"}
    assert row.first_seen_at == NOW, "the first sighting is not overwritten"
    assert row.last_seen_at == NOW + 1000


async def test_record_miss_does_not_repeat_a_conversation_in_observed_in(
    notes: _Harness,
) -> None:
    """`observed_in` answers "in how many DIFFERENT rooms", which is what makes
    a probe repeated in one room weaker evidence than the same probe in three.
    Appending unconditionally turns one chatty room into a false consensus, and
    grows the array without bound."""
    await notes.store.record_miss(
        workspace=WS, probe="who is driving", conversation_id="conv-1", now_ms=NOW,
    )
    count = await notes.store.record_miss(
        workspace=WS, probe="who is driving", conversation_id="conv-1",
        now_ms=NOW + 1000,
    )

    assert count == 2, "the occurrence still counts"
    row = await notes.read_candidate(WS, "who is driving")
    assert row is not None
    assert row.observed_in == ("conv-1",)


async def test_record_miss_keys_on_workspace_as_well_as_probe(
    notes: _Harness,
) -> None:
    mine_count = await notes.store.record_miss(
        workspace=WS, probe="who is driving", conversation_id="conv-1", now_ms=NOW,
    )
    theirs_count = await notes.store.record_miss(
        workspace=OTHER_WS, probe="who is driving", conversation_id="conv-9",
        now_ms=NOW + 1,
    )

    assert (mine_count, theirs_count) == (1, 1), (
        "another workspace's miss is not this workspace's evidence"
    )
    mine = await notes.read_candidate(WS, "who is driving")
    theirs = await notes.read_candidate(OTHER_WS, "who is driving")
    assert mine is not None and theirs is not None
    assert mine.observed_in == ("conv-1",)
    assert theirs.observed_in == ("conv-9",)


async def test_record_miss_distinguishes_two_probes(notes: _Harness) -> None:
    assert await notes.store.record_miss(
        workspace=WS, probe="who is driving", conversation_id="conv-1", now_ms=NOW,
    ) == 1
    assert await notes.store.record_miss(
        workspace=WS, probe="where are we eating", conversation_id="conv-1",
        now_ms=NOW + 1,
    ) == 1


# --- DreamSink: both implementations, and the refusals in particular --------
#
# `InMemoryNoteStore` does NOT join `tests/contract/test_dream_sink_contract.py`
# — that file's own docstring records why the doubles mirroring SQL stores stay
# out of `IMPLEMENTATIONS`. So this is where the double's `propose`/`supersede`
# get exercised against the same assertions the real store faces, and the
# emphasis is on the REFUSALS, because that is where a double drifts first: it
# passes the happy path for free and refuses only what something else refuses
# for it.


async def test_propose_writes_a_revision_and_returns_the_note_id(
    notes: _Harness,
) -> None:
    """The NOTE id, not the revision's — `supersede` is the only other verb and
    it retires a note, so returning revision ids would hand the caller handles
    its counterpart cannot accept."""
    note = await _make(notes.store)
    ids = await notes.store.propose(
        key=WS,
        proposals=(
            Proposal(
                payload=NoteRevisionProposal(
                    note_id=note.id, body="the curator's tidier arrangement",
                    parent_revision_id=note.head_revision_id,
                ),
                instruction_sha256="c" * 64, model="curator-model",
                established_by="run-curator-1",
            ),
        ),
        now_ms=NOW + 5,
    )

    assert ids == (note.id,)
    found = await notes.store.read(workspace=WS, note_id=note.id)
    assert found is not None
    assert found[1].body == "the curator's tidier arrangement"
    # The provenance the tag path cannot supply, on the one writer that can.
    assert found[1].instruction_sha256 == "c" * 64
    assert found[1].model == "curator-model"


async def test_propose_refuses_a_foreign_payload_rather_than_duck_typing_it(
    notes: _Harness,
) -> None:
    """`Proposal.payload` is `object` at the port so the runtime never names an
    adaptor's value type. The price of that opacity is that a wrong value
    travels a long way quietly, and `isinstance` at the sink is where it is
    paid — for the DOUBLE as much as for the store, or the two are not
    interchangeable."""
    with pytest.raises(TypeError):
        await notes.store.propose(
            key=WS,
            proposals=(
                Proposal(
                    payload="a string, which is the fact tier's shape",
                    instruction_sha256="d" * 64, model="m",
                    established_by="run-1",
                ),
            ),
            now_ms=NOW,
        )


async def test_supersede_accepts_a_retirement_with_no_successor(
    notes: _Harness,
) -> None:
    """The case `SqlFactStore.supersede` RAISES on, and the entire reason
    `DreamSink.supersede` types `replaced_by` as `str | None`. An anchor date
    passed and the dinner happened; it is replaced by nothing."""
    note = await _make(notes.store)

    affected = await notes.store.supersede(
        key=WS, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=NOW + 9,
    )

    assert affected == 1
    assert await notes.store.standing(workspace=WS) == ()


async def test_supersede_records_the_successor_and_the_reason(
    notes: _Harness,
) -> None:
    survivor = await _make(notes.store, title="Grant wedding")
    duplicate = await _make(notes.store, title="Grant's wedding")

    affected = await notes.store.supersede(
        key=WS, target=duplicate.id, replaced_by=survivor.id,
        because=NoteSupersedeReason.MERGED, source=None, now_ms=NOW + 9,
    )

    assert affected == 1
    standing = await notes.store.standing(workspace=WS)
    assert [n.id for n in standing] == [survivor.id]
    # READ THE REASON BACK. Until 2026-08-27 this test named the reason in its
    # own title and never asserted it: `retire_reason` was read only by the
    # sink suite's `_note_read_row`, which runs the SQL store alone, so
    # deleting `retire_reason=because` from the double left the whole suite
    # green. A field written by both implementations and asserted for one is
    # not under contract.
    retired = await notes.store.read(workspace=WS, note_id=duplicate.id)
    assert retired is not None
    assert retired[0].retire_reason == NoteSupersedeReason.MERGED
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
    """Free text would make "how many did the curator merge this week"
    unanswerable, which is the reading that justifies the curator existing."""
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


async def test_supersede_cannot_reach_another_workspaces_note(
    notes: _Harness,
) -> None:
    note = await _make(notes.store)

    assert await notes.store.supersede(
        key="someone-elses-workspace", target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=NOW,
    ) == 0
    assert [n.id for n in await notes.store.standing(workspace=WS)] == [note.id]


async def test_two_proposals_against_one_note_chain_within_the_tick(
    notes: _Harness,
) -> None:
    """**This test asserted the opposite until 2026-08-27, and that is the
    finding.** It pinned `propose` as non-atomic — "the FIRST one landed and is
    not rolled back" — while `propose`'s own docstring required the reverse.
    Both were mine. A test that encodes the defect as the spec is worse than no
    test: it defends the behaviour and every later reader takes it as intent.

    What the tier actually needs: `DreamSink.propose` takes a SEQUENCE and one
    tick may write many, so two proposals naming the same note is ordinary
    rather than exceptional — the curator's merge is exactly «revise the
    survivor, then retire the duplicate». The batch therefore carries the head
    each proposal moved, and the next against that note compares against it.

    The CAS is not weakened by this. It still refuses anything the batch did
    not itself write, which is the case it exists for: the tag path revising
    mid-tick."""
    note = await _make(notes.store)

    def _p(body: str) -> Proposal:
        return Proposal(
            payload=NoteRevisionProposal(
                note_id=note.id, body=body,
                parent_revision_id=note.head_revision_id,
            ),
            instruction_sha256="e" * 64, model="curator-model",
            established_by="run-curator-1",
        )

    ids = await notes.store.propose(
        key=WS, proposals=(_p("first"), _p("second")), now_ms=NOW + 5,
    )

    assert ids == (note.id, note.id), (
        "both proposals revised the same note, so both return its id — the "
        "port says 'in order', not 'distinct'"
    )
    found = await notes.store.read(workspace=WS, note_id=note.id)
    assert found is not None
    assert found[1].body == "second", "the second revision is the head"
    assert found[1].parent_revision_id != note.head_revision_id, (
        "the second must be parented on what the FIRST wrote, not on the head "
        "the caller read — otherwise the chain is a lie and the CAS passed by "
        "accident"
    )


async def test_a_stale_head_writes_nothing_at_all(notes: _Harness) -> None:
    """The atomicity requirement, stated as the case it exists for.

    A batch whose SECOND proposal loses the CAS — because the tag path revised
    that note mid-tick — must leave the notebook untouched. The curator's merge
    is «append the merged body to the survivor, then retire the originals»; if
    the append commits and the retirement does not, the notebook holds a
    survivor carrying merged text beside a duplicate nobody retired, and the
    caller was told nothing happened. **A partially applied cross-note edit is
    the one outcome worse than not running**, because it is invisible: no
    error, no count, and an index that looks ordinary.

    **The exemption that used to be here is gone, and it was the tell.** This
    test read `if isinstance(notes.store, SqlNoteStore)` before the final
    assertion, on the reasoning that a double cannot roll back — which is true
    and was the wrong conclusion. A double cannot roll back, but it CAN decide
    every refusal before mutating anything, and that is the same observable
    behaviour from outside. While the exemption stood, the two implementations
    were not being compared on the one case the transaction exists for; the
    double wrote the first proposal and kept it."""
    good = await _make(notes.store, title="Dinner, 9 Sept")
    stale = await _make(notes.store, title="Grant wedding")

    with pytest.raises(StaleNoteHeadError):
        await notes.store.propose(
            key=WS,
            proposals=(
                Proposal(
                    payload=NoteRevisionProposal(
                        note_id=good.id, body="a revision that must not survive",
                        parent_revision_id=good.head_revision_id,
                    ),
                    instruction_sha256="e" * 64, model="m",
                    established_by="run-1",
                ),
                Proposal(
                    payload=NoteRevisionProposal(
                        note_id=stale.id, body="doomed",
                        parent_revision_id="00000000-0000-0000-0000-0000000000ff",
                    ),
                    instruction_sha256="e" * 64, model="m",
                    established_by="run-1",
                ),
            ),
            now_ms=NOW + 5,
        )

    found = await notes.store.read(workspace=WS, note_id=good.id)
    assert found is not None
    assert found[1].body != "a revision that must not survive", (
        "a failed batch leaves nothing behind — Postgres by rollback, the "
        "double by refusing before it writes"
    )


async def test_a_foreign_payload_is_refused_before_anything_is_written(
    notes: _Harness,
) -> None:
    """Validation runs over the WHOLE batch before the first write. A bad
    payload at index 1 used to land after index 0 had already committed, which
    is the accept-and-discard failure one verb over: the call reports one thing
    and the store holds another."""
    note = await _make(notes.store)

    with pytest.raises(TypeError):
        await notes.store.propose(
            key=WS,
            proposals=(
                Proposal(
                    payload=NoteRevisionProposal(
                        note_id=note.id, body="must not be written",
                        parent_revision_id=note.head_revision_id,
                    ),
                    instruction_sha256="e" * 64, model="m",
                    established_by="run-1",
                ),
                Proposal(
                    payload="the fact tier's shape", instruction_sha256="e" * 64,
                    model="m", established_by="run-1",
                ),
            ),
            now_ms=NOW,
        )

    found = await notes.store.read(workspace=WS, note_id=note.id)
    assert found is not None
    assert found[1].body != "must not be written"


# --- the SECOND payload shape: create, for the debrief ----------------------


def _creation(**over: object) -> Proposal:
    kw: dict = {
        "title": "Dinner, 9 Sept",
        "description": "Three places proposed, nobody has picked one.",
        "uncertainty": "where, and whether it is still the 9th",
        "closing_condition": "somebody books a table",
        "established_in": "conv-1",
        "body": "Ada suggested Sai Kung. Ben wants the buffet. Nobody called.",
        "anchor_date": 20260909,
    }
    kw.update(over)
    return Proposal(
        payload=NoteCreationProposal(**kw),
        instruction_sha256="f" * 64, model="debrief-model",
        established_by="run-debrief-1",
    )


async def test_propose_can_start_a_note_that_does_not_exist_yet(
    notes: _Harness,
) -> None:
    """The debrief's half. Its value is an uncertainty NOBODY tracked, which is
    by definition a note that is not there — the opposite of the curator's job,
    which acts across notes that already exist."""
    ids = await notes.store.propose(key=WS, proposals=(_creation(),), now_ms=NOW)

    assert len(ids) == 1
    found = await notes.store.read(workspace=WS, note_id=ids[0])
    assert found is not None
    note, head = found
    assert note.title == "Dinner, 9 Sept"
    assert note.anchor_date == 20260909
    # Provenance reaches the FIRST revision, which the tag path cannot supply.
    assert head.instruction_sha256 == "f" * 64
    assert head.model == "debrief-model"


async def test_the_two_payload_shapes_are_discriminated_not_coerced(
    notes: _Harness,
) -> None:
    """§7: two shapes with a discriminant, never one with optional fields. One
    call carrying both must do the right thing with each — if the sink
    collapsed them it would either create twice or revise twice, and both read
    as success."""
    ids = await notes.store.propose(key=WS, proposals=(_creation(),), now_ms=NOW)
    created = ids[0]
    note = await notes.store.read(workspace=WS, note_id=created)
    assert note is not None

    both = await notes.store.propose(
        key=WS,
        proposals=(
            _creation(title="Grant wedding", anchor_date=20261011,
                            anchor_entity_ids=("grant",)),
            Proposal(
                payload=NoteRevisionProposal(
                    note_id=created, body="Ada called Sai Kung. Still no table.",
                    parent_revision_id=note[0].head_revision_id,
                ),
                instruction_sha256="a" * 64, model="curator-model",
                established_by="run-curator-1",
            ),
        ),
        now_ms=NOW + 1,
    )

    assert both[1] == created, "the revision must return the note it revised"
    assert both[0] != created, "the creation must be a different note"
    revised = await notes.store.read(workspace=WS, note_id=created)
    assert revised is not None
    assert revised[1].body == "Ada called Sai Kung. Still no table."


async def test_a_creation_naming_an_existing_occasion_is_not_silently_merged(
    notes: _Harness,
) -> None:
    """The decision, written down rather than discovered: the sink does not look
    for a similar note and does not reinterpret a create as a revision.

    Deduplication is anchor-keyed and belongs to the curator (§7.1), the only
    thing permitted to act across notes. A sink matching on a plausible
    collision would be doing cross-note reasoning at the storage boundary,
    silently, on a guess. Two notes about one occasion is a state the curator
    is built to resolve; one note silently absorbing another's content is not
    recoverable in the direction that matters."""
    first = await notes.store.propose(key=WS, proposals=(_creation(),), now_ms=NOW)
    second = await notes.store.propose(
        key=WS, proposals=(_creation(),), now_ms=NOW + 1,
    )

    assert first[0] != second[0]
    assert len(await notes.store.standing(workspace=WS)) == 2


async def test_a_creation_with_no_anchor_is_refused_at_the_type(
    notes: _Harness,
) -> None:
    """`anchor_entity_ids` needs a populated entity registry and `note_entity`
    is empty, so in practice a debrief note anchors on a DATE. A type accepting
    only entity anchors would be a shape that cannot be satisfied in
    production; a type accepting NEITHER would push the refusal into `Note`'s
    constructor, inside a background tick, as a raise rather than a refusal."""
    with pytest.raises(ValueError):
        NoteCreationProposal(
            title="t", description="d", uncertainty="u",
            closing_condition="c", established_in="conv-1", body="b",
        )


# --- the curator's selection reads (§9.2), SQL-only and token-free ----------


async def test_merge_groups_finds_notes_sharing_an_anchor(
    notes: _Harness,
) -> None:
    """§7.1: merge is keyed on the ANCHOR, never on topical similarity, so two
    weddings stay two notes. This read is what makes that structural — the
    model is never asked whether two notes belong together, because SQL
    already decided and it decided on the date and the people."""
    a = await _make(notes.store, title="Grant wedding", anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    b = await _make(notes.store, title="Grant's wedding", anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    await _make(notes.store, title="Ada wedding", anchor_date=20270301,
                          anchor_entity_ids=("ada",))

    groups, _refused = await notes.store.merge_groups(workspace=WS, limit=10)

    assert len(groups) == 1, "only the two sharing an anchor collide"
    assert {n.id for n, _ in groups[0]} == {a.id, b.id}
    assert [body for _, body in groups[0]] == [
        "Two venues on the shortlist. Nobody has called either.",
    ] * 2, "the BODIES come back — the model must not merge what it cannot see"


async def test_two_weddings_on_different_days_are_never_one_group(
    notes: _Harness,
) -> None:
    """The negative half, and the reason the rule exists. These two are as
    topically alike as two notes can be — same words, same shape, same kind of
    uncertainty — and merging them would lose which revision belonged to which
    occasion, permanently and silently."""
    await _make(notes.store, title="Wedding venue", anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    await _make(notes.store, title="Wedding venue", anchor_date=20270301,
                          anchor_entity_ids=("ada",))

    assert await notes.store.merge_groups(workspace=WS, limit=10) == ((), ())


async def test_dateless_notes_never_collide_with_each_other(
    notes: _Harness,
) -> None:
    """An entity-only anchor is not an OCCASION — «Grant» is not a thing that
    happens once. And `note_entity` is empty in production, so a dateless group
    would be every dateless note in the workspace anchored to `{}`."""
    await _make(
        notes.store, title="Someone's flat", anchor_date=None,
        anchor_entity_ids=("person-1",),
    )
    await _make(
        notes.store, title="Someone's car", anchor_date=None,
        anchor_entity_ids=("person-1",),
    )

    assert await notes.store.merge_groups(workspace=WS, limit=10) == ((), ())


async def test_a_retired_note_never_joins_a_merge_group(
    notes: _Harness,
) -> None:
    survivor = await _make(notes.store, anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    duplicate = await _make(notes.store, anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    await notes.store.supersede(
        key=WS, target=duplicate.id, replaced_by=survivor.id,
        because=NoteSupersedeReason.MERGED, source=None, now_ms=NOW + 1,
    )

    assert await notes.store.merge_groups(workspace=WS, limit=10) == ((), ()), (
        "a merge that already happened must not be found again next tick"
    )


async def test_anchored_before_selects_expired_notes_with_their_bodies(
    notes: _Harness,
) -> None:
    """Retirement is TIME-driven: a note whose anchor has passed needs looking
    at even if nobody has touched the notebook in a month. The body comes back
    because the anchor passing makes a note ELIGIBLE, never retired — «we moved
    it to the 16th» leaves the question open, and only the body says so."""
    past = await _make(
        notes.store, title="Dinner, 9 Sept", anchor_date=20260909,
        body="Nobody booked anything.",
    )
    await _make(notes.store, title="Wedding", anchor_date=20271231)

    hits = await notes.store.anchored_before(workspace=WS, day=20261001, limit=10)

    assert [n.id for n, _ in hits] == [past.id]
    assert hits[0][1] == "Nobody booked anything."


async def test_anchored_before_is_strict_and_workspace_scoped(
    notes: _Harness,
) -> None:
    """Strict: a note anchored to TODAY is not expired — the occasion has not
    happened yet."""
    await _make(notes.store, anchor_date=20260909)

    assert await notes.store.anchored_before(
        workspace=WS, day=20260909, limit=10,
    ) == ()
    assert await notes.store.anchored_before(
        workspace="another-workspace", day=20270101, limit=10,
    ) == ()


async def test_a_second_proposal_may_not_redeclare_a_different_parent(
    notes: _Harness,
) -> None:
    """The tracked head is a CHECK, not a silent override.

    Head chaining made two proposals against one note legal. It also made the
    declared `parent_revision_id` of the second one ignorable — until
    2026-08-27 a bogus parent landed as long as the batch had already touched
    that note.

    The scenario is the likely one, not an exotic one: a curator emitting two
    proposals BOTH parented on the head it read, with different bodies, which
    is what a model duplicating its own output produces. Silently chained, both
    land, the second body wins, the first is buried, and the caller is told
    both succeeded — a silent overwrite on the one verb whose docstring is
    about not half-applying.

    A caller cannot name the intermediate revision, because it does not exist
    until this batch writes it. So re-declaring the SAME parent is the only
    legitimate thing a second proposal can do, and anything else is a stale
    read."""
    note = await _make(notes.store)

    def _p(body: str, parent: str) -> Proposal:
        return Proposal(
            payload=NoteRevisionProposal(
                note_id=note.id, body=body, parent_revision_id=parent,
            ),
            instruction_sha256="e" * 64, model="curator-model",
            established_by="run-curator-1",
        )

    with pytest.raises(StaleNoteHeadError):
        await notes.store.propose(
            key=WS,
            proposals=(
                _p("first", note.head_revision_id),
                _p("second", "00000000-0000-0000-0000-0000000000ff"),
            ),
            now_ms=NOW + 5,
        )

    found = await notes.store.read(workspace=WS, note_id=note.id)
    assert found is not None
    assert found[1].body not in {"first", "second"}, (
        "the refusal is decided before anything is written"
    )


# --- merge: the one cross-note write, and it is atomic ---------------------


async def test_merge_appends_to_the_survivor_and_retires_the_rest(
    notes: _Harness,
) -> None:
    survivor = await _make(notes.store, title="Grant wedding", anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    dup = await _make(notes.store, title="Grant's wedding", anchor_date=20261011,
                            anchor_entity_ids=("grant",))

    retired = await notes.store.merge(
        workspace=WS, survivor=survivor.id,
        survivor_parent=survivor.head_revision_id,
        merged_body="Both shortlists, and the disagreement about the date.",
        retire=(dup.id,), established_by="run-curator-1",
        instruction_sha256="a" * 64, model="curator-model", now_ms=NOW + 9,
    )

    assert retired == 1
    assert [n.id for n in await notes.store.standing(workspace=WS)] == [survivor.id]
    head = await notes.store.read(workspace=WS, note_id=survivor.id)
    assert head is not None
    assert head[1].body == "Both shortlists, and the disagreement about the date."
    gone = await notes.store.read(workspace=WS, note_id=dup.id)
    assert gone is not None
    assert gone[0].replaced_by == survivor.id
    assert gone[0].retire_reason == NoteSupersedeReason.MERGED


async def test_a_merge_that_loses_the_cas_retires_nothing(
    notes: _Harness,
) -> None:
    """**The finding this verb exists for.** `propose` was made atomic on the
    argument that a merge must not proceed on the half that landed — and
    `supersede` opened its own transaction, so the named scenario stayed fully
    reachable. If the tag path revised the survivor while the curator was
    thinking, the whole merge is refused and the duplicate stays standing."""
    survivor = await _make(notes.store, title="Grant wedding", anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    dup = await _make(notes.store, title="Grant's wedding", anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    # The tag path gets there first.
    await notes.store.append_revision(
        workspace=WS, note_id=survivor.id, body="Miles says the 12th now.",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:miles", now_ms=NOW + 1,
    )

    with pytest.raises(StaleNoteHeadError):
        await notes.store.merge(
            workspace=WS, survivor=survivor.id,
            survivor_parent=survivor.head_revision_id,
            merged_body="a merge written against a body that moved",
            retire=(dup.id,), established_by="run-curator-1",
            instruction_sha256="a" * 64, model="curator-model", now_ms=NOW + 9,
        )

    standing = {n.id for n in await notes.store.standing(workspace=WS)}
    assert dup.id in standing, "a refused merge retires nothing"
    head = await notes.store.read(workspace=WS, note_id=survivor.id)
    assert head is not None
    assert head[1].body == "Miles says the 12th now.", (
        "the tag path's revision stands; the curator's is not applied over it"
    )


async def test_merge_never_retires_the_survivor_itself(notes: _Harness) -> None:
    """A model naming the survivor among the duplicates would otherwise retire
    the note it just wrote into, leaving a workspace whose merged content is
    only reachable through a retired row."""
    survivor = await _make(notes.store, anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    dup = await _make(notes.store, anchor_date=20261011,
                            anchor_entity_ids=("grant",))

    retired = await notes.store.merge(
        workspace=WS, survivor=survivor.id,
        survivor_parent=survivor.head_revision_id, merged_body="everything",
        retire=(survivor.id, dup.id), established_by="run-curator-1",
        instruction_sha256="a" * 64, model="m", now_ms=NOW + 9,
    )

    assert retired == 1
    assert [n.id for n in await notes.store.standing(workspace=WS)] == [survivor.id]


async def test_merge_cannot_retire_another_workspaces_note(
    notes: _Harness,
) -> None:
    survivor = await _make(notes.store, anchor_date=20261011,
                            anchor_entity_ids=("grant",))
    theirs = await _make(
        notes.store, workspace="another-workspace", anchor_date=20261011,
    )

    retired = await notes.store.merge(
        workspace=WS, survivor=survivor.id,
        survivor_parent=survivor.head_revision_id, merged_body="everything",
        retire=(theirs.id,), established_by="run-curator-1",
        instruction_sha256="a" * 64, model="m", now_ms=NOW + 9,
    )

    assert retired == 0
    still = await notes.store.read(
        workspace="another-workspace", note_id=theirs.id,
    )
    assert still is not None and still[0].retired_at is None


# --- the REFUSED half of merge_groups, which had no coverage at all --------
#
# It is only counted, never rendered, which is precisely why the two
# implementations were free to disagree about it — `SqlNoteStore` grouped over
# a placeholder-body pass and returned `''` where the double returned the real
# text. Harmless, and therefore the kind that survives. Both now return NOTES
# with no body at all, so the disagreement is unrepresentable rather than fixed.


async def test_a_date_only_collision_comes_back_in_the_refused_half(
    notes: _Harness,
) -> None:
    """The refusal is the reading that says what entity resolution would
    unlock. A store that silently dropped these would make "merge does nothing"
    indistinguishable from "there is nothing to merge"."""
    a = await _make(
        notes.store, title="Dinner, 9 Sept", anchor_date=20260909,
        anchor_entity_ids=(),
    )
    b = await _make(
        notes.store, title="Ada's flight to Tokyo", anchor_date=20260909,
        anchor_entity_ids=(),
    )

    eligible, refused = await notes.store.merge_groups(workspace=WS, limit=10)

    assert eligible == (), "a date is not an occasion"
    assert len(refused) == 1
    assert {n.id for n in refused[0]} == {a.id, b.id}


async def test_the_refused_half_carries_no_bodies_in_either_implementation(
    notes: _Harness,
) -> None:
    """Both return `Note` objects, not `(Note, body)` pairs. Asserted through
    the SHAPE rather than by comparing two body strings, because comparing
    strings is what let the divergence stand: `''` and the real text are both
    strings and neither side was wrong on its own terms."""
    await _make(notes.store, title="A", anchor_date=20260909,
                anchor_entity_ids=())
    await _make(notes.store, title="B", anchor_date=20260909,
                anchor_entity_ids=())

    _eligible, refused = await notes.store.merge_groups(workspace=WS, limit=10)

    assert refused, "positive control: there IS a refused group to inspect"
    for group in refused:
        for entry in group:
            assert isinstance(entry, Note), (
                f"refused entries are Notes, got {type(entry).__name__} — a "
                f"body here is a value with no consumer, which is where two "
                f"implementations quietly disagree"
            )


async def test_an_eligible_group_still_carries_its_bodies(
    notes: _Harness,
) -> None:
    """The positive control for the asymmetry. Stripping bodies from the
    REFUSED half must not strip them from the eligible one — the merge is asked
    to preserve content it must therefore be shown."""
    await _make(notes.store, title="Grant wedding", anchor_date=20261011,
                anchor_entity_ids=("grant",), body="Ada can't do Fridays.")
    await _make(notes.store, title="Grant's wedding", anchor_date=20261011,
                anchor_entity_ids=("grant",), body="Ben found Sheung Wan.")

    eligible, _refused = await notes.store.merge_groups(workspace=WS, limit=10)

    assert len(eligible) == 1
    bodies = {body for _, body in eligible[0]}
    assert bodies == {"Ada can't do Fridays.", "Ben found Sheung Wan."}


async def test_the_refused_half_is_not_capped_by_limit(
    notes: _Harness,
) -> None:
    """`limit` bounds what the tick will READ, and a refused group is never
    read — so capping it would under-report the very number that argues for
    populating `note_entity`."""
    for day in (20260901, 20260902, 20260903):
        for title in ("A", "B"):
            await _make(notes.store, title=f"{title}{day}", anchor_date=day,
                        anchor_entity_ids=())

    _eligible, refused = await notes.store.merge_groups(workspace=WS, limit=1)

    assert len(refused) == 3, "all three refusals are reported, not one"


# --- the wake pass (§9.6) — the writer these three columns never had -------
#
# `revisit_at`, `strikes` and `last_wake_revision_id` existed since 0026 with a
# CHECK and nothing anywhere set them, so the revisitor's `revisit_at` trigger
# was structurally unreachable: every note reaching it was due by anchor date
# alone, and an undated note was never revisited at all.


async def test_a_wake_on_an_unchanged_note_takes_a_strike_and_does_not_reschedule(
    notes: _Harness,
) -> None:
    """The no-progress arm. **`revisit_at` must NOT advance** — the note stays
    due and the count climbs toward the ceiling the caller enforces. Advancing
    it here would make a note that never changes wake forever on a fresh
    schedule, which is the loop the strike exists to break."""
    note = await _make(notes.store)
    # A first wake establishes the baseline, so the second sees an unmoved head.
    await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW + 1000, now_ms=NOW,
    )

    outcome = await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW + 9999,
        now_ms=NOW + 1,
    )

    assert outcome is not None
    assert outcome.reset is False
    assert outcome.strikes == 1
    after = await notes.store.read(workspace=WS, note_id=note.id)
    assert after is not None
    assert after[0].revisit_at == NOW + 1000, (
        "the schedule is NOT advanced by a wake that found no progress"
    )


async def test_a_tag_path_revision_resets_the_strike_and_reschedules(
    notes: _Harness,
) -> None:
    note = await _make(notes.store)
    await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW + 1000, now_ms=NOW,
    )
    await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW + 1000,
        now_ms=NOW + 1,
    )  # strikes -> 1

    moved = await notes.store.append_revision(
        workspace=WS, note_id=note.id, body="Ada booked Sai Kung.",
        parent_revision_id=note.head_revision_id,
        established_by="tag:miles", now_ms=NOW + 2,
    )
    outcome = await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW + 5000,
        now_ms=NOW + 3,
    )

    assert outcome is not None
    assert outcome.reset is True
    assert outcome.strikes == 0
    after = await notes.store.read(workspace=WS, note_id=note.id)
    assert after is not None
    assert after[0].last_wake_revision_id == moved.id
    assert after[0].revisit_at == NOW + 5000


async def test_a_background_passes_own_write_does_not_reset_the_strike(
    notes: _Harness,
) -> None:
    """**§9.6's trap, and the whole reason the qualifier exists.** The reset
    must be «head moved BY THE TAG PATH», not «head moved». If a background
    pass's own writes counted, a note the revisitor keeps rewriting would reset
    its own strike forever and the breaker would be decorative — a
    self-referential reset is not a reset.

    The spec says the mechanism *"reads correct without the qualifier and loops
    forever with it missing"*, which is exactly what this asserts."""
    note = await _make(notes.store)
    await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW + 1000, now_ms=NOW,
    )

    # The curator revises it — a run id, not a person.
    await notes.store.append_revision(
        workspace=WS, note_id=note.id, body="tidied by the curator",
        parent_revision_id=note.head_revision_id,
        established_by="run-curator-1", now_ms=NOW + 2,
    )
    outcome = await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW + 5000,
        now_ms=NOW + 3,
    )

    assert outcome is not None
    assert outcome.reset is False, (
        "a background pass's own write is not progress; counting it lets a "
        "note reset its own strike forever"
    )
    assert outcome.strikes == 1
    after = await notes.store.read(workspace=WS, note_id=note.id)
    assert after is not None
    assert after[0].revisit_at == NOW + 1000, "and it does not reschedule"


async def test_strikes_accumulate_across_wakes(notes: _Harness) -> None:
    """The positive control for the two `reset is False` assertions above: they
    are also true of a store that does nothing at all."""
    note = await _make(notes.store)
    seen = [
        (await notes.store.record_wake(
            workspace=WS, note_id=note.id, next_revisit_at=NOW + 1000,
            now_ms=NOW + n,
        )).strikes
        for n in range(4)
    ]

    assert seen == [0, 1, 2, 3], (
        "the FIRST wake establishes the baseline and resets; the rest strike"
    )


async def test_a_wake_cannot_reach_another_workspaces_note(
    notes: _Harness,
) -> None:
    note = await _make(notes.store)

    assert await notes.store.record_wake(
        workspace="another-workspace", note_id=note.id,
        next_revisit_at=NOW + 1000, now_ms=NOW,
    ) is None
    after = await notes.store.read(workspace=WS, note_id=note.id)
    assert after is not None
    assert after[0].strikes == 0, "and it wrote nothing"


async def test_a_wake_on_an_unknown_or_retired_note_reports_none(
    notes: _Harness,
) -> None:
    """`None`, never a raise and never a fabricated outcome — a background pass
    that raises on a row it does not recognise takes the whole tick down."""
    assert await notes.store.record_wake(
        workspace=WS, note_id="00000000-0000-0000-0000-0000000000ff",
        next_revisit_at=NOW, now_ms=NOW,
    ) is None
    assert await notes.store.record_wake(
        workspace=WS, note_id="not-a-uuid", next_revisit_at=NOW, now_ms=NOW,
    ) is None

    note = await _make(notes.store)
    await notes.store.supersede(
        key=WS, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=NOW,
    )
    assert await notes.store.record_wake(
        workspace=WS, note_id=note.id, next_revisit_at=NOW, now_ms=NOW,
    ) is None, "a retired note does not wake"
