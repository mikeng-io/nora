"""`know()`'s conclusion-seeding lane must create the target peer in Honcho
before writing about them.

Production never needs this: a peer always exists by the time anyone
concludes about it, because it was created by a real `feed()` call first. The
eval harness's `know()` is a second caller of `conclude()` that skips straight
to the write — the sandbox's Honcho workspace is `_baseline()`'s `-eval`
sibling of the deployment's, not the one production feeds, so the peer may
never have been created in it, and Honcho answers 404 (`Peer miles_novak not
found in workspace nora`).

`FakeMemory` below mirrors that behaviour (`conclude` 404s for an
unensured peer) rather than a mock that can trivially be satisfied without
actually calling `ensure_peer` correctly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from nora.config import MemoryConfig
from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.subject.knowledge import Knowledge, Message, know
from nora.testing import FakeRecordStore


class FakeMemory:
    """`MemoryStore` double that ENFORCES the real Honcho invariant: `conclude`
    fails for a peer nobody `ensure_peer`'d first."""

    def __init__(self) -> None:
        self.known_peers: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.routes: list[tuple[str, str | None, str | None]] = []

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str, conversation_id: str | None = None,
                          unit: str | None = None) -> None:
        self.calls.append(("ensure_peer", peer_id))
        self.routes.append(("ensure_peer", conversation_id, unit))
        self.known_peers.add(peer_id)

    async def feed(self, *, peer_id: str, text: str, conversation_id: str = "",
                    metadata: dict | None = None) -> bool:
        self.calls.append(("feed", peer_id))
        return True

    async def conclude(self, *, peer_id: str, content: str, target: str,
                        conversation_id: str | None = None,
                        unit: str | None = None) -> bool:
        self.calls.append(("conclude", target))
        self.routes.append(("conclude", conversation_id, unit))
        return target in self.known_peers

    async def peer_card(self, *, peer_id: str, conversation_id: str | None = None,
                        unit: str | None = None) -> list[str]:
        return []


@dataclass
class _FakeDeps:
    audit: FakeRecordStore = field(default_factory=FakeRecordStore)
    memory: FakeMemory = field(default_factory=FakeMemory)
    persons: Persons = field(default_factory=lambda: Persons(by_person={}, index={}))


@dataclass
class _FakeCfg:
    memory: MemoryConfig = field(
        default_factory=lambda: MemoryConfig(base_url="http://fake", workspace_id="nora")
    )


@dataclass
class _FakeSubject:
    deps: _FakeDeps = field(default_factory=_FakeDeps)
    cfg: _FakeCfg = field(default_factory=_FakeCfg)
    room: tuple[str, str] = ("signal", "g1")
    database_url: str = "postgresql+asyncpg://user:pass@localhost/db"


async def test_know_seeds_roster_senders_for_production_name_projection() -> None:
    subject = _FakeSubject()
    subject.deps.persons = Persons(
        by_person={"ada": PersonRef("ada", "Ada Roster", "ada"),
                   "ben": PersonRef("ben", "Ben Roster", "ben")},
        index={("signal", "+85222222222"): "ada", ("signal", "+852111"): "ben"},
        _subject_aliases={"ada_before": "ada"},
    )
    await know(subject, Knowledge(transcript=(
        Message("human", name="Profile", person="ada"),
        Message("unknown", name="Miles", person=None),
        Message("own", name="Profile", person="ada", is_nora=True),
        Message("other platform", name="Elsewhere", person="no_account"),
        Message("historical person id", name="Old Profile", person="ada_before"),
    )))  # type: ignore[arg-type]
    rows = await subject.deps.audit.recent(conversation_id="g1", limit=10)
    named = subject.deps.persons.name_transcript(rows, platform="signal")
    assert named[0].sender_id == "+85222222222"
    assert named[0].person_name == "Ada Roster"
    assert named[0].sender_name == "Profile"
    assert named[1].person_id is None and named[1].person_name is None
    assert named[2].is_nora and named[2].sender_id != "+85222222222"
    assert named[3].person_id == "no_account" and named[3].person_name is None
    assert named[4].person_id == "ada_before" and named[4].person_name == "Ada Roster"


async def test_know_ensures_the_peer_before_concluding_about_them() -> None:
    subject = _FakeSubject()
    knowledge = Knowledge(
        transcript=(Message(body="hi"),),
        conclusions={"miles_novak": ("miles likes coffee",)},
    )

    await know(subject, knowledge)  # type: ignore[arg-type]

    assert ("ensure_peer", "miles_novak") in subject.deps.memory.calls
    ensure_index = subject.deps.memory.calls.index(("ensure_peer", "miles_novak"))
    conclude_index = subject.deps.memory.calls.index(("conclude", "miles_novak"))
    assert ensure_index < conclude_index


