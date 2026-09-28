"""`nora memory detach-unit` (`nora.app.memory_detach`, DEC-214): a moved
unit's rooms deleted from the Honcho workspace it left, once its backfill has
landed, through the production client against `tests.fakes.FakeHoncho`.
Placeholder people; the names mirror the operator's mapping.
"""

from __future__ import annotations

import json

import pytest

from nora.app.memory_detach import (
    DetachRefusedError,
    detach_unit,
    refresh_cards,
    render,
)
from nora.app.memory_routing import build_workspace_router
from nora.domain.models import NormalizedMessage
from nora.infra.honcho import HonchoClient
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeRecordStore
from tests.fakes import FakeHoncho

_IDENTITY = """
[persons.jordan]
name = "Jordan"
[[persons.jordan.signal]]
phone = "+85200000001"

[persons.blair]
name = "Blair"
[[persons.blair.signal]]
phone = "+85200000002"

[workspaces.67]
honcho_workspace = "nora"

[workspaces.ridgeline]
honcho_workspace = "nora-ridgeline"
"""

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
async def world(tmp_path):
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    policy = load_policy(tmp_path / "policy.toml")
    persons = load_persons(tmp_path / "identity.toml")
    router = build_workspace_router(
        policy=policy, bindings=persons.honcho_workspaces, default_workspace="nora",
    )
    records = FakeRecordStore()
    for ts in (1000, 2000):
        await records.append(message=NormalizedMessage(
            platform="signal", conversation_id="g-ridgeline", conversation_type="group",
            sender_id="+85200000002", sender_name=None, timestamp=ts, body=f"said {ts}",
            raw={},
        ), person_id="blair")
    return policy, persons, router, records, tmp_path / "state" / "detach.json"


def _landed(honcho: FakeHoncho, *, fully: bool = True) -> None:
    honcho.seed("nora-ridgeline", created_at="2026-09-28T00:00:00Z",
                metadata={"nora_owner": "nora"})
    honcho.sessions["nora-ridgeline"][_OLD] = {
        "id": _OLD, "is_active": True, "workspace_id": "nora-ridgeline",
    }
    replayed = [{**_message("blair", "said 1000"), "metadata": {"ts": 1000}}]
    if fully:
        replayed.append({**_message("blair", "said 2000"), "metadata": {"ts": 2000}})
    honcho.messages[("nora-ridgeline", _OLD)] = replayed


async def _run(world, honcho, *, unit="ridgeline", source="nora", apply=False):
    policy, persons, router, records, peers_file = world
    return await detach_unit(
        unit=unit, from_honcho_workspace=source, policy=policy, persons=persons,
        router=router, records=records,
        memory=HonchoClient(honcho.url, "nora", workspace_router=router),
        peers_file=peers_file, apply=apply,
    )


async def _refresh(world, honcho, *, attempts=3):
    _policy, _persons, router, _records, peers_file = world
    return await refresh_cards(
        peers_file=peers_file,
        memory=HonchoClient(honcho.url, "nora", workspace_router=router),
        ai_peer="nora", poll_attempts=attempts, poll_seconds=0,
    )


async def test_it_refuses_until_the_backfill_has_landed(world, honcho):
    with pytest.raises(DetachRefusedError, match="run `nora memory backfill` first"):
        await _run(world, honcho, apply=True)
    assert honcho.deleted_sessions == []


async def test_it_refuses_the_workspace_the_unit_is_bound_to_now(world, honcho):
    _landed(honcho)
    with pytest.raises(DetachRefusedError, match="live memory"):
        await _run(world, honcho, source="nora-ridgeline", apply=True)


async def test_it_refuses_while_the_backfill_would_still_feed_something(world, honcho):
    """One replayed message is not «landed»: the backfill's own dry run over
    the same rows must have nothing left."""
    _landed(honcho, fully=False)
    with pytest.raises(DetachRefusedError, match="would still feed 1 message"):
        await _run(world, honcho, apply=True)
    assert honcho.deleted_sessions == []


async def test_a_dry_run_names_what_it_would_delete_and_deletes_nothing(world, honcho):
    _landed(honcho)
    report = await _run(world, honcho)
    assert report.sessions == [_OLD]
    assert report.peers == ["blair", "nora"]
    assert honcho.deleted_sessions == [] and honcho.dreams == []
    assert not world[-1].exists()
    assert "dry run: nothing deleted" in render(report, world[-1])[0]


async def test_apply_records_the_peers_then_deletes_only_the_units_sessions(world, honcho):
    _landed(honcho)
    report = await _run(world, honcho, apply=True)
    assert not report.failed
    assert honcho.deleted_sessions == [("nora", _OLD)]
    recorded = json.loads(world[-1].read_text())
    assert recorded == {
        "unit": "ridgeline", "old_workspace": "nora",
        "sessions": [_OLD], "peers": ["blair", "nora"],
    }
    # No card is touched by the delete step: Honcho has not finished deleting.
    assert honcho.dreams == []
    # The session's documents go with it (Honcho's cascade); the unit that
    # stayed keeps everything; the new workspace is untouched.
    assert [c["id"] for c in honcho.conclusions["nora"]] == ["c2"]
    assert ("nora", "nora-conv-g-friends") in honcho.messages
    assert honcho.messages[("nora-ridgeline", _OLD)]


async def test_a_rerun_after_the_delete_keeps_the_recorded_peers(world, honcho):
    """After the delete Honcho can no longer name the sessions' peers, so a
    rerun must not overwrite the file with an empty list."""
    _landed(honcho)
    await _run(world, honcho, apply=True)
    await _run(world, honcho, apply=True)
    assert json.loads(world[-1].read_text())["peers"] == ["blair", "nora"]


async def test_cards_wait_for_honchos_delete_queue_and_then_rebuild(world, honcho):
    _landed(honcho)
    honcho.defer_session_deletes = True
    await _run(world, honcho, apply=True)
    # Queued, not done: the ridgeline document is still there, so no card may
    # be rebuilt yet — it would be rebuilt FROM it.
    with pytest.raises(DetachRefusedError, match="delete queue has not finished"):
        await _refresh(world, honcho)
    assert honcho.dreams == []

    honcho.run_delete_queue()
    report = await _refresh(world, honcho)
    assert not report.failed
    refreshed = sorted(
        (b["observer"], b["observed"]) for (w, b) in honcho.dreams if w == "nora"
    )
    assert refreshed == [("blair", "blair"), ("nora", "blair"), ("nora", "nora")]
    assert all(b["dream_type"] == "card_refresh" and b["rebuild"] for (_w, b) in honcho.dreams)
    # Rerunnable.
    again = await _refresh(world, honcho)
    assert len(again.refreshed) == 3
