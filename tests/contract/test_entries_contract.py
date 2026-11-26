"""One entry contract for SQL and memory, with generated data only."""

import asyncio
from dataclasses import replace

import pytest

from nora.domain.entries import EntryContent, EntryStore
from nora.domain.lineage import ROW, Lineage, message
from nora.testing.entries import FakeEntryStore

ROOM_A_LINEAGE = Lineage.of({ROW: (message("signal:room-a"),)})
CONTENT = EntryContent(
    (("topic", "generated-racing"),),
    "A recurring generated subject.",
    (("social", "41"),),
    "medium",
    "active",
    "cognition",
    "generated-session",
    "generated-model",
    "a" * 64,
)


@pytest.fixture(scope="module")
def _entry_db(pg_module_url: str) -> str:
    return pg_module_url


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeEntryStore(
            lineages={
                ("social", "41"): ROOM_A_LINEAGE,
                ("social", "42"): ROOM_A_LINEAGE,
            }
        )
        return
    import sqlalchemy as sa

    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.entries import SqlEntryStore
    from nora.infra.sql.schema import STIMULUS

    engine = create_sql_engine(request.getfixturevalue("_entry_db"))
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
        await conn.execute(sa.text("TRUNCATE notebook_entry_revision, notebook_entry CASCADE"))
        # The row the contract's stimulus citation resolves to, generated and
        # naming a room.
        await conn.execute(sa.text("DELETE FROM stimulus WHERE id IN (41, 42)"))
        await conn.execute(
            sa.insert(STIMULUS), [
                {"id": 41, "unit": "u", "kind": "social", "item": "generated-item",
                 "ref": "generated-ref", "platform": "signal",
                 "conversation_id": "room-a", "at_ms": 1},
                {"id": 42, "unit": "other", "kind": "social", "item": "generated-item-other",
                 "ref": "generated-ref-other", "platform": "signal",
                 "conversation_id": "room-a", "at_ms": 1},
            ]
        )
    try:
        yield SqlEntryStore(engine)
    finally:
        await engine.dispose()


async def test_topic_lifecycle_transitions(store: EntryStore) -> None:
    entry = await store.create(kind="topic", unit="u", content=CONTENT)
    for state in ("dormant", "active", "retired"):
        head = await store.append(
            unit="u",
            entry_id=entry.id,
            base_revision_id=entry.head.id,
            content=replace(CONTENT, state=state),
        )
        got = await store.get(unit="u", entry_id=entry.id)
        assert got and got.head == head
        entry = got
    with pytest.raises(ValueError, match="lifecycle"):
        await store.append(unit="u", entry_id=entry.id, base_revision_id=head.id, content=CONTENT)
    assert [r.content.state for r in await store.revisions(unit="u", entry_id=entry.id)] == [
        "retired",
        "active",
        "dormant",
        "active",
    ]
    assert entry.audience == "internal"


@pytest.mark.parametrize("about", [(), (("person", "p"),), (("topic", "a"), ("topic", "b"))])
async def test_topic_about_exactly_one_topic(store: EntryStore, about) -> None:
    with pytest.raises(ValueError, match="topic_about"):
        await store.create(kind="topic", unit="u", content=replace(CONTENT, about=about))


async def test_topic_has_no_links(store: EntryStore) -> None:
    with pytest.raises(ValueError, match="topic_links"):
        await store.create(
            kind="topic", unit="u", content=replace(CONTENT, links=(("lesson", "l"),))
        )


@pytest.mark.parametrize("kind", ["topic", "profile_line", "lesson", "unknown", "person"])
async def test_topic_citations_record_rollup_only(store: EntryStore, kind: str) -> None:
    with pytest.raises(ValueError, match="citation_layer"):
        await store.create(
            kind="topic", unit="u", content=replace(CONTENT, citations=((kind, "x"),))
        )


async def test_a_stimulus_citation_resolves_the_room_it_names(
    store: EntryStore,
) -> None:
    entry = await store.create(
        kind="topic", unit="u",
        content=replace(CONTENT, citations=(("social", "41"),)),
    )
    assert entry.head.lineage.section("row") == (
        message("signal:room-a"),
    )


