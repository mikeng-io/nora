"""`know()`'s conclusion-seeding lane must create the target peer in Honcho
before writing about them.

Production never needs this: a peer always exists by the time anyone
concludes about it, because it was created by a real `feed()` call first. The
eval harness's `know()` is a second caller of `conclude()` that skips straight
to the write — the sandbox's Honcho workspace is `_baseline()`'s `-eval`
sibling of the deployment's, not the one production feeds, so the peer may
never have been created in it. Live-run evidence
(`design/evaluation/10-the-scenario-map.md`): `participation`'s
`admin-joke-known`/`asked-before-known` cases errored against a real,
tunnelled Honcho with "did not accept it" — traced to a live 404, `Peer
miles_novak not found in workspace nora`, confirmed by curling the endpoint
directly.

`FakeMemory` below mirrors that real behaviour (`conclude` 404s for an
unensured peer) rather than a mock the fix can trivially satisfy without
actually calling `ensure_peer` correctly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from nora.config import MemoryConfig
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
    persons: object = field(default_factory=object)


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
        return False  # simulates the old code path: no ensure_peer ever ran

    subject.deps.memory.conclude = _broken_conclude  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="could not write a conclusion"):
        await know(subject, knowledge)  # type: ignore[arg-type]


# --- `settle()` waits for THIS case's turn, not for an empty process --------


class _Background:
    """`BackgroundTasks`'s public surface, as `settle()` uses it."""

    def __init__(self, count: int) -> None:
        self.count = count

    def __len__(self) -> int:
        return self.count


@pytest.mark.asyncio
async def test_settle_returns_once_the_turn_is_done_though_a_loop_is_still_running():
    """MEASURED LIVE on the deploy host, 2026-09-21: every `near_miss` case
    ERRORed with "the turn never settled" against a subject composed from the
    DEPLOYMENT's config, and passed under the example's.

    Composition spawns ENDLESS loops into the same set the turn is spawned
    into — `ComputeJobPoller.run()` when `[compute]` is enabled,
    `VersionAnnouncer.run()`, `heartbeat.run()`. Waiting for that set to reach
    ZERO is waiting for the process to exit, so every case that drove the
    inbound path timed out and was reported as an un-run case. The deployment
    enables compute; the example does not, which is why no test saw it.
    """
    from nora.subject import pending, settle

    background = _Background(2)          # two endless loops, already running
    subject = SimpleNamespace(deps=SimpleNamespace(background=background))
    baseline = pending(subject)          # read BEFORE the line arrives
    assert baseline == 2

    background.count = 3                 # the turn is spawned

    async def _finish() -> None:
        await asyncio.sleep(0.05)
        background.count = 2             # the turn ends; the loops do not

    task = asyncio.create_task(_finish())
    assert await settle(subject, baseline=baseline, timeout_seconds=5.0) is True
    await task


@pytest.mark.asyncio
async def test_settle_still_times_out_when_the_turn_never_finishes():
    """The control: the baseline must not turn a hung turn into a settled one,
    which is the failure mode this whole distinction exists to keep visible."""
    from nora.subject import settle

    subject = SimpleNamespace(deps=SimpleNamespace(background=_Background(3)))
    assert await settle(subject, baseline=2, timeout_seconds=0.2) is False
