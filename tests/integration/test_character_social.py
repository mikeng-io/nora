"""A person's social items as Character evidence (design/cognition-loop/22 §6),
on the session path: the real `SqlSocialStreamStore` read, `PersonEvidence`'s
count and read, the `<social_items>` block and `character_revise`.

What each test guards, if it went wrong without anything saying so: a session
woken by social items alone that has nothing it can cite; a citation that
reaches another person's item, or one the store must not hand over (gone,
hidden, opted out); a back-fill that wakes every person it pages; and an
identifier that reaches the account or the prompt through a social item.

Every value here is synthetic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nora.agent.dreamer import DreamerRuntime
from nora.agent.egress import scan_identifiers
from nora.domain.culture import character_key
from nora.domain.models import PersonRef
from nora.domain.social_stream import MediaItem, PersonSocialItem, SocialItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEDIA_OBJECTS
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.language.character import (
    CHARACTER_PERSON_ADAPTOR_NAME,
    CharacterPersonAdaptor,
    CharacterRoom,
    PersonEvidence,
    PersonKey,
)
from nora.language.character_session import AccountNotRecordableError
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence, InMemoryCultureStore

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
NOW = T0 + timedelta(days=2)
ROOMS = (CharacterRoom(key="room-a", platform="signal", label="Friends"),)
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")
CY = PersonRef(person_id="cy", name="Cy", honcho_peer_id="cy")
KEY = character_key("friends", "ada")
CY_KEY = character_key("friends", "cy")
KEYS = {
    KEY: PersonKey(unit="friends", person=ADA, rooms=ROOMS, home="room-a"),
    CY_KEY: PersonKey(unit="friends", person=CY, rooms=ROOMS, home="room-a"),
}
SWIM = "a1" * 32
LEAK = "b2" * 32


@pytest.fixture
async def social(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        # `cy` has `social = false`.
        yield SqlSocialStreamStore(engine, opted_out=frozenset({"cy"})), engine
    finally:
        await engine.dispose()


async def _record(
    store: SqlSocialStreamStore, item_id: str, *, person: str = "ada",
    origin: str = "poll", caption: str = "dawn swim again", kind: str = "post",
    media: str | None = None, collab: bool = False,
) -> None:
    assert await store.insert_item(
        SocialItem(
            network="instagram", item_id=item_id, account="10000001", kind=kind,  # type: ignore[arg-type]
            taken_at=T0, expires_at=T0 + timedelta(days=1) if kind == "story_frame" else None,
            caption=caption, alt_text="alt text naming a place", collab=collab,
            media=(MediaItem("image", "https://x"),),
        ),
        person=person, origin=origin, seen_at=T0, media_ref=media,  # type: ignore[arg-type]
        location="Placeholder Square",
    )


async def _perceived(engine, sha: str, description: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(
            sha256=sha, media_type="image/jpeg", source="instagram",
            title="a photo", description=description, created_at_ms=0,
        ))


def _session(store: SqlSocialStreamStore, record: InMemoryCharacterEvidence | None = None,
             metrics: Metrics | None = None):
    evidence = PersonEvidence(
        store=record or InMemoryCharacterEvidence(), keys=KEYS, social=store,
    )
    return CharacterPersonAdaptor(
        culture=InMemoryCultureStore(), evidence=evidence, keys=KEYS,
        screen=scan_identifiers, render_chars=240, metrics=metrics, clock=lambda: NOW,
    )


async def _wake(adaptor: CharacterPersonAdaptor, key: str = KEY) -> str:
    items, _cursor = await adaptor.evidence.newest(key=key, after=0, limit=100)
    return await adaptor.inputs(key=key, batch=items)


async def test_a_social_only_wake_renders_its_items_and_may_cite_one(social) -> None:
    store, engine = social
    await _record(store, "9001", media=SWIM)
    await _perceived(engine, SWIM, "someone swimming in the sea at sunrise")
    adaptor = _session(store)

    assert await adaptor.evidence.count(key=KEY, after=0) == 1
    items, cursor = await adaptor.evidence.newest(key=KEY, after=0, limit=100)
    assert cursor == 0 and [type(i) for i in items] == [PersonSocialItem]
    out = await adaptor.inputs(key=KEY, batch=items)

    assert "<social_items " in out and "<person_messages" not in out
    assert 'cite="social:instagram:9001"' in out
    assert 'kind="Instagram post"' in out
    assert 'shows="a photo - someone swimming in the sea at sunrise"' in out
    assert "2026-10-05" in out and "2d ago" in out
    assert ">dawn swim again</item>" in out
    # Never the location, the network's alt text, or a raw handle.
    assert "Placeholder Square" not in out and "alt text" not in out

    revise, _keep = adaptor.verbs(key=KEY)
    answer = await revise(entity="Ada", body="swims at dawn, often",
                          citations=["social:instagram:9001"])
    assert answer == {"status": "ok"}
    (proposal,) = await adaptor.accept(key=KEY, items=())
    assert proposal.citations == ("social:instagram:9001",)


async def test_only_the_persons_own_readable_items_can_be_cited(social) -> None:
    store, _engine = social
    await _record(store, "9001")
    await _record(store, "9002", person="bo")
    await _record(store, "9003")
    await store.record_state(network="instagram", item_id="9003", state="gone")
    await _record(store, "9004")
    await store.record_state(network="instagram", item_id="9004", state="hidden")
    await _record(store, "9005", person="cy")
    adaptor = _session(store)

    out = await _wake(adaptor)
    assert 'cite="social:instagram:9001"' in out
    for other in ("9002", "9003", "9004", "9005"):
        assert f"social:instagram:{other}" not in out
    revise, _keep = adaptor.verbs(key=KEY)
    for other in ("9002", "9003", "9004", "9005"):
        answer = await revise(entity="Ada", body="x", citations=[f"social:instagram:{other}"])
        assert (answer["status"], answer["reason"]) == ("refused", "unresolved_citation"), other
    # A bare item id or another network's spelling of a shown item is not its citation.
    for loose in ("9001", "social:threads:9001", "instagram:9001"):
        answer = await revise(entity="Ada", body="x", citations=[loose])
        assert answer["reason"] == "unresolved_citation", loose

    # The opted-out person's own session counts and reads none of theirs.
    assert await adaptor.evidence.count(key=CY_KEY, after=0) == 0
    assert await _wake(adaptor, CY_KEY) == ""


async def test_a_backfill_never_counts_toward_the_wake_but_is_read(social) -> None:
    store, _engine = social
    record = InMemoryCharacterEvidence()
    adaptor = _session(store, record)
    started = await store.now()
    record.set_last_run(adaptor=CHARACTER_PERSON_ADAPTOR_NAME, key=KEY,
                        at_ms=int((started - timedelta(seconds=1)).timestamp() * 1000))

    await _record(store, "9010", origin="backfill", caption="an old trip")
    await _record(store, "9011", origin="backfill", caption="another old trip")
    assert await adaptor.evidence.count(key=KEY, after=0) == 0

    await _record(store, "9012", caption="today's swim")
    assert await adaptor.evidence.count(key=KEY, after=0) == 1
    out = await _wake(adaptor)
    for item in ("9010", "9011", "9012"):
        assert f'cite="social:instagram:{item}"' in out

    # Once the key has run, what came before it is neither counted nor read.
    after = await store.now()
    record.set_last_run(adaptor=CHARACTER_PERSON_ADAPTOR_NAME, key=KEY,
                        at_ms=int(after.timestamp() * 1000) + 1)
    assert await adaptor.evidence.count(key=KEY, after=0) == 0
    assert await _wake(adaptor) == ""


async def test_the_screen_applies_to_a_social_session(social) -> None:
    store, engine = social
    await _record(store, "9001", media=LEAK, caption="weekend")
    await _perceived(engine, LEAK, "a note reading +85291234567")
    metrics = Metrics()
    adaptor = _session(store, metrics=metrics)

    out = await _wake(adaptor)
    # The description carried an identifier: withheld whole, the item kept.
    assert 'cite="social:instagram:9001"' in out
    assert "+852" not in out and "shows=" not in out
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1

    # The write verb, as the runtime hands it to the model: an account that
    # would carry an identifier is refused before it is staged (R-18/R-28).
    revise, _keep = adaptor.verbs(key=KEY)
    screened = DreamerRuntime._screened(
        SimpleNamespace(_metrics=metrics), CHARACTER_PERSON_ADAPTOR_NAME, revise,  # type: ignore[arg-type]
    )
    answer = await screened(entity="Ada", body="ring her on +85291234567",
                            citations=["social:instagram:9001"])
    assert (answer["status"], answer.get("reason")) == ("refused", "identifier")
    with pytest.raises(AccountNotRecordableError):
        await adaptor.accept(key=KEY, items=())
