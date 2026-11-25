"""`FakePlatformAdapter` — a platform-neutral adapter for the liveness path
(design/platform-bridges/platform-liveness.md), on the shared `DegradeLatch`
like every real adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from nora.domain.latch import DegradeLatch
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import (
    AdapterHealth,
    LinkPreview,
    Mention,
    OutboundAttachment,
    PlatformIdentity,
    ReactionResult,
    SendResult,
    TextStyle,
)

#: A scripted `resolve_identity` answer that never returns — the hung bridge.
HANG = object()


class FakePlatformAdapter(DegradeLatch):
    """A platform-neutral `PlatformAdapter` for the liveness path
    (design/platform-bridges/platform-liveness.md): any `platform` name —
    the stand-in for a platform the table does not have yet (`telegram`) —
    on the shared `DegradeLatch`, like every real adapter.

    `identities` scripts `resolve_identity`, one answer per call: a
    `PlatformIdentity` is returned, an exception raised, `HANG` never
    returns; the LAST answer repeats. Every upstream effect is counted —
    `resolve_calls`, `listen_calls`, `sent`, `reactions`, `health_calls` —
    so a test can prove a degraded adapter made none: the latch answers
    `send`/`react`/`health` before this class's own code runs.
    """

    def __init__(self, platform: str = "telegram", *,
                 identities: Sequence[object] = ()) -> None:
        self.platform = platform
        self._answers = list(identities) or [
            PlatformIdentity(platform=platform, primary=f"{platform}-nora")]
        self.resolve_calls = 0
        self.listen_calls = 0
        self.health_calls = 0
        self.sent: list[tuple[ConversationTarget, str]] = []
        self.reactions: list[tuple[ConversationTarget, str]] = []
        #: What the registry knew at the moment `listen` first ran — the
        #: probe for «identity registered before any event».
        self.on_first_listen: Callable[[], object] | None = None

    async def resolve_identity(self) -> PlatformIdentity:
        index = min(self.resolve_calls, len(self._answers) - 1)
        self.resolve_calls += 1
        answer = self._answers[index]
        if answer is HANG:
            await asyncio.Event().wait()
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, PlatformIdentity)
        return answer

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]],
    ) -> None:
        self.listen_calls += 1
        if self.listen_calls == 1 and self.on_first_listen is not None:
            self.on_first_listen()
        await asyncio.Event().wait()

    async def send(
        self, *, target: ConversationTarget, text: str,
        mentions: tuple[Mention, ...] = (), styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: object | None = None,
    ) -> SendResult:
        self.sent.append((target, text))
        return SendResult(ok=True)

    async def react(
        self, *, target: ConversationTarget, emoji: str, to_author: str,
        to_timestamp: int, to_message_id: str = "", remove: bool = False,
    ) -> ReactionResult:
        self.reactions.append((target, emoji))
        return ReactionResult(ok=True)

    async def health(self) -> AdapterHealth:
        self.health_calls += 1
        return AdapterHealth(platform=self.platform, connected=True,
                             last_event_age_seconds=0.0)
