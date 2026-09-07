"""`MediaObjectStore` has two implementations — `SqlMediaObjectStore` and
`nora.testing.FakeMediaObjectStore` — so it gets one suite both pass
(CLAUDE.md), and the reason the fake cannot quietly drift from Postgres.

Why this suite exists at all (R-C, issue #113, measured 2026-09-07): the port
had a SQL store and NO shared fake, so two test files each wrote their own —
`tests/test_tools.py` substring-matched `description`/`prompt`/`title`, and
`tests/unit/test_media_custody.py` returned every row for any query. Neither
was compared against the SQL store, and the tools built on the port
(`media_search`, `media_send`) were green against whichever semantics their
file happened to invent. The properties below are the ones those tools rely
on, pinned against the real `ilike`-over-`provenance::text` query.

Database scoped to the MODULE, truncated between tests (CLAUDE.md's
contract-suite rule): the isolation this needs is «no rows from the last
test», not «no schema from the last test».
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.media import MediaObject
from nora.domain.ports import MediaObjectStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.testing import FakeMediaObjectStore

CAT = "aabbcc" + "0" * 58
CAT_TWIN = "aabbcc" + "f" * 58          # shares the 6-hex handle with CAT
DOG = "112233" + "0" * 58
PHOTO = "ddeeff" + "0" * 58


def _obj(
    sha256: str = CAT, *, prompt: str = "a cat on a sofa", source: str = "generated",
    description: str | None = None, created_at_ms: int = 1_700_000_000_000,
) -> MediaObject:
    return MediaObject(
        sha256=sha256, media_type="image/png", size=4, source=source,
        description=prompt if description is None else description,
        provenance={"prompt": prompt, "title": prompt[:80], "platform": "signal"},
        created_at_ms=created_at_ms,
    )


@pytest.fixture(scope="module")
def _media_db(pg_admin: Any, pg_template: str) -> Iterator[str]:
    """ONE database for this whole file, cloned from the session's migrated
    template — see `tests/conftest.py::pg_template`."""
    dbname = "t_media_objects_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}" TEMPLATE "{pg_template}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["fake", "sql"])
async def store(request: Any) -> AsyncIterator[MediaObjectStore]:
    if request.param == "fake":
        yield FakeMediaObjectStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_media_db"))
    try:
        yield SqlMediaObjectStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("truncate media_objects"))
        await engine.dispose()


@pytest.fixture(params=["fake", "sql"])
async def broken(request: Any) -> AsyncIterator[MediaObjectStore]:
    """A store whose database is unreachable — the fake's `fail` switch, and the
    SQL store pointed at a port nothing listens on."""
    if request.param == "fake":
        yield FakeMediaObjectStore(fail=True)
        return
    engine = create_sql_engine("postgresql+asyncpg://u:p@127.0.0.1:1/dead")
    try:
        yield SqlMediaObjectStore(engine)
    finally:
        await engine.dispose()


def test_both_implementations_satisfy_the_port() -> None:
    assert isinstance(FakeMediaObjectStore(), MediaObjectStore)
    assert isinstance(
        SqlMediaObjectStore(create_sql_engine("postgresql+asyncpg://u:p@127.0.0.1:1/x")),
        MediaObjectStore,
    )


async def test_an_unknown_object_is_a_miss_on_every_read(store):
    assert await store.get(sha256=CAT) is None
    assert await store.by_prefix(handle="aabbcc") == ()
    assert await store.search(query="cat") == ()
    assert await store.search(query="") == ()


async def test_a_saved_object_reads_back_whole(store):
    obj = _obj()
    assert await store.save(obj=obj) is True
    assert await store.get(sha256=CAT) == obj


async def test_save_is_first_source_wins_and_still_answers_true(store):
    """`on_conflict_do_nothing` on `sha256`: the bytes' birth record is written
    once. A second writer — the same photo forwarded into another room, a
    describer re-seeing it — must neither overwrite nor be told it failed
    (a False would make custody count a healthy write as an error)."""
    first = _obj(source="inbound", prompt="", description="")
    second = _obj(source="generated", prompt="a cat on a sofa")
    assert await store.save(obj=first) is True
    assert await store.save(obj=second) is True
    got = await store.get(sha256=CAT)
    assert got is not None
    assert got.source == "inbound"
    assert got.description == ""


async def test_search_matches_a_word_in_the_description_case_insensitively(store):
    await store.save(obj=_obj(CAT, prompt="A Cat on a sofa"))
    await store.save(obj=_obj(DOG, prompt="a dog in the park"))
    hits = await store.search(query="cat")
    assert [h.sha256 for h in hits] == [CAT]


async def test_search_matches_a_word_carried_only_by_the_provenance(store):
    """The SQL query reads `description` OR `provenance::text`; a generated
    image whose description was rewritten by a describer is still findable by
    the prompt that made it."""
    await store.save(obj=_obj(CAT, prompt="birthday card for miles", description="a greeting card"))
    hits = await store.search(query="birthday")
    assert [h.sha256 for h in hits] == [CAT]


async def test_search_ands_every_word_in_any_order(store):
    await store.save(obj=_obj(CAT, prompt="birthday card for miles"))
    await store.save(obj=_obj(DOG, prompt="birthday cake"))
    assert [h.sha256 for h in await store.search(query="miles birthday")] == [CAT]
    assert await store.search(query="birthday sofa") == ()


async def test_an_empty_query_lists_the_newest_first_up_to_the_limit(store):
    await store.save(obj=_obj(CAT, created_at_ms=1_000))
    await store.save(obj=_obj(DOG, prompt="a dog", created_at_ms=3_000))
    await store.save(obj=_obj(PHOTO, prompt="a photo", created_at_ms=2_000))
    assert [h.sha256 for h in await store.search(query="")] == [DOG, PHOTO, CAT]
    assert [h.sha256 for h in await store.search(query="", limit=2)] == [DOG, PHOTO]


async def test_search_can_be_scoped_to_sources(store):
    """`media_search` searches Nora's OWN media and never inbound rows — other
    people's photos are not her library (DEC-138). The scope is the caller's
    to set; the store must honour it exactly."""
    await store.save(obj=_obj(CAT, prompt="a cat", source="generated"))
    await store.save(obj=_obj(DOG, prompt="a cat", source="inbound"))
    hits = await store.search(query="cat", sources=("generated", "fetched"))
    assert [h.sha256 for h in hits] == [CAT]
    everything = await store.search(query="cat")
    assert {h.sha256 for h in everything} == {CAT, DOG}


async def test_by_prefix_surfaces_every_collision_newest_first(store):
    """A 6-hex handle shared by two objects is a QUESTION for the caller
    (`media_send` answers `ambiguous`), never a silent first pick — so the
    store must return BOTH, and the window must not hide the second."""
    await store.save(obj=_obj(CAT, prompt="cat one", created_at_ms=1_000))
    await store.save(obj=_obj(CAT_TWIN, prompt="cat two", created_at_ms=2_000))
    await store.save(obj=_obj(DOG, prompt="a dog"))
    hits = await store.by_prefix(handle="aabbcc")
    assert [h.sha256 for h in hits] == [CAT_TWIN, CAT]
    assert [h.sha256 for h in await store.by_prefix(handle="aabbcc", limit=1)] == [CAT_TWIN]


async def test_an_unreachable_database_answers_false_and_empty_and_never_raises(broken):
    """The SQL store's `except Exception` arms: a write that could not land
    answers False (media stays deliverable), and every read is empty. The
    tools above turn that into `unavailable`; a raise here would surface as a
    turn failure instead."""
    assert await broken.save(obj=_obj()) is False
    assert await broken.get(sha256=CAT) is None
    assert await broken.search(query="cat") == ()
    assert await broken.by_prefix(handle="aabbcc") == ()
    # `comprehend` runs in a SPAWNED task, where an exception would surface
    # only as a done-callback log line. A lost comprehension costs one re-read
    # on the next sighting and must never cost anything else.
    assert await broken.comprehend(
        sha256=CAT, title="a cat", description="a cat", content="", model="glm",
    ) is False


# ---- the comprehension verb (issue #116) -----------------------------------
#
# `save` is first-source-wins and `comprehend` is an upsert, and the pair has
# to hold in BOTH orders because the two writers race by design: custody writes
# the index row INLINE (the handle is a promise the bytes are retrievable) and
# the model pass is SPAWNED, so a fast model on a slow disk arrives first.


async def test_a_comprehension_lands_on_a_row_custody_already_wrote(store):
    await store.save(obj=_obj(CAT, source="inbound", prompt="", description=""))

    assert await store.comprehend(
        sha256=CAT, title="a plate of noodles",
        description="a bowl of wonton noodles on a marble table",
        content="生記粥麵", model="glm",
    ) is True

    got = await store.get(sha256=CAT)
    assert got is not None
    assert (got.title, got.description, got.content) == (
        "a plate of noodles",
        "a bowl of wonton noodles on a marble table",
        "生記粥麵",
    )
    assert got.describe_model == "glm"
    assert got.comprehended is True


async def test_a_comprehension_that_beats_custody_creates_the_row(store):
    """The spawned read finishing before the inline index write is not an
    error — it is the ordinary race. A plain UPDATE would have silently dropped
    the comprehension on the floor."""
    assert await store.comprehend(
        sha256=DOG, title="a dog", description="a dog in the park",
        content="", model="glm",
    ) is True

    got = await store.get(sha256=DOG)
    assert got is not None
    assert got.title == "a dog"


async def test_a_comprehension_never_overwrites_the_birth_record(store):
    """DEC-138: provenance belongs to whoever first held the bytes, and a read
    is not a birth. The chunk record a video read leaves (issue #117) MERGES
    beside it rather than replacing it — a fact about the reading and a fact
    about the birth have to coexist on one row."""
    await store.save(obj=_obj(CAT, source="generated", prompt="a cat on a sofa"))

    await store.comprehend(
        sha256=CAT, title="a cat", description="a cat on a sofa", content="",
        model="glm", provenance={"video_capped": True, "video_chunks": 10},
    )

    got = await store.get(sha256=CAT)
    assert got is not None
    assert got.source == "generated", "source is never touched by a read"
    assert got.provenance is not None
    assert got.provenance["prompt"] == "a cat on a sofa"
    assert got.provenance["video_capped"] is True
    assert got.provenance["video_chunks"] == 10


async def test_an_uncomprehended_row_says_so(store):
    """All three fields empty is an INDEX ENTRY, not a description of nothing.
    Everything downstream branches on this: the job skips a row that is already
    read, `media_fetch` refuses to serve an empty one as an answer, and the
    transcript renders the bare handle."""
    await store.save(obj=_obj(CAT, source="inbound", prompt="", description=""))
    got = await store.get(sha256=CAT)
    assert got is not None
    assert got.comprehended is False


async def test_search_finds_a_word_carried_only_by_the_title_or_the_content(store):
    """One model pass writes all three fields; an index that could not find
    «the whiteboard that said Q3» would be holding the answer and refusing to
    look at it."""
    await store.save(obj=_obj(CAT, source="inbound", prompt="", description=""))
    await store.comprehend(
        sha256=CAT, title="a whiteboard", description="a photo of a meeting room",
        content="Q3 targets: 40%", model="glm",
    )

    assert [h.sha256 for h in await store.search(query="whiteboard")] == [CAT]
    assert [h.sha256 for h in await store.search(query="Q3")] == [CAT]