async def test_know_routes_the_seed_by_the_subjects_room() -> None:
    """DEC-214: a seeded conclusion goes where a `remember` in this room
    would — routed by the room, so its unit's workspace and named scope, the
    place the room's reads look. Unrouted, it would land in the implicit
    workspace's unscoped collection and every scoped read would miss it."""
    subject = _FakeSubject()
    knowledge = Knowledge(
        transcript=(Message(body="hi"),),
        conclusions={"miles_novak": ("miles likes coffee",)},
    )

    await know(subject, knowledge)  # type: ignore[arg-type]

    routed = [r for r in subject.deps.memory.routes if r[0] in ("ensure_peer", "conclude")]
    assert routed and all(r[1] == "g1" and r[2] is None for r in routed), routed


async def test_know_raises_loudly_if_the_peer_was_never_ensured() -> None:
    """Falsification: with `ensure_peer` never called, `FakeMemory.conclude`
    reproduces the live 404 and `know()` must still fail loud, not silently."""
    subject = _FakeSubject()
    subject.deps.memory.known_peers.discard("miles_novak")  # never ensured
    knowledge = Knowledge(conclusions={"miles_novak": ("miles likes coffee",)})

    async def _broken_conclude(*, peer_id: str, content: str, target: str,
                                conversation_id: str | None = None,
                                unit: str | None = None) -> bool:
        return False  # simulates a path where ensure_peer never ran

    subject.deps.memory.conclude = _broken_conclude  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="could not write a conclusion"):
        await know(subject, knowledge)  # type: ignore[arg-type]


# --- `settle()` waits for THIS case's turn, not for an empty process --------


def _subject() -> Any:
    from nora.app.runtime import BackgroundTasks

    return SimpleNamespace(deps=SimpleNamespace(background=BackgroundTasks()))


async def _forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_settle_returns_once_the_turn_is_done_though_a_loop_is_still_running():
    """Against a subject composed from the DEPLOYMENT's config, `settle()` waits
    for THIS case's turn, not for an empty process.

    Composition spawns ENDLESS loops into the same set the turn is spawned
    into — `ComputeJobPoller.run()` when `[compute]` is enabled,
    `heartbeat.run()`. Waiting for that set to reach
    ZERO is waiting for the process to exit.
    """
    from nora.subject import settle, watch

    subject = _subject()
    for _ in range(2):
        subject.deps.background.spawn(_forever())     # two endless loops, already running
    spawned = watch(subject)                          # BEFORE the line arrives
    subject.deps.background.spawn(asyncio.sleep(0.05))  # the turn
    assert await settle(subject, spawned=spawned, timeout_seconds=5.0) is True
    await subject.deps.background.cancel_all()


@pytest.mark.asyncio
async def test_settle_waits_for_the_turn_when_an_earlier_task_finishes_first():
    """A presence pre-flight still running when the line arrives and finishing
    mid-turn must not let a count fall back to its baseline with the turn still
    running: that would tear the turn down and grade it silent."""
    from nora.subject import settle, watch

    subject = _subject()
    subject.deps.background.spawn(asyncio.sleep(0.05))  # a pre-flight, still reading
    spawned = watch(subject)
    turn_done = asyncio.Event()

    async def turn() -> None:
        await asyncio.sleep(0.3)
        turn_done.set()

    subject.deps.background.spawn(turn())
    assert await settle(subject, spawned=spawned, timeout_seconds=5.0) is True
    assert turn_done.is_set()


@pytest.mark.asyncio
async def test_settle_waits_for_what_the_turn_itself_spawns():
    from nora.subject import settle, watch

    subject = _subject()
    spawned = watch(subject)
    follow_up_done = asyncio.Event()

    async def follow_up() -> None:
        await asyncio.sleep(0.2)
        follow_up_done.set()

    async def turn() -> None:
        subject.deps.background.spawn(follow_up())

    subject.deps.background.spawn(turn())
    assert await settle(subject, spawned=spawned, timeout_seconds=5.0) is True
    assert follow_up_done.is_set()


@pytest.mark.asyncio
async def test_settle_still_times_out_when_the_turn_never_finishes():
    """The control: a hung turn is never a settled one, which is the failure
    mode this whole distinction exists to keep visible. And the subject's own
    `spawn` is put back either way."""
    from nora.subject import settle, watch

    subject = _subject()
    spawned = watch(subject)
    subject.deps.background.spawn(_forever())
    assert await settle(subject, spawned=spawned, timeout_seconds=0.2) is False
    assert "spawn" not in vars(subject.deps.background)
    await subject.deps.background.cancel_all()
