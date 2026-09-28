"""`nora memory detach-unit` (`nora.app.memory_detach`, DEC-214): a moved
unit's rooms deleted from the Honcho workspace it left, once its backfill has
landed, through the production client against `tests.fakes.FakeHoncho`.
Placeholder people; the names mirror the operator's mapping.
"""

from __future__ import annotations

import pytest

from nora.app.memory_detach import DetachRefusedError, detach_unit, render
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
persons = ["jordan"]
unit = "friends"

[[conversations]]
platform = "signal"
id = "g-ridgeline"
label = "Ridgeline"
persons = ["blair"]
unit = "ridgeline"

[units.friends]
workspace = "67"

[units.ridgeline]
workspace = "ridgeline"
"""

_OLD = "nora-conv-g-ridgeline"


def _message(peer: str, content: str) -> dict:
    return {"id": "m", "content": content, "peer_id": peer, "session_id": _OLD,
            "metadata": {}}


@pytest.fixture
async def honcho():
    fake = FakeHoncho()
    await fake.start()
    fake.seed("nora", created_at="2026-01-01T00:00:00Z")
    for sid in (_OLD, "nora-conv-g-friends"):
        fake.sessions["nora"][sid] = {"id": sid, "is_active": True, "workspace_id": "nora"}
    fake.messages[("nora", _OLD)] = [_message("blair", "old words"), _message("nora", "hi")]
    fake.messages[("nora", "nora-conv-g-friends")] = [_message("jordan", "stays")]
    fake.conclusions["nora"] = [
        {"id": "c1", "content": "from ridgeline", "observer_id": "nora",
         "observed_id": "blair", "session_id": _OLD, "level": "explicit"},
        {"id": "c2", "content": "from friends", "observer_id": "nora",
         "observed_id": "jordan", "session_id": "nora-conv-g-friends", "level": "explicit"},
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


def _landed(honcho: FakeHoncho) -> None:
    honcho.seed("nora-ridgeline", created_at="2026-09-28T00:00:00Z",
                metadata={"nora_owner": "nora"})
    honcho.sessions["nora-ridgeline"][_OLD] = {
        "id": _OLD, "is_active": True, "workspace_id": "nora-ridgeline",
    }
    honcho.messages[("nora-ridgeline", _OLD)] = [_message("blair", "old words")]


async def _run(world, honcho, *, unit="ridgeline", source="nora", apply=False):
    policy, router = world
    return await detach_unit(
        unit=unit, from_honcho_workspace=source, policy=policy, router=router,
        memory=HonchoClient(honcho.url, "nora", workspace_router=router),
        ai_peer="nora", apply=apply,
    )


async def test_it_refuses_until_the_backfill_has_landed(world, honcho):
    with pytest.raises(DetachRefusedError, match="run `nora memory backfill` first"):
        await _run(world, honcho, apply=True)
    assert honcho.deleted_sessions == []


async def test_it_refuses_the_workspace_the_unit_is_bound_to_now(world, honcho):
    _landed(honcho)
    with pytest.raises(DetachRefusedError, match="live memory"):
        await _run(world, honcho, source="nora-ridgeline", apply=True)


async def test_a_dry_run_names_what_it_would_delete_and_deletes_nothing(world, honcho):
    _landed(honcho)
    report = await _run(world, honcho)
    assert report.sessions == [_OLD]
    assert report.peers == ["blair", "nora"]
    assert honcho.deleted_sessions == [] and honcho.dreams == []
    assert "dry run: nothing deleted" in render(report)[0]


async def test_apply_deletes_only_the_units_sessions_and_rebuilds_the_cards(world, honcho):
    _landed(honcho)
    report = await _run(world, honcho, apply=True)
    assert not report.failed
    assert honcho.deleted_sessions == [("nora", _OLD)]
    # The session's documents go with it (Honcho's cascade); the unit that
    # stayed keeps everything.
    assert [c["id"] for c in honcho.conclusions["nora"]] == ["c2"]
    assert ("nora", "nora-conv-g-friends") in honcho.messages
    refreshed = sorted(
        (b["observer"], b["observed"]) for (w, b) in honcho.dreams if w == "nora"
    )
    assert refreshed == [("blair", "blair"), ("nora", "blair"), ("nora", "nora")]
    assert all(b["dream_type"] == "card_refresh" and b["rebuild"] for (_w, b) in honcho.dreams)
    # The new workspace is untouched.
    assert honcho.messages[("nora-ridgeline", _OLD)]
