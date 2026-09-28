"""`nora memory carry-conclusions` (`nora.app.memory_carry`, DEC-214): the
facts Nora was told before unit scopes existed, copied into every unit scope
of their workspace, through the production client against
`tests.fakes.FakeHoncho`. Placeholder people; the workspace names mirror the
operator's mapping (`67` = friends + wedding on `nora`).
"""

from __future__ import annotations

import pytest

from nora.app.memory_carry import CarryRefusedError, carry_conclusions, render
from nora.app.memory_routing import build_workspace_router
from nora.infra.honcho import HonchoClient
from nora.policy import load_policy
from tests.fakes import FakeHoncho

_POLICY = """
default = "deny"

[[conversations]]
platform = "signal"
id = "g-friends"
label = "Friends"
persons = "*"
unit = "friends"

[[conversations]]
platform = "signal"
id = "g-wedding"
label = "Wedding"
persons = "*"
unit = "wedding"

[[conversations]]
platform = "signal"
id = "g-ridgeline"
label = "Ridgeline"
persons = "*"
unit = "ridgeline"

[units.friends]
workspace = "67"

[units.wedding]
workspace = "67"

[units.ridgeline]
workspace = "ridgeline"
"""


@pytest.fixture
async def honcho():
    fake = FakeHoncho()
    await fake.start()
    fake.seed("nora", created_at="2026-01-01T00:00:00Z")
    fake.conclusions["nora"] = [
        # What `remember` and note discharge wrote: Nora observing, no session.
        {"id": "c1", "content": "likes tea", "observer_id": "nora",
         "observed_id": "jordan", "session_id": None, "level": "explicit"},
        {"id": "c2", "content": "moved to Kowloon", "observer_id": "nora",
         "observed_id": "blair", "session_id": None, "level": "explicit"},
        # Derived from a message: its session's scope backfill already covers it.
        {"id": "c3", "content": "said hi", "observer_id": "nora",
         "observed_id": "jordan", "session_id": "nora-conv-g-friends", "level": "explicit"},
        # A dream: reasoned across sessions, rebuilt by each scope's own dreams.
        {"id": "c4", "content": "tends to plan", "observer_id": "nora",
         "observed_id": "jordan", "session_id": None, "level": "deductive"},
        # Somebody else's view.
        {"id": "c5", "content": "x", "observer_id": "jordan",
         "observed_id": "jordan", "session_id": None, "level": "explicit"},
    ]
    yield fake
    await fake.close()


@pytest.fixture
def world(tmp_path):
    (tmp_path / "policy.toml").write_text(_POLICY)
    policy = load_policy(tmp_path / "policy.toml")
    router = build_workspace_router(
        policy=policy, bindings={"67": "nora", "ridgeline": "nora-ridgeline"},
        default_workspace="nora",
    )
    return policy, router


async def _run(world, honcho, *, workspace="67", apply=False):
    policy, router = world
    memory = HonchoClient(honcho.url, "nora", workspace_router=router)
    return await carry_conclusions(
        workspace=workspace, policy=policy, router=router, memory=memory,
        ai_peer="nora", apply=apply,
    )


def _scope_rows(honcho, scope: str) -> list[tuple[str, str]]:
    return sorted(
        (c["observed_id"], c["content"]) for c in honcho.conclusions["nora"]
        if c["observer_id"] == f"scope.{scope}"
    )


async def test_a_dry_run_counts_and_writes_nothing(world, honcho):
    before = list(honcho.conclusions["nora"])
    report = await _run(world, honcho)
    assert report.sources == 2
    assert [(u.unit, u.carried) for u in report.units] == [("friends", 2), ("wedding", 2)]
    assert honcho.conclusions["nora"] == before
    assert "dry run: nothing written" in render(report)[0]


async def test_apply_carries_session_less_facts_into_every_unit_scope(world, honcho):
    report = await _run(world, honcho, apply=True)
    assert not report.failed
    expected = [("jordan", "likes tea"), ("blair", "moved to Kowloon")]
    assert _scope_rows(honcho, "friends") == expected
    assert _scope_rows(honcho, "wedding") == expected
    # Never into another workspace's scope.
    assert "nora-ridgeline" not in honcho.conclusions


async def test_a_second_run_carries_nothing(world, honcho):
    await _run(world, honcho, apply=True)
    again = await _run(world, honcho, apply=True)
    assert [(u.carried, u.already) for u in again.units] == [(0, 2), (0, 2)]
    assert len(_scope_rows(honcho, "friends")) == 2


async def test_an_unbound_workspace_is_refused(world, honcho):
    with pytest.raises(CarryRefusedError, match="no workspace named"):
        await _run(world, honcho, workspace="nobody")