@pytest.mark.parametrize(
    "citations",
    [(("web_page", "generated-page"),), (("send", "generated-send"),)],
)
async def test_a_citation_that_resolves_to_nothing_is_refused(
    store: EntryStore, citations: tuple[tuple[str, str], ...]
) -> None:
    """An admitted kind whose row carries no resolvable lineage is refused at
    write time — never an ok account no room can show."""
    with pytest.raises(ValueError, match="citation_lineage"):
        await store.create(
            kind="topic", unit="u", content=replace(CONTENT, citations=citations)
        )


@pytest.mark.parametrize(
    ("kind", "change", "reason"),
    [
        ("lesson", {}, "unknown_kind"),
        ("topic", {"confidence": "certain"}, "confidence"),
        ("topic", {"body": " "}, "empty"),
        ("topic", {"instruction_hash": "not-a-hash"}, "instruction_hash"),
    ],
)
async def test_entry_refuses_what_its_kind_does_not_admit(
    store: EntryStore, kind: str, change: dict, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        await store.create(kind=kind, unit="u", content=replace(CONTENT, **change))


async def test_held_returns_the_units_non_retired_entries_by_subject(
    store: EntryStore,
) -> None:
    retired = await store.create(kind="topic", unit="u", content=CONTENT)
    await store.append(
        unit="u", entry_id=retired.id, base_revision_id=retired.head.id,
        content=replace(CONTENT, state="retired"))
    beta = await store.create(
        kind="topic", unit="u",
        content=replace(CONTENT, about=(("topic", "generated-beta"),)))
    alpha = await store.create(
        kind="topic", unit="u",
        content=replace(CONTENT, about=(("topic", "generated-alpha"),)))
    await store.create(
        kind="topic", unit="other",
        content=replace(CONTENT, citations=(("social", "42"),)))
    assert [e.id for e in await store.held(unit="u")] == [alpha.id, beta.id]


async def test_entry_stale_base(store: EntryStore) -> None:
    entry = await store.create(kind="topic", unit="u", content=CONTENT)
    first = await store.append(
        unit="u",
        entry_id=entry.id,
        base_revision_id=entry.head.id,
        content=replace(CONTENT, body="First revision."),
    )
    with pytest.raises(ValueError, match="stale_base"):
        await store.append(
            unit="u",
            entry_id=entry.id,
            base_revision_id=entry.head.id,
            content=replace(CONTENT, body="Stale revision."),
        )
    held = await store.get(unit="u", entry_id=entry.id)
    assert held and held.head == first
    assert first.base_revision_id == entry.head.id
    assert len(await store.revisions(unit="u", entry_id=entry.id)) == 2


async def test_entry_concurrent_append_and_unit_boundary(store: EntryStore) -> None:
    entry = await store.create(kind="topic", unit="u", content=CONTENT)
    assert await store.get(unit="other", entry_id=entry.id) is None
    assert await store.revisions(unit="other", entry_id=entry.id) == ()
    results = await asyncio.gather(
        *(
            store.append(
                unit="u",
                entry_id=entry.id,
                base_revision_id=entry.head.id,
                content=replace(CONTENT, body=f"Generated {i}"),
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ValueError) and str(r) == "stale_base" for r in results) == 1
    assert len(await store.revisions(unit="u", entry_id=entry.id)) == 2


async def test_two_concurrent_creates_share_one_live_subject(store: EntryStore) -> None:
    """One live account per (unit, subject), case-folded, and the answer is
    the store's — the uniqueness the session's own read cannot guarantee."""
    results = await asyncio.gather(
        store.create(kind="topic", unit="u", content=CONTENT),
        store.create(
            kind="topic", unit="u",
            content=replace(CONTENT, about=(("topic", "Generated-RACING"),))),
        return_exceptions=True,
    )
    created = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, ValueError)]
    assert len(created) == 1 and len(refused) == 1
    assert str(refused[0]) == "duplicate"
    assert len(await store.held(unit="u")) == 1
