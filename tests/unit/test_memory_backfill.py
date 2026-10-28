"""`nora memory backfill` (`nora.app.memory_backfill`, DEC-214): one unit's
recorded human messages replayed into its own Honcho workspace, through the
production client and router, against `tests.fakes.FakeHoncho`.

Every id and name is a placeholder; the unit names mirror the operator's
mapping (`67` = friends + wedding on `nora`, `ridgeline` on its own).
"""

from __future__ import annotations

import pytest

from nora.app.memory_backfill import BackfillRefusedError, backfill_unit, render
from nora.app.memory_routing import build_workspace_router
from nora.domain.models import NormalizedMessage
from nora.infra.honcho import HonchoClient
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeRecordStore
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
id = "g-ridgeline"
also_ids = ["g-ridgeline-old"]
label = "Ridgeline"
persons = "*"
unit = "ridgeline"

[units.friends]
workspace = "67"

[units.ridgeline]
workspace = "ridgeline"
"""

_IDENTITY = """
[persons.jordan]
name = "Jordan"
[[persons.jordan.signal]]
phone = "+85200000001"

[persons.blair]
name = "Blair"
former_person_ids = ["blair_old"]
[[persons.blair.signal]]
phone = "+85200000002"

[workspaces.67]
honcho_workspace = "nora"
"""

_SESSION = "nora-conv-g-ridgeline"


def _msg(cid: str, ts: int, body: str, sender: str = "+85200000001") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=cid, conversation_type="group",
        sender_id=sender, sender_name=None, timestamp=ts, body=body, raw={},
    )


@pytest.fixture
async def honcho():
    fake = FakeHoncho()
    await fake.start()
    yield fake
    await fake.close()


@pytest.fixture
def world(tmp_path):
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    policy = load_policy(tmp_path / "policy.toml")
    persons = load_persons(tmp_path / "identity.toml")
    router = build_workspace_router(
        policy=policy, bindings=persons.honcho_workspaces, default_workspace="nora",
    )
    return policy, persons, router


async def _records() -> FakeRecordStore:
    """Out of timestamp order on purpose, with one of each thing the replay
    must skip: Nora's own row, an unmapped sender, an attachment-only row,
    and a row of ANOTHER unit that must never reach this one's workspace."""
    store = FakeRecordStore()
    await store.append(message=_msg("g-ridgeline", 3000, "third"), person_id="jordan")
    await store.append(message=_msg("g-ridgeline", 1000, "first"), person_id="jordan")
    await store.append(
        message=_msg("g-ridgeline", 2000, "second", "+85200000002"), person_id="blair_old",
    )
    await store.append(message=_msg("g-ridgeline", 2500, "nora speaking"), is_nora=True)
    await store.append(message=_msg("g-ridgeline", 2600, "who is this", "+85299999999"))
    await store.append(message=_msg("g-ridgeline", 2700, "   "), person_id="jordan")
    await store.append(message=_msg("g-ridgeline-old", 500, "from the old id"), person_id="jordan")
    edited = await store.append(message=_msg("g-ridgeline", 4000, "at 7"), person_id="jordan")
    await store.append_edit(message_id=edited, message=_msg("g-ridgeline", 4100, "at 8"))
    await store.append(message=_msg("g-friends", 1500, "friends only"), person_id="jordan")
    return store


async def _run(world, honcho, records, **kw):
    policy, persons, router = world
    memory = HonchoClient(honcho.url, "nora", workspace_router=router)
    return await backfill_unit(
        unit=kw.pop("unit", "ridgeline"), policy=policy, persons=persons, router=router,
        records=records, memory=memory, default_workspace="nora", **kw,
    )


async def test_a_dry_run_reads_everything_and_writes_nothing(world, honcho):
    report = await _run(world, honcho, await _records())
    assert not report.applied
    assert honcho.messages == {}
    assert not [r for r in honcho.requests if r[0] == "POST" and r[1].endswith("/messages")]
    by_id = {room.conversation_id: room for room in report.rooms}
    assert by_id["g-ridgeline"].fed == 5      # first, second, third, and the edit's two
    assert by_id["g-ridgeline-old"].fed == 1
    assert "dry run: nothing written" in render(report)[0]


async def test_apply_replays_human_rows_in_timestamp_order_the_live_way(world, honcho):
    report = await _run(world, honcho, await _records(), apply=True)
    assert report.failed == 0
    held = honcho.messages[("ridgeline", _SESSION)]
    assert [(m["peer_id"], m["content"]) for m in held] == [
        ("jordan", "first"),
        ("blair", "second"),          # a retired person_id replays as the person now
        ("jordan", "third"),
        ("jordan", "at 7"),
        ("jordan", "[edited an earlier message; it first said: «at 7»] at 8"),
    ]
    assert [m["metadata"] for m in held][:1] == [{"ts": 1000}]
    assert held[-1]["metadata"] == {"ts": 4100, "edit_of_ts": 4000}
    # Dated when it was SAID, not when it was replayed: `MessageCreate.created_at`.
    assert held[0]["created_at"] == "1970-01-01T00:00:01+00:00"
    assert held[-1]["created_at"] == "1970-01-01T00:00:04.100000+00:00"
    # The retired id's rows go to ITS OWN session, as live ingestion fed them.
    assert [m["content"] for m in honcho.messages[("ridgeline", "nora-conv-g-ridgeline-old")]] == [
        "from the old id",
    ]
    # The same scope membership the live path gives the room.
    assert _SESSION in honcho.scopes[("ridgeline", "ridgeline")]
    # Nothing of any other unit, and nothing at all in the workspace it left.
    assert not [k for k in honcho.messages if k[0] == "nora"]
    counts = {room.conversation_id: room for room in report.rooms}["g-ridgeline"]
    assert (counts.unmapped, counts.empty) == (1, 1), "unmapped sender, blank row"


