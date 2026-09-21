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

from dataclasses import dataclass, field

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

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str) -> None:
        self.calls.append(("ensure_peer", peer_id))
        self.known_peers.add(peer_id)

    async def feed(self, *, peer_id: str, text: str, conversation_id: str = "",
                    metadata: dict | None = None) -> bool:
        self.calls.append(("feed", peer_id))
        return True

    async def conclude(self, *, peer_id: str, content: str, target: str,
                        workspace_id: str | None = None) -> bool:
        self.calls.append(("conclude", target))
        return target in self.known_peers

    async def peer_card(self, *, peer_id: str) -> list[str]:
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


async def test_know_raises_loudly_if_the_peer_was_never_ensured() -> None:
    """Falsification: with `ensure_peer` never called, `FakeMemory.conclude`
    reproduces the live 404 and `know()` must still fail loud, not silently."""
    subject = _FakeSubject()
    subject.deps.memory.known_peers.discard("miles_novak")  # never ensured
    knowledge = Knowledge(conclusions={"miles_novak": ("miles likes coffee",)})

    async def _broken_conclude(*, peer_id: str, content: str, target: str,
                                workspace_id: str | None = None) -> bool:
        return False  # simulates the old code path: no ensure_peer ever ran

    subject.deps.memory.conclude = _broken_conclude  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="could not write a conclusion"):
        await know(subject, knowledge)  # type: ignore[arg-type]
