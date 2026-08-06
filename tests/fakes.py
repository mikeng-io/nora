"""Offline test doubles shared across the suite. Grows as the plan progresses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import (
    AdapterHealth,
    MemoryHealth,
    PlatformIdentity,
    Recollection,
    SendResult,
)


class FakeHoncho:
    """Offline Honcho double. `fail=True` simulates the service being down."""

    def __init__(
        self,
        *,
        cards: dict[str, list[str]] | None = None,
        conclusions: dict[str, list[str]] | None = None,
        fail: bool = False,
    ) -> None:
        self.fed: list[tuple[str, str]] = []
        self.peers: list[str] = []
        self.cards = cards or {}
        # Kept apart from `cards`: `peer_card` and `representation` are
        # different live endpoints with different failure shapes, and one dict
        # serving both would make them look interchangeable.
        self.conclusions = conclusions or {}
        self.queries: list[tuple[str, str]] = []
        self.fail = fail

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, peer_id: str) -> None:
        if not self.fail:
            self.peers.append(peer_id)

    async def feed(self, peer_id: str, text: str) -> bool:
        if self.fail:
            return False
        self.fed.append((peer_id, text))
        return True

    async def peer_card(self, peer_id: str) -> list[str]:
        if self.fail:
            return []
        return list(self.cards.get(peer_id, []))

    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = 5
    ) -> Recollection:
        """Mirrors `HonchoClient.representation` including its unknown-peer
        shape: the live instance answers 200 with an EMPTY STRING rather than
        404, so an unknown peer is reachable-but-empty here, and only
        `fail=True` is unreachable."""
        self.queries.append((peer_id, search_query))
        if self.fail:
            return Recollection(reachable=False)
        return Recollection(
            reachable=True,
            conclusions=tuple(self.conclusions.get(peer_id, ()))[:max_conclusions],
        )

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=not self.fail)

    async def aclose(self) -> None:
        return None


class SinkSender:
    """A PlatformAdapter that records sends instead of performing them (spec E.3)."""

    platform = "signal"

    def __init__(
        self, *, succeed: bool = True, account: str = "+00000000000",
        timestamp: int | None = None,
    ) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self.events: list[dict] = []
        self._succeed = succeed
        self._account = account
        # The platform's OWN sent-timestamp, which real signal-cli returns and
        # a reply's `quote.id` points at (design/11 §2.3). `None` — the default
        # — is the shape of a daemon that answered without one.
        self._timestamp = timestamp

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        for event in self.events:
            await on_event(event)

    async def parse(self, raw_event: dict) -> NormalizedMessage | None:
        from nora.platform.signal.parser import parse_event

        return parse_event(raw_event, own_account="+00000000000")

    async def resolve_identity(self) -> PlatformIdentity:
        return PlatformIdentity(platform=self.platform, primary=self._account)

    async def send(self, target: ConversationTarget, text: str) -> SendResult:
        self.sent.append((target, text))
        return SendResult(ok=self._succeed, timestamp=self._timestamp)

    async def health(self) -> AdapterHealth:
        return AdapterHealth(platform=self.platform, connected=True, last_event_age_seconds=0.0)

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FixedModelSource:
    """A `nora.agent.factory.ModelSource` stand-in that hands out ONE given model.

    For the tests that drive the REAL Strands loop against a stub `Model` — the
    turn-ending probe, the stop-reason probe, the scripted-agent fidelity probe
    — where the model is the instrument being read and there is exactly one
    turn, so per-tag freshness is not the property under test.

    **Deliberately the same object every time, and that is not the
    answers-everything-the-same trap**: the property this class does NOT hold
    is the subject of its own probe file, `tests/test_model_per_tag.py`, which
    uses a source that builds a fresh model per `create()` and asserts on what
    each tag's model call actually saw. Reach for this one only when a test
    wants a single model it can inspect afterwards.
    """

    def __init__(self, model: object) -> None:
        self._model = model
        self.creates = 0

    def create(self) -> object:
        self.creates += 1
        return self._model