async def test_a_session_that_already_holds_messages_refuses_without_resume(world, honcho):
    records = await _records()
    await _run(world, honcho, records, apply=True)
    before = {k: list(v) for k, v in honcho.messages.items()}
    with pytest.raises(BackfillRefusedError, match="--resume"):
        await _run(world, honcho, records, apply=True)
    assert honcho.messages == before, "the refusal came before any write"


async def test_resume_skips_what_the_session_holds_and_feeds_the_rest(world, honcho):
    records = await _records()
    honcho.fail_messages_after = 2          # a run that dies after two feeds
    first = await _run(world, honcho, records, apply=True)
    assert first.failed == 1
    assert "STOPPED at the first failure" in "\n".join(render(first))

    honcho.fail_messages_after = None
    second = await _run(world, honcho, records, apply=True, resume=True)
    assert second.failed == 0
    contents = [m["content"] for m in honcho.messages[("ridgeline", _SESSION)]]
    assert contents.count("first") == 1 and contents.count("third") == 1
    assert len(contents) == 5, contents
    third = await _run(world, honcho, records, apply=True, resume=True)
    assert sum(room.fed for room in third.rooms) == 0, "a finished run feeds nothing"


async def test_it_refuses_the_workspace_that_already_holds_this_history(world, honcho):
    """`friends` is bound to `nora`, `honcho.workspace_id` — replaying there
    would feed every message a second time."""
    with pytest.raises(BackfillRefusedError, match="twice"):
        await _run(world, honcho, await _records(), unit="friends")


async def test_it_refuses_a_policy_with_no_units(world, honcho, tmp_path):
    _, persons, _ = world
    (tmp_path / "flat.toml").write_text(_POLICY.split("[units.friends]")[0])
    flat = load_policy(tmp_path / "flat.toml")
    router = build_workspace_router(policy=flat, bindings={}, default_workspace="nora")
    with pytest.raises(BackfillRefusedError, match="no \\[units\\]"):
        await backfill_unit(
            unit="ridgeline", policy=flat, persons=persons, router=router,
            records=await _records(),
            memory=HonchoClient(honcho.url, "nora", workspace_router=router),
            default_workspace="nora",
        )


async def test_it_refuses_a_honcho_workspace_another_application_uses(world, honcho):
    """The deploy host's `ridgeline` Honcho workspace holds another
    application's sessions. A dry run must find that out and refuse, and
    write nothing — no workspace, no marker, no message."""
    honcho.seed("ridgeline", created_at="2026-01-01T00:00:00Z")
    honcho.sessions["ridgeline"]["other-app-session-1"] = {
        "id": "other-app-session-1", "is_active": True, "workspace_id": "ridgeline",
    }
    with pytest.raises(BackfillRefusedError, match="another application"):
        await _run(world, honcho, await _records())
    assert honcho.workspaces["ridgeline"]["metadata"] == {}
    assert honcho.messages == {}


async def test_a_dry_run_creates_no_workspace(world, honcho):
    await _run(world, honcho, await _records())
    assert "ridgeline" not in honcho.workspaces


async def test_a_refusal_provisions_nothing(world, honcho):
    """`--apply` sets the workspace up only after every refusal has passed:
    refused for holding history already, nothing may have been created."""
    with pytest.raises(BackfillRefusedError, match="twice"):
        await _run(world, honcho, await _records(), unit="friends", apply=True)
    assert honcho.workspaces == {}


async def test_a_message_said_during_the_run_is_not_replayed(world, honcho):
    """The rows are fixed at the pre-pass: a message recorded while the run is
    writing is live ingestion's to feed, and replaying it too would feed it
    twice."""
    policy, persons, router = world
    records = await _records()

    class _SaysSomethingMidRun(HonchoClient):
        said = False

        async def feed(self, **kw):  # type: ignore[override]
            if not self.said:
                self.said = True
                # In a room the run has not reached yet.
                await records.append(
                    message=_msg("g-ridgeline-old", 9000, "said during the run"),
                    person_id="jordan",
                )
            return await super().feed(**kw)

    memory = _SaysSomethingMidRun(honcho.url, "nora", workspace_router=router)
    await backfill_unit(
        unit="ridgeline", policy=policy, persons=persons, router=router, records=records,
        memory=memory, default_workspace="nora", apply=True,
    )
    later_room = [m["content"] for m in honcho.messages[("ridgeline", "nora-conv-g-ridgeline-old")]]
    assert later_room == ["from the old id"]
    assert "first" in [m["content"] for m in honcho.messages[("ridgeline", _SESSION)]]
