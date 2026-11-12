"""Every platform's person-visible transport lands in a sandbox, the way
`landed_signal_sends` lands Signal's text sends.

An adapter holds its sends in two transports: the `*Sender` (posts,
comments, text) and the `*Reactor` (reactions). Both dial the platform's
endpoint: an unlanded reactor puts a real emoji on a real person's
message exactly the way an unlanded sender puts a real reply under it.
`nora.subject.sandbox_signal`'s argument applies unchanged — a send or
react that cannot land changes the tool result the model reads, so the
sandbox keeps the adapter's own validation and payload building real and
replaces only the wire.

Each platform's seam is the same shape `LandedSignalSender` uses: the
adapter builds its transport from a name in ITS OWN module
(`adapter.SignalSender`), so patching that name while composition runs
makes every adapter built inside the block land. Re-entrant the same
way: two subjects composing at once cannot restore each other's patch,
and each original goes back only when the last block leaves it.
"""

from __future__ import annotations

import contextlib
import importlib
from collections.abc import Iterator
from typing import Any

from nora.domain.ports import ReactionResult, SendResult
from nora.platform.discord.react import DiscordReactor
from nora.platform.discord.send import DiscordSender
from nora.platform.instagram.react import InstagramReactor
from nora.platform.instagram.send import InstagramSender
from nora.platform.signal.react import SignalReactor
from nora.platform.whatsapp.react import WhatsAppReactor
from nora.platform.whatsapp.send import WhatsAppSender

from .sandbox_signal import LandedSignalSender


class LandedSignalReactor(SignalReactor):
    """A `SignalReactor` whose `_rpc` accepts every well-formed reaction."""

    async def _rpc(
        self, payload: dict, conversation_id: str, request_id: str,
    ) -> ReactionResult:
        return ReactionResult(ok=True)


class LandedDiscordSender(DiscordSender):
    """A `DiscordSender` whose `_post` accepts every well-formed send."""

    async def _post(self, *args: Any, **kwargs: Any) -> SendResult:
        return SendResult(ok=True)


class LandedDiscordReactor(DiscordReactor):
    """A `DiscordReactor` whose `_request` accepts every well-formed react."""

    async def _request(self, *args: Any, **kwargs: Any) -> ReactionResult:
        return ReactionResult(ok=True)


class LandedWhatsAppSender(WhatsAppSender):
    """A `WhatsAppSender` whose `_post` accepts every well-formed send."""

    async def _post(
        self, path: str, payload: dict, *, conversation_id: str,
    ) -> SendResult:
        return SendResult(ok=True)


class LandedWhatsAppReactor(WhatsAppReactor):
    """A `WhatsAppReactor` whose `_post` accepts every well-formed react."""

    async def _post(self, payload: dict, *, conversation_id: str) -> ReactionResult:
        return ReactionResult(ok=True)


class LandedInstagramSender(InstagramSender):
    """An `InstagramSender` whose `_post` accepts every well-formed send."""

    async def _post(self, payload: dict, *, conversation_id: str) -> SendResult:
        return SendResult(ok=True)


class LandedInstagramReactor(InstagramReactor):
    """An `InstagramReactor` whose `_put` accepts every well-formed react."""

    async def _put(
        self, payload: dict, conversation_id: str, message_id: str,
    ) -> ReactionResult:
        return ReactionResult(ok=True)


#: `(adapter module, attribute, landed class)` — the name each adapter
#: module resolves when it builds its transport.
_PATCHES: tuple[tuple[str, str, Any], ...] = (
    ("nora.platform.signal.adapter", "SignalSender", LandedSignalSender),
    ("nora.platform.signal.adapter", "SignalReactor", LandedSignalReactor),
    ("nora.platform.discord.adapter", "DiscordSender", LandedDiscordSender),
    ("nora.platform.discord.adapter", "DiscordReactor", LandedDiscordReactor),
    ("nora.platform.whatsapp.adapter", "WhatsAppSender", LandedWhatsAppSender),
    ("nora.platform.whatsapp.adapter", "WhatsAppReactor", LandedWhatsAppReactor),
    ("nora.platform.instagram.adapter", "InstagramSender", LandedInstagramSender),
    ("nora.platform.instagram.adapter", "InstagramReactor", LandedInstagramReactor),
)


def _land(module: Any, name: str, landed: Any) -> Any:
    current: Any = getattr(module, name)
    cls: Any
    if getattr(current, "_landed_depth", None) is not None:
        cls = current
    else:
        cls = type(landed.__name__, (landed,), {})
        cls._landed_depth = 0
        cls._original = current
        setattr(module, name, cls)
    cls._landed_depth += 1
    return cls


@contextlib.contextmanager
def landed_platform_transports() -> Iterator[None]:
    """Make every adapter composed inside the block send and react through
    `Landed*` transports — the `landed_signal_sends` shape, all platforms."""
    actives: list[tuple[Any, str, Any]] = []
    for module_name, attr, landed in _PATCHES:
        module = importlib.import_module(module_name)
        actives.append((module, attr, _land(module, attr, landed)))
    try:
        yield
    finally:
        for module, attr, cls in actives:
            cls._landed_depth -= 1
            if cls._landed_depth == 0:
                setattr(module, attr, cls._original)
