"""`nora memory carry-conclusions` (`nora.app.memory_carry`, DEC-214): the
facts Nora was told before unit scopes existed, copied into every unit scope
of their workspace, through the production client against
`tests.fakes.FakeHoncho`. Placeholder people; the workspace names mirror the
operator's mapping (`67` = friends + wedding on `nora`).
"""

from __future__ import annotations

from datetime import UTC, datetime

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
persons = ["jordan", "blair"]
unit = "friends"

[[conversations]]
platform = "signal"
id = "g-wedding"
label = "Wedding"
persons = ["jordan"]
unit = "wedding"

[[conversations]]
platform = "signal"
id = "g-ridgeline"
label = "Ridgeline"
persons = ["blair"]
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
    old, new = "2026-09-01T00:00:00+00:00", "2026-09-30T00:00:00+00:00"
    fake.conclusions["nora"] = [
        # What `remember` and note discharge wrote BEFORE the cutover: Nora
        # observing, no session. jordan is in workspace 67 only.
        {"id": "c1", "content": "likes tea", "observer_id": "nora",
         "observed_id": "jordan", "session_id": None, "level": "explicit",
         "created_at": old},
        # About blair, who is in 67 AND ridgeline: no provenance says which
        # workspace it was told in, so it is never copied automatically.
        {"id": "c2", "content": "moved to Kowloon", "observer_id": "nora",
         "observed_id": "blair", "session_id": None, "level": "explicit",
         "created_at": old},
        # Derived from a message: its session's scope backfill already covers it.
        {"id": "c3", "content": "said hi", "observer_id": "nora",
         "observed_id": "jordan", "session_id": "nora-conv-g-friends", "level": "explicit",
         "created_at": old},
        # A dream: reasoned across sessions, rebuilt by each scope's own dreams.
        {"id": "c4", "content": "tends to plan", "observer_id": "nora",
         "observed_id": "jordan", "session_id": None, "level": "deductive",
         "created_at": old},
        # Somebody else's view.
        {"id": "c5", "content": "x", "observer_id": "nora-not",
         "observed_id": "jordan", "session_id": None, "level": "explicit",
         "created_at": old},
        # AFTER the cutover: a `remember` told in the wedding room, whose
        # session-less (nora, jordan) row looks exactly like a pre-cutover one.
        {"id": "c6", "content": "is the best man", "observer_id": "nora",
         "observed_id": "jordan", "session_id": None, "level": "explicit",
         "created_at": new},
        {"id": "c7", "content": "is the best man", "observer_id": "scope.wedding",
         "observed_id": "jordan", "session_id": None, "level": "explicit",
         "created_at": new},
        # Before the cutover, but some scope already holds the same fact.
        {"id": "c8", "content": "has a dog", "observer_id": "nora",
         "observed_id": "jordan", "session_id": None, "level": "explicit",
         "created_at": old},
        {"id": "c9", "content": "has a dog", "observer_id": "scope.friends",
         "observed_id": "jordan", "session_id": None, "level": "explicit",
         "created_at": new},
    ]
    fake.scopes[("nora", "friends")] = set()
    fake.scopes[("nora", "wedding")] = set()
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
        ai_peer="nora", apply=apply, before=_CUTOVER,
    )


_CUTOVER = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _scope_rows(honcho, scope: str) -> list[tuple[str, str]]:
    return sorted(
        (c["observed_id"], c["content"]) for c in honcho.conclusions["nora"]
        if c["observer_id"] == f"scope.{scope}"
    )


async def test_a_dry_run_counts_lists_the_ambiguous_by_id_and_writes_nothing(world, honcho):
    before = list(honcho.conclusions["nora"])
    report = await _run(world, honcho)
    assert report.carried == 1                     # "likes tea" only
    assert report.held == 1                        # "has a dog", already in a scope
    assert report.ambiguous == ["c2"]              # blair: two workspaces
    assert honcho.conclusions["nora"] == before
    rendered = "\n".join(render(report))
    assert "dry run: nothing written" in rendered
    assert "c2" in rendered and "Kowloon" not in rendered, "ids only, never content"


async def test_apply_carries_only_pre_cutover_unambiguous_facts_into_every_scope(
    world, honcho,
):
    report = await _run(world, honcho, apply=True)
    assert not report.failed
    assert _scope_rows(honcho, "friends") == [("jordan", "has a dog"), ("jordan", "likes tea")]
    assert _scope_rows(honcho, "wedding") == [
        ("jordan", "is the best man"), ("jordan", "likes tea"),
    ]
    # A fact told in the wedding room after the cutover never reaches friends.
    assert ("jordan", "is the best man") not in _scope_rows(honcho, "friends")
    # Never into another workspace's scope, and never the ambiguous one.
    assert "nora-ridgeline" not in honcho.conclusions
    assert not [r for r in _scope_rows(honcho, "friends") if r[0] == "blair"]


async def test_a_second_run_carries_nothing(world, honcho):
    await _run(world, honcho, apply=True)
    again = await _run(world, honcho, apply=True)
    assert again.carried == 0
    assert len(_scope_rows(honcho, "friends")) == 2


async def test_an_unbound_workspace_is_refused(world, honcho):
    with pytest.raises(CarryRefusedError, match="no workspace named"):
        await _run(world, honcho, workspace="nobody")
