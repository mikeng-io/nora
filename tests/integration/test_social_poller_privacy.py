"""The social poller places an account's items in units only when its latest
recorded probe answered public, across a restart too."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa

from nora.domain.models import PersonRef
from nora.domain.social_stream import Page
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.infra.sql.schema import SOCIAL_ITEM
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.persons import Persons
from nora.policy import Conversation, Policy
from tests.integration.test_social_poller import (  # noqa: F401 — fixtures are used by name
    ADA,
    BEA,
    World,
    _db,
    grid,
    post,
    probe,
    quiet,
    ticks,
    world,
)


class _Revisions:
    def current(self) -> dict[str, str]:
        return {f"rev_{k}": f"test-{k}" for k in ("code", "config", "policy", "identity")}


@pytest.fixture
async def placing(world: World) -> AsyncIterator[World]:  # noqa: F811
    # The store places items in ada's one unit, `test-unit`.
    room = Conversation("signal", frozenset({"test-room"}), frozenset({"ada"}), unit="test-unit")
    scope = RegistrationScope(
        Policy((room,), _by_id={("signal", "test-room"): room}),
        Persons({"ada": PersonRef("ada", "Generated")}, {}),
    )
    world.store = SqlSocialStreamStore(
        world.engine, registration=SqlItemRegistration(scope, _Revisions()),
    )
    try:
        yield world
    finally:
        async with world.engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate registry cascade"))


async def _units(placed: World, item_ids: list[str]) -> list[str | None]:
    async with placed.engine.connect() as conn:
        return list((await conn.execute(
            sa.select(SOCIAL_ITEM.c.unit).where(SOCIAL_ITEM.c.item_id.in_(item_ids)),
        )).scalars())


@pytest.mark.parametrize("is_private,unit", [(False, "test-unit"), (True, None), (None, None)])
async def test_an_item_is_placed_in_a_unit_only_when_its_account_probed_public(
    placing, is_private, unit,
) -> None:
    quiet(placing, BEA)
    placing.source.probes[ADA] = probe(posts=1, is_private=is_private, following=True)
    placing.source.pages[ADA] = Page(items=(post(placing, "p1"),), next_cursor=None)
    await placing.poller().tick()
    assert await _units(placing, ["p1"]) == [unit]


@pytest.mark.parametrize("is_private,unit", [(False, "test-unit"), (True, None)])
async def test_a_restarted_poller_places_backfill_items_from_the_recorded_probe(
    placing, is_private, unit,
) -> None:
    quiet(placing, BEA)
    grid(placing, ADA, 40)
    placing.source.probes[ADA] = probe(posts=40, is_private=is_private, following=True)
    await placing.poller().tick()
    assert placing.source.cursors == [None, None], "the first page was read"

    await ticks(placing, placing.poller(), 1)
    assert placing.probes().count(ADA) == 1, "the new poller has not probed ada"
    assert placing.source.cursors[2:] == [f"{ADA}-c1"]
    assert set(await _units(placing, [f"{ADA}-{k}" for k in range(12, 24)])) == {unit}
