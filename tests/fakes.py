"""Offline test doubles shared across the suite. Grows as the plan progresses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import (
    AdapterHealth,
    PlatformIdentity,
    SendResult,
)


class SinkSender:
    """A PlatformAdapter that records sends instead of performing them (spec E.3)."""

    platform = "signal"

    def __init__(
        self, *, succeed: bool = True, account: str = "+00000000000",
        timestamp: int | None = None,
    ) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self.sent_mentions: list[tuple] = []
        self.sent_styles: list[tuple] = []
        self.sent_attachments: list[tuple] = []
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

    async def send(
        self, target: ConversationTarget, text: str,
        mentions: tuple = (), styles: tuple = (), preview: object | None = None,
        attachments: tuple = (),
    ) -> SendResult:
        """Accepts (and records) the port's decoration parameters.

        Not optional politeness: `Deliverer` passes all three on EVERY send, so
        a double that did not accept them would `TypeError` on a plain message —
        which is the point of the port having grown them rather than the
        `Deliverer` passing them conditionally. Recorded rather than dropped so
        an integration test can assert an @-mention or a bold range actually
        reached the transport, not merely that the composer built one.
        """
        self.sent.append((target, text))
        self.sent_mentions.append(tuple(mentions))
        self.sent_styles.append(tuple(styles))
        self.sent_attachments.append(tuple(attachments))
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
